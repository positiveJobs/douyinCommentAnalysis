"""FastAPI 应用入口。

启动：
    uv run uvicorn backend.main:app --reload

Web 层只做：接收文件 → 创建任务 → 后台调用 CommentAnalysisPipeline → 查询状态 → 返回结果/下载。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles as _StarletteStaticFiles

from analysis.utils import LOG
from analysis.embedding import device_info as embedding_device_info

from . import __version__, config
from .api import reports, tasks
from .services.task_service import get_task_service
from .storage.cache import cache_info
from .storage.database import get_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    database = get_database()
    interrupted = database.mark_interrupted_tasks("服务重启导致任务中断，请重新发起分析（缓存会复用已完成的部分）")
    if interrupted:
        LOG.warning("有 %d 个任务因服务重启被标记为失败", interrupted)
    service = get_task_service()
    service.start()
    LOG.info("评论分析 Web 服务已就绪")
    try:
        yield
    finally:
        service.stop()


app = FastAPI(
    title="评论观点智能分析服务",
    description="上传评论 JSON，自动完成语义观点提取、观点归并、理由归纳、统计与报告生成。",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)
app.include_router(reports.router)


@app.get("/api/health", tags=["system"], summary="健康检查")
def health() -> dict:
    database = get_database()
    return {
        "status": "ok",
        "version": __version__,
        "tasks": database.count_tasks(),
        "cache": cache_info(),
        "embedding": embedding_device_info(),
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底异常处理：日志记录完整堆栈，返回给前端的只有可读信息（不含 Traceback）。"""
    LOG.exception("未处理异常：%s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请查看后端日志了解详情", "error_type": type(exc).__name__},
    )


# 若前端已构建（npm run build），由后端直接托管，单端口即可访问完整服务
class _ImmutableStaticFiles(_StarletteStaticFiles):
    """带内容 hash 的资源可以长期缓存，避免每次打开页面都重新下载前端包。"""

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return response


if config.FRONTEND_DIST.exists():
    assets_dir = config.FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", _ImmutableStaticFiles(directory=assets_dir), name="assets")

    def _index_response() -> FileResponse:
        return FileResponse(
            config.FRONTEND_DIST / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "接口不存在"})
        candidate = config.FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if full_path.startswith("assets/") else {}
            return FileResponse(candidate, headers=headers)
        return _index_response()
