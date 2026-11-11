"""Web 服务配置（文件限制、并发、前端静态资源等）。"""
from __future__ import annotations

import os
from pathlib import Path

from analysis import config as analysis_config

PROJECT_ROOT = analysis_config.PROJECT_ROOT
DATA_DIR = analysis_config.DATA_DIR

# 上传限制
ALLOWED_EXTENSIONS = {".json"}
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

# 任务执行
# LLM 调用本身耗时且接口有限流，默认同时只跑 1 个任务，其余排队
TASK_WORKERS = int(os.environ.get("TASK_WORKERS", "1"))
TASK_QUEUE_SIZE = int(os.environ.get("TASK_QUEUE_SIZE", "50"))

# 列表接口默认分页
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200

# 前端静态资源（npm run build 产物），存在时由后端直接托管
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

# 允许跨域的前端地址（开发时 Vite 使用 5173）
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if origin.strip()
]

DATABASE_FILE: Path = DATA_DIR / "tasks.db"
