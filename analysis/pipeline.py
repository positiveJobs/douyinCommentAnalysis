"""CommentAnalysisPipeline：评论观点分析的统一编排入口。

CLI、FastAPI、未来的 Skill 都通过这个类执行分析，分析逻辑本身完全复用各阶段模块：

    pipeline = CommentAnalysisPipeline(
        input_file="comments.json",
        task_id="20260919_abc123",          # Web 任务：数据落在 data/ 下并与其他任务隔离
        progress_callback=on_progress,      # 可选：实时进度回调
    )
    result = pipeline.run()

设计要点：
- 不做任何算法实现，只编排 清洗 → LLM 语义分析 → Embedding/聚类 → 观点归并 → 理由归纳 → 统计 → 报告；
- 每个阶段都可以单独重跑，中间结果落盘（Web 任务在 data/working/<task_id>/），失败不会丢弃已完成阶段；
- LLM / Embedding 缓存在 cache/ 下全局共享，同一份数据重复分析不会重复消耗 API；
- 不支持或未配置的能力（例如没有 API Key）会以 PipelineError 抛出，由调用方决定如何展示。
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import cancel, cleaning, clustering, extraction, merging, progress, reasons, report, sampling, stats
from .paths import Paths, default_paths, task_paths
from .utils import LOG, read_json

ProgressCallback = progress.ProgressCallback


class PipelineError(RuntimeError):
    """分析失败：携带失败阶段与面向用户的错误信息。"""

    def __init__(self, stage: str, message: str, original: BaseException | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.stage_label = progress.STAGE_LABELS.get(stage, stage)
        self.message = message
        self.original = original

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "stage_label": self.stage_label,
            "message": self.message,
            "error_type": type(self.original).__name__ if self.original else None,
            "detail": str(self.original) if self.original else None,
        }


class PipelineCancelled(PipelineError):
    """任务被用户取消（与失败区分开，便于上层清理数据）。"""

    def __init__(self, stage: str, message: str = "分析已取消") -> None:
        super().__init__(stage, message, None)


@dataclass
class PipelineResult:
    task_id: str | None
    input_file: Path
    result_path: Path
    report_path: Path
    summary: dict
    viewpoint_count: int
    elapsed_seconds: float
    stages: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "input_file": str(self.input_file),
            "result_path": str(self.result_path),
            "report_path": str(self.report_path),
            "summary": self.summary,
            "viewpoint_count": self.viewpoint_count,
            "elapsed_seconds": self.elapsed_seconds,
            "stages": self.stages,
        }


class CommentAnalysisPipeline:
    """评论观点分析流水线（CLI / Web / 未来 Skill 共用）。"""

    STAGES: Sequence[str] = (
        "cleaning",
        "llm_analysis",
        "clustering",
        "viewpoint_merge",
        "reason_analysis",
        "statistics",
        "report",
        "sampling",
    )

    STAGE_LABELS = {
        "cleaning": "数据清洗",
        "llm_analysis": "LLM 语义分析",
        "clustering": "Embedding / 观点聚类",
        "viewpoint_merge": "观点归并命名",
        "reason_analysis": "理由归纳",
        "statistics": "统计人数占比",
        "report": "生成报告",
        "sampling": "抽样校验",
    }

    def __init__(
        self,
        input_file: str | Path,
        task_id: str | None = None,
        *,
        paths: Paths | None = None,
        force: bool = False,
        sample_size: int | None = None,
        limit: int | None = None,
        topic: str | None = None,
        repair: bool = True,
        run_sampling: bool = True,
        progress_callback: ProgressCallback | None = None,
        cancel_event: "object | None" = None,
    ) -> None:
        self.input_file = Path(input_file)
        self.task_id = task_id
        if paths is not None:
            self.paths = paths
        elif task_id:
            self.paths = task_paths(task_id)
        else:
            self.paths = default_paths()
        self.force = force
        self.sample_size = sample_size
        self.limit = limit
        self.topic = topic
        self.repair = repair
        self.run_sampling = run_sampling
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event

    # ------------------------------------------------------------------ 工具
    def _prepare_input(self) -> None:
        """确保任务目录下的输入文件存在（Web 上传已落盘，CLI 直接使用原文件）。"""
        self.paths.ensure_dirs()
        target = self.paths.raw_comments
        if self.input_file.resolve() == target.resolve():
            return
        if self.input_file.exists() and not target.exists():
            shutil.copyfile(self.input_file, target)
            LOG.info("已复制输入文件到任务目录：%s", target)

    def _report(self, stage: str, message: str, **kwargs) -> None:
        progress.report(stage, message, **kwargs)

    # ------------------------------------------------------------------ 主流程
    def run(self) -> PipelineResult:
        started = time.time()
        stage_records: list[dict] = []
        self._prepare_input()

        with progress.context(self.progress_callback), cancel.context(self.cancel_event):
            self._report("loading", "准备开始分析…")
            for stage in self.STAGES:
                if stage == "sampling" and not self.run_sampling:
                    continue
                stage_started = time.time()
                LOG.info("=== 阶段开始：%s ===", self.STAGE_LABELS[stage])
                try:
                    # 阶段边界检查取消信号（放在 try 内，统一转成 PipelineCancelled）
                    cancel.check(f"分析已取消（{self.STAGE_LABELS[stage]}）")
                    self._run_stage(stage)
                except cancel.AnalysisCancelled as exc:
                    LOG.warning("阶段被取消：%s", self.STAGE_LABELS[stage])
                    self._report(stage, f"{self.STAGE_LABELS[stage]} 已取消", ratio=1.0)
                    raise PipelineCancelled(stage, str(exc) or "分析已取消") from exc
                except Exception as exc:  # noqa: BLE001  统一转为可展示的错误
                    LOG.exception("阶段失败：%s", self.STAGE_LABELS[stage])
                    message = self._friendly_error(stage, exc)
                    self._report(stage, message, ratio=1.0)
                    raise PipelineError(stage, message, exc) from exc
                elapsed = round(time.time() - stage_started, 1)
                stage_records.append({"stage": stage, "label": self.STAGE_LABELS[stage], "elapsed_seconds": elapsed})
                LOG.info("=== 阶段完成：%s（%.1fs）===", self.STAGE_LABELS[stage], elapsed)

            results = read_json(self.paths.results) or {}
            summary = results.get("summary", {})
            self._report("completed", "分析完成", percent=100.0)

        return PipelineResult(
            task_id=self.task_id,
            input_file=self.paths.raw_comments,
            result_path=self.paths.results,
            report_path=self.paths.report,
            summary=summary,
            viewpoint_count=summary.get("viewpoint_count", 0),
            elapsed_seconds=round(time.time() - started, 1),
            stages=stage_records,
        )

    def _run_stage(self, stage: str) -> None:
        """执行单个阶段：直接复用第一阶段已验证的分析模块。"""
        if stage == "cleaning":
            cleaning.run(force=self.force, paths=self.paths, topic=self.topic)
        elif stage == "llm_analysis":
            extraction.run(force=self.force, limit=self.limit, paths=self.paths)
            if self.repair:
                extraction.run(repair=True, limit=self.limit, paths=self.paths)
        elif stage == "clustering":
            clustering.run(force=self.force, paths=self.paths)
        elif stage == "viewpoint_merge":
            merging.run(force=self.force, paths=self.paths)
        elif stage == "reason_analysis":
            reasons.run(force=self.force, paths=self.paths)
        elif stage == "statistics":
            stats.run(force=self.force, paths=self.paths)
        elif stage == "report":
            report.run(paths=self.paths)
        elif stage == "sampling":
            sampling.run(force=self.force, sample_size=self.sample_size or 100, paths=self.paths)
        else:  # pragma: no cover
            raise ValueError(f"未知阶段：{stage}")

    def _friendly_error(self, stage: str, exc: BaseException) -> str:
        """把底层异常转换成用户能看懂的失败信息（不向前端暴露 Traceback）。"""
        label = self.STAGE_LABELS.get(stage, stage)
        text = str(exc) or type(exc).__name__
        lowered = text.lower()
        if "429" in text or "rate limit" in lowered:
            detail = "LLM 接口触发限流（rate limit），请稍后重试"
        elif "timeout" in lowered or "timed out" in lowered:
            detail = "LLM 接口请求超时"
        elif "http 401" in lowered or "http 403" in lowered:
            detail = "LLM 接口鉴权失败，请检查 .env 中的 API_KEY"
        elif isinstance(exc, FileNotFoundError):
            detail = f"缺少文件：{exc.filename or text}"
        elif isinstance(exc, (ValueError, RuntimeError)):
            detail = text
        else:
            detail = text[:200]
        return f"{label} 失败：{detail}"


def run_all(*, force: bool = False, sample_size: int | None = None) -> dict:
    """CLI 兼容入口：使用项目根目录布局跑完整流程。"""
    from . import config

    started = time.time()
    LOG.info("=== 开始执行完整流程 ===")
    pipeline = CommentAnalysisPipeline(
        input_file=config.RAW_COMMENTS_FILE,
        paths=default_paths(),
        force=force,
        sample_size=sample_size or config.SAMPLE_SIZE,
        topic=config.POST_TOPIC,
    )
    pipeline.run()
    LOG.info("=== 流程完成，总耗时 %.1fs ===", time.time() - started)
    return {"elapsed_seconds": round(time.time() - started, 1)}
