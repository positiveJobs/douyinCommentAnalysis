"""上传文件处理：类型/大小校验、按 task_id 落盘（避免路径穿越）、评论数量快速统计。"""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile

from analysis.cleaning import iter_comment_records
from analysis.paths import task_paths, validate_task_id

from .. import config


class FileServiceError(ValueError):
    """上传文件不合法（面向用户展示的错误）。"""


def new_task_id() -> str:
    """任务 ID：时间戳 + 随机串，例如 20260919_101530_ab12cd。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{secrets.token_hex(3)}"


def _check_extension(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in config.ALLOWED_EXTENSIONS:
        allowed = "、".join(sorted(config.ALLOWED_EXTENSIONS))
        raise FileServiceError(f"文件格式错误：只支持 {allowed} 文件，当前为 {suffix or '未知类型'}")
    return suffix


async def save_upload(task_id: str, upload: UploadFile) -> Path:
    """把上传文件按 task_id 保存到 data/uploads/，并限制文件大小。"""
    validate_task_id(task_id)
    suffix = _check_extension(upload.filename or "")
    paths = task_paths(task_id)
    target = paths.raw_comments
    if target.suffix != suffix:
        target = target.with_suffix(suffix)
    target.parent.mkdir(parents=True, exist_ok=True)

    size = 0
    with open(target, "wb") as handle:
        while True:
            chunk = await upload.read(config.UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > config.MAX_UPLOAD_BYTES:
                handle.close()
                target.unlink(missing_ok=True)
                raise FileServiceError(
                    f"文件过大：最大支持 {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                )
            handle.write(chunk)
    if size == 0:
        target.unlink(missing_ok=True)
        raise FileServiceError("上传文件为空")
    return target


def validate_comment_file(path: Path) -> int:
    """校验 JSON 可解析且包含评论，返回评论条数（不修改文件）。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise FileServiceError(f"文件格式错误：不是合法的 JSON（{exc.msg}，第 {exc.lineno} 行）") from exc
    except UnicodeDecodeError as exc:
        raise FileServiceError("文件格式错误：请使用 UTF-8 编码的 JSON 文件") from exc

    if not isinstance(payload, (list, dict)):
        raise FileServiceError("文件格式错误：JSON 顶层必须是数组或对象")

    count = sum(1 for _ in iter_comment_records(payload))
    if count == 0:
        raise FileServiceError(
            "没有解析到任何评论：请确认文件包含评论文本字段（如 content）与评论 ID 字段（如 comment_id）"
        )
    return count


def validate_comment_bytes(data: bytes, filename: str) -> tuple[int, str | None]:
    """校验内存中的上传内容（用于上传前预检），返回 (评论条数, 检测到的主题)。"""
    _check_extension(filename)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise FileServiceError(f"文件过大：最大支持 {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data:
        raise FileServiceError("上传文件为空")
    try:
        payload = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise FileServiceError(f"文件格式错误：不是合法的 JSON（{exc.msg}，第 {exc.lineno} 行）") from exc
    except UnicodeDecodeError as exc:
        raise FileServiceError("文件格式错误：请使用 UTF-8 编码的 JSON 文件") from exc
    if not isinstance(payload, (list, dict)):
        raise FileServiceError("文件格式错误：JSON 顶层必须是数组或对象")
    count = sum(1 for _ in iter_comment_records(payload))
    if count == 0:
        raise FileServiceError(
            "没有解析到任何评论：请确认文件包含评论文本字段（如 content）与评论 ID 字段（如 comment_id）"
        )
    from analysis.cleaning import detect_topic

    return count, detect_topic(payload)
