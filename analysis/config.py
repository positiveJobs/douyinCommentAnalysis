"""全局配置：读取 .env、定义路径与超参数。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """缺少必需配置时抛出。"""


def _clean_url(raw: str) -> str:
    """把 chat/completions 完整地址归一化为 API root。"""
    url = (raw or "").strip().strip('"').strip("'")
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url.rstrip("/")


load_dotenv(PROJECT_ROOT / ".env", override=False)


def _env(*names: str, default: str = "") -> str:
    """按优先级读取环境变量（兼容 LLM_* 别名写法）。"""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


RAW_BASE_URL = _env("BASE_URL", "LLM_BASE_URL")
API_KEY = _env("API_KEY", "LLM_API_KEY")
CHAT_MODEL = _env("MODEL_NAME", "LLM_MODEL")
API_ROOT = _clean_url(RAW_BASE_URL)
CHAT_URL = f"{API_ROOT}/chat/completions" if API_ROOT else ""

# 数据根目录：原始数据、CLI 产物、Web 任务数据、样例都放在 data/ 下，
# 项目根目录只保留代码与配置。
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
WORKING_DIR = DATA_DIR / "working"
RESULTS_DIR = DATA_DIR / "results"
REPORTS_DIR = DATA_DIR / "reports"
SAMPLES_DIR = DATA_DIR / "samples"
CLI_OUTPUT_DIR = DATA_DIR / "cli"

# 帖子主题（真实数据来自 detail_comments.json 对应的抖音帖子）
POST_TOPIC = (
    "我今年17岁，明年成年就想出去工作了。现在纠结是直接进厂，"
    "还是先慢慢找适合自己的工作，大家都是怎么走过来的？能给点建议吗？"
)

# 未提供主题时给 LLM 的说明（让模型先判断讨论话题，再判断相关性）
TOPIC_UNKNOWN_NOTE = (
    "本条数据没有提供主题信息：请先根据这一批评论的内容判断大家讨论的核心话题，"
    "再判断每条评论是否与这个话题相关。"
)

# 数据文件：CLI 默认输入为 data/samples 下的样例数据，输出写入 data/cli/
RAW_COMMENTS_FILE = SAMPLES_DIR / "detail_comments.json"
CLEANED_FILE = CLI_OUTPUT_DIR / "cleaned_comments.json"
EXTRACTION_FILE = CLI_OUTPUT_DIR / "llm_comment_analysis.json"
EXTRACTION_ERRORS_FILE = CLI_OUTPUT_DIR / "llm_comment_analysis_errors.json"
EMBEDDINGS_FILE = CLI_OUTPUT_DIR / "viewpoint_embeddings.npz"
CLUSTERS_FILE = CLI_OUTPUT_DIR / "viewpoint_clusters.json"
FINAL_VIEWPOINTS_FILE = CLI_OUTPUT_DIR / "final_viewpoints.json"
MERGE_DETAIL_FILE = CLI_OUTPUT_DIR / "viewpoint_merge_detail.json"
RESULTS_FILE = CLI_OUTPUT_DIR / "analysis_results.json"
REPORT_FILE = CLI_OUTPUT_DIR / "analysis_report.md"
HTML_REPORT_FILE = CLI_OUTPUT_DIR / "analysis_report.html"
SAMPLE_FILE = CLI_OUTPUT_DIR / "manual_sample_check.json"
SAMPLE_MD_FILE = CLI_OUTPUT_DIR / "manual_sample_check.md"
SAMPLE_JUDGED_FILE = CLI_OUTPUT_DIR / "manual_sample_check_judged.json"

# 缓存目录
CACHE_DIR = PROJECT_ROOT / "cache"
LLM_CACHE_DIR = CACHE_DIR / "llm"
EMBEDDING_CACHE_FILE = CACHE_DIR / "embeddings" / "store.jsonl"
REPAIR_STORE_FILE = CACHE_DIR / "repair" / "decisions.json"
# 评论级语义分析缓存：同一段评论（相同主题 / 模型 / Prompt 版本）只需分析一次，
# 换批大小、数据集之间有重复评论、任务中断续跑都不会重复调用 LLM
EXTRACTION_CACHE_FILE = CACHE_DIR / "extraction" / "comments.jsonl"

# 本地向量模型（bge-base-zh-v1.5 的 ONNX 量化版，首次运行自动下载到本目录）
EMBEDDING_MODEL_REPO = "Xenova/bge-base-zh-v1.5"
EMBEDDING_MODEL_DIR = Path(_env("EMBEDDING_MODEL_DIR", default=str(PROJECT_ROOT / "models" / "bge-base-zh-v1.5")))
EMBEDDING_ONNX_FILE = "onnx/model_quantized.onnx"
EMBEDDING_MODEL_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
    EMBEDDING_ONNX_FILE,
)
# 向量模型：自动检测 GPU（CUDA / DirectML / CoreML），不可用则回退 CPU
EMBEDDING_DEVICE = _env("EMBEDDING_DEVICE", default="auto")
EMBEDDING_MAX_LENGTH = 512
EMBEDDING_BATCH_SIZE = 32        # CPU 批大小
EMBEDDING_BATCH_SIZE_GPU = 64    # GPU 批大小
EMBEDDING_GPU_BATCH_SIZE_DML = 32  # DirectML 批大小（过大容易失败）
# auto 模式下先实测再决定用 GPU 还是 CPU，结果缓存复用（避免小模型上 GPU 反而更慢）
EMBEDDING_AUTO_BENCHMARK = _env("EMBEDDING_AUTO_BENCHMARK", default="1") not in ("0", "false", "False")
EMBEDDING_BENCHMARK_SAMPLES = 128
EMBEDDING_DEVICE_CACHE_FILE = CACHE_DIR / "embedding_device.json"

# LLM 调用参数
LLM_BATCH_SIZE = 30          # 每批评论数：越大越省 prompt token
LLM_BATCH_CHAR_BUDGET = 6000  # 每批评论字符数上限
LLM_CONCURRENCY = 8          # 并发请求数
LLM_MAX_RETRIES = 4
LLM_RATE_LIMIT_COOLDOWN = 20.0  # 命中限流后的全局冷却秒数
LLM_TIMEOUT_SECONDS = 180.0
LLM_MAX_TOKENS = 4096
LLM_TEMPERATURE = 0.0

# 聚类阈值（余弦相似度，bge-base-zh-v1.5 标定）
VIEWPOINT_CLUSTER_THRESHOLD = 0.74
VIEWPOINT_GLOBAL_MERGE_THRESHOLD = 0.84
REASON_CLUSTER_THRESHOLD = 0.78
# 超过该数量的观点不使用层次聚类（O(n²) 内存），改用分块相似度聚类
CLUSTER_AGGLOMERATIVE_MAX = 8000
REASON_CONSOLIDATE_CHUNK_LIMIT = 60   # 理由归纳时单次请求包含的理由条数
REASON_CONSOLIDATE_RATIO = 0.45
REASON_CONSOLIDATE_MAX_ROUNDS = 2
REASON_TARGET_MAX = 10                # 单个观点最多保留的主要理由数量
# 单次归并请求的规模上限（控制输出长度，避免 JSON 被截断）
MERGE_CHUNK_GROUP_LIMIT = 30
MERGE_CHUNK_CHAR_BUDGET = 5000
MERGE_CHUNK_ITEM_LIMIT = 40
CONSOLIDATE_CHUNK_ITEM_LIMIT = 70     # 核心观点归纳时单次请求包含的观点条数
CONSOLIDATE_TARGET_RATIO = 0.5        # 每批归纳后保留的比例（目标上限）
CONSOLIDATE_MIN_TARGET = 8
CONSOLIDATE_MAX_ROUNDS = 3
CORE_VIEWPOINT_TARGET_MAX = 40        # 最终核心观点数量上限

REPRESENTATIVE_COMMENT_LIMIT = 6
SAMPLE_SIZE = 100
SAMPLE_RANDOM_SEED = 20260918


def require_llm_config() -> None:
    """校验 LLM 配置，缺失时给出明确错误。"""
    missing = [
        name
        for name, value in (
            ("BASE_URL", RAW_BASE_URL),
            ("API_KEY", API_KEY),
            ("MODEL_NAME", CHAT_MODEL),
        )
        if not (value or "").strip()
    ]
    if missing:
        raise ConfigError("缺少 .env 配置项：" + "、".join(missing))
