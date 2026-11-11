"""阶段 3：LLM 批量提取每条评论的结构化观点（Prompt A）。"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import config
from . import cancel
from . import progress
from .llm import LLMClient, LLMError
from .paths import Paths, default_paths
from .utils import LOG, chunk_by_budget, read_json, sha256_hex, write_json

PROMPT_VERSION = "A-v3"

STANCES = ("support", "oppose", "conditional", "neutral", "unclear")


# ------------------------------------------------------------------ 评论级缓存
def comment_cache_key(content: str, topic: str | None, model: str) -> str:
    """同一段评论（相同 Prompt 版本 / 主题 / 模型）只分析一次。

    这样换批大小、数据集之间有重复评论、任务中断续跑都不会重复调用 LLM。
    """
    return sha256_hex(PROMPT_VERSION, model, topic or "", content)


def load_comment_cache(path: Path | None = None) -> dict[str, dict]:
    path = Path(path or config.EXTRACTION_CACHE_FILE)
    cache: dict[str, dict] = {}
    if not path.exists():
        return cache
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                cache[record["k"]] = record["v"]
            except Exception:  # noqa: BLE001
                continue
    return cache


def append_comment_cache(entries: dict[str, dict], path: Path | None = None) -> None:
    if not entries:
        return
    path = Path(path or config.EXTRACTION_CACHE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in entries.items():
            handle.write(json.dumps({"k": key, "v": value}, ensure_ascii=False) + "\n")

SYSTEM_PROMPT = """你是中文社交平台评论语义分析专家，负责把用户评论（抖音、小红书、B站、微博、电商评价、问卷文本等都可以）转成结构化观点。严格遵守以下规则：
1. 只分析给定评论，不得编造评论内容或 comment_id。
2. 一条评论可以有 0 个、1 个或多个观点，不要强行归类。
3. statement 是标准化后的观点表达：不超过 40 字、简洁、可比较、去掉口语化修饰、不要直接复制原评论；语义相同的评论必须得到尽可能一致的 statement 表述；statement 需要体现该评论主张的做法或立场方向。
4. stance 只能是 support / oppose / conditional / neutral / unclear。带前提条件的建议用 conditional。
5. reason 必须是该评论自身给出的理由，用评论的信息概括；评论没有给理由时填空字符串 ""。
6. 与帖子主题无关（例如问背景音乐名字、闲聊、打广告）→ is_relevant=false，viewpoints 为空数组。
7. 与主题相关但没有明确观点（例如“兄弟加油”“哈哈哈哈”“我也不知道”“17岁？”）→ is_relevant=true，viewpoints 为空数组。这类请用 stance=unclear 且不要放进 viewpoints。
8. 简短但立场明确的评论必须保留观点，例如“进厂吧”“别进厂”。
9. 观点必须是“该评论主张的做法或方向”，不要支持理由本身当成观点。例如“不建议进厂，进厂风险大”只应输出一个观点“不建议进厂”，不要额外输出“进厂风险大”。
10. 同一条评论内不要输出多个核心意思相同的观点：例如“不建议进厂”“别去流水线”“进厂不好”属于同一个观点，只输出一个；一份理由只能支撑一个观点。
11. 一条评论最多输出 3 个观点；评论确实给出多个并列建议时才输出多个观点。
12. 只输出 JSON，不要输出任何解释或 Markdown 代码块。
输出格式：{"results":[{"comment_id":"...","is_relevant":true,"viewpoints":[{"statement":"...","stance":"support","reason":"..."}]}]}
输入中的每一条评论都必须出现在 results 中，comment_id 与输入完全一致。"""


class ViewpointItem(BaseModel):
    statement: str
    stance: Literal["support", "oppose", "conditional", "neutral", "unclear"] = "unclear"
    reason: str = ""

    @field_validator("statement")
    @classmethod
    def _statement_not_empty(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("statement 不能为空")
        return value[:120]

    @field_validator("stance", mode="before")
    @classmethod
    def _normalize_stance(cls, value: Any) -> str:
        text = str(value or "").strip().lower()
        mapping = {
            "support": "support", "supportive": "support", "支持": "support", "赞成": "support",
            "oppose": "oppose", "opposed": "oppose", "反对": "oppose",
            "conditional": "conditional", "有条件的": "conditional", "条件性": "conditional",
            "neutral": "neutral", "中立": "neutral",
            "unclear": "unclear", "不明": "unclear", "无法判断": "unclear",
        }
        return mapping.get(text, "unclear")

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str) -> str:
        text = (value or "").strip()
        if text.lower() in ("none", "null", "无", "n/a", "无理由"):
            return ""
        return text[:200]


class CommentAnalysis(BaseModel):
    comment_id: str
    is_relevant: bool = True
    viewpoints: list[ViewpointItem] = Field(default_factory=list)

    @field_validator("viewpoints", mode="before")
    @classmethod
    def _coerce_viewpoints(cls, value: Any) -> Any:
        return value or []


def _build_user_prompt(comments: list[dict], topic: str | None = None) -> str:
    payload = [{"comment_id": c["comment_id"], "content": c["content"]} for c in comments]
    if topic:
        topic_block = f"帖子主题：{topic}"
    else:
        topic_block = config.TOPIC_UNKNOWN_NOTE
    return (
        f"{topic_block}\n\n"
        "待分析评论（JSON 数组）：\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n\n请按要求输出 JSON。"
    )


def _parse_batch_payload(payload: Any) -> tuple[list[CommentAnalysis], set[str]]:
    """解析一批结果，返回 (校验通过的结果, 输出异常需要重试的 comment_id)。"""
    if isinstance(payload, dict):
        raw_items = payload.get("results") or payload.get("data") or payload.get("comments")
        if raw_items is None:
            # 也可能是 {"comment_id": {...}} 形态
            raw_items = list(payload.values()) if all(isinstance(v, dict) for v in payload.values()) else []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    parsed: list[CommentAnalysis] = []
    suspect: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if "comment_id" not in item:
            continue
        comment_id = str(item.get("comment_id"))
        try:
            parsed.append(CommentAnalysis.model_validate(item))
        except ValidationError as exc:
            LOG.warning("单条结果校验失败，尝试宽松修复：%s", exc)
            raw_viewpoints = [vp for vp in (item.get("viewpoints") or []) if isinstance(vp, dict)]
            fixed = {
                "comment_id": comment_id,
                "is_relevant": bool(item.get("is_relevant", True)),
                "viewpoints": [
                    {
                        "statement": str(vp.get("statement", "")).strip(),
                        "stance": vp.get("stance", "unclear"),
                        "reason": str(vp.get("reason", "") or ""),
                    }
                    for vp in raw_viewpoints
                    if str(vp.get("statement", "")).strip()
                ],
            }
            if len(fixed["viewpoints"]) != len(raw_viewpoints):
                # 有观点条目被丢弃，说明该条评论的输出不可信，需要重试
                suspect.add(comment_id)
            try:
                parsed.append(CommentAnalysis.model_validate(fixed))
            except ValidationError:
                LOG.warning("跳过无法修复的条目：%s", str(item)[:120])
                suspect.add(comment_id)
    return parsed, suspect


def _analyze_batch(
    client: LLMClient,
    comments: list[dict],
    topic: str | None = None,
) -> tuple[list[CommentAnalysis], set[str], str | None]:
    """分析一批评论，返回 (结果, 需要重试的 comment_id, 错误信息)。"""
    try:
        payload, _ = client.chat_json(SYSTEM_PROMPT, _build_user_prompt(comments, topic))
    except cancel.AnalysisCancelled:
        raise  # 取消要向上传递，不能被当作普通错误吞掉
    except LLMError as exc:
        return [], {c["comment_id"] for c in comments}, f"llm_error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return [], {c["comment_id"] for c in comments}, f"unexpected: {type(exc).__name__}: {exc}"
    parsed, suspect = _parse_batch_payload(payload)
    return parsed, suspect, None


def _retry_comments(
    client: LLMClient,
    comments: list[dict],
    by_id: dict[str, CommentAnalysis],
    topic: str | None = None,
) -> tuple[dict[str, CommentAnalysis], str | None]:
    """对小批量评论重新分析（用于补齐缺失或输出异常的评论）。"""
    error: str | None = None
    small_batches = chunk_by_budget(
        comments,
        lambda c: c["content"],
        max_items=5,
        max_chars=1200,
    )
    for batch in small_batches:
        results, _suspect, batch_error = _analyze_batch(client, batch, topic)
        error = error or batch_error
        for result in results:
            by_id[result.comment_id] = result
    return by_id, error


def analyze_comments(
    comments: list[dict],
    client: LLMClient,
    *,
    existing: dict[str, dict] | None = None,
    stage: str = "llm_analysis",
    topic: str | None = None,
    comment_cache: dict[str, dict] | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    """批量分析评论，支持断点续跑（existing 为已完成结果）。"""
    existing = dict(existing or {})
    todo = [c for c in comments if c["comment_id"] not in existing]
    if not todo:
        LOG.info("全部评论已有分析结果（%d 条），无需调用 LLM", len(comments))
        progress.report(stage, f"全部 {len(comments)} 条评论已有分析结果，直接复用缓存", ratio=1.0)
        return existing, []

    # 评论级缓存命中：同一段评论之前分析过就直接复用，不再请求 LLM
    new_cache_entries: dict[str, dict] = {}
    if comment_cache is not None:
        still_todo: list[dict] = []
        for comment in todo:
            key = comment_cache_key(comment["content"], topic, client.model)
            hit = comment_cache.get(key)
            if hit:
                existing[comment["comment_id"]] = {**hit, "from_comment_cache": True}
            else:
                still_todo.append(comment)
        if len(still_todo) != len(todo):
            LOG.info("评论级缓存命中 %d 条，仍需请求 %d 条", len(todo) - len(still_todo), len(still_todo))
            progress.report(
                stage,
                f"命中评论级缓存 {len(todo) - len(still_todo)} 条，需要新分析 {len(still_todo)} 条",
                current=0,
                total=len(still_todo) or 1,
            )
        todo = still_todo
        if not todo:
            return existing, []

    batches = chunk_by_budget(
        todo,
        lambda c: c["content"],
        max_items=config.LLM_BATCH_SIZE,
        max_chars=config.LLM_BATCH_CHAR_BUDGET,
    )
    total_batches = len(batches)
    processed_comments = len(comments) - len(todo)
    started_at = time.monotonic()
    LOG.info(
        "待分析评论 %d 条（已完成 %d 条），切分为 %d 批，并发 %d",
        len(todo),
        len(comments) - len(todo),
        len(batches),
        client.concurrency,
    )
    progress.report(
        stage,
        progress.progress_text(
            processed_comments, len(comments), started_at, unit="条", prefix="正在分析评论 "
        ),
        current=processed_comments,
        total=len(comments),
    )

    errors: list[dict] = []
    lock = threading.Lock()
    done_batches = 0
    done_comments = 0

    def worker(batch: list[dict]):
        return batch, _analyze_batch(client, batch, topic)

    with ThreadPoolExecutor(max_workers=client.concurrency) as pool:
        # 按窗口提交（每窗口 = 并发数），窗口之间检查取消信号，
        # 取消后最多只有一个窗口的请求在飞，能很快停下来
        window_size = max(1, client.concurrency)
        for window_start in range(0, len(batches), window_size):
            cancel.check(f"语义分析已取消（已完成 {done_batches}/{len(batches)} 批）")
            window = batches[window_start : window_start + window_size]
            futures = [pool.submit(worker, batch) for batch in window]
            for future in as_completed(futures):
                batch, (results, suspect, error) = future.result()
                by_id = {r.comment_id: r for r in results}
                need_retry = [
                    c for c in batch if c["comment_id"] not in by_id or c["comment_id"] in suspect
                ]

                # 缺失或输出异常的评论单独重试一次（更小的批量）
                if need_retry:
                    by_id, retry_error = _retry_comments(client, need_retry, by_id, topic)
                    if retry_error:
                        error = error or retry_error

                with lock:
                    for comment in batch:
                        result = by_id.get(comment["comment_id"])
                        if result is not None:
                            existing[comment["comment_id"]] = result.model_dump()
                        else:
                            existing[comment["comment_id"]] = {
                                "comment_id": comment["comment_id"],
                                "is_relevant": None,
                                "viewpoints": [],
                                "status": "missing_in_llm_output",
                            }
                            errors.append(
                                {
                                    "comment_id": comment["comment_id"],
                                    "content": comment["content"][:120],
                                    "error": error or "missing_in_llm_output",
                                }
                            )
                    done_batches += 1
                    done_comments += len(batch)
                    progress.report(
                        stage,
                        progress.progress_text(
                            min(processed_comments + done_comments, len(comments)),
                            len(comments),
                            started_at,
                            unit="条",
                            prefix="正在分析评论 ",
                        ),
                        current=done_batches,
                        total=total_batches,
                    )
                    if done_batches % 5 == 0 or done_batches == len(batches):
                        LOG.info("语义提取进度 %d/%d 批", done_batches, len(batches))

                # 写入评论级缓存（供其他任务 / 后续重跑复用）
                if comment_cache is not None:
                    with lock:
                        for comment in batch:
                            result = existing.get(comment["comment_id"])
                            if not result or result.get("is_relevant") is None:
                                continue
                            key = comment_cache_key(comment["content"], topic, client.model)
                            if key in comment_cache:
                                continue
                            value = {k: v for k, v in result.items() if k != "from_comment_cache"}
                            comment_cache[key] = value
                            new_cache_entries[key] = value
                        append_comment_cache(new_cache_entries)
                        new_cache_entries.clear()

    return existing, errors


def repair_no_viewpoint_comments(
    comments: list[dict],
    existing: dict[str, dict],
    client: LLMClient,
    *,
    paths: Paths | None = None,
    stage: str = "llm_analysis",
    topic: str | None = None,
) -> int:
    """复核“相关但没有观点”或“输出异常”的评论，用小批量重新分析。

    小批量重新分析能显著降低模型输出被截断/结构错误的概率，
    从而找回因输出异常而丢失的观点。
    """
    # 复核结论按 comment_id 落盘：同一批评论不会因为分批变化而被反复复核，保证可重复执行
    paths = paths or default_paths()
    store = read_json(paths.repair_store) or {}
    comment_cache = load_comment_cache()
    cache_updates: dict[str, dict] = {}
    for comment in comments:
        if comment["comment_id"] in store:
            existing[comment["comment_id"]] = store[comment["comment_id"]]

    targets = [
        c
        for c in comments
        if c["comment_id"] not in store
        if (
            (existing.get(c["comment_id"]) is None)
            or existing[c["comment_id"]].get("is_relevant") is None
            or (
                existing[c["comment_id"]].get("is_relevant") is True
                and not (existing[c["comment_id"]].get("viewpoints") or [])
            )
        )
    ]
    if not targets:
        return 0
    LOG.info("复核 %d 条“无观点/输出异常”的评论（每批 5 条）", len(targets))
    small_batches = chunk_by_budget(targets, lambda c: c["content"], max_items=5, max_chars=1200)
    outcomes = client.map_concurrent(
        lambda batch: _analyze_batch(client, batch, topic),
        small_batches,
        label="复核",
    )
    progress.report(
        stage,
        f"正在复核 {len(targets)} 条无明确观点的评论",
        current=0,
        total=len(targets),
    )
    recovered = 0
    for batch, outcome in zip(small_batches, outcomes):
        if isinstance(outcome, Exception):
            LOG.warning("复核批次失败：%s", outcome)
            continue
        parsed, _suspect, error = outcome
        if error:
            LOG.warning("复核批次存在错误：%s", error)
        batch_ids = {c["comment_id"] for c in batch}
        for result in parsed:
            if result.comment_id not in batch_ids:
                continue
            previous = existing.get(result.comment_id) or {}
            if (result.viewpoints or []) and not (previous.get("viewpoints") or []):
                recovered += 1
            existing[result.comment_id] = result.model_dump()
        for comment in batch:
            if comment["comment_id"] in existing:
                store[comment["comment_id"]] = existing[comment["comment_id"]]
                key = comment_cache_key(comment["content"], topic, client.model)
                if key not in comment_cache:
                    value = {
                        k: v
                        for k, v in existing[comment["comment_id"]].items()
                        if k != "from_comment_cache"
                    }
                    comment_cache[key] = value
                    cache_updates[key] = value
    write_json(paths.repair_store, store)
    append_comment_cache(cache_updates)
    LOG.info("复核完成，找回 %d 条原本没有观点的评论", recovered)
    return recovered


def run(
    *,
    force: bool = False,
    limit: int | None = None,
    repair: bool = False,
    paths: Paths | None = None,
    stage: str = "llm_analysis",
) -> dict:
    config.require_llm_config()
    paths = paths or default_paths()
    cleaned = read_json(paths.cleaned)
    if not cleaned:
        raise RuntimeError("请先运行清洗步骤（cleaned_comments.json 不存在）")
    comments = cleaned["comments"]
    topic = (cleaned.get("meta") or {}).get("post_topic")
    if limit:
        comments = comments[:limit]

    previous = read_json(paths.extraction) or {}
    existing = (
        {
            item["comment_id"]: item
            for item in previous.get("results", [])
            if item.get("is_relevant") is not None
        }
        if not force
        else {}
    )
    if force:
        LOG.info("--force 指定，忽略已有提取结果")

    started = time.time()
    with LLMClient() as client:
        comment_cache = load_comment_cache()
        results, errors = analyze_comments(
            comments,
            client,
            existing=existing,
            stage=stage,
            topic=topic,
            comment_cache=comment_cache,
        )
        if repair:
            repair_no_viewpoint_comments(comments, results, client, paths=paths, stage=stage, topic=topic)
        stats = dict(client.stats)

    ordered = [results[c["comment_id"]] for c in comments if c["comment_id"] in results]
    valid = sum(1 for r in ordered if r.get("is_relevant") is True)
    irrelevant = sum(1 for r in ordered if r.get("is_relevant") is False)
    unknown = sum(1 for r in ordered if r.get("is_relevant") is None)
    viewpoint_total = sum(len(r.get("viewpoints") or []) for r in ordered)

    # 接口整体不可用时（例如 API Key 错误、限流、网络不可达）必须让任务失败，
    # 而不是产出“0 个观点”的成功结果。
    if errors and len(errors) > 0.5 * max(len(ordered), 1):
        first_error = errors[0].get("error") or "未知错误"
        raise RuntimeError(
            f"LLM 接口调用失败：{len(errors)} / {len(ordered)} 条评论未能成功分析（{first_error}）。"
            "请检查 .env 中的 BASE_URL / API_KEY / MODEL_NAME 或稍后重试。"
        )
    if errors:
        LOG.warning("有 %d / %d 条评论未成功分析，已记录到 %s", len(errors), len(ordered), paths.extraction_errors.name)

    output = {
        "meta": {
            "model": config.CHAT_MODEL,
            "prompt_version": PROMPT_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "comment_count": len(ordered),
            "relevant_comment_count": valid,
            "irrelevant_comment_count": irrelevant,
            "unknown_comment_count": unknown,
            "viewpoint_mention_count": viewpoint_total,
            "llm_stats": stats,
            "elapsed_seconds": round(time.time() - started, 1),
        },
        "results": ordered,
    }
    write_json(paths.extraction, output)
    if errors:
        write_json(
            paths.extraction_errors,
            {"meta": {"count": len(errors)}, "errors": errors},
        )
    LOG.info(
        "语义提取完成：评论 %d 条，相关 %d，无关 %d，未识别 %d，观点提及 %d 次，LLM 调用 %s，耗时 %.1fs",
        len(ordered),
        valid,
        irrelevant,
        unknown,
        viewpoint_total,
        stats,
        time.time() - started,
    )
    progress.report(
        stage,
        f"语义分析完成：{valid} 条相关评论，共 {viewpoint_total} 条观点表述",
        ratio=1.0,
    )
    return output
