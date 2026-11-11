"""进度上报：让 Pipeline 能把真实进度回调给 CLI / FastAPI。

进度信息通过 ContextVar 传递，因此分析代码不需要感知 Web 或任务系统的存在：

    with progress.context(callback):
        pipeline_stage()

分析过程中调用 progress.report(...)，没有回调时是空操作。
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

# 回调签名：(stage, message, current, total, percent)
ProgressCallback = Callable[[str, str, int | None, int | None, float | None], None]

_callback: ContextVar[ProgressCallback | None] = ContextVar("analysis_progress_callback", default=None)

# 各阶段在总进度中的权重（总和为 100）
STAGE_WEIGHTS: dict[str, float] = {
    "loading": 1.0,
    "cleaning": 2.0,
    "llm_analysis": 45.0,
    "embedding": 6.0,
    "clustering": 2.0,
    "viewpoint_merge": 18.0,
    "reason_analysis": 18.0,
    "statistics": 3.0,
    "report": 3.0,
    "sampling": 2.0,
}

STAGE_ORDER = [
    "loading",
    "cleaning",
    "llm_analysis",
    "embedding",
    "clustering",
    "viewpoint_merge",
    "reason_analysis",
    "statistics",
    "report",
    "sampling",
]

STAGE_LABELS = {
    "pending": "等待开始",
    "loading": "读取数据",
    "cleaning": "数据清洗",
    "llm_analysis": "LLM 语义分析",
    "embedding": "Embedding 向量化",
    "clustering": "观点语义聚类",
    "viewpoint_merge": "观点归并命名",
    "reason_analysis": "理由归纳",
    "statistics": "统计人数占比",
    "report": "生成报告",
    "sampling": "抽样校验",
    "completed": "分析完成",
    "failed": "分析失败",
}


def set_callback(callback: ProgressCallback | None):
    return _callback.set(callback)


def reset(token) -> None:
    _callback.reset(token)


@contextmanager
def context(callback: ProgressCallback | None) -> Iterator[None]:
    token = set_callback(callback)
    try:
        yield
    finally:
        reset(token)


def get_callback() -> ProgressCallback | None:
    return _callback.get()


def stage_offset(stage: str) -> float:
    """该阶段在总进度中的起始百分比。"""
    offset = 0.0
    for name in STAGE_ORDER:
        if name == stage:
            break
        offset += STAGE_WEIGHTS.get(name, 0.0)
    return offset


def stage_weight(stage: str) -> float:
    return STAGE_WEIGHTS.get(stage, 1.0)


def overall_percent(stage: str, ratio: float) -> float:
    """把阶段内比例（0~1）换算成总进度百分比。"""
    ratio = min(max(ratio, 0.0), 1.0)
    return round(stage_offset(stage) + stage_weight(stage) * ratio, 1)


def report(
    stage: str,
    message: str,
    *,
    current: int | None = None,
    total: int | None = None,
    ratio: float | None = None,
    percent: float | None = None,
) -> None:
    """上报一次进度（无回调时为空操作）。"""
    callback = _callback.get()
    if callback is None:
        return
    if percent is None:
        if ratio is None and current is not None and total:
            ratio = current / total
        if ratio is not None:
            percent = overall_percent(stage, ratio)
    if percent is None:
        # 无法精确计算时只上报阶段信息，不伪造百分比
        percent = stage_offset(stage)
    try:
        callback(stage, message, current, total, percent)
    except Exception:  # noqa: BLE001  进度上报不能影响分析主流程
        pass


# ------------------------------------------------------------------ 进度文案
def format_duration(seconds: float | None) -> str:
    """把秒数格式化成“3 分 20 秒”，用于展示预计剩余时间。"""
    if seconds is None or seconds < 0 or seconds != seconds:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds:02d} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes:02d} 分"


def progress_text(
    current: int,
    total: int,
    started_at: float,
    *,
    unit: str = "条",
    prefix: str = "",
) -> str:
    """生成带百分比、处理速度和预计剩余时间的进度文案（全部来自真实处理量）。"""
    elapsed = max(time.monotonic() - started_at, 1e-6)
    ratio = current / total if total else 0.0
    speed = current / elapsed if current else 0.0
    remaining = (total - current) / speed if speed > 0 else None
    text = f"{prefix}{current} / {total} {unit}（{ratio * 100:.1f}%"
    if speed:
        text += f"，{speed:.1f} {unit}/秒"
    if remaining is not None:
        text += f"，预计剩余 {format_duration(remaining)}"
    return text + "）"


class Throttle:
    """限制进度写库频率（默认每个阶段最多每 0.5 秒一次）。"""

    def __init__(self, interval: float = 0.5) -> None:
        self.interval = interval
        self._last = 0.0
        self._stage = ""

    def allow(self, stage: str, force: bool = False) -> bool:
        now = time.monotonic()
        if force or stage != self._stage or now - self._last >= self.interval:
            self._last = now
            self._stage = stage
            return True
        return False
