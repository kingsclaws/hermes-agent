"""Plugin-owned legal project persistence layered beside Hermes sessions."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from hermes_state import SessionDB


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    client TEXT DEFAULT '',
    goal TEXT DEFAULT '',
    path TEXT NOT NULL,
    cwd TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'INIT',
    notes TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    harness_version TEXT DEFAULT '',
    last_harness_migration_at REAL
);
CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);

CREATE TABLE IF NOT EXISTS project_init_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'INIT_READING',
    started_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL,
    summary TEXT DEFAULT '',
    config_json TEXT DEFAULT '{}',
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_project_init_runs_project
    ON project_init_runs(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS project_sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    run_id TEXT,
    path TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    ext TEXT DEFAULT '',
    file_type TEXT DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'supporting',
    reason TEXT DEFAULT '',
    must_read_before_init INTEGER DEFAULT 0,
    read_status TEXT NOT NULL DEFAULT 'pending',
    read_method TEXT DEFAULT '',
    size_bytes INTEGER DEFAULT 0,
    mtime REAL DEFAULT 0,
    sha1 TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(project_id, rel_path),
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (run_id) REFERENCES project_init_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_project_sources_project
    ON project_sources(project_id, priority, read_status);

CREATE TABLE IF NOT EXISTS project_source_digests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    run_id TEXT,
    read_method TEXT DEFAULT '',
    read_coverage TEXT NOT NULL DEFAULT 'partial',
    confidence TEXT NOT NULL DEFAULT 'medium',
    digest_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (source_id) REFERENCES project_sources(id),
    FOREIGN KEY (run_id) REFERENCES project_init_runs(id)
);

CREATE TABLE IF NOT EXISTS project_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    source_session_id TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_project_decisions_project
    ON project_decisions(project_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS project_constraints (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    constraint_text TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'required',
    status TEXT NOT NULL DEFAULT 'active',
    source_session_id TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_project_constraints_project
    ON project_constraints(project_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS project_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    label TEXT DEFAULT '',
    summary TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    source_session_id TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_project_snapshots_project
    ON project_snapshots(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS task_handoffs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    actor TEXT DEFAULT '',
    target TEXT DEFAULT '',
    note TEXT DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    source_session_id TEXT DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_task_handoffs_project
    ON task_handoffs(project_id, task_id, created_at DESC);
"""


class LegalProjectStore:
    """Legal project API without extending Hermes' core ``SessionDB`` class."""

    def __init__(self, db_path: Optional[Path] = None):
        self._sessions = SessionDB(db_path=db_path) if db_path else SessionDB()
        # Keep these aliases because coordinator lookup intentionally performs
        # one joined/locked query against the shared sessions database.
        self._conn = self._sessions._conn
        self._lock = self._sessions._lock
        self._execute_write = self._sessions._execute_write
        self._execute_write(lambda conn: conn.executescript(SCHEMA_SQL))

    def close(self) -> None:
        self._sessions.close()

    def __getattr__(self, name: str):
        return getattr(self._sessions, name)

    def create_project(
        self, name: str, path: str, client: str = "", goal: str = "", cwd: str = "",
    ) -> str:
        project_id = f"proj_{uuid.uuid4().hex[:10]}"
        now = time.time()

        def write(conn):
            conn.execute(
                """INSERT INTO projects
                   (id, name, client, goal, path, cwd, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'INIT', ?, ?)""",
                (project_id, name, client, goal, path, cwd, now, now),
            )

        self._execute_write(write)
        return project_id

    def get_project(self, id_or_name: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM projects WHERE id = ? OR name = ?",
                (id_or_name, id_or_name),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_project_by_path(self, path: str) -> Optional[dict]:
        resolved = str(os.path.realpath(os.path.normpath(path)))
        with self._lock:
            rows = self._conn.execute("SELECT * FROM projects").fetchall()
        for row in rows:
            candidate = row["path"] or row["cwd"] or ""
            if str(os.path.realpath(os.path.normpath(candidate))) == resolved:
                return dict(row)
        return None

    def list_projects(self, status: Optional[str] = None) -> list[dict]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM projects WHERE status = ? ORDER BY updated_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM projects ORDER BY updated_at DESC"
                ).fetchall()
        return [dict(row) for row in rows]

    def update_project(self, project_id: str, **fields) -> bool:
        allowed = {
            "name", "client", "goal", "path", "cwd", "status", "notes",
            "harness_version", "last_harness_migration_at",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return False
        updates["updated_at"] = time.time()
        clause = ", ".join(f"{key} = ?" for key in updates)
        values = [*updates.values(), project_id]

        def write(conn):
            cur = conn.execute(
                f"UPDATE projects SET {clause} WHERE id = ?", values,
            )
            return cur.rowcount > 0

        return bool(self._execute_write(write))

    def set_session_project(
        self, session_id: str, project_id: str, project_cwd: str = "",
    ) -> bool:
        def write(conn):
            if project_cwd:
                cur = conn.execute(
                    "UPDATE sessions SET project_id = ?, project_cwd = ? WHERE id = ?",
                    (project_id, project_cwd, session_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE sessions SET project_id = ? WHERE id = ?",
                    (project_id, session_id),
                )
            return cur.rowcount > 0

        return bool(self._execute_write(write))

    def create_project_init_run(
        self, project_id: str, *, status: str = "INIT_READING",
        config: Optional[dict] = None,
    ) -> str:
        run_id = f"init_{uuid.uuid4().hex[:12]}"
        now = time.time()
        self._execute_write(lambda conn: conn.execute(
            """INSERT INTO project_init_runs
               (id, project_id, status, started_at, updated_at, config_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, project_id, status, now, now,
             json.dumps(config or {}, ensure_ascii=False)),
        ))
        return run_id

    def update_project_init_run(self, run_id: str, **fields) -> bool:
        allowed = {"status", "summary", "completed_at", "config_json"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return False
        updates["updated_at"] = time.time()
        clause = ", ".join(f"{key} = ?" for key in updates)
        values = [*updates.values(), run_id]
        self._execute_write(lambda conn: conn.execute(
            f"UPDATE project_init_runs SET {clause} WHERE id = ?", values,
        ))
        return True

    def upsert_project_sources(
        self, project_id: str, run_id: str, sources: list[dict],
    ) -> int:
        now = time.time()

        def write(conn):
            for src in sources:
                conn.execute(
                    """INSERT INTO project_sources
                       (id, project_id, run_id, path, rel_path, file_name, ext,
                        file_type, priority, reason, must_read_before_init,
                        read_status, read_method, size_bytes, mtime, sha1,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(project_id, rel_path) DO UPDATE SET
                         run_id=excluded.run_id, path=excluded.path,
                         file_name=excluded.file_name, ext=excluded.ext,
                         file_type=excluded.file_type, priority=excluded.priority,
                         reason=excluded.reason,
                         must_read_before_init=excluded.must_read_before_init,
                         read_status=excluded.read_status,
                         read_method=excluded.read_method,
                         size_bytes=excluded.size_bytes, mtime=excluded.mtime,
                         sha1=excluded.sha1, updated_at=excluded.updated_at""",
                    (
                        src["id"], project_id, run_id, src["path"], src["rel_path"],
                        src.get("file_name", ""), src.get("ext", ""),
                        src.get("file_type", ""), src.get("priority", "supporting"),
                        src.get("reason", ""),
                        1 if src.get("must_read_before_init") else 0,
                        src.get("read_status", "pending"), src.get("read_method", ""),
                        int(src.get("size_bytes") or 0), float(src.get("mtime") or 0),
                        src.get("sha1", ""), now, now,
                    ),
                )

        self._execute_write(write)
        return len(sources)

    def list_project_sources(
        self, project_id: str, *, run_id: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> list[dict]:
        query = "SELECT * FROM project_sources WHERE project_id = ?"
        params: list[Any] = [project_id]
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if priority:
            query += " AND priority = ?"
            params.append(priority)
        query += " ORDER BY must_read_before_init DESC, priority ASC, rel_path ASC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_project_source(
        self, *, project_id: Optional[str] = None,
        source_id: Optional[str] = None, rel_path: Optional[str] = None,
        path: Optional[str] = None,
    ) -> Optional[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if source_id:
            clauses.append("id = ?")
            params.append(source_id)
        elif rel_path:
            clauses.append("rel_path = ?")
            params.append(rel_path)
        elif path:
            clauses.append("path = ?")
            params.append(path)
        else:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM project_sources WHERE " + " AND ".join(clauses)
                + " ORDER BY updated_at DESC LIMIT 1",
                params,
            ).fetchone()
        return dict(row) if row is not None else None

    def upsert_project_source_digest(
        self, *, project_id: str, source_id: str,
        run_id: Optional[str] = None, read_method: str = "",
        read_coverage: str = "partial", confidence: str = "medium",
        digest: Optional[dict] = None, created_by: str = "",
    ) -> str:
        key = f"{project_id}:{source_id}:{run_id or ''}"
        digest_id = f"sdig_{hashlib.sha1(key.encode()).hexdigest()[:16]}"
        now = time.time()
        status = "read_failed" if read_coverage == "failed" else "digested"

        def write(conn):
            conn.execute(
                """INSERT INTO project_source_digests
                   (id, project_id, source_id, run_id, read_method,
                    read_coverage, confidence, digest_json, created_by,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     read_method=excluded.read_method,
                     read_coverage=excluded.read_coverage,
                     confidence=excluded.confidence,
                     digest_json=excluded.digest_json,
                     created_by=excluded.created_by,
                     updated_at=excluded.updated_at""",
                (
                    digest_id, project_id, source_id, run_id, read_method,
                    read_coverage, confidence,
                    json.dumps(digest or {}, ensure_ascii=False, sort_keys=True),
                    created_by, now, now,
                ),
            )
            conn.execute(
                """UPDATE project_sources
                   SET read_status = ?,
                       read_method = COALESCE(NULLIF(?, ''), read_method),
                       updated_at = ? WHERE id = ?""",
                (status, read_method, now, source_id),
            )

        self._execute_write(write)
        return digest_id

    def record_decision(self, project_id: str, title: str, decision: str, **meta: Any) -> str:
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        now = time.time()
        self._execute_write(lambda conn: conn.execute(
            """INSERT INTO project_decisions
               (id, project_id, title, decision, rationale, status,
                source_session_id, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (decision_id, project_id, title, decision, meta.get("rationale", ""),
             meta.get("status", "active"), meta.get("source_session_id", ""),
             meta.get("created_by", ""), now, now),
        ))
        return decision_id

    def list_decisions(self, project_id: str, status: Optional[str] = None) -> list[dict]:
        query = "SELECT * FROM project_decisions WHERE project_id = ?"
        params: list[Any] = [project_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def record_constraint(self, project_id: str, title: str, constraint_text: str, **meta: Any) -> str:
        constraint_id = f"con_{uuid.uuid4().hex[:12]}"
        now = time.time()
        self._execute_write(lambda conn: conn.execute(
            """INSERT INTO project_constraints
               (id, project_id, title, constraint_text, severity, status,
                source_session_id, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (constraint_id, project_id, title, constraint_text,
             meta.get("severity", "required"), meta.get("status", "active"),
             meta.get("source_session_id", ""), meta.get("created_by", ""), now, now),
        ))
        return constraint_id

    def list_constraints(self, project_id: str, status: Optional[str] = None) -> list[dict]:
        query = "SELECT * FROM project_constraints WHERE project_id = ?"
        params: list[Any] = [project_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def create_snapshot(self, project_id: str, summary: str, **meta: Any) -> str:
        snapshot_id = f"snap_{uuid.uuid4().hex[:12]}"
        now = time.time()
        self._execute_write(lambda conn: conn.execute(
            """INSERT INTO project_snapshots
               (id, project_id, label, summary, state_json, source_session_id,
                created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_id, project_id, meta.get("label", ""), summary,
             json.dumps(meta.get("state") or {}, ensure_ascii=False, sort_keys=True),
             meta.get("source_session_id", ""), meta.get("created_by", ""), now),
        ))
        return snapshot_id

    def latest_snapshot(self, project_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM project_snapshots WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result["state"] = json.loads(result.pop("state_json") or "{}")
        except Exception:
            result["state"] = {}
        return result

    def record_task_handoff(self, project_id: str, task_id: str, payload: dict, **meta: Any) -> str:
        handoff_id = f"handoff_{uuid.uuid4().hex[:12]}"
        self._execute_write(lambda conn: conn.execute(
            """INSERT INTO task_handoffs
               (id, project_id, task_id, actor, target, note, payload_json,
                source_session_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (handoff_id, project_id, task_id, meta.get("actor", ""), meta.get("target", ""),
             str(payload.get("note") or ""), json.dumps(payload, ensure_ascii=False, sort_keys=True),
             meta.get("source_session_id", ""), time.time()),
        ))
        return handoff_id

    def build_context_pack(self, project_id: str, *, objective: str = "", limit: int = 12) -> dict:
        project = self.get_project(project_id) or {}
        return {
            "version": 1,
            "project": {key: project.get(key) for key in ("id", "name", "client", "goal", "path", "status")},
            "objective": objective,
            "latest_snapshot": self.latest_snapshot(project_id),
            "active_decisions": self.list_decisions(project_id, "active")[:limit],
            "active_constraints": self.list_constraints(project_id, "active")[:limit],
        }

    def context_health(self, project_id: str) -> dict:
        decisions = self.list_decisions(project_id, "active")
        constraints = self.list_constraints(project_id, "active")
        snapshot = self.latest_snapshot(project_id)
        return {
            "active_decisions": len(decisions),
            "active_constraints": len(constraints),
            "latest_snapshot": snapshot,
            "snapshot_age_seconds": max(0, time.time() - snapshot["created_at"]) if snapshot else None,
            "cross_session_ready": bool(snapshot or decisions or constraints),
            "session_context_provider": "lcm-compatible",
        }
