"""任务模型。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

TASK_STATUSES = ("pending", "running", "completed", "failed")


@dataclass
class TaskRecord:
    task_id: str
    filename: str
    topic: str | None = None
    status: str = "pending"
    stage: str | None = None
    stage_label: str | None = None
    progress: float = 0.0
    message: str | None = None
    error: str | None = None
    error_stage: str | None = None
    current_step: int | None = None
    total_steps: int | None = None
    comment_count: int | None = None
    viewpoint_count: int | None = None
    upload_path: str | None = None
    result_path: str | None = None
    report_path: str | None = None
    summary_json: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
