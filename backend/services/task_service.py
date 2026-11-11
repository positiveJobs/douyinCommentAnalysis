"""任务服务：创建任务、后台排队执行 CommentAnalysisPipeline、读取结果。

HTTP 层只调用本服务，不直接接触分析逻辑；分析全部交给 CommentAnalysisPipeline。
"""
from __future__ import annotations

import json
import queue
import shutil
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from analysis import cancel as cancel_module
from analysis import progress
from analysis.paths import task_paths
from analysis.pipeline import CommentAnalysisPipeline, PipelineCancelled, PipelineError
from analysis.report_html import build_html_report
from analysis.report_html import GENERATOR_VERSION as HTML_GENERATOR_VERSION
from analysis.utils import LOG, read_json, write_json

from .. import config
from ..storage.database import Database, get_database


class TaskService:
    """分析任务的管理者。"""

    def __init__(self, database: Database | None = None, workers: int | None = None) -> None:
        self.db = database or get_database()
        self.worker_count = max(1, workers or config.TASK_WORKERS)
        self._queue: "queue.Queue[str]" = queue.Queue(maxsize=config.TASK_QUEUE_SIZE)
        self._workers: list[threading.Thread] = []
        self._stop = threading.Event()
        self._result_cache: dict[str, tuple[float, dict]] = {}
        self._comment_index_cache: dict[str, tuple[float, dict[str, dict]]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._started = False

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._started:
            return
        self.db.init()
        self._stop.clear()
        for index in range(self.worker_count):
            thread = threading.Thread(target=self._worker_loop, name=f"analysis-worker-{index}", daemon=True)
            thread.start()
            self._workers.append(thread)
        self._started = True
        # 服务重启后把未跑完的 pending 任务重新入队（中间结果与缓存仍在，可继续跑）
        for task_id in self.db.pending_task_ids():
            self.enqueue(task_id)
        LOG.info("任务服务已启动，worker=%d", self.worker_count)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._workers:
            thread.join(timeout=2)
        self._workers.clear()
        self._started = False

    # ------------------------------------------------------------------ 任务管理
    def create_task(
        self,
        filename: str,
        upload_path: Path,
        comment_count: int,
        topic: str | None = None,
    ) -> dict[str, Any]:
        task_id = Path(upload_path).stem
        record = {
            "task_id": task_id,
            "filename": filename,
            "topic": topic,
            "status": "pending",
            "stage": "pending",
            "stage_label": progress.STAGE_LABELS["pending"],
            "progress": 0.0,
            "message": "任务已创建，等待开始分析",
            "comment_count": comment_count,
            "upload_path": str(upload_path),
        }
        return self.db.create_task(record)

    def enqueue(self, task_id: str) -> None:
        try:
            self._queue.put_nowait(task_id)
        except queue.Full:  # pragma: no cover
            LOG.error("任务队列已满，无法入队：%s", task_id)
            self.db.update_task(
                task_id,
                status="failed",
                error="任务队列已满，请稍后重试",
                message="任务队列已满，请稍后重试",
            )

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        """请求取消任务：设置取消信号，worker 会尽快停下并删除本次任务的数据。"""
        task = self.db.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if task["status"] not in ("running", "pending"):
            raise RuntimeError(f"任务当前状态为 {task['status']}，无法取消")
        event = self._cancel_events.get(task_id)
        if event is None:
            event = cancel_module.new_event()
            self._cancel_events[task_id] = event
        event.set()
        self.db.update_task(
            task_id,
            message="已请求取消，正在停止分析并清理数据…",
        )
        LOG.info("收到取消请求：%s", task_id)
        return {"task_id": task_id, "status": "cancelling"}

    # ------------------------------------------------------------------ worker
    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                task_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._run_task(task_id)
            except Exception:  # noqa: BLE001  兜底，保证 worker 线程不退出
                LOG.exception("任务执行异常：%s", task_id)
            finally:
                self._queue.task_done()

    def _run_task(self, task_id: str) -> None:
        task = self.db.get_task(task_id)
        if not task:
            LOG.warning("任务不存在：%s", task_id)
            return
        paths = task_paths(task_id)
        started = time.time()
        self.db.update_task(
            task_id,
            status="running",
            stage="loading",
            stage_label=progress.STAGE_LABELS["loading"],
            progress=0.0,
            message="开始分析",
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            error=None,
            error_stage=None,
        )

        throttle = progress.Throttle(0.5)
        event = self._cancel_events.setdefault(task_id, cancel_module.new_event())

        def on_progress(
            stage: str, message: str, current: int | None, total: int | None, percent: float | None
        ) -> None:
            if stage == "completed" or throttle.allow(stage):
                self.db.update_task(
                    task_id,
                    status="running",
                    stage=stage,
                    stage_label=progress.STAGE_LABELS.get(stage, stage),
                    progress=round(percent or 0, 1),
                    message=message,
                    current_step=current,
                    total_steps=total,
                )

        pipeline = CommentAnalysisPipeline(
            input_file=paths.raw_comments,
            task_id=task_id,
            progress_callback=on_progress,
            topic=task.get("topic"),
            cancel_event=event,
        )
        try:
            result = pipeline.run()
        except PipelineCancelled as exc:
            # 取消：清空该任务的全部数据（上传文件、中间结果、结果、报告）与任务记录
            LOG.info("任务已取消，开始清理数据：%s", task_id)
            self.db.update_task(
                task_id,
                status="cancelled",
                stage=exc.stage,
                stage_label="已取消",
                message="分析已取消，正在删除本次任务产生的数据…",
                error=None,
                error_stage=None,
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                elapsed_seconds=round(time.time() - started, 1),
            )
            try:
                removed = self.delete_task(task_id)
                LOG.info("任务 %s 已取消并清理 %d 项数据", task_id, len(removed.get("removed", [])))
            except Exception as exc:  # noqa: BLE001 清理失败时保留记录便于排查
                LOG.exception("取消后清理数据失败：%s", task_id)
                self.db.update_task(
                    task_id,
                    status="cancelled",
                    message=f"分析已取消，但部分数据清理失败：{exc}",
                )
            return
        except PipelineError as exc:
            LOG.error("任务失败 %s：%s", task_id, exc.message)
            self.db.update_task(
                task_id,
                status="failed",
                error=exc.message,
                error_stage=exc.stage,
                stage=exc.stage,
                stage_label=exc.stage_label,
                message=exc.message,
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                elapsed_seconds=round(time.time() - started, 1),
            )
            return
        except Exception as exc:  # noqa: BLE001
            LOG.exception("任务异常 %s", task_id)
            self.db.update_task(
                task_id,
                status="failed",
                error=f"分析失败：{exc}",
                error_stage="unknown",
                message=f"分析失败：{exc}",
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                elapsed_seconds=round(time.time() - started, 1),
            )
            return
        finally:
            self._cancel_events.pop(task_id, None)

        summary = result.summary or {}
        self.db.update_task(
            task_id,
            status="completed",
            stage="completed",
            stage_label=progress.STAGE_LABELS["completed"],
            progress=100.0,
            message="分析完成",
            viewpoint_count=result.viewpoint_count,
            comment_count=summary.get("valid_comment_count") or task.get("comment_count"),
            result_path=str(result.result_path),
            report_path=str(result.report_path),
            summary_json=json.dumps(summary, ensure_ascii=False),
            finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            elapsed_seconds=result.elapsed_seconds,
        )
        LOG.info("任务完成：%s（%.1fs）", task_id, result.elapsed_seconds)

    # ------------------------------------------------------------------ 查询
    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.db.get_task(task_id)

    def task_info(self, task: dict[str, Any]) -> dict[str, Any]:
        paths = task_paths(task["task_id"])
        return {**task, "has_result": paths.results.exists(), "has_report": paths.report.exists()}

    def list_tasks(
        self,
        limit: int = 20,
        offset: int = 0,
        *,
        status: str | None = None,
        keyword: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        total = self.db.count_tasks(status=status, keyword=keyword)
        tasks = self.db.list_tasks(limit, offset, status=status, keyword=keyword)
        return total, [self.task_info(task) for task in tasks]

    def status_counts(self) -> dict[str, int]:
        return self.db.status_counts()

    def paths(self, task_id: str):
        return task_paths(task_id)

    def load_results(self, task_id: str) -> dict[str, Any]:
        paths = task_paths(task_id)
        if not paths.results.exists():
            raise FileNotFoundError("分析结果不存在，请确认任务是否已完成")
        mtime = paths.results.stat().st_mtime
        with self._lock:
            cached = self._result_cache.get(task_id)
        if cached and cached[0] == mtime:
            return cached[1]
        payload = read_json(paths.results) or {}
        with self._lock:
            self._result_cache[task_id] = (mtime, payload)
        return payload

    def _comment_index(self, task_id: str) -> dict[str, dict]:
        """comment_id -> {content, like_count}，用于评论分页查询。"""
        paths = task_paths(task_id)
        if not paths.cleaned.exists():
            return {}
        mtime = paths.cleaned.stat().st_mtime
        with self._lock:
            cached = self._comment_index_cache.get(task_id)
        if cached and cached[0] == mtime:
            return cached[1]
        cleaned = read_json(paths.cleaned) or {}
        index = {
            comment["comment_id"]: {
                "comment_id": comment["comment_id"],
                "content": comment["content"],
                "like_count": comment.get("like_count", 0),
            }
            for comment in cleaned.get("comments", [])
        }
        with self._lock:
            self._comment_index_cache[task_id] = (mtime, index)
        return index

    def summary(self, task_id: str) -> dict[str, Any]:
        results = self.load_results(task_id)
        return {
            "task_id": task_id,
            "meta": results.get("meta", {}),
            "summary": results.get("summary", {}),
            "integrity": results.get("integrity", {}),
        }

    def viewpoints(self, task_id: str) -> dict[str, Any]:
        results = self.load_results(task_id)
        summary = results.get("summary", {})
        items = [
            {
                "viewpoint_id": viewpoint["viewpoint_id"],
                "name": viewpoint["name"],
                "description": viewpoint.get("description", ""),
                "stance": viewpoint.get("stance", "unclear"),
                "comment_count": viewpoint["comment_count"],
                "percentage": viewpoint["percentage"],
                "reason_count": len(viewpoint.get("reasons", [])),
                "representative_comments": viewpoint.get("representative_comments", [])[:3],
            }
            for viewpoint in results.get("viewpoints", [])
        ]
        return {
            "task_id": task_id,
            "total": len(items),
            "valid_comment_count": summary.get("valid_comment_count", 0),
            "items": items,
        }

    def viewpoint_detail(self, task_id: str, viewpoint_id: str) -> dict[str, Any]:
        results = self.load_results(task_id)
        for viewpoint in results.get("viewpoints", []):
            if viewpoint["viewpoint_id"] == viewpoint_id:
                return {
                    **viewpoint,
                    "reason_count": len(viewpoint.get("reasons", [])),
                    "representative_comments": viewpoint.get("representative_comments", []),
                    "reasons": [
                        {
                            "reason_id": reason["reason_id"],
                            "name": reason["name"],
                            "description": reason.get("description", ""),
                            "comment_count": reason["comment_count"],
                            "percentage": reason["percentage"],
                        }
                        for reason in viewpoint.get("reasons", [])
                    ],
                    "sub_viewpoints": viewpoint.get("sub_viewpoints", []),
                }
        raise KeyError(viewpoint_id)

    def comments_page(
        self,
        task_id: str,
        *,
        page: int = 1,
        page_size: int = config.DEFAULT_PAGE_SIZE,
        viewpoint_id: str | None = None,
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), config.MAX_PAGE_SIZE)
        index = self._comment_index(task_id)
        if viewpoint_id:
            detail = self.viewpoint_detail(task_id, viewpoint_id)
            ordered_ids = [cid for cid in detail.get("comment_ids", []) if cid in index]
        else:
            ordered_ids = list(index)
        total = len(ordered_ids)
        start = (page - 1) * page_size
        selected = ordered_ids[start : start + page_size]
        return {
            "task_id": task_id,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size) if total else 0,
            "items": [index[cid] for cid in selected],
        }

    def report_markdown(self, task_id: str) -> str:
        paths = task_paths(task_id)
        if not paths.report.exists():
            raise FileNotFoundError("分析报告不存在，请确认任务是否已完成")
        return paths.report.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ HTML 报告
    def report_html(self, task_id: str, *, mode: str = "report", force: bool = False) -> str:
        """生成（或复用）HTML 报告。

        mode="report"     仅报告正文，供应用内 iframe 嵌入展示（对应 /report 页面）
        mode="standalone" 完整离线页面：顶部导航 +「分析结果」/「完整报告」两个视图，
                          结构与网页一致，用于下载与打包（对应 /result + /report 页面）
        """
        paths = task_paths(task_id)
        if not paths.results.exists():
            raise FileNotFoundError("分析结果不存在，请确认任务是否已完成")

        if mode == "report":
            target = paths.report_html.with_name("analysis_report.view.html")
        else:
            target = paths.report_html
        meta_file = target.with_name(target.name + ".meta.json")

        results_mtime = paths.results.stat().st_mtime
        if target.exists() and not force and target.stat().st_mtime >= results_mtime:
            cached_meta = read_json(meta_file) or {}
            if cached_meta.get("generator_version") == HTML_GENERATOR_VERSION:
                return target.read_text(encoding="utf-8")
            LOG.info("HTML 报告生成器已升级（%s → %s），重新生成 %s",
                     cached_meta.get("generator_version"), HTML_GENERATOR_VERSION, target.name)

        results = self.load_results(task_id)
        comments = self._comment_index(task_id)
        task = self.db.get_task(task_id) or {}
        filename = task.get("filename") or task_id
        title = f"评论观点分析报告 · {filename}" if mode == "report" else f"评论观点分析 · {filename}"
        html_report = build_html_report(results, comments=comments, task_title=title, mode=mode)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html_report, encoding="utf-8")
        write_json(meta_file, {"generator_version": HTML_GENERATOR_VERSION, "mode": mode})
        LOG.info("HTML 报告已生成（%s）：%s（%.1f KB）", mode, target, len(html_report.encode("utf-8")) / 1024)
        return html_report

    def report_html_standalone(self, task_id: str, *, force: bool = False) -> str:
        """完整离线页面（分析结果 + 完整报告），用于下载/打包。"""
        return self.report_html(task_id, mode="standalone", force=force)

    # ------------------------------------------------------------------ 打包下载
    def package_zip(self, task_id: str) -> BytesIO:
        """把结果 JSON、Markdown 报告、HTML 报告与原始评论打包成一个 zip。"""
        paths = task_paths(task_id)
        if not paths.results.exists():
            raise FileNotFoundError("分析结果不存在，请确认任务是否已完成")
        self.report_html_standalone(task_id)  # 确保离线 HTML 报告已生成

        task = self.db.get_task(task_id) or {}
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            if paths.results.exists():
                archive.write(paths.results, f"{task_id}/analysis_results.json")
            if paths.report.exists():
                archive.write(paths.report, f"{task_id}/analysis_report.md")
            if paths.report_html.exists():
                archive.write(paths.report_html, f"{task_id}/analysis_report.html")
            if paths.cleaned.exists():
                archive.write(paths.cleaned, f"{task_id}/cleaned_comments.json")
            if paths.raw_comments.exists():
                archive.write(paths.raw_comments, f"{task_id}/source/{paths.raw_comments.name}")
            readme = (
                f"评论观点分析结果包\n"
                f"任务 ID：{task_id}\n"
                f"文件名：{task.get('filename', '-')}\n"
                f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "文件说明：\n"
                "  analysis_report.html   单文件 HTML 报告（含内嵌数据，可直接双击查看）\n"
                "  analysis_report.md     Markdown 报告\n"
                "  analysis_results.json  结构化结果（观点、人数、占比、理由、comment_ids）\n"
                "  cleaned_comments.json  清洗后的评论\n"
                "  source/                上传的原始评论文件\n"
            )
            archive.writestr(f"{task_id}/README.txt", readme)
        buffer.seek(0)
        return buffer

    # ------------------------------------------------------------------ 删除任务
    def delete_task(self, task_id: str) -> dict[str, Any]:
        """删除任务记录与其本地数据（上传文件、中间结果、结果、报告）。"""
        task = self.db.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if task["status"] in ("running", "pending"):
            raise RuntimeError("任务正在分析中，请等待完成后再删除")

        paths = task_paths(task_id)
        removed: list[str] = []
        for target in (
            paths.raw_comments,
            paths.working_dir,
            paths.results.parent,
            paths.report.parent,
        ):
            try:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                    removed.append(str(target))
                elif target.exists():
                    target.unlink(missing_ok=True)
                    removed.append(str(target))
            except OSError as exc:  # 文件被占用等情况：记录但不阻断删除任务记录
                LOG.warning("删除 %s 失败：%s", target, exc)

        self.db.delete_task(task_id)
        with self._lock:
            self._result_cache.pop(task_id, None)
            self._comment_index_cache.pop(task_id, None)
        LOG.info("任务已删除：%s（清理 %d 项）", task_id, len(removed))
        return {"task_id": task_id, "removed": removed}


_service: TaskService | None = None


def get_task_service() -> TaskService:
    global _service
    if _service is None:
        _service = TaskService()
    return _service
