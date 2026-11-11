"""阶段 2：读取 detail_comments.json、自动识别字段、清洗与去重。"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import config
from . import progress
from .paths import Paths, default_paths
from .utils import LOG, normalize_text, strip_emoji_placeholders, write_json

# 字段自动识别候选（不假设固定字段名）
CONTENT_KEYS = ("content", "text", "comment_text", "content_text", "comment", "message", "desc")
ID_KEYS = ("comment_id", "cid", "id", "commentId", "commentid")
LIKE_KEYS = ("like_count", "digg_count", "likes", "like_num", "digg")
TIME_KEYS = ("create_time", "createTime", "time", "ctime", "timestamp")
PARENT_KEYS = ("parent_comment_id", "parent_id", "reply_id", "parent", "root_comment_id")
USER_KEYS = ("nickname", "nick_name", "user_name", "username", "author")
SUB_COUNT_KEYS = ("sub_comment_count", "reply_count", "sub_comment_num")

# 明显广告/引流：只识别“无歧义的推广或联系方式”，避免误伤正常建议
# （例如“学费”“招聘平台”“日结工”属于正常讨论内容，不在此列）
AD_PATTERNS = (
    "加微信", "加微信v", "加微信V", "微信号", "薇信", "威信", "vx:", "vx：",
    "加我qq", "加q群", "扣扣群", "私信我", "私聊我", "点击链接", "点击主页",
    "扫码加", "扫码进", "免费领取", "代刷", "刷单", "包就业", "有偿带",
)
AD_REGEX = re.compile(
    r"(https?://|www\.|[a-z0-9-]+\.com\b|[a-z0-9-]+\.cn\b|\b1[3-9]\d{9}\b)",
    re.IGNORECASE,
)
_MEANINGFUL_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def _pick(record: dict, keys: Iterable[str]) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def iter_comment_records(obj: Any) -> Iterator[dict]:
    """递归遍历任意嵌套结构，产出所有“像评论”的字典。"""
    if isinstance(obj, dict):
        has_content = any(key in obj for key in CONTENT_KEYS)
        has_id = any(key in obj for key in ID_KEYS)
        if has_content and has_id:
            yield obj
            return
        for value in obj.values():
            yield from iter_comment_records(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_comment_records(item)


def records_from_payload(raw: Any) -> list[dict]:
    """把任意嵌套的 JSON 结构转换成统一的评论记录列表。"""
    records: list[dict] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(iter_comment_records(raw)):
        content = _pick(item, CONTENT_KEYS)
        comment_id = _pick(item, ID_KEYS)
        if comment_id is None:
            comment_id = f"row_{idx}"
        comment_id = str(comment_id)
        parent = _pick(item, PARENT_KEYS)
        parent = str(parent) if parent is not None else "0"
        like_count = _pick(item, LIKE_KEYS)
        try:
            like_count = int(like_count) if like_count is not None else 0
        except (TypeError, ValueError):
            like_count = 0
        create_time = _pick(item, TIME_KEYS)
        try:
            create_time = int(create_time) if create_time is not None else 0
        except (TypeError, ValueError):
            create_time = 0
        sub_count = _pick(item, SUB_COUNT_KEYS)
        try:
            sub_count = int(sub_count) if sub_count is not None else 0
        except (TypeError, ValueError):
            sub_count = 0

        if comment_id in seen_ids:
            continue
        seen_ids.add(comment_id)

        records.append(
            {
                "comment_id": comment_id,
                "content": content if isinstance(content, str) else ("" if content is None else str(content)),
                "like_count": like_count,
                "create_time": create_time,
                "parent_comment_id": parent,
                "nickname": _pick(item, USER_KEYS) or "",
                "sub_comment_count": sub_count,
                "is_reply": parent not in ("0", "", "None"),
            }
        )
    return records


def load_raw_comments(path: Path | None = None) -> list[dict]:
    """读取原始评论文件并归一化为统一记录结构。"""
    path = Path(path or config.RAW_COMMENTS_FILE)
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return records_from_payload(raw)


TOPIC_KEYS = ("topic", "title", "post_topic", "subject", "question", "desc", "description", "caption")


def detect_topic(raw: Any) -> str | None:
    """如果上传的 JSON 自带主题字段（topic/title/...），则直接作为帖子主题。"""
    if isinstance(raw, dict):
        for key in TOPIC_KEYS:
            value = raw.get(key)
            if isinstance(value, str) and 4 <= len(value.strip()) <= 300:
                return value.strip()
    return None


def classify_invalid(content: str) -> str | None:
    """返回无效原因；有效评论返回 None。"""
    if not content:
        return "empty"
    text = normalize_text(content)
    if not text:
        return "empty"
    stripped = strip_emoji_placeholders(text)
    if not _MEANINGFUL_RE.search(stripped):
        return "no_meaningful_content"  # 纯标点、纯表情
    for pattern in AD_PATTERNS:
        if pattern in text:
            return "advertisement"
    if AD_REGEX.search(text):
        return "advertisement"
    return None


def clean_comments(
    raw_records: list[dict],
    *,
    source_file: str = "detail_comments.json",
    topic: str | None = None,
) -> dict:
    """清洗：过滤无效评论、标记重复评论（重复不参与分析但保留追溯信息）。"""
    comments: list[dict] = []
    invalid: list[dict] = []
    duplicates: list[dict] = []
    seen_content: dict[str, str] = {}
    invalid_reasons: dict[str, int] = {}

    for record in raw_records:
        content = record["content"]
        key = normalize_text(content)
        reason = classify_invalid(content)
        if reason:
            invalid.append({"comment_id": record["comment_id"], "content": content, "reason": reason})
            invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1
            continue
        if key in seen_content:
            duplicates.append(
                {
                    "comment_id": record["comment_id"],
                    "content": content,
                    "duplicate_of": seen_content[key],
                }
            )
            continue
        seen_content[key] = record["comment_id"]
        comments.append(
            {
                "comment_id": record["comment_id"],
                "content": content,
                "content_normalized": key,
                "like_count": record["like_count"],
                "create_time": record["create_time"],
                "parent_comment_id": record["parent_comment_id"],
                "is_reply": record["is_reply"],
                "nickname": record["nickname"],
            }
        )

    return {
        "meta": {
            "source_file": source_file,
            "post_topic": topic,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "raw_comment_count": len(raw_records),
            "valid_comment_count": len(comments),
            "invalid_comment_count": len(invalid),
            "duplicate_comment_count": len(duplicates),
            "invalid_reasons": invalid_reasons,
        },
        "comments": comments,
        "invalid_comments": invalid,
        "duplicate_comments": duplicates,
    }


def run(
    *,
    force: bool = False,
    output: Path | None = None,
    paths: Paths | None = None,
    topic: str | None = None,
) -> dict:
    paths = paths or default_paths()
    output = Path(output or paths.cleaned)
    if output.exists() and not force:
        cached = json.loads(output.read_text(encoding="utf-8"))
        if topic and (cached.get("meta") or {}).get("post_topic") != topic:
            LOG.info("主题发生变化，重新清洗：%s", output)
            return run(force=True, output=output, paths=paths, topic=topic)
        LOG.info("清洗结果已存在，直接复用：%s", output)
        progress.report("cleaning", "复用已有清洗结果", ratio=1.0)
        return cached

    started = time.time()
    progress.report("loading", "正在读取评论文件…")
    with open(paths.raw_comments, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    resolved_topic = topic or detect_topic(payload)
    raw_records = records_from_payload(payload)
    LOG.info("读取原始评论 %d 条（%s）", len(raw_records), paths.raw_comments.name)
    progress.report("loading", f"已读取 {len(raw_records)} 条原始评论", current=len(raw_records), total=len(raw_records))
    if not raw_records:
        raise ValueError("评论文件中没有解析到任何评论，请确认文件格式")
    progress.report("cleaning", "正在清洗与去重…")
    cleaned = clean_comments(raw_records, source_file=paths.raw_comments.name, topic=resolved_topic)
    write_json(output, cleaned)
    meta = cleaned["meta"]
    LOG.info(
        "清洗完成：有效 %d，无效 %d（%s），重复 %d，耗时 %.1fs",
        meta["valid_comment_count"],
        meta["invalid_comment_count"],
        meta["invalid_reasons"],
        meta["duplicate_comment_count"],
        time.time() - started,
    )
    progress.report(
        "cleaning",
        f"有效评论 {meta['valid_comment_count']} 条，无效 {meta['invalid_comment_count']} 条，"
        f"重复 {meta['duplicate_comment_count']} 条",
        ratio=1.0,
    )
    return cleaned
