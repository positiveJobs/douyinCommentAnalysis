"""本地 Embedding 模型（ONNX Runtime + tokenizers），带磁盘缓存。

模型：BAAI/bge-small-zh-v1.5（Xenova 提供的 ONNX 转换版），CLS 池化 + L2 归一化。
首次运行会自动下载到 models/bge-small-zh-v1.5。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Sequence

import numpy as np

from . import cancel
from . import config
from . import progress
from .utils import LOG, sha256_hex

CACHE_KEY_PREFIX = config.EMBEDDING_MODEL_REPO + "-onnx-cls-v1"

# 优先级：CUDA > DirectML(Windows) > CoreML(macOS) > OpenVINO > CPU
PROVIDER_PRIORITY = (
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "CoreMLExecutionProvider",
    "OpenVINOExecutionProvider",
    "CPUExecutionProvider",
)

DEVICE_LABELS = {
    "CUDAExecutionProvider": "GPU (CUDA)",
    "DmlExecutionProvider": "GPU (DirectML)",
    "CoreMLExecutionProvider": "GPU (CoreML)",
    "OpenVINOExecutionProvider": "GPU (OpenVINO)",
    "CPUExecutionProvider": "CPU",
}

# 用于设备实测的固定短文本（不涉及用户数据），与真实评论长度接近
BENCHMARK_TEXTS = [
    "建议先学一门技术再考虑进厂",
    "年轻的时候可以多尝试几份工作",
    "工厂流水线工作强度大而且枯燥",
    "家里经济压力大可以先攒点钱",
    "先去大城市找找适合自己的工作",
    "读书提升学历仍然很重要",
    "女生进厂要注意保护自己",
    "可以先做服务员或者奶茶店过渡",
    "不要一直待在舒适圈里",
    "建议先了解清楚再决定",
    "有条件的话尽量读完大学",
    "遇到骚扰要学会拒绝和报警",
    "工作不开心可以考虑换一份",
    "先把简历准备好再投递",
    "多向有经验的人请教",
    "要为自己的选择负责",
]

# 记录最近一次实际使用的执行提供方，供 /api/health 等展示
_LAST_PROVIDER: str | None = None


def available_providers() -> list[str]:
    """当前 onnxruntime 提供的能力列表（不加载模型）。"""
    try:
        import onnxruntime as ort

        return list(ort.get_available_providers())
    except Exception as exc:  # noqa: BLE001
        LOG.warning("无法获取 onnxruntime 执行提供方：%s", exc)
        return []


def last_used_provider() -> str | None:
    return _LAST_PROVIDER


def device_info() -> dict:
    available = available_providers()
    preferred = resolve_providers(config.EMBEDDING_DEVICE, available)
    cached = _read_device_cache()
    winner = cached.get("winner")
    if winner and winner in available and str(cached.get("key", "")).startswith(config.EMBEDDING_MODEL_REPO):
        preferred = [winner] + [name for name in preferred if name != winner]
    return {
        "preference": config.EMBEDDING_DEVICE,
        "available_providers": available,
        "will_use": preferred[0] if preferred else None,
        "will_use_label": DEVICE_LABELS.get(preferred[0], preferred[0]) if preferred else "未知",
        "benchmark": cached.get("speeds", {}),
        "decided_at": cached.get("chosen_at"),
        "last_used": _LAST_PROVIDER,
        "last_used_label": DEVICE_LABELS.get(_LAST_PROVIDER or "", _LAST_PROVIDER or "未运行"),
    }


def resolve_providers(preference: str, available: list[str] | None = None) -> list[str]:
    """根据偏好与环境能力挑选执行提供方；GPU 不可用时自动回退 CPU。"""
    available = available if available is not None else available_providers()
    available = [name for name in available]
    if not available:
        return ["CPUExecutionProvider"]

    wanted = (preference or "auto").strip().lower()
    explicit = {
        "cuda": "CUDAExecutionProvider",
        "gpu": "CUDAExecutionProvider",
        "dml": "DmlExecutionProvider",
        "directml": "DmlExecutionProvider",
        "coreml": "CoreMLExecutionProvider",
        "openvino": "OpenVINOExecutionProvider",
    }.get(wanted)
    providers: list[str] = []
    if explicit and explicit in available:
        providers.append(explicit)
    elif wanted == "cpu":
        providers.append("CPUExecutionProvider")
    else:
        for name in PROVIDER_PRIORITY:
            if name in available and name != "CPUExecutionProvider":
                providers.append(name)
                break
    # CPU 始终作为兜底提供方放在最后
    providers = [name for name in providers if name in available]
    if "CPUExecutionProvider" in available and "CPUExecutionProvider" not in providers:
        providers.append("CPUExecutionProvider")
    return providers or ["CPUExecutionProvider"]


def _read_device_cache() -> dict:
    try:
        with open(config.EMBEDDING_DEVICE_CACHE_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:  # noqa: BLE001
        return {}


def _write_device_cache(payload: dict) -> None:
    try:
        config.EMBEDDING_DEVICE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(config.EMBEDDING_DEVICE_CACHE_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("写入设备实测缓存失败：%s", exc)


def _benchmark_session(session, input_names: set[str], output_name: str, tokenizer, batch_size: int) -> float:
    """返回该执行提供方的处理速度（条/秒）。"""
    texts = (BENCHMARK_TEXTS * ((config.EMBEDDING_BENCHMARK_SAMPLES // len(BENCHMARK_TEXTS)) + 1))[
        : config.EMBEDDING_BENCHMARK_SAMPLES
    ]
    warmup = _make_batch(tokenizer, texts[: min(16, len(texts))], input_names)
    session.run([output_name], warmup)
    batches = [
        texts[start : start + batch_size] for start in range(0, len(texts), batch_size)
    ]
    started = time.monotonic()
    for batch in batches:
        feeds = _make_batch(tokenizer, batch, input_names)
        session.run([output_name], feeds)
    elapsed = max(time.monotonic() - started, 1e-6)
    return len(texts) / elapsed


def _make_batch(tokenizer, texts: Sequence[str], input_names: set[str] | None = None) -> dict:
    """把文本编码成 ONNX 输入（按模型要求补齐 token_type_ids）。"""
    import numpy as np

    encodings = tokenizer.encode_batch(list(texts))
    feeds = {
        "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
        "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
    }
    if input_names is None or "token_type_ids" in input_names:
        feeds["token_type_ids"] = np.array([e.type_ids for e in encodings], dtype=np.int64)
    return feeds


def benchmark_providers(onnx_path: str | Path, providers: list[str], tokenizer) -> dict[str, float]:
    """对每个执行提供方跑一次真实推理，返回 {provider: 条/秒}。"""
    import onnxruntime as ort

    results: dict[str, float] = {}
    for provider in providers:
        batch_size = EMBEDDING_BATCH_BY_PROVIDER.get(provider, config.EMBEDDING_BATCH_SIZE_GPU)
        try:
            ort.set_default_logger_severity(3)  # 只看错误，避免实测刷屏
            session = ort.InferenceSession(str(onnx_path), providers=[provider])
            input_names = {i.name for i in session.get_inputs()}
            output_name = session.get_outputs()[0].name
            speed = _benchmark_session(session, input_names, output_name, tokenizer, batch_size)
            results[provider] = round(speed, 1)
            LOG.info("设备实测：%s 约 %.1f 条/秒（批 %d）", DEVICE_LABELS.get(provider, provider), speed, batch_size)
            del session
        except Exception as exc:  # noqa: BLE001
            LOG.warning("设备实测失败（%s）：%s", DEVICE_LABELS.get(provider, provider), exc)
    return results


EMBEDDING_BATCH_BY_PROVIDER = {
    "CUDAExecutionProvider": config.EMBEDDING_BATCH_SIZE_GPU,
    "DmlExecutionProvider": config.EMBEDDING_GPU_BATCH_SIZE_DML,
    "CoreMLExecutionProvider": config.EMBEDDING_BATCH_SIZE_GPU,
    "OpenVINOExecutionProvider": config.EMBEDDING_BATCH_SIZE_GPU,
    "CPUExecutionProvider": config.EMBEDDING_BATCH_SIZE,
}


def ensure_model_files(model_dir: Path | None = None) -> Path:
    """确保本地存在 embedding 模型文件，缺失时从 HuggingFace 下载。"""
    model_dir = Path(model_dir or config.EMBEDDING_MODEL_DIR)
    missing = [name for name in config.EMBEDDING_MODEL_FILES if not (model_dir / name).exists()]
    if not missing:
        return model_dir
    LOG.info("下载 embedding 模型 %s -> %s（缺少 %s）", config.EMBEDDING_MODEL_REPO, model_dir, missing)
    from huggingface_hub import hf_hub_download

    for name in missing:
        hf_hub_download(repo_id=config.EMBEDDING_MODEL_REPO, filename=name, local_dir=str(model_dir))
    return model_dir


class LocalEmbedder:
    """本地语义向量模型封装。"""

    def __init__(
        self,
        model_dir: Path | None = None,
        cache_file: Path | None = None,
        *,
        max_length: int = config.EMBEDDING_MAX_LENGTH,
        batch_size: int | None = None,
        auto_download: bool = True,
    ) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.model_dir = Path(model_dir or config.EMBEDDING_MODEL_DIR)
        if auto_download:
            ensure_model_files(self.model_dir)

        self.cache_file = Path(cache_file or config.EMBEDDING_CACHE_FILE)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.max_length = max_length

        self.tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=self.max_length)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]", direction="right")

        # 自动检测设备：CUDA / DirectML / CoreML 可用时先用实测挑出更快的后端
        available = list(ort.get_available_providers())
        onnx_path = self.model_dir / config.EMBEDDING_ONNX_FILE
        candidates = resolve_providers(config.EMBEDDING_DEVICE, available)
        chosen = candidates[0] if candidates else "CPUExecutionProvider"
        benchmark: dict[str, float] = {}
        if (
            (config.EMBEDDING_DEVICE or "auto").strip().lower() in ("auto", "")
            and len(candidates) > 1
            and config.EMBEDDING_AUTO_BENCHMARK
        ):
            cache_key = f"{config.EMBEDDING_MODEL_REPO}|{'+'.join(candidates)}"
            cached = _read_device_cache()
            if cached.get("key") == cache_key and cached.get("winner") in candidates:
                chosen = cached["winner"]
                benchmark = cached.get("speeds", {})
                LOG.info(
                    "设备选择（复用实测结果）：%s（实测 %s）",
                    DEVICE_LABELS.get(chosen, chosen),
                    benchmark,
                )
            else:
                LOG.info("首次运行：实测各后端速度以决定是否使用 GPU…")
                benchmark = benchmark_providers(onnx_path, candidates, self.tokenizer)
                if benchmark:
                    fastest = max(benchmark, key=benchmark.get)
                    cpu_speed = benchmark.get("CPUExecutionProvider", 0.0)
                    # GPU 必须明显更快（>15%）才切换，避免测量噪声导致反而变慢
                    if (
                        fastest != "CPUExecutionProvider"
                        and cpu_speed
                        and benchmark[fastest] <= cpu_speed * 1.15
                    ):
                        LOG.info(
                            "GPU 实测未明显快于 CPU（%.1f vs %.1f 条/秒），本次使用 CPU",
                            benchmark[fastest],
                            cpu_speed,
                        )
                        chosen = "CPUExecutionProvider"
                    else:
                        chosen = fastest
                _write_device_cache(
                    {"key": cache_key, "winner": chosen, "speeds": benchmark, "chosen_at": time.strftime("%Y-%m-%d %H:%M:%S")}
                )
                LOG.info("设备实测结论：使用 %s（%s）", DEVICE_LABELS.get(chosen, chosen), benchmark)

        self.device_benchmark = benchmark
        self.providers = [chosen]
        if chosen != "CPUExecutionProvider" and "CPUExecutionProvider" in available:
            self.providers.append("CPUExecutionProvider")
        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            self.session = ort.InferenceSession(
                str(onnx_path), sess_options=session_options, providers=self.providers
            )
        except Exception as exc:  # noqa: BLE001  GPU 初始化失败（缺 CUDA 库等）时回退 CPU
            LOG.warning("使用 %s 初始化失败（%s），回退 CPU", self.providers, exc)
            self.providers = ["CPUExecutionProvider"]
            self.session = ort.InferenceSession(
                str(onnx_path), sess_options=session_options, providers=self.providers
            )

        active = list(self.session.get_providers())
        global _LAST_PROVIDER
        _LAST_PROVIDER = active[0] if active else "CPUExecutionProvider"
        self.device_label = DEVICE_LABELS.get(_LAST_PROVIDER, _LAST_PROVIDER)
        self.using_gpu = _LAST_PROVIDER != "CPUExecutionProvider"
        self.batch_size = batch_size or EMBEDDING_BATCH_BY_PROVIDER.get(
            _LAST_PROVIDER, config.EMBEDDING_BATCH_SIZE
        )
        LOG.info(
            "Embedding 运行设备：%s（可用提供方：%s，批大小 %d）",
            self.device_label,
            ", ".join(available) or "未知",
            self.batch_size,
        )

        self.input_names = {i.name for i in self.session.get_inputs()}
        self.output_name = self.session.get_outputs()[0].name

        self._cache: dict[str, list[float]] = {}
        self._pending: list[str] = []
        self._load_cache()

    # ------------------------------------------------------------ 缓存
    def _load_cache(self) -> None:
        if not self.cache_file.exists():
            return
        with open(self.cache_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    self._cache[record["k"]] = record["v"]
                except Exception:  # noqa: BLE001
                    continue
        LOG.info("Embedding 缓存载入 %d 条", len(self._cache))

    def _flush(self) -> None:
        if not self._pending:
            return
        with open(self.cache_file, "a", encoding="utf-8") as f:
            for key in self._pending:
                f.write(json.dumps({"k": key, "v": self._cache[key]}, ensure_ascii=False) + "\n")
        self._pending.clear()

    @staticmethod
    def _key(text: str) -> str:
        return sha256_hex(CACHE_KEY_PREFIX, text)

    # ------------------------------------------------------------ 推理
    def _run(self, texts: Sequence[str]) -> np.ndarray:
        encodings = self.tokenizer.encode_batch(list(texts))
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.array([e.type_ids for e in encodings], dtype=np.int64)
        last_hidden = self.session.run([self.output_name], feeds)[0]
        vectors = last_hidden[:, 0, :].astype(np.float32)  # BGE 使用 CLS 池化
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms

    @property
    def dim(self) -> int:
        dim = self.session.get_outputs()[0].shape[-1]
        return int(dim) if isinstance(dim, int) else 768

    def encode(
        self,
        texts: Sequence[str],
        *,
        progress_label: str = "",
        stage: str = "embedding",
    ) -> np.ndarray:
        """返回 L2 归一化后的向量矩阵，形状 (len(texts), dim)。"""
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)

        dirty = False
        result: list[np.ndarray | None] = [None] * len(texts)
        todo_idx: list[int] = []
        todo_text: list[str] = []
        for idx, text in enumerate(texts):
            cached = self._cache.get(self._key(text))
            if cached is not None:
                result[idx] = np.asarray(cached, dtype=np.float32)
            else:
                todo_idx.append(idx)
                todo_text.append(text)

        if todo_text:
            LOG.info(
                "%s向量化：总 %d 条，缓存命中 %d 条，需计算 %d 条（%s）",
                progress_label,
                len(texts),
                len(texts) - len(todo_text),
                len(todo_text),
                self.device_label,
            )
            unique_text = list(dict.fromkeys(todo_text))
            chunks = [
                unique_text[start : start + self.batch_size]
                for start in range(0, len(unique_text), self.batch_size)
            ]
            started = time.monotonic()
            vectors_list: list[np.ndarray] = []
            for index, chunk in enumerate(chunks, start=1):
                cancel.check("向量化已取消")
                vectors_list.append(self._run(chunk))
                done = min(index * self.batch_size, len(unique_text))
                # 真实进度：按已完成的向量条数上报（含速度与预计剩余时间）
                progress.report(
                    stage,
                    progress.progress_text(done, len(unique_text), started, unit="条",
                                           prefix=f"{progress_label}向量化 "),
                    current=done,
                    total=len(unique_text),
                )
            vectors = np.vstack(vectors_list)
            lookup = {text: vectors[i] for i, text in enumerate(unique_text)}
            for idx, text in zip(todo_idx, todo_text):
                vec = lookup[text]
                result[idx] = vec
                key = self._key(text)
                if key not in self._cache:
                    self._cache[key] = [round(float(x), 6) for x in vec]
                    self._pending.append(key)
                    dirty = True
            if dirty:
                self._flush()

        return np.vstack([r if r is not None else np.zeros(self.dim, dtype=np.float32) for r in result]).astype(
            np.float32
        )

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]
