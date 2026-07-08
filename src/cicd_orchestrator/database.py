"""
SQLite-backed run history for the CI/CD Orchestrator.

Tables
------
runs        — one row per pipeline execution (metadata + summary)
run_steps   — one row per step result within a run
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# Default DB path — can be overridden by env var or explicit argument
DEFAULT_DB_PATH = Path.home() / ".cicd_orchestrator" / "history.db"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunDatabase:
    """Thread-safe wrapper around the SQLite history store."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── schema ──────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL DEFAULT 'anonymous',
                    project_path TEXT,
                    project_type TEXT,
                    status      TEXT NOT NULL DEFAULT 'running',
                    total_steps INTEGER DEFAULT 0,
                    passed      INTEGER DEFAULT 0,
                    failed      INTEGER DEFAULT 0,
                    started_at  TEXT NOT NULL,
                    finished_at TEXT,
                    duration_s  REAL
                );

                CREATE TABLE IF NOT EXISTS run_steps (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id      TEXT NOT NULL REFERENCES runs(id),
                    step_index  INTEGER NOT NULL,
                    step_name   TEXT,
                    command     TEXT,
                    stage       TEXT,
                    success     INTEGER,
                    attempts    INTEGER DEFAULT 1,
                    output      TEXT,
                    started_at  TEXT,
                    finished_at TEXT
                );
            """)

    # ── write helpers ────────────────────────────────────────────────────────

    def create_run(
        self,
        project_path: str,
        project_type: str,
        user_id: str = "anonymous",
    ) -> str:
        run_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO runs (id, user_id, project_path, project_type, started_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, user_id, project_path, project_type, _utcnow()),
            )
        return run_id

    def add_step_result(
        self,
        run_id: str,
        step_index: int,
        step_name: str,
        command: str,
        stage: str,
        success: bool,
        attempts: int,
        output: str,
    ):
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO run_steps
                       (run_id, step_index, step_name, command, stage,
                        success, attempts, output, started_at, finished_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, step_index, step_name, command, stage,
                    int(success), attempts, output[:4000], now, now,
                ),
            )

    def finish_run(
        self,
        run_id: str,
        passed: int,
        failed: int,
        total: int,
        status: str = "success",
    ):
        now = _utcnow()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT started_at FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            duration: Optional[float] = None
            if row:
                try:
                    start = datetime.fromisoformat(row["started_at"])
                    end = datetime.fromisoformat(now)
                    duration = (end - start).total_seconds()
                except Exception:
                    pass

            conn.execute(
                """UPDATE runs
                   SET status=?, total_steps=?, passed=?, failed=?,
                       finished_at=?, duration_s=?
                   WHERE id=?""",
                (status, total, passed, failed, now, duration, run_id),
            )

    # ── read helpers ─────────────────────────────────────────────────────────

    def list_runs(
        self,
        user_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if user_id:
                rows = conn.execute(
                    "SELECT * FROM runs WHERE user_id=? ORDER BY started_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            steps = conn.execute(
                "SELECT * FROM run_steps WHERE run_id=? ORDER BY step_index",
                (run_id,),
            ).fetchall()
        result = dict(row)
        result["steps"] = [dict(s) for s in steps]
        return result

    def delete_run(self, run_id: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM run_steps WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM runs WHERE id=?", (run_id,))


# ── module-level singleton ────────────────────────────────────────────────────

import os

_db: Optional[RunDatabase] = None


def get_db() -> RunDatabase:
    global _db
    if _db is None:
        db_path = os.environ.get("CICD_DB_PATH")
        _db = RunDatabase(Path(db_path) if db_path else None)
    return _db
