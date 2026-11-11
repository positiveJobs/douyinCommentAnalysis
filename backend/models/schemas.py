"""API 响应结构（Pydantic）。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateTaskResponse(BaseModel):
    task_id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    filename: str = ""
    topic: str | None = None
    comment_count: int | None = None
    message: str | None = None


class TaskInfo(BaseModel):
    task_id: str
    filename: str
    topic: str | None = None
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    stage: str | None = None
    stage_label: str | None = None
    progress: float = 0
    message: str | None = None
    error: str | None = None
    error_stage: str | None = None
    current_step: int | None = None
    total_steps: int | None = None
    comment_count: int | None = None
    viewpoint_count: int | None = None
    created_at: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None
    elapsed_seconds: float | None = None
    has_result: bool = False
    has_report: bool = False


class TaskListResponse(BaseModel):
    total: int
    page: int = 1
    page_size: int = 20
    status_counts: dict[str, int] = Field(default_factory=dict)
    items: list[TaskInfo]


class SummaryResponse(BaseModel):
    task_id: str
    meta: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    integrity: dict[str, Any] = Field(default_factory=dict)


class ReasonItem(BaseModel):
    reason_id: str
    name: str
    description: str = ""
    comment_count: int
    percentage: float


class ViewpointBrief(BaseModel):
    viewpoint_id: str
    name: str
    description: str = ""
    stance: str = "unclear"
    comment_count: int
    percentage: float
    reason_count: int = 0
    representative_comments: list[str] = Field(default_factory=list)


class ViewpointListResponse(BaseModel):
    task_id: str
    total: int
    valid_comment_count: int
    items: list[ViewpointBrief]


class ViewpointDetail(ViewpointBrief):
    stance_distribution: dict[str, int] = Field(default_factory=dict)
    reasons: list[ReasonItem] = Field(default_factory=list)
    representative_comment_details: list[dict[str, Any]] = Field(default_factory=list)
    sub_viewpoints: list[dict[str, Any]] = Field(default_factory=list)
    statements: list[dict[str, Any]] = Field(default_factory=list)
    reason_stats: dict[str, Any] = Field(default_factory=dict)


class CommentPageItem(BaseModel):
    comment_id: str
    content: str
    like_count: int = 0


class CommentPage(BaseModel):
    task_id: str
    total: int
    page: int
    page_size: int
    total_pages: int
    items: list[CommentPageItem]
