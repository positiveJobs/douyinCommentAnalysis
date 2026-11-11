"""任务级文件路径布局。

CLI 沿用项目根目录（保持第一阶段行为不变）；Web 任务使用 data/ 下的独立工作目录，
不同任务之间完全隔离；LLM / Embedding 缓存是全局共享的，便于同一份数据复用缓存。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import config

TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Paths:
    """一个分析任务涉及的全部文件路径。"""

    root: Path
    working_dir: Path
    raw_comments: Path
    cleaned: Path
    extraction: Path
    extraction_errors: Path
    embeddings: Path
    clusters: Path
    final_viewpoints: Path
    merge_detail: Path
    results: Path
    report: Path
    report_html: Path
    sample: Path
    sample_md: Path
    sample_judged: Path

    # 全局共享缓存
    llm_cache: Path = config.LLM_CACHE_DIR
    embedding_cache: Path = config.EMBEDDING_CACHE_FILE
    repair_store: Path = config.REPAIR_STORE_FILE
    embedding_model: Path = config.EMBEDDING_MODEL_DIR

    def ensure_dirs(self) -> "Paths":
        for directory in {
            self.working_dir,
            self.results.parent,
            self.report.parent,
            self.raw_comments.parent,
            self.llm_cache,
            self.embedding_cache.parent,
            self.repair_store.parent,
        }:
            Path(directory).mkdir(parents=True, exist_ok=True)
        return self


def default_paths() -> Paths:
    """项目根目录布局（CLI / 第一阶段兼容）。"""
    root = config.PROJECT_ROOT
    return Paths(
        root=root,
        working_dir=root,
        raw_comments=config.RAW_COMMENTS_FILE,
        cleaned=config.CLEANED_FILE,
        extraction=config.EXTRACTION_FILE,
        extraction_errors=config.EXTRACTION_ERRORS_FILE,
        embeddings=config.EMBEDDINGS_FILE,
        clusters=config.CLUSTERS_FILE,
        final_viewpoints=config.FINAL_VIEWPOINTS_FILE,
        merge_detail=config.MERGE_DETAIL_FILE,
        results=config.RESULTS_FILE,
        report=config.REPORT_FILE,
        report_html=config.HTML_REPORT_FILE,
        sample=config.SAMPLE_FILE,
        sample_md=config.SAMPLE_MD_FILE,
        sample_judged=config.SAMPLE_JUDGED_FILE,
    )


def validate_task_id(task_id: str) -> str:
    if not TASK_ID_RE.match(task_id or ""):
        raise ValueError(f"非法 task_id: {task_id!r}")
    return task_id


def task_paths(task_id: str) -> Paths:
    """Web 任务布局：data/uploads、data/working、data/results、data/reports。"""
    validate_task_id(task_id)
    data = config.DATA_DIR
    uploads = data / "uploads"
    working = data / "working" / task_id
    results_dir = data / "results" / task_id
    reports_dir = data / "reports" / task_id
    return Paths(
        root=data,
        working_dir=working,
        raw_comments=uploads / f"{task_id}.json",
        cleaned=working / "cleaned_comments.json",
        extraction=working / "llm_comment_analysis.json",
        extraction_errors=working / "llm_comment_analysis_errors.json",
        embeddings=working / "viewpoint_embeddings.npz",
        clusters=working / "viewpoint_clusters.json",
        final_viewpoints=working / "final_viewpoints.json",
        merge_detail=working / "viewpoint_merge_detail.json",
        results=results_dir / "analysis_results.json",
        report=reports_dir / "analysis_report.md",
        report_html=reports_dir / "analysis_report.html",
        sample=working / "manual_sample_check.json",
        sample_md=working / "manual_sample_check.md",
        sample_judged=working / "manual_sample_check_judged.json",
    )
