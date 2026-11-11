"""通用工具：JSON 读写、日志、文本规范化、哈希。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

_LOGGER_NAME = "viewpoint"


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


LOG = get_logger()

# 零宽字符、方向控制符、BOM、软连字符等对语义没有影响的字符
_INVISIBLE_CHARS = "".join(
    chr(code)
    for code in list(range(0x200B, 0x2010)) + list(range(0x202A, 0x202F)) + [0xFEFF, 0x00AD]
)
_INVISIBLE_RE = re.compile("[" + re.escape(_INVISIBLE_CHARS) + "]")
# 短视频平台表情占位符，例如 [笑哭]、[看]、[比心]
_BRACKET_EMOJI_RE = re.compile(r"\[[^\[\]]{1,10}\]")
_WS_RE = re.compile(r"[ \t\u3000]+")
_MULTI_NL_RE = re.compile(r"\n{2,}")


def normalize_text(text: str) -> str:
    """规范化评论文本：去不可见字符、统一空白、压缩重复换行。"""
    if not isinstance(text, str):
        return ""
    text = _INVISIBLE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _MULTI_NL_RE.sub("\n", text)
    return text.strip()


def strip_emoji_placeholders(text: str) -> str:
    """去掉 [笑哭] 这类表情占位，用于判断“纯表情”评论。"""
    return _BRACKET_EMOJI_RE.sub("", text)


def sha256_hex(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """原子写入 JSON，避免中断时留下损坏文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def chunk_by_budget(items: Iterable[Any], text_of, *, max_items: int, max_chars: int) -> list[list[Any]]:
    """按“条数上限 + 字符上限”把序列切分成批次。"""
    batches: list[list[Any]] = []
    current: list[Any] = []
    size = 0
    for item in items:
        length = len(text_of(item))
        if current and (len(current) >= max_items or size + length > max_chars):
            batches.append(current)
            current = []
            size = 0
        current.append(item)
        size += length
    if current:
        batches.append(current)
    return batches
