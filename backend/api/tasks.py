"""任务相关接口：上传创建任务、查询状态、查询结果（分页）。"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile

from .. import config
from ..models.schemas import (
    CommentPage,
    CreateTaskResponse,
    SummaryResponse,
    TaskInfo,
    TaskListResponse,
    ViewpointDetail,
    ViewpointListResponse,
)
from ..services.file_service import (
    FileServiceError,
    new_task_id,
    save_upload,
    validate_comment_bytes,
    validate_comment_file,
)
from ..services.task_service import get_task_service

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _task_or_404(task_id: str) -> dict:
    task = get_task_service().get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    return task


@router.post("/preflight", summary="上传前预检：校验 JSON 并返回评论数量")
async def preflight(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    try:
        comment_count, topic = validate_comment_bytes(data, file.filename or "comments.json")
    except FileServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "filename": file.filename,
        "size_bytes": len(data),
        "comment_count": comment_count,
        "topic": topic,
    }


@router.post("", response_model=CreateTaskResponse, summary="上传评论 JSON 并创建分析任务")
async def create_task(
    background: BackgroundTasks,
    file: UploadFile = File(..., description="评论 JSON 文件"),
    topic: str | None = Form(None, description="可选的讨论主题；不填则从文件或评论内容推断"),
) -> CreateTaskResponse:
    service = get_task_service()
    task_id = new_task_id()
    try:
        path = await save_upload(task_id, file)
        comment_count = validate_comment_file(path)
    except FileServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record = service.create_task(file.filename or path.name, path, comment_count, topic=(topic or None))
    # 立即返回 task_id，真正的分析在后台 worker 中排队执行，不阻塞接口
    background.add_task(service.enqueue, record["task_id"])
    return CreateTaskResponse(
        task_id=record["task_id"],
        status=record["status"],
        filename=record["filename"],
        topic=record.get("topic"),
        comment_count=comment_count,
        message="任务已创建，正在排队分析",
    )


@router.get("", response_model=TaskListResponse, summary="任务列表（支持状态筛选与关键字搜索）")
def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(config.DEFAULT_PAGE_SIZE, ge=1, le=100),
    status: str | None = Query(None, description="pending / running / completed / failed / cancelled"),
    q: str | None = Query(None, description="按文件名搜索"),
) -> TaskListResponse:
    if status and status not in ("pending", "running", "completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"不支持的任务状态：{status}")
    service = get_task_service()
    total, items = service.list_tasks(
        limit=page_size, offset=(page - 1) * page_size, status=status, keyword=q
    )
    return TaskListResponse(
        total=total,
        page=page,
        page_size=page_size,
        status_counts=service.status_counts(),
        items=[TaskInfo(**item) for item in items],
    )


@router.get("/{task_id}", response_model=TaskInfo, summary="查询任务状态与进度")
def get_task(task_id: str) -> TaskInfo:
    task = _task_or_404(task_id)
    return TaskInfo(**get_task_service().task_info(task))


@router.get("/{task_id}/summary", response_model=SummaryResponse, summary="数据概况与统计摘要")
def get_summary(task_id: str) -> SummaryResponse:
    _task_or_404(task_id)
    try:
        return SummaryResponse(**get_task_service().summary(task_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{task_id}/result", summary="完整分析结果 JSON")
def get_result(task_id: str) -> dict:
    _task_or_404(task_id)
    try:
        return get_task_service().load_results(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{task_id}/viewpoints", response_model=ViewpointListResponse, summary="观点列表")
def get_viewpoints(task_id: str) -> ViewpointListResponse:
    _task_or_404(task_id)
    try:
        return ViewpointListResponse(**get_task_service().viewpoints(task_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{task_id}/viewpoints/{viewpoint_id}", response_model=ViewpointDetail, summary="观点详情（含理由）")
def get_viewpoint(task_id: str, viewpoint_id: str) -> ViewpointDetail:
    _task_or_404(task_id)
    try:
        return ViewpointDetail(**get_task_service().viewpoint_detail(task_id, viewpoint_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"观点不存在：{viewpoint_id}") from exc


@router.get(
    "/{task_id}/viewpoints/{viewpoint_id}/comments",
    response_model=CommentPage,
    summary="该观点对应的原始评论（分页）",
)
def get_viewpoint_comments(
    task_id: str,
    viewpoint_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(config.DEFAULT_PAGE_SIZE, ge=1, le=config.MAX_PAGE_SIZE),
) -> CommentPage:
    _task_or_404(task_id)
    try:
        payload = get_task_service().comments_page(
            task_id, page=page, page_size=page_size, viewpoint_id=viewpoint_id
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"观点不存在：{viewpoint_id}") from exc
    return CommentPage(**payload)


@router.get("/{task_id}/comments", response_model=CommentPage, summary="全部有效评论（分页）")
def get_comments(
    task_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(config.DEFAULT_PAGE_SIZE, ge=1, le=config.MAX_PAGE_SIZE),
) -> CommentPage:
    _task_or_404(task_id)
    try:
        payload = get_task_service().comments_page(task_id, page=page, page_size=page_size)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CommentPage(**payload)


@router.delete("/{task_id}", summary="删除任务及其本地数据")
def delete_task(task_id: str) -> dict:
    _task_or_404(task_id)
    try:
        return get_task_service().delete_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/cancel", summary="取消分析并删除本次任务产生的数据")
def cancel_task(task_id: str) -> dict:
    _task_or_404(task_id)
    try:
        return get_task_service().cancel_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
