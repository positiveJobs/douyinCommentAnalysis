"""SQLite 任务持久化。

每次操作使用独立连接（SQLite + WAL 模式），因此后台分析线程与 API 线程可以安全并发访问。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id         TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    topic           TEXT,
    status          TEXT NOT NULL,
    stage           TEXT,
    stage_label     TEXT,
    progress        REAL DEFAULT 0,
    message         TEXT,
    error           TEXT,
    error_stage     TEXT,
    current_step    INTEGER,
    total_steps     INTEGER,
    comment_count   INTEGER,
    viewpoint_count INTEGER,
    upload_path     TEXT,
    result_path     TEXT,
    report_path     TEXT,
    summary_json    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    elapsed_seconds REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);
"""

TASK_FIELDS = (
    "task_id",
    "filename",
    "topic",
    "status",
    "stage",
    "stage_label",
    "progress",
    "message",
    "error",
    "error_stage",
    "current_step",
    "total_steps",
    "comment_count",
    "viewpoint_count",
    "upload_path",
    "result_path",
    "report_path",
    "summary_json",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
    "elapsed_seconds",
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or config.DATABASE_FILE)

    # ------------------------------------------------------------------ 基础
    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def init(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    # ------------------------------------------------------------------ 写入
    def create_task(self, record: dict[str, Any]) -> dict[str, Any]:
        timestamp = _now()
        payload = {field: record.get(field) for field in TASK_FIELDS}
        payload["created_at"] = record.get("created_at") or timestamp
        payload["updated_at"] = timestamp
        columns = ", ".join(TASK_FIELDS)
        placeholders = ", ".join(f":{field}" for field in TASK_FIELDS)
        with self.connect() as connection:
            connection.execute(f"INSERT INTO tasks ({columns}) VALUES ({placeholders})", payload)
        return self.get_task(payload["task_id"])

    def update_task(self, task_id: str, **fields: Any) -> None:
        updates = {key: value for key, value in fields.items() if key in TASK_FIELDS}
        if not updates:
            return
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        updates["task_id"] = task_id
        with self.connect() as connection:
            connection.execute(f"UPDATE tasks SET {assignments} WHERE task_id = :task_id", updates)

    def mark_interrupted_tasks(self, message: str) -> int:
        """服务重启后把仍在 running 的任务标记为失败，避免状态卡死。"""
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status = 'failed', error = ?, error_stage = 'interrupted', "
                "message = ?, finished_at = ?, updated_at = ? WHERE status IN ('running', 'pending')",
                (message, message, _now(), _now()),
            )
            return cursor.rowcount or 0

    # ------------------------------------------------------------------ 读取
    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _filters(status: str | None, keyword: str | None) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if keyword:
            clauses.append("filename LIKE ?")
            params.append(f"%{keyword}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def list_tasks(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        status: str | None = None,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        where, params = self._filters(status, keyword)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM tasks {where} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_tasks(self, *, status: str | None = None, keyword: str | None = None) -> int:
        where, params = self._filters(status, keyword)
        with self.connect() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM tasks {where}", tuple(params)).fetchone()[0])

    def status_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS total FROM tasks GROUP BY status").fetchall()
        return {row["status"]: int(row["total"]) for row in rows}

    def delete_task(self, task_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))

    def pending_task_ids(self) -> Iterable[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT task_id FROM tasks WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
        return [row["task_id"] for row in rows]


_database: Database | None = None


def get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
        _database.init()
    return _database
