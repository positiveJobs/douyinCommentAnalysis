"""报告与下载接口。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse

from ..services.task_service import get_task_service

router = APIRouter(prefix="/api/tasks", tags=["reports"])


def _paths_or_404(task_id: str):
    service = get_task_service()
    if not service.get_task(task_id):
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    return service.paths(task_id)


@router.get("/{task_id}/report", response_class=PlainTextResponse, summary="获取 Markdown 报告")
def get_report(task_id: str) -> PlainTextResponse:
    _paths_or_404(task_id)
    try:
        markdown = get_task_service().report_markdown(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8")


@router.get("/{task_id}/report/json", summary="获取 Markdown 报告的 JSON 包装")
def get_report_json(task_id: str) -> dict:
    _paths_or_404(task_id)
    try:
        markdown = get_task_service().report_markdown(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"task_id": task_id, "format": "markdown", "markdown": markdown}


@router.get("/{task_id}/report/html", response_class=HTMLResponse, summary="查看单文件 HTML 报告")
def get_report_html(
    task_id: str,
    mode: str = Query(
        "report",
        description="report=只显示报告正文（应用内查看）；standalone=完整离线页面（含分析结果视图）",
    ),
) -> HTMLResponse:
    """服务端渲染的 HTML 报告：浏览器直接渲染，不受报告体积影响，也无需前端解析 Markdown。"""
    _paths_or_404(task_id)
    if mode not in ("report", "standalone"):
        raise HTTPException(status_code=400, detail=f"不支持的 mode：{mode}")
    try:
        html_report = get_task_service().report_html(task_id, mode=mode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return HTMLResponse(html_report)


@router.get("/{task_id}/download/json", summary="下载 analysis_results.json")
def download_json(task_id: str) -> FileResponse:
    paths = _paths_or_404(task_id)
    if not paths.results.exists():
        raise HTTPException(status_code=409, detail="分析结果不存在，请确认任务是否已完成")
    return FileResponse(
        paths.results,
        media_type="application/json",
        filename=f"analysis_results_{task_id}.json",
    )


@router.get("/{task_id}/download/report", summary="下载 analysis_report.md")
def download_report(task_id: str) -> FileResponse:
    paths = _paths_or_404(task_id)
    if not paths.report.exists():
        raise HTTPException(status_code=409, detail="分析报告不存在，请确认任务是否已完成")
    return FileResponse(
        paths.report,
        media_type="text/markdown; charset=utf-8",
        filename=f"analysis_report_{task_id}.md",
    )


@router.get("/{task_id}/download/html", summary="下载单文件 HTML 报告（含内嵌数据）")
def download_html(task_id: str) -> FileResponse:
    _paths_or_404(task_id)
    try:
        # 下载的是完整离线页面：结构与网页 /result + /report 一致，数据全部内嵌
        get_task_service().report_html_standalone(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    paths = get_task_service().paths(task_id)
    return FileResponse(
        paths.report_html,
        media_type="text/html; charset=utf-8",
        filename=f"analysis_report_{task_id}.html",
    )


@router.get("/{task_id}/download/package", summary="打包下载（HTML + Markdown + JSON + 原始评论）")
def download_package(task_id: str) -> StreamingResponse:
    _paths_or_404(task_id)
    try:
        buffer = get_task_service().package_zip(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="analysis_package_{task_id}.zip"'},
    )
