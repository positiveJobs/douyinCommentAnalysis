"""任务取消：协作式取消（cooperative cancellation）。

分析过程是「批处理 + 线程池」的，直接杀线程会留下半截数据，因此采用协作式取消：
Pipeline 注册一个 threading.Event，各个阶段在批次边界调用 `check()`，
一旦发现取消请求就抛 AnalysisCancelled，由上层负责收尾（删掉本次任务的数据）。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_current: ContextVar["threading.Event | None"] = ContextVar("analysis_cancel_event", default=None)


class AnalysisCancelled(RuntimeError):
    """分析被用户取消。"""

    def __init__(self, message: str = "分析已取消") -> None:
        super().__init__(message)
        self.message = message


def new_event() -> threading.Event:
    return threading.Event()


def set_event(event: "threading.Event | None"):
    return _current.set(event)


def reset(token) -> None:
    _current.reset(token)


def event() -> "threading.Event | None":
    return _current.get()


@contextmanager
def context(target: "threading.Event | None") -> Iterator[None]:
    token = set_event(target)
    try:
        yield
    finally:
        reset(token)


def is_cancelled() -> bool:
    current = _current.get()
    return bool(current is not None and current.is_set())


def check(message: str = "分析已取消") -> None:
    """批次边界调用：若已请求取消则抛出 AnalysisCancelled。"""
    if is_cancelled():
        raise AnalysisCancelled(message)
