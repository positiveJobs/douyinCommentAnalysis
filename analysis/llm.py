"""LLM 客户端：并发批量调用、重试、JSON 解析修复、磁盘缓存。"""
from __future__ import annotations

import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import httpx

from . import config
from . import cancel
from .utils import LOG, read_json, sha256_hex, write_json


class LLMError(RuntimeError):
    """LLM 调用失败（重试后仍未成功）。"""


@dataclass
class LLMResponse:
    content: str
    cached: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _strip_trailing_commas(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == ",":
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_json_loose(text: str) -> Any:
    """尽量从模型输出中解析出 JSON 对象/数组。"""
    if text is None:
        raise ValueError("空输出")
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = raw.find(open_ch)
        end = raw.rfind(close_ch)
        if start != -1 and end > start:
            candidate = raw[start : end + 1]
            for transform in (
                lambda s: s,
                _strip_trailing_commas,
                lambda s: _strip_trailing_commas(s).replace("\u201c", '"').replace("\u201d", '"'),
            ):
                try:
                    return json.loads(transform(candidate))
                except Exception:  # noqa: BLE001
                    continue
    raise ValueError("无法解析为 JSON: " + raw[:200])


class LLMClient:
    """OpenAI 兼容 Chat Completions 客户端（带磁盘缓存与重试）。"""

    def __init__(
        self,
        *,
        cache_dir: Path = config.LLM_CACHE_DIR,
        model: str = config.CHAT_MODEL,
        chat_url: str = config.CHAT_URL,
        api_key: str = config.API_KEY,
        concurrency: int = config.LLM_CONCURRENCY,
        max_retries: int = config.LLM_MAX_RETRIES,
        timeout: float = config.LLM_TIMEOUT_SECONDS,
        cancel_event: "object | None" = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.chat_url = chat_url
        self.api_key = api_key
        self.concurrency = max(1, concurrency)
        self.max_retries = max(0, max_retries)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=30.0))
        self.stats = {"calls": 0, "cache_hits": 0, "errors": 0, "retries": 0, "rate_limited": 0}
        # 取消信号：构造时从 Pipeline 的上下文里取，之后所有工作线程都能看到
        self._cancel_event = cancel_event if cancel_event is not None else cancel.event()
        # 命中限流后进入全局冷却：所有并发线程一起等待，避免继续硬撞接口
        self._cooldown_until = 0.0
        self._cooldown_lock = threading.Lock()

    # ------------------------------------------------------------ 限流协同
    def _enter_cooldown(self, seconds: float) -> None:
        with self._cooldown_lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)

    def _wait_for_cooldown(self) -> None:
        while True:
            with self._cooldown_lock:
                wait = self._cooldown_until - time.monotonic()
            if wait <= 0:
                return
            time.sleep(min(wait, 1.0))

    # ------------------------------------------------------------ 缓存
    def _cache_key(self, messages: Sequence[dict], temperature: float, max_tokens: int, json_mode: bool) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": list(messages),
                "temperature": temperature,
                "max_tokens": max_tokens,
                "json_mode": json_mode,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return sha256_hex(payload)

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    # ------------------------------------------------------------ 调用
    def chat(
        self,
        messages: Sequence[dict],
        *,
        temperature: float = config.LLM_TEMPERATURE,
        max_tokens: int = config.LLM_MAX_TOKENS,
        json_mode: bool = True,
    ) -> LLMResponse:
        key = self._cache_key(messages, temperature, max_tokens, json_mode)
        cached = read_json(self._cache_path(key))
        if cached and cached.get("content"):
            with self._lock:
                self.stats["cache_hits"] += 1
            return LLMResponse(
                content=cached["content"],
                cached=True,
                prompt_tokens=cached.get("prompt_tokens", 0),
                completion_tokens=cached.get("completion_tokens", 0),
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self._cancel_event is not None and self._cancel_event.is_set():
                raise cancel.AnalysisCancelled("分析已取消（请求未发出）")
            self._wait_for_cooldown()
            try:
                with self._lock:
                    self.stats["calls"] += 1
                resp = self._client.post(
                    self.chat_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    if resp.status_code == 429:
                        with self._lock:
                            self.stats["rate_limited"] += 1
                        # 限流：全局冷却，并让当前请求退避更久
                        self._enter_cooldown(config.LLM_RATE_LIMIT_COOLDOWN)
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                if resp.status_code >= 400:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage") or {}
                write_json(
                    self._cache_path(key),
                    {
                        "model": self.model,
                        "content": content,
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                    },
                )
                return LLMResponse(
                    content=content,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )
            except LLMError as exc:
                last_error = exc
                if not str(exc).startswith(("HTTP 429", "HTTP 5")):
                    break
            except Exception as exc:  # noqa: BLE001  网络异常、超时等
                last_error = exc
            if attempt < self.max_retries:
                with self._lock:
                    self.stats["retries"] += 1
                time.sleep(min(2**attempt, 30) + random.random())
        with self._lock:
            self.stats["errors"] += 1
        raise LLMError(f"LLM 调用失败: {last_error}")

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = config.LLM_TEMPERATURE,
        max_tokens: int = config.LLM_MAX_TOKENS,
        repair_attempts: int = 2,
    ) -> tuple[Any, bool]:
        """返回 (解析后的 JSON, 是否命中缓存)；解析失败会追加修复请求。"""
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        cached_any = False
        last_text = ""
        for attempt in range(repair_attempts + 1):
            resp = self.chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)
            cached_any = cached_any or resp.cached
            last_text = resp.content
            try:
                return parse_json_loose(resp.content), cached_any
            except ValueError as exc:
                LOG.warning("JSON 解析失败（第 %d 次）：%s", attempt + 1, exc)
                messages = messages[:2] + [
                    {"role": "assistant", "content": last_text[:2000]},
                    {
                        "role": "user",
                        "content": (
                            "上一次输出不是合法 JSON，json.loads 解析失败。"
                            "请只输出一个合法 JSON 对象，不要输出解释、注释或 Markdown 代码块。"
                        ),
                    },
                ]
        raise LLMError(f"JSON 解析失败（已重试 {repair_attempts} 次）：{last_text[:300]}")

    def map_concurrent(
        self,
        fn: Callable[[Any], Any],
        items: Iterable[Any],
        *,
        label: str = "",
    ) -> list[Any]:
        """受控并发执行 fn，按输入顺序返回结果；异常以异常对象形式返回。"""
        items = list(items)
        if not items:
            return []
        results: list[Any] = [None] * len(items)
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(fn, item): idx for idx, item in enumerate(items)}
            done = 0
            for future in as_completed(futures):
                cancel.check("分析已取消（等待中的请求已停止）")
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:  # noqa: BLE001
                    results[idx] = exc
                done += 1
                if label and (done % 10 == 0 or done == len(items)):
                    LOG.info("%s 进度 %d/%d", label, done, len(items))
        return results

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
