#!/usr/bin/env python3
"""
Kanban Toolset — native legal-swarm coordination via SQLite-backed kanban.

Two tool groups:
  Coordinator (full 🛠️): board_create, board_info, task_create, task_assign,
                          task_wait, workflow_compile, board_status
  Worker (limited):       task_claim, task_read, task_handoff, task_approve,
                          task_reject, task_revise

Multi-layer gate protocol:
  Drafter → handoff → Reviewer → approve → (next gate or done)
  Reviewer → reject  → back to Drafter → revise → handoff → Reviewer

Independent of hermes_cli modules. Board DB lives at <project>/kanban/kanban.db.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .legal_evidence_gate import (
    read_before_conclude_errors as _read_before_conclude_errors,
)

# ── Constants ──────────────────────────────────────────────────────────────────

VALID_TASK_STATUSES = {"todo", "in_progress", "in_review", "approved", "rejected", "done"}
VALID_GATE_TYPES = {"review", "approve", "verify"}
DEFAULT_CLAIM_TTL_SECONDS = 15 * 60  # 15 min, same as existing kanban

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS board (
    id           TEXT PRIMARY KEY,
    project_path TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS columns (
    id        TEXT PRIMARY KEY,
    board_id  TEXT NOT NULL,
    name      TEXT NOT NULL,
    position  INTEGER NOT NULL DEFAULT 0,
    wip_limit INTEGER DEFAULT 0,
    FOREIGN KEY (board_id) REFERENCES board(id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id                    TEXT PRIMARY KEY,
    board_id              TEXT NOT NULL,
    column_id             TEXT,
    title                 TEXT NOT NULL,
    description           TEXT DEFAULT '',
    assignee              TEXT,
    status                TEXT NOT NULL DEFAULT 'todo',
    priority              INTEGER DEFAULT 0,
    gates_json            TEXT DEFAULT '[]',
    gate_index            INTEGER DEFAULT 0,
    handoff_history_json  TEXT DEFAULT '[]',
    claim_lock            TEXT,
    claim_expires         INTEGER,
    created_by            TEXT,
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL,
    completed_at          INTEGER,
    session_id            TEXT,
    FOREIGN KEY (board_id) REFERENCES board(id),
    FOREIGN KEY (column_id) REFERENCES columns(id)
);

CREATE TABLE IF NOT EXISTS task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    actor      TEXT,
    payload    TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS task_links (
    parent_id  TEXT NOT NULL,
    child_id   TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id),
    FOREIGN KEY (parent_id) REFERENCES tasks(id),
    FOREIGN KEY (child_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_board_status ON tasks(board_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee      ON tasks(assignee, status);
CREATE INDEX IF NOT EXISTS idx_events_task         ON task_events(task_id, created_at);
"""

DEFAULT_COLUMNS = [
    ("col_todo", "To Do", 0, 0),
    ("col_in_progress", "In Progress", 1, 0),
    ("col_review", "In Review", 2, 0),
    ("col_done", "Done", 3, 0),
]

LEGAL_FILE_READING_PROTOCOL = """

## Mandatory Legal File-Reading Protocol
This is a legal evidence/review task. Filename matching is not evidence.

Before reaching any conclusion about whether a checklist item is answered,
the Worker must:
1. Enumerate every file in the relevant response/material directory, including
   files extracted from archives and nested folders.
2. Open and read each file's actual content with an appropriate tool:
   - DOCX: lex_read
   - PDF/scanned PDF/image: lex_ocr or the configured OCR fallback
   - XLS/XLSX/CSV: terminal/python spreadsheet inspection
   - TXT/MD/HTML: read_file or terminal
3. Produce a File Evidence Ledger in the completion summary/metadata covering:
   file path, tool used, content actually observed, related checklist/Q number,
   and conclusion.
4. Mark a file as unread only after recording the attempted tool and the
   concrete failure reason.

Forbidden: concluding from file name, file path, extension, or keyword matching
alone. If a file name suggests one thing but content may answer another legal
question, read the content first.

Completion is invalid unless the final report includes a File Evidence Ledger
or equivalent evidence coverage table. Any conclusion that materials are
"missing" must be tagged as one of:
- verified_not_provided / 已核实未提供
- unverified / 未核实
- provided_but_incomplete / 已提供但不完整
"""


def _lex_project_context_pack(project_path: str, objective: str) -> dict[str, Any] | None:
    try:
        from .project_store import LegalProjectStore
        store = LegalProjectStore()
        try:
            project = store.get_project_by_path(project_path)
            return store.build_context_pack(project["id"], objective=objective) if project else None
        finally:
            store.close()
    except Exception:
        return None


def _structured_handoff(args: dict[str, Any], note: str) -> dict[str, Any]:
    def values(name: str) -> list[str]:
        raw = args.get(name, [])
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = [item.strip() for item in raw.split("\n") if item.strip()]
        return [str(item).strip() for item in (raw or []) if str(item).strip()]
    return {"version": 1, "note": note, **{name: values(name) for name in (
        "decisions", "constraints", "evidence_refs", "files_modified",
        "unresolved_items", "required_next_actions",
    )}}


def _persist_structured_handoff(project_path: str, task_id: str, payload: dict[str, Any], *, actor: str = "", target: str = "") -> None:
    try:
        from .project_store import LegalProjectStore
        store = LegalProjectStore()
        try:
            project = store.get_project_by_path(project_path)
            if project:
                store.record_task_handoff(
                    project["id"], task_id, payload, actor=actor, target=target,
                    source_session_id=os.environ.get("HERMES_SESSION_ID", ""),
                )
                session_id = os.environ.get("HERMES_SESSION_ID", "")
                for item in payload.get("decisions", []):
                    store.record_decision(
                        project["id"], item[:120], item, created_by=actor,
                        source_session_id=session_id,
                    )
                for item in payload.get("constraints", []):
                    store.record_constraint(
                        project["id"], item[:120], item, created_by=actor,
                        source_session_id=session_id,
                    )
                if payload.get("note"):
                    store.create_snapshot(
                        project["id"], payload["note"], label=f"handoff:{task_id}",
                        state={
                            "task_id": task_id,
                            "unresolved_items": payload.get("unresolved_items", []),
                            "required_next_actions": payload.get("required_next_actions", []),
                        },
                        created_by=actor, source_session_id=session_id,
                    )
        finally:
            store.close()
    except Exception:
        pass

# ── DB helpers ─────────────────────────────────────────────────────────────────

def _resolve_coordinator_session_for_project(project_path: str) -> str | None:
    """Find the coordinator session for a project via coordinator_for column.

    Returns the session_id of the active coordinator, or None if not found.
    This ensures kanban task notifications go to the project coordinator,
    not to whoever created the task (which might be lex-master).
    """
    try:
        from .project_management_tool import (
            _ensure_coordinator_column,
            _project_session_db,
            _resolve_project,
        )
        # Project CRUD belongs to LegalProjectStore. A raw SessionDB exposes
        # sessions only and does not implement get_project/list_projects.
        db = _project_session_db()
        _ensure_coordinator_column(db)
        # Find project by path
        projects = db.list_projects()
        project = None
        for p in projects:
            p_path = str(p.get("path") or p.get("cwd") or "")
            if p_path and (p_path == project_path or project_path.startswith(p_path)):
                project = p
                break
        if not project:
            return None
        # Look up coordinator_for
        conn = db._conn if hasattr(db, "_conn") else None
        if conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE coordinator_for = ? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
                (project["id"],),
            ).fetchone()
            if row:
                return row[0]
    except Exception:
        pass
    return None


def _now() -> int:
    return int(time.time())


def _subscribe_session(kb, session_id: str, task_id: str, *, board: str) -> None:
    """Subscribe a local CLI/TUI session using the existing notify schema."""
    conn = kb.connect(board=board)
    try:
        kb.add_notify_sub(
            conn,
            task_id=task_id,
            platform="local",
            chat_id=session_id,
        )
    finally:
        conn.close()


def _uid(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_hex(6)}"


def _connect(project_path: str, title: str = "") -> tuple[sqlite3.Connection, str]:
    """Open (or create) the kanban DB for a project. Returns (conn, board_id)."""
    kanban_dir = Path(project_path) / "kanban"
    kanban_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(kanban_dir / "kanban.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    board_id = _ensure_board(conn, str(Path(project_path).resolve()), title or project_path)
    return conn, board_id


def _ensure_board(conn: sqlite3.Connection, project_path: str, title_hint: str) -> str:
    """Return existing board ID or create a new board for this project."""
    row = conn.execute(
        "SELECT id FROM board WHERE project_path = ?", (project_path,)
    ).fetchone()
    if row:
        return row["id"]

    board_id = _uid("brd_")
    title = Path(title_hint).name if title_hint else "Project Board"
    now = _now()
    conn.execute(
        "INSERT INTO board (id, project_path, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (board_id, project_path, title, now, now),
    )
    for col_id, col_name, pos, wip in DEFAULT_COLUMNS:
        conn.execute(
            "INSERT INTO columns (id, board_id, name, position, wip_limit) VALUES (?, ?, ?, ?, ?)",
            (col_id, board_id, col_name, pos, wip),
        )
    conn.commit()
    return board_id


def _board_for_project(project_path: str) -> dict | None:
    """Read-only lookup: return board dict or None."""
    kanban_db = Path(project_path) / "kanban" / "kanban.db"
    if not kanban_db.exists():
        return None
    conn = sqlite3.connect(str(kanban_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM board WHERE project_path = ?", (str(Path(project_path).resolve()),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── CAS claim helpers ──────────────────────────────────────────────────────────

def _cas_claim(conn: sqlite3.Connection, task_id: str, assignee: str,
               ttl: int = DEFAULT_CLAIM_TTL_SECONDS) -> str | None:
    """Atomically claim a task. Returns claim_token or None if already claimed."""
    token = secrets.token_hex(16)
    now = _now()
    expires = now + ttl
    cur = conn.execute(
        """UPDATE tasks SET claim_lock = ?, claim_expires = ?, assignee = ?,
           status = CASE WHEN status = 'todo' THEN 'in_progress' ELSE status END,
           updated_at = ?
           WHERE id = ? AND (claim_expires IS NULL OR claim_expires < ?)""",
        (token, expires, assignee, now, task_id, now),
    )
    if cur.rowcount == 0:
        return None
    conn.commit()
    return token


def _cas_handoff(conn: sqlite3.Connection, task_id: str, claim_token: str) -> bool:
    """Verify claim token still holds. Returns True if CAS check passes."""
    row = conn.execute(
        "SELECT claim_lock FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row and row["claim_lock"] == claim_token:
        return True
    return False


# ── Task helpers ───────────────────────────────────────────────────────────────

def _advance_gate(conn: sqlite3.Connection, task_id: str) -> dict | None:
    """Move to next gate. Returns next gate dict or None (no more gates = done)."""
    row = conn.execute("SELECT gates_json, gate_index FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None
    gates = json.loads(row["gates_json"])
    next_idx = row["gate_index"] + 1
    if next_idx >= len(gates):
        return None  # all gates passed
    next_gate = gates[next_idx]
    conn.execute(
        "UPDATE tasks SET gate_index = ?, status = 'in_review', updated_at = ? WHERE id = ?",
        (next_idx, _now(), task_id),
    )
    conn.commit()
    return next_gate


def _log_event(conn: sqlite3.Connection, task_id: str, kind: str,
               actor: str = "", payload: dict | None = None):
    conn.execute(
        "INSERT INTO task_events (task_id, kind, actor, payload, created_at) VALUES (?, ?, ?, ?, ?)",
        (task_id, kind, actor, json.dumps(payload or {}, ensure_ascii=False), _now()),
    )
    conn.commit()


def _task_to_dict(row: sqlite3.Row) -> dict:
    """Convert a task Row to a JSON-safe dict with parsed gates/history."""
    t = dict(row)
    t["gates"] = json.loads(t.pop("gates_json", "[]"))
    t["handoff_history"] = json.loads(t.pop("handoff_history_json", "[]"))
    t.pop("claim_lock", None)
    return t


# ── Resolve project context ────────────────────────────────────────────────────

def _resolve_project_path(args: dict, parent_agent=None) -> str | None:
    """Resolve project path from args or active project context."""
    project_path = args.get("project_path", "").strip()
    if project_path:
        return str(Path(project_path).resolve())

    # Try resolve_selected_project from project_management_tool
    try:
        from .project_management_tool import resolve_selected_project
        selected = resolve_selected_project(
            parent_agent,
            session_id=getattr(parent_agent, "session_id", None),
        )
        if selected and selected.get("path"):
            return str(Path(selected["path"]).resolve())
    except Exception:
        pass

    return None


def _official_board_slug(project_path: str) -> str:
    """Stable Hermes Kanban board slug for a legal project path."""
    import hashlib
    import re

    name = Path(project_path).name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    digest = hashlib.sha1(str(Path(project_path).resolve()).encode("utf-8")).hexdigest()[:8]
    if slug:
        slug = slug[:48].strip("-")
        return f"legal-{slug}-{digest}"
    return f"legal-project-{digest}"


def _official_board(project_path: str, title: str = "") -> dict:
    """Create/read the canonical Hermes Kanban board for a project."""
    from hermes_cli import kanban_db as kb

    slug = _official_board_slug(project_path)
    return kb.create_board(
        slug,
        name=title or Path(project_path).name,
        description=f"Legal swarm board for {project_path}",
        default_workdir=str(Path(project_path).resolve()),
    )


def _official_task_to_dict(task) -> dict:
    project_path = task.workspace_path
    return {
        "id": task.id,
        "task_id": task.id,
        "board_slug": getattr(task, "board", None),
        "title": task.title,
        "description": task.body or "",
        "assignee": task.assignee,
        "status": task.status,
        "priority": task.priority,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "workspace_kind": task.workspace_kind,
        "workspace_path": task.workspace_path,
        "project_path": project_path,
        "session_id": task.session_id,
        "result": task.result,
        "source": "hermes_kanban",
    }


def _official_board_arg(args: dict) -> str | None:
    board = str(args.get("board") or "").strip()
    return board or None


def _should_try_official_board(args: dict) -> bool:
    """Return True when a tool call likely targets Hermes' official kanban DB."""
    return bool(
        _official_board_arg(args)
        or os.environ.get("HERMES_KANBAN_BOARD")
        or os.environ.get("HERMES_KANBAN_DB")
        or os.environ.get("HERMES_KANBAN_TASK")
    )


def _connect_official_for_task(
    task_id: str,
    args: dict,
    parent_agent=None,
):
    """Find an official Hermes kanban task.

    The swarm toolset predates Hermes' multi-board kanban DB and still has a
    project-local DB fallback.  Dispatcher-spawned workers, however, are pinned
    to the official DB via HERMES_KANBAN_* env vars.  Try that path first so
    worker tools mutate the same board the dispatcher claimed from.
    """
    from hermes_cli import kanban_db as kb

    candidates: list[str | None] = []
    explicit_board = _official_board_arg(args)
    if explicit_board:
        candidates.append(explicit_board)

    env_board = os.environ.get("HERMES_KANBAN_BOARD", "").strip()
    if env_board and env_board not in candidates:
        candidates.append(env_board)

    project_path = _resolve_project_path(args, parent_agent)
    if project_path:
        slug = _official_board_slug(project_path)
        if slug not in candidates:
            candidates.append(slug)

    if os.environ.get("HERMES_KANBAN_DB") and None not in candidates:
        candidates.append(None)

    for board in candidates:
        conn = kb.connect(board=board)
        task = kb.get_task(conn, task_id)
        if task is not None:
            return kb, conn, task, board
        conn.close()

    if _should_try_official_board(args) or not project_path:
        for meta in kb.list_boards(include_archived=False):
            board = meta.get("slug")
            if board in candidates:
                continue
            conn = kb.connect(board=board)
            task = kb.get_task(conn, task_id)
            if task is not None:
                return kb, conn, task, board
            conn.close()

    return None


def _official_claim_ok(task, claim_token: str) -> bool:
    return bool(claim_token and task.claim_lock and str(task.claim_lock) == claim_token)


def _official_result(success: bool, **fields: Any) -> str:
    return json.dumps({"success": success, **fields}, ensure_ascii=False)


def _parse_gates(gates_raw: Any) -> list[dict]:
    gates = []
    if gates_raw:
        if isinstance(gates_raw, str):
            gates = json.loads(gates_raw)
        else:
            gates = gates_raw
    if not isinstance(gates, list):
        raise ValueError("gates 必须是 JSON 数组。")
    for g in gates:
        if not isinstance(g, dict):
            raise ValueError("gates 的每一项必须是对象。")
        if g.get("type") not in VALID_GATE_TYPES:
            raise ValueError(
                f"无效的 gate 类型: {g.get('type')}。有效值: {', '.join(sorted(VALID_GATE_TYPES))}"
            )
    return gates


def _map_legacy_status_filter(status: str | None) -> str | None:
    if not status:
        return None
    mapping = {
        "todo": "todo",
        "in_progress": "running",
        "in_review": "review",
        "approved": "done",
        "rejected": "blocked",
        "done": "done",
        "blocked": "blocked",
        "ready": "ready",
        "running": "running",
        "review": "review",
    }
    return mapping.get(status, status)


def _latest_official_progress(conn, kb, task_id: str) -> dict | None:
    events = kb.list_events(conn, task_id)
    for event in reversed(events):
        if event.kind in {"heartbeat", "comment", "completed", "blocked", "claimed"}:
            return {
                "kind": event.kind,
                "at": event.created_at,
                "payload": event.payload,
            }
    return None


def _dispatcher_status() -> dict[str, Any]:
    """Return coordinator-visible dispatcher health for swarm tools."""
    try:
        from hermes_cli.kanban import _check_dispatcher_presence

        active, message = _check_dispatcher_presence()
    except Exception as exc:
        return {
            "active": None,
            "source": "unknown",
            "message": f"无法检测 dispatcher 状态: {exc}",
            "warning": None,
        }

    warning = None
    if not active:
        warning = (
            "Kanban dispatcher 当前不可用，assigned ready 任务不会自动启动。"
            "如果你在 CLI/TUI 直接会话中工作，可以调用 swarm_dispatch_now 跑一次手动 dispatch；"
            "不要启动 assignee profile gateway。"
        )
    return {
        "active": bool(active),
        "source": "gateway",
        "message": message,
        "warning": warning,
    }


def _assignee_status(assignee: str | None) -> dict[str, Any] | None:
    if not assignee:
        return None
    try:
        from hermes_cli.profiles import profile_exists

        exists = bool(profile_exists(assignee))
    except Exception as exc:
        return {
            "assignee": assignee,
            "exists": None,
            "warning": f"无法检查 assignee profile 是否存在: {exc}",
        }
    return {
        "assignee": assignee,
        "exists": exists,
        "warning": None if exists else f"assignee profile 不存在: {assignee}。任务无法被 dispatcher spawn。",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COORDINATOR TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Schemas ────────────────────────────────────────────────────────────────────

KANBAN_BOARD_CREATE_SCHEMA = {
    "name": "swarm_board_create",
    "description": (
        "为项目创建/读取官方 Hermes Kanban Board。"
        "Board 由 Hermes Kanban DB 管理，dispatcher 根据 board slug 扫描并 spawn worker。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": "项目根目录的绝对路径。如果省略，使用当前选中的项目。",
            },
            "title": {
                "type": "string",
                "description": "Board 标题，默认使用项目目录名。",
            },
        },
        "required": [],
    },
}

KANBAN_BOARD_INFO_SCHEMA = {
    "name": "swarm_board_info",
    "description": (
        "获取项目的 Kanban Board 信息，包含列定义和各列任务计数。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": "项目根目录的绝对路径。如果省略，使用当前选中的项目。",
            },
        },
        "required": [],
    },
}

KANBAN_TASK_CREATE_SCHEMA = {
    "name": "swarm_task_create",
    "description": (
        "在 Board 上创建一个新任务。Coordinator 可以指定 gates（门禁链）来定义多步审核流程。"
        "例如：先由 reviewer-content 审核，通过后由 reviewer-format 审核。"
        "\n\n"
        "Gates 格式（JSON 数组）：\n"
        '[{"type": "review", "target_pool": "hpswarm-reviewer-content"},'
        ' {"type": "approve", "target_pool": "hpswarm-reviewer-format"}]'
        "\n\n"
        "不指定 gates/assignee 时，任务进入 todo/待分配状态。"
        "指定 gates 时，第一个 gate 的 target_pool 即为初始 assignee；"
        "assigned ready 任务由 coordinator/default gateway 的 Kanban dispatcher spawn worker。"
        "不要启动 assignee profile gateway。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": "项目根目录的绝对路径。如果省略，使用当前选中的项目。",
            },
            "title": {
                "type": "string",
                "description": "任务标题（必填）。例如：'起草第三条担保条款'",
            },
            "description": {
                "type": "string",
                "description": "任务详细描述、要求、参考文件等。",
            },
            "assignee": {
                "type": "string",
                "description": "初始执行者的 profile 名称，如 'hpswarm-drafter'。省略则放入待认领池。",
            },
            "gates": {
                "type": "string",
                "description": (
                    "门禁链 JSON 数组。每项含 type(review/approve/verify) 和 "
                    "target_pool。例如：'[{\"type\":\"review\",\"target_pool\":\"hpswarm-reviewer-content\"}]'"
                ),
            },
            "priority": {
                "type": "integer",
                "description": "优先级（0=普通, 1=高, 2=紧急）。默认 0。",
            },
            "column": {
                "type": "string",
                "description": "目标列的 ID。省略则放入第一个列（To Do）。",
            },
        },
        "required": ["title"],
    },
}

KANBAN_TASK_ASSIGN_SCHEMA = {
    "name": "swarm_task_assign",
    "description": (
        "将任务分配给指定 Worker。Coordinator 可以重新分配未认领或已释放的任务。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "任务 ID。",
            },
            "assignee": {
                "type": "string",
                "description": "目标 Worker 的 profile 名称。",
            },
        },
        "required": ["task_id", "assignee"],
    },
}

KANBAN_TASK_WAIT_SCHEMA = {
    "name": "swarm_task_wait",
    "description": (
        "[DEPRECATED] 等待任务完成（阻塞式轮询）。Coordinator 创建任务后调用此工具等待结果。"
        "超时后返回当前状态但不报错。"
        "\n\n"
        "建议使用 swarm_task_poll（非阻塞状态查询）+ swarm_task_collect（收集产出）代替。"
        "poll + collect 模式避免阻塞，让 Coordinator 有更多并发能力。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要等待的任务 ID。",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "最大等待秒数（默认 300，即 5 分钟）。",
            },
            "poll_interval": {
                "type": "integer",
                "description": "轮询间隔秒数（默认 5）。",
            },
        },
        "required": ["task_id"],
    },
}

KANBAN_WORKFLOW_COMPILE_SCHEMA = {
    "name": "swarm_workflow_compile",
    "description": (
        "从 YAML 工作流文件编译生成 Kanban 任务。YAML 中的每个节点变成一个 Task，"
        "节点间的依赖关系通过 task_links 表示。支持 analysis / worker / fanout / gate 节点类型。"
        "\n\n"
        "返回所有创建的任务列表。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "yaml_path": {
                "type": "string",
                "description": "YAML 工作流文件的绝对路径。",
            },
            "project_path": {
                "type": "string",
                "description": "项目根目录的绝对路径。如果省略，使用当前选中的项目。",
            },
        },
        "required": ["yaml_path"],
    },
}

KANBAN_BOARD_STATUS_SCHEMA = {
    "name": "swarm_board_status",
    "description": (
        "获取 Board 的完整状态概览：所有列及其下的所有任务（按优先级和时间排序）。"
        "Coordinator 用此工具了解全局进度。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": "项目根目录的绝对路径。如果省略，使用当前选中的项目。",
            },
            "status_filter": {
                "type": "string",
                "description": "可选：只显示指定状态的任务（todo/in_progress/in_review/approved/rejected/done）。",
            },
            "assignee_filter": {
                "type": "string",
                "description": "可选：只显示指定 Worker 的任务。",
            },
        },
        "required": [],
    },
}

KANBAN_TASK_POLL_SCHEMA = {
    "name": "swarm_task_poll",
    "description": (
        "非阻塞查询多个任务的状态。立即返回每个任务的当前状态、"
        "assignee、完成时间等。不会等待——Coordinator 用它了解进度，"
        "然后决定是否需要等待或收集结果。"
        "\n\n"
        "相比 swarm_task_wait（阻塞式轮询），此工具为非阻塞立即返回，"
        "适合批量检查多个任务的状态。建议新 workflow 使用 poll + collect 替代 wait。"
        "\n\n"
        "每个任务还会返回 latest_progress —— Worker 通过 swarm_task_progress 发布的"
        "最近一条进度（做了什么 / 当前状态）。据此可在 Drafter 仍在执行时看到实时进展，"
        "无需等到 handoff。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_ids": {
                "type": "string",
                "description": "逗号分隔的任务 ID 列表，如 'tsk_abc,tsk_def'",
            },
            "project_path": {
                "type": "string",
                "description": "项目路径。省略则使用当前项目。",
            },
        },
        "required": ["task_ids"],
    },
}

KANBAN_TASK_COLLECT_SCHEMA = {
    "name": "swarm_task_collect",
    "description": (
        "收集已完成任务的 Worker 产出。从任务的 event log、blackboard 评论、"
        "和 handoff_history 中提取 Worker 的工作成果。"
        "\n\n"
        "只对终态任务有效（done/approved/rejected）。"
        "进行中的任务返回其当前 event 摘要。"
        "\n\n"
        "Coordinator 使用此工具在任务完成后收集 Worker 的具体产出（修改了哪些文件、"
        "做出了哪些决策、需要注意的事项）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要收集的任务 ID。",
            },
            "include_events": {
                "type": "boolean",
                "description": "是否包含完整 event log（默认 false，仅摘要）。",
            },
            "project_path": {
                "type": "string",
                "description": "项目路径。省略则使用当前项目。",
            },
        },
        "required": ["task_id"],
    },
}

KANBAN_DISPATCH_NOW_SCHEMA = {
    "name": "swarm_dispatch_now",
    "description": (
        "手动运行一次当前项目 Kanban board 的 dispatcher。"
        "用于 CLI/TUI 直接会话且 gateway dispatcher 不在线时的 fallback。"
        "这不是启动 assignee profile gateway；它直接复用 Hermes Kanban dispatch_once 路径 spawn worker。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": "项目路径。省略则使用当前项目。",
            },
            "dry_run": {
                "type": "boolean",
                "description": "只报告会 spawn 哪些任务，不实际启动 worker。",
            },
            "max_spawn": {
                "type": "integer",
                "description": "可选：本次 dispatch 的并发上限。",
            },
        },
        "required": [],
    },
}

# ── Handlers ───────────────────────────────────────────────────────────────────

def kanban_board_create_handler(args: dict, **kwargs) -> str:
    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))
    if not project_path:
        return json.dumps({"success": False, "error": "无法确定项目路径。请指定 project_path 或先使用 project_select 选择项目。"})

    title = args.get("title", "").strip() or Path(project_path).name
    try:
        meta = _official_board(project_path, title=title)
        from hermes_cli import kanban_db as kb

        conn = kb.connect(board=meta["slug"])
        try:
            counts = {}
            for status in kb.VALID_STATUSES:
                counts[status] = len(kb.list_tasks(conn, status=status))
        finally:
            conn.close()
        return json.dumps({
            "success": True,
            "board": {
                "id": meta["slug"],
                "slug": meta["slug"],
                "project_path": str(Path(project_path).resolve()),
                "title": meta.get("name") or title,
                "db_path": meta.get("db_path"),
                "source": "hermes_kanban",
            },
            "dispatch_status": _dispatcher_status(),
            "columns": [
                {"id": "todo", "name": "To Do"},
                {"id": "ready", "name": "Ready"},
                {"id": "running", "name": "Running"},
                {"id": "review", "name": "Review"},
                {"id": "blocked", "name": "Blocked"},
                {"id": "done", "name": "Done"},
            ],
            "counts": counts,
            "message": (
                f"Board '{meta.get('name') or title}' 已就绪（Hermes Kanban: {meta['slug']}）。"
                " 任务由当前 coordinator/default gateway 内置的 Kanban dispatcher 扫描并 spawn worker 子进程。"
                " 不要启动 assignee profile 的 gateway；请用 swarm_task_poll 或 /swarm follow 监控。"
            ),
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"创建 Hermes Kanban board 失败: {exc}"}, ensure_ascii=False)

    conn, board_id = _connect(project_path, title=title)

    # Update title if this was an existing board (ensure title stays current)
    conn.execute("UPDATE board SET title = ?, updated_at = ? WHERE id = ? AND title != ?",
                 (title, _now(), board_id, title))
    conn.commit()

    row = conn.execute("SELECT * FROM board WHERE id = ?", (board_id,)).fetchone()
    columns = [dict(c) for c in conn.execute(
        "SELECT * FROM columns WHERE board_id = ? ORDER BY position", (board_id,)
    ).fetchall()]
    conn.close()

    return json.dumps({
        "success": True,
        "board": {
            "id": row["id"],
            "project_path": row["project_path"],
            "title": row["title"],
            "created_at": datetime.fromtimestamp(row["created_at"], tz=timezone.utc).isoformat(),
        },
        "columns": columns,
        "message": f"Board '{row['title']}' 已就绪。共 {len(columns)} 列。" if title else f"Board 已存在：'{row['title']}'",
    }, ensure_ascii=False)


def kanban_board_info_handler(args: dict, **kwargs) -> str:
    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))
    if not project_path:
        return json.dumps({"success": False, "error": "无法确定项目路径。"})

    try:
        meta = _official_board(project_path, title=Path(project_path).name)
        from hermes_cli import kanban_db as kb

        conn = kb.connect(board=meta["slug"])
        try:
            columns = []
            for status in ["triage", "todo", "ready", "running", "review", "blocked", "done"]:
                columns.append({
                    "id": status,
                    "name": status,
                    "task_count": len(kb.list_tasks(conn, status=status)),
                })
            total_tasks = len(kb.list_tasks(conn))
        finally:
            conn.close()
        return json.dumps({
            "success": True,
            "board": {
                "id": meta["slug"],
                "slug": meta["slug"],
                "project_path": str(Path(project_path).resolve()),
                "title": meta.get("name"),
                "db_path": meta.get("db_path"),
                "source": "hermes_kanban",
            },
            "columns": columns,
            "total_tasks": total_tasks,
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"读取 Hermes Kanban board 失败: {exc}"}, ensure_ascii=False)

    conn, board_id = _connect(project_path)
    row = conn.execute("SELECT * FROM board WHERE id = ?", (board_id,)).fetchone()
    if not row:
        conn.close()
        return json.dumps({"success": False, "error": "Board 不存在，请先创建。"})

    columns = []
    for c in conn.execute(
        "SELECT * FROM columns WHERE board_id = ? ORDER BY position", (board_id,)
    ).fetchall():
        cd = dict(c)
        count_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE column_id = ? AND status != 'done'", (c["id"],)
        ).fetchone()
        cd["task_count"] = count_row["cnt"] if count_row else 0
        columns.append(cd)

    total_tasks = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE board_id = ?", (board_id,)
    ).fetchone()["cnt"]

    conn.close()
    return json.dumps({
        "success": True,
        "board": dict(row),
        "columns": columns,
        "total_tasks": total_tasks,
    }, ensure_ascii=False)


def kanban_task_create_handler(args: dict, **kwargs) -> str:
    title = args.get("title", "").strip()
    if not title:
        return json.dumps({"success": False, "error": "任务标题为必填项。"})

    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))
    if not project_path:
        return json.dumps({"success": False, "error": "无法确定项目路径。"})

    try:
        from hermes_cli import kanban_db as kb

        meta = _official_board(project_path, title=Path(project_path).name)
        gates = _parse_gates(args.get("gates", ""))
        assignee = args.get("assignee", "").strip() or None
        if not assignee and gates:
            assignee = (gates[0].get("target_pool") or "").strip() or None

        priority = int(args.get("priority", 0) or 0)
        description = args.get("description", "").strip()
        description_with_protocol = (description + LEGAL_FILE_READING_PROTOCOL).strip()
        context_pack = _lex_project_context_pack(project_path, title)
        context_text = ""
        if context_pack:
            context_text = (
                "\n\n## Durable project context\n<LEX_CONTEXT_PACK_JSON>\n"
                + json.dumps(context_pack, ensure_ascii=False, indent=2)
                + "\n</LEX_CONTEXT_PACK_JSON>\n"
                "Treat this pack as cross-session ground truth. Optional LCM/session recall may add detail, "
                "but must not override durable project constraints."
            )
        gates_text = ""
        if gates:
            gates_text = (
                "\n\n## Review / gate hints\n"
                "<LEX_REVIEW_GATES_JSON>\n"
                + json.dumps(gates, ensure_ascii=False, indent=2)
                + "\n</LEX_REVIEW_GATES_JSON>\n\n"
                + "These gates are hard workflow gates. `kanban_complete` will route the task to review before final done."
            )
        body = (
            description_with_protocol
            + context_text
            + gates_text
            + f"\n\n## Project path\n{Path(project_path).resolve()}\n"
        ).strip()

        session_id = os.environ.get("HERMES_SESSION_ID", "") or None
        parent_agent = kwargs.get("parent_agent")
        if not session_id and parent_agent is not None:
            session_id = getattr(parent_agent, "session_id", None)

        # Route notifications to the project's coordinator session, not the
        # current agent. This ensures lex-master dispatches wake the project
        # coordinator, not lex-master itself.
        coordinator_session_id = _resolve_coordinator_session_for_project(project_path)
        if coordinator_session_id:
            session_id = coordinator_session_id

        conn = kb.connect(board=meta["slug"])
        try:
            task_id = kb.create_task(
                conn,
                title=title,
                body=body,
                assignee=assignee,
                created_by="swarm_task_create",
                workspace_kind="dir",
                workspace_path=str(Path(project_path).resolve()),
                priority=priority,
                session_id=session_id,
                board=meta["slug"],
            )
            if session_id:
                _subscribe_session(kb, session_id, task_id, board=meta["slug"])
            task = kb.get_task(conn, task_id)
        finally:
            conn.close()

        return json.dumps({
            "success": True,
            "task": _official_task_to_dict(task) if task else {"id": task_id, "task_id": task_id},
            "board": {"slug": meta["slug"], "source": "hermes_kanban"},
            "dispatch_status": _dispatcher_status(),
            "assignee_status": _assignee_status(assignee),
            "message": (
                f"任务 '{title}' 已创建（ID: {task_id}，Hermes Kanban board: {meta['slug']}）。"
                + (
                    f" assignee={assignee}，coordinator/default gateway 的 Kanban dispatcher 会在下一 tick spawn worker 子进程。"
                    " 不要检查或启动 assignee profile gateway；请用 swarm_task_poll 或 /swarm follow 监控。"
                    if assignee else
                    " 未指定 assignee，需先分配。"
                )
            ),
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"创建 Hermes Kanban 任务失败: {exc}"}, ensure_ascii=False)

    conn, board_id = _connect(project_path)

    # Parse gates
    gates = []
    gates_raw = args.get("gates", "")
    if gates_raw:
        try:
            if isinstance(gates_raw, str):
                gates = json.loads(gates_raw)
            else:
                gates = gates_raw
        except json.JSONDecodeError:
            conn.close()
            return json.dumps({"success": False, "error": "gates 格式错误：必须是有效的 JSON 数组。"})

        for g in gates:
            if g.get("type") not in VALID_GATE_TYPES:
                conn.close()
                return json.dumps({"success": False, "error": f"无效的 gate 类型: {g.get('type')}。有效值: {', '.join(sorted(VALID_GATE_TYPES))}"})

    # Resolve assignee: explicit > first gate's target_pool > None
    assignee = args.get("assignee", "").strip() or None
    if not assignee and gates:
        assignee = gates[0].get("target_pool", "")

    # Resolve column: explicit > first column
    column_id = args.get("column", "").strip() or None
    if not column_id:
        col_row = conn.execute(
            "SELECT id FROM columns WHERE board_id = ? ORDER BY position LIMIT 1", (board_id,)
        ).fetchone()
        if col_row:
            column_id = col_row["id"]

    priority = args.get("priority", 0)
    description = args.get("description", "").strip()

    task_id = _uid("tsk_")
    now = _now()
    session_id = os.environ.get("HERMES_SESSION_ID", "")

    initial_status = "ready" if assignee else "todo"

    conn.execute(
        """INSERT INTO tasks
           (id, board_id, column_id, title, description, assignee, status, priority,
            gates_json, gate_index, created_at, updated_at, session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, board_id, column_id, title, description, assignee, initial_status,
         priority, json.dumps(gates, ensure_ascii=False), 0, now, now, session_id),
    )
    _log_event(conn, task_id, "created", actor=kwargs.get("parent_agent", {}).get("name", "coordinator"),
               payload={"title": title, "gates": gates, "assignee": assignee})

    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()

    return json.dumps({
        "success": True,
        "task": _task_to_dict(row),
        "message": f"任务 '{title}' 已创建（ID: {task_id}）。" + (f" 门禁链: {len(gates)} 步。" if gates else ""),
    }, ensure_ascii=False)


def kanban_task_assign_handler(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    assignee = args.get("assignee", "").strip()
    if not task_id or not assignee:
        return json.dumps({"success": False, "error": "task_id 和 assignee 为必填项。"})

    # Find the board from task_id — we need to search across known projects
    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))
    if not project_path:
        return json.dumps({"success": False, "error": "无法确定项目路径。"})

    try:
        from hermes_cli import kanban_db as kb

        meta = _official_board(project_path, title=Path(project_path).name)
        conn = kb.connect(board=meta["slug"])
        try:
            ok = kb.assign_task(conn, task_id, assignee)
            task = kb.get_task(conn, task_id)
        finally:
            conn.close()
        if not ok or not task:
            return json.dumps({"success": False, "error": f"任务未找到或不可分配: {task_id}"}, ensure_ascii=False)
        return json.dumps({
            "success": True,
            "task": _official_task_to_dict(task),
            "board": {"slug": meta["slug"], "source": "hermes_kanban"},
            "dispatch_status": _dispatcher_status(),
            "assignee_status": _assignee_status(assignee),
            "message": (
                f"任务 {task_id} 已分配给 {assignee}。coordinator/default gateway 的 Kanban dispatcher"
                " 会在下一 tick spawn worker 子进程；不要启动 assignee profile gateway。"
            ),
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"分配 Hermes Kanban 任务失败: {exc}"}, ensure_ascii=False)

    conn, board_id = _connect(project_path)
    row = conn.execute("SELECT * FROM tasks WHERE id = ? AND board_id = ?", (task_id, board_id)).fetchone()
    if not row:
        conn.close()
        return json.dumps({"success": False, "error": f"任务未找到: {task_id}"})

    prev_assignee = row["assignee"]
    now = _now()
    conn.execute(
        "UPDATE tasks SET assignee = ?, status = 'in_progress', updated_at = ? WHERE id = ?",
        (assignee, now, task_id),
    )
    _log_event(conn, task_id, "assigned", actor="coordinator",
               payload={"from": prev_assignee, "to": assignee})

    updated = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()

    return json.dumps({
        "success": True,
        "task": _task_to_dict(updated),
        "message": f"任务已从 {prev_assignee or '（未分配）'} 分配给 {assignee}。",
    }, ensure_ascii=False)


def kanban_task_wait_handler(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    if not task_id:
        return json.dumps({"success": False, "error": "task_id 为必填项。"})

    timeout = int(args.get("timeout_seconds", 300))
    interval = int(args.get("poll_interval", 5))
    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))
    if not project_path:
        return json.dumps({"success": False, "error": "无法确定项目路径。"})

    conn, board_id = _connect(project_path)
    start = time.time()

    while True:
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ? AND board_id = ?", (task_id, board_id)
        ).fetchone()
        if not row:
            conn.close()
            return json.dumps({"success": False, "error": f"任务未找到: {task_id}"})

        status = row["status"]
        if status in ("done", "approved", "rejected"):
            conn.close()
            return json.dumps({
                "success": True,
                "task": _task_to_dict(row),
                "final_status": status,
                "elapsed_seconds": int(time.time() - start),
            }, ensure_ascii=False)

        if time.time() - start >= timeout:
            conn.close()
            return json.dumps({
                "success": True,
                "task": _task_to_dict(row),
                "timed_out": True,
                "message": f"等待超时（{timeout}s），任务当前状态: {status}。",
            }, ensure_ascii=False)

        time.sleep(interval)


def kanban_workflow_compile_handler(args: dict, **kwargs) -> str:
    yaml_path = args.get("yaml_path", "").strip()
    if not yaml_path:
        return json.dumps({"success": False, "error": "yaml_path 为必填项。"})

    if not Path(yaml_path).is_file():
        return json.dumps({"success": False, "error": f"YAML 文件未找到: {yaml_path}"})

    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))
    if not project_path:
        return json.dumps({"success": False, "error": "无法确定项目路径。"})

    try:
        import yaml as _yaml
    except ImportError:
        return json.dumps({"success": False, "error": "需要 PyYAML 库。请安装: pip install pyyaml"})

    with open(yaml_path, "r", encoding="utf-8") as fh:
        workflow = _yaml.safe_load(fh)

    conn, board_id = _connect(project_path)
    now = _now()
    created_tasks = []

    nodes = workflow.get("nodes", [])
    if isinstance(nodes, dict):
        nodes = list(nodes.values())

    task_id_map: dict[str, str] = {}  # node_key -> task_id

    for node in nodes:
        node_key = node.get("key", node.get("id", ""))
        node_title = node.get("title", node.get("name", node_key))
        node_kind = node.get("kind", "worker")
        node_profile = node.get("profile", node.get("assignee", ""))
        node_desc = node.get("description", node.get("desc", ""))

        task_id = _uid("tsk_")
        initial_status = "in_progress" if node_profile else "todo"

        gates = []
        if node_kind == "gate":
            gate_def = node.get("gate", {})
            if gate_def:
                gates = [{
                    "type": gate_def.get("type", "review"),
                    "target_pool": gate_def.get("target_pool", gate_def.get("target", "")),
                }]

        conn.execute(
            """INSERT INTO tasks
               (id, board_id, title, description, assignee, status,
                gates_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, board_id, node_title, node_desc, node_profile or None,
             initial_status, json.dumps(gates, ensure_ascii=False), now, now),
        )
        _log_event(conn, task_id, "created", actor="workflow_compile",
                   payload={"node_key": node_key, "kind": node_kind, "yaml": yaml_path})

        task_id_map[node_key] = task_id
        created_tasks.append({
            "task_id": task_id,
            "title": node_title,
            "kind": node_kind,
            "assignee": node_profile or None,
            "node_key": node_key,
        })

    # Wire dependencies
    edges = workflow.get("edges", [])
    if not edges and isinstance(nodes, list):
        # Linear implicit edges: node[i] depends on node[i-1]
        for i in range(1, len(nodes)):
            prev_key = nodes[i - 1].get("key", nodes[i - 1].get("id", ""))
            curr_key = nodes[i].get("key", nodes[i].get("id", ""))
            if prev_key in task_id_map and curr_key in task_id_map:
                edges.append({"from": prev_key, "to": curr_key})

    for edge in edges:
        parent_id = task_id_map.get(edge.get("from", ""))
        child_id = task_id_map.get(edge.get("to", ""))
        if parent_id and child_id:
            conn.execute(
                "INSERT OR IGNORE INTO task_links (parent_id, child_id) VALUES (?, ?)",
                (parent_id, child_id),
            )

    conn.commit()
    conn.close()

    return json.dumps({
        "success": True,
        "workflow_file": yaml_path,
        "tasks_created": len(created_tasks),
        "tasks": created_tasks,
        "edges": len(edges),
    }, ensure_ascii=False)


def kanban_board_status_handler(args: dict, **kwargs) -> str:
    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))
    if not project_path:
        return json.dumps({"success": False, "error": "无法确定项目路径。"})

    try:
        from hermes_cli import kanban_db as kb

        meta = _official_board(project_path, title=Path(project_path).name)
        status_filter = _map_legacy_status_filter(args.get("status_filter", "").strip() or None)
        assignee_filter = args.get("assignee_filter", "").strip() or None
        conn = kb.connect(board=meta["slug"])
        try:
            columns = []
            statuses = ["triage", "todo", "ready", "running", "review", "blocked", "done"]
            for status in statuses:
                if status_filter and status != status_filter:
                    continue
                tasks = kb.list_tasks(
                    conn,
                    status=status,
                    assignee=assignee_filter,
                )
                columns.append({
                    "id": status,
                    "name": status,
                    "tasks": [_official_task_to_dict(t) for t in tasks],
                    "task_count": len(tasks),
                })
        finally:
            conn.close()
        return json.dumps({
            "success": True,
            "board": {
                "id": meta["slug"],
                "slug": meta["slug"],
                "project_path": str(Path(project_path).resolve()),
                "title": meta.get("name"),
                "source": "hermes_kanban",
            },
            "dispatch_status": _dispatcher_status(),
            "columns": columns,
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"读取 Hermes Kanban 状态失败: {exc}"}, ensure_ascii=False)

    conn, board_id = _connect(project_path)
    board_row = conn.execute("SELECT * FROM board WHERE id = ?", (board_id,)).fetchone()

    columns = conn.execute(
        "SELECT * FROM columns WHERE board_id = ? ORDER BY position", (board_id,)
    ).fetchall()

    status_filter = args.get("status_filter", "").strip() or None
    assignee_filter = args.get("assignee_filter", "").strip() or None

    board_data = {
        "board": dict(board_row),
        "columns": [],
    }

    for col in columns:
        cd = dict(col)
        query = "SELECT * FROM tasks WHERE board_id = ? AND column_id = ?"
        params: list[Any] = [board_id, col["id"]]

        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)
        if assignee_filter:
            query += " AND assignee = ?"
            params.append(assignee_filter)

        query += " ORDER BY priority DESC, created_at ASC"

        tasks_rows = conn.execute(query, params).fetchall()
        cd["tasks"] = [_task_to_dict(r) for r in tasks_rows]
        cd["task_count"] = len(tasks_rows)
        board_data["columns"].append(cd)

    conn.close()
    return json.dumps({"success": True, **board_data}, ensure_ascii=False)


# ── Gate inference helper ──────────────────────────────────────────────────────

def _infer_current_gate(task_row: sqlite3.Row, spec: dict) -> str:
    """Infer the current delivery gate from task state.

    Uses handoff_history count and task status to determine the current phase:
    - 0 handoffs → "plan"
    - 1 handoff  → "draft"
    - 2 handoffs → "review"
    - 3+ handoffs → "finalize"
    """
    history = json.loads(task_row["handoff_history_json"])
    handoff_count = sum(1 for h in history if h.get("action") == "handoff")
    approved_count = sum(1 for h in history if h.get("action") == "approved")

    gates_order = ["plan", "draft", "review", "finalize"]
    gate_index = min(handoff_count, len(gates_order) - 1)
    return gates_order[gate_index]


def _handoff_chain_digest(history: list) -> list[dict]:
    """Condense handoff_history into a readable from→to→note chain.

    Workers and the Coordinator use this instead of parsing the raw event log:
    each entry is one move (handoff/approved/rejected/revised) with its full note,
    so context flows visibly between Drafter and Reviewers across rounds.
    """
    chain = []
    for i, h in enumerate(history):
        if not isinstance(h, dict):
            continue
        entry = {
            "seq": i + 1,
            "action": h.get("action", ""),
            "from": h.get("from", ""),
            "timestamp": h.get("timestamp"),
        }
        if h.get("to"):
            entry["to"] = h["to"]
        # Keep the full note — it is the inter-agent context, not a summary.
        if h.get("note"):
            entry["note"] = h["note"]
        chain.append(entry)
    return chain


def _latest_progress(conn: sqlite3.Connection, task_id: str) -> dict | None:
    """Return the most recent worker progress update for a task, or None.

    Progress is posted via swarm_task_progress as a `progress` task_event, so a
    Coordinator polling a running Drafter sees what has actually been done so far.
    """
    row = conn.execute(
        "SELECT actor, payload, created_at FROM task_events "
        "WHERE task_id = ? AND kind = 'progress' ORDER BY created_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload"] or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {
        "by": row["actor"],
        "at": row["created_at"],
        "item": payload.get("item"),
        "status": payload.get("status"),
        "note": payload.get("note", ""),
    }


# ── Poll & Collect handlers ────────────────────────────────────────────────────

def kanban_task_poll_handler(args: dict, **kwargs) -> str:
    task_ids_raw = args.get("task_ids", "").strip()
    if not task_ids_raw:
        return json.dumps({"success": False, "error": "task_ids 为必填项。"})

    task_ids = [tid.strip() for tid in task_ids_raw.split(",") if tid.strip()]
    if not task_ids:
        return json.dumps({"success": False, "error": "task_ids 为空或格式不正确。"})

    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))
    if not project_path:
        return json.dumps({"success": False, "error": "无法确定项目路径。"})

    try:
        from hermes_cli import kanban_db as kb

        meta = _official_board(project_path, title=Path(project_path).name)
        dispatch_status = _dispatcher_status()
        conn = kb.connect(board=meta["slug"])
        tasks = []
        status_counts = {"todo": 0, "ready": 0, "running": 0, "review": 0, "done": 0, "blocked": 0}
        try:
            for tid in task_ids:
                task = kb.get_task(conn, tid)
                if not task:
                    tasks.append({"task_id": tid, "error": "not found"})
                    continue
                item = _official_task_to_dict(task)
                item["latest_progress"] = _latest_official_progress(conn, kb, tid)
                item["assignee_status"] = _assignee_status(task.assignee)
                if task.status == "ready" and task.assignee and dispatch_status.get("active") is False:
                    item["dispatch_blocked"] = True
                    item["dispatch_blocked_reason"] = dispatch_status.get("message") or dispatch_status.get("warning")
                elif task.status == "ready" and item["assignee_status"] and item["assignee_status"].get("exists") is False:
                    item["dispatch_blocked"] = True
                    item["dispatch_blocked_reason"] = item["assignee_status"].get("warning")
                tasks.append(item)
                if task.status in status_counts:
                    status_counts[task.status] += 1
        finally:
            conn.close()
        return json.dumps({
            "success": True,
            "board": {
                "slug": meta["slug"],
                "title": meta.get("name") or Path(project_path).name,
                "source": "hermes_kanban",
                "project_path": str(Path(project_path).resolve()),
                "default_workdir": meta.get("default_workdir"),
                "db_path": meta.get("db_path"),
            },
            "dispatch_status": dispatch_status,
            "tasks": tasks,
            "summary": {
                "total": len(tasks),
                "todo": status_counts["todo"],
                "ready": status_counts["ready"],
                "in_progress": status_counts["running"],
                "running": status_counts["running"],
                "review": status_counts["review"],
                "done": status_counts["done"],
                "blocked": status_counts["blocked"],
            },
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"查询 Hermes Kanban 任务失败: {exc}"}, ensure_ascii=False)

    conn, board_id = _connect(project_path)
    tasks = []
    summary = {"total": 0, "todo": 0, "in_progress": 0, "in_review": 0, "done": 0, "blocked": 0}

    for tid in task_ids:
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ? AND board_id = ?", (tid, board_id)
        ).fetchone()
        if not row:
            tasks.append({"task_id": tid, "error": "not found"})
            continue

        status = row["status"]
        summary["total"] += 1
        if status in summary:
            summary[status] = summary.get(status, 0) + 1
        # Handle done/approved/rejected as "done"
        if status in ("done", "approved"):
            summary["done"] = summary.get("done", 0) + 1

        # Count gate progress from handoff history
        gates = json.loads(row["gates_json"])
        history = json.loads(row["handoff_history_json"])
        approved_in_history = sum(1 for h in history if h.get("action") == "approved")
        gate_progress = f"{approved_in_history}/{len(gates)}" if gates else "N/A"

        tasks.append({
            "task_id": tid,
            "title": row["title"],
            "status": status,
            "assignee": row["assignee"],
            "completed_at": row["completed_at"],
            "gate_progress": gate_progress,
            "latest_progress": _latest_progress(conn, tid),
        })

    # Recalculate summary correctly
    status_counts = {"todo": 0, "in_progress": 0, "in_review": 0, "done": 0, "approved": 0, "rejected": 0, "blocked": 0}
    for t in tasks:
        status = t.get("status", "")
        if status in status_counts:
            status_counts[status] += 1
        elif status == "done":
            status_counts["done"] += 1
        elif status == "approved":
            status_counts["approved"] += 1
        elif status == "rejected":
            status_counts["rejected"] += 1

    conn.close()

    return json.dumps({
        "success": True,
        "tasks": tasks,
        "summary": {
            "total": len(tasks),
            "todo": status_counts["todo"],
            "in_progress": status_counts["in_progress"],
            "in_review": status_counts["in_review"],
            "done": status_counts["done"] + status_counts["approved"],
            "rejected": status_counts["rejected"],
        },
    }, ensure_ascii=False)


def kanban_task_collect_handler(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    if not task_id:
        return json.dumps({"success": False, "error": "task_id 为必填项。"})

    include_events = bool(args.get("include_events", False))
    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))
    if not project_path:
        return json.dumps({"success": False, "error": "无法确定项目路径。"})

    try:
        from hermes_cli import kanban_db as kb

        meta = _official_board(project_path, title=Path(project_path).name)
        conn = kb.connect(board=meta["slug"])
        try:
            task = kb.get_task(conn, task_id)
            if not task:
                return json.dumps({"success": False, "error": f"任务未找到: {task_id}"}, ensure_ascii=False)
            events = kb.list_events(conn, task_id)
            comments = kb.list_comments(conn, task_id)
            run_summary = kb.latest_summary(conn, task_id)
            completed_payload = next(
                (
                    event.payload
                    for event in reversed(events)
                    if event.kind == "completed" and isinstance(event.payload, dict)
                ),
                {},
            )
            artifacts = completed_payload.get("artifacts") or []
            if not isinstance(artifacts, list):
                artifacts = []
        finally:
            conn.close()

        terminal = task.status in {"done", "blocked", "archived"}
        event_summary = [
            {"kind": e.kind, "at": e.created_at, "payload": e.payload}
            for e in events
            if e.kind in {"claimed", "completed", "blocked", "comment", "heartbeat", "crashed", "timed_out"}
        ]
        result = {
            "success": True,
            "board": {"slug": meta["slug"], "source": "hermes_kanban"},
            "task_id": task_id,
            "status": task.status,
            "worker": task.assignee or "unknown",
            "summary": run_summary or task.result or completed_payload.get("summary") or (
                f"Task completed with status '{task.status}'." if terminal else f"Task is in progress (status: {task.status})."
            ),
            "events_summary": event_summary,
            "output": {
                "comments": [
                    {"author": c.author, "body": c.body, "at": c.created_at}
                    for c in comments
                ],
                "files_touched": artifacts,
                "handoff_notes": [],
            },
        }
        if not terminal:
            result["warning"] = "task not yet terminal — output may be incomplete"
            result["current_status"] = task.status
        if include_events:
            result["events"] = event_summary
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"收集 Hermes Kanban 任务失败: {exc}"}, ensure_ascii=False)

    conn, board_id = _connect(project_path)
    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ? AND board_id = ?", (task_id, board_id)
    ).fetchone()
    if not row:
        conn.close()
        return json.dumps({"success": False, "error": f"任务未找到: {task_id}"})

    status = row["status"]
    is_terminal = status in ("done", "approved", "rejected")

    # Read events
    events = [
        dict(e) for e in conn.execute(
            "SELECT id, kind, actor, payload, created_at FROM task_events WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
    ]

    # Read handoff history
    history = json.loads(row["handoff_history_json"])

    # Read comments (if comment system is linked — check DB schema)
    comments = []
    try:
        comment_rows = conn.execute(
            "SELECT id, author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        comments = [dict(c) for c in comment_rows]
    except Exception:
        pass

    # Extract blackboard comments
    blackboard_entries = []
    for c in comments:
        body = c.get("body", "")
        if isinstance(body, str) and "[swarm:blackboard]" in body:
            blackboard_entries.append({
                "author": c.get("author"),
                "content": body.replace("[swarm:blackboard]", "").strip(),
                "at": c.get("created_at"),
            })

    # Synthesize summary
    worker = row["assignee"] or "unknown"
    event_summary = [
        {
            "kind": e["kind"],
            "at": e["created_at"],
            "note": (json.loads(e.get("payload", "{}")) if isinstance(e.get("payload"), str) else e.get("payload", {})),
        }
        for e in events
        if e["kind"] in ("claimed", "handoff", "revised", "approved", "rejected", "completed")
    ]

    handoff_notes = [
        {"action": h.get("action"), "note": h.get("note") or h.get("reason"), "timestamp": h.get("timestamp")}
        for h in history
        if h.get("note") or h.get("reason")
    ]

    # Extract files_touched from event payloads and handoff notes
    files_touched = []
    for e in events:
        payload = e.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        for key in ("files", "modified_files", "document_path", "output_path"):
            val = payload.get(key)
            if val:
                if isinstance(val, list):
                    files_touched.extend(val)
                elif isinstance(val, str):
                    files_touched.append(val)

    summary_text = ""
    if is_terminal:
        summary_text = f"Task completed with status '{status}'."
        if handoff_notes:
            last_note = handoff_notes[-1].get("note", "")
            summary_text += f" Last note: {last_note[:200]}"
    else:
        summary_text = f"Task is in progress (status: {status}). Not yet terminal."

    conn.close()

    result = {
        "success": True,
        "task_id": task_id,
        "status": status,
        "worker": worker,
        "summary": summary_text,
        "events_summary": event_summary,
        "output": {
            "blackboard": blackboard_entries,
            "files_touched": list(set(files_touched)),
            "handoff_notes": handoff_notes,
        },
    }

    if not is_terminal:
        result["warning"] = "task not yet terminal — output may be incomplete"
        result["current_status"] = status

    if include_events:
        result["events"] = events

    return json.dumps(result, ensure_ascii=False)


def kanban_dispatch_now_handler(args: dict, **kwargs) -> str:
    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))
    if not project_path:
        return json.dumps({"success": False, "error": "无法确定项目路径。"})

    try:
        from hermes_cli import kanban_db as kb

        meta = _official_board(project_path, title=Path(project_path).name)
        dry_run = bool(args.get("dry_run", False))
        max_spawn_raw = args.get("max_spawn", None)
        max_spawn = int(max_spawn_raw) if max_spawn_raw not in (None, "") else None

        conn = kb.connect(board=meta["slug"])
        try:
            dispatch_result = kb.dispatch_once(
                conn,
                board=meta["slug"],
                dry_run=dry_run,
                max_spawn=max_spawn,
            )
        finally:
            conn.close()

        spawned = [
            {"task_id": tid, "assignee": who, "workspace": ws}
            for tid, who, ws in dispatch_result.spawned
        ]
        return json.dumps({
            "success": True,
            "board": {"slug": meta["slug"], "source": "hermes_kanban"},
            "manual_dispatch": True,
            "dry_run": dry_run,
            "dispatch_status": _dispatcher_status(),
            "result": {
                "spawned": spawned,
                "spawned_count": len(spawned),
                "reclaimed": dispatch_result.reclaimed,
                "crashed": dispatch_result.crashed,
                "timed_out": dispatch_result.timed_out,
                "stale": dispatch_result.stale,
                "auto_blocked": dispatch_result.auto_blocked,
                "promoted": dispatch_result.promoted,
                "skipped_unassigned": dispatch_result.skipped_unassigned,
                "skipped_nonspawnable": dispatch_result.skipped_nonspawnable,
            },
            "message": (
                f"已手动运行一次 dispatcher（board={meta['slug']}，spawned={len(spawned)}）。"
                " 这是 CLI/TUI 直接会话的 fallback；不要启动 assignee profile gateway。"
            ),
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"手动 dispatch 失败: {exc}"}, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# WORKER TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

KANBAN_TASK_CLAIM_SCHEMA = {
    "name": "swarm_task_claim",
    "description": (
        "认领一个待处理的任务。使用 CAS（Compare-And-Swap）原子操作，"
        "确保同一任务不会被多个 Worker 同时认领。成功认领后返回任务详情和 claim_token。"
        "\n\n"
        "Worker 必须先认领任务才能对其进行操作（handoff / revise）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要认领的任务 ID。",
            },
            "board": {
                "type": "string",
                "description": "可选：Hermes Kanban board slug。不填则使用 HERMES_KANBAN_BOARD/HERMES_KANBAN_DB 或当前项目推导。",
            },
        },
        "required": ["task_id"],
    },
}

KANBAN_TASK_READ_SCHEMA = {
    "name": "swarm_task_read",
    "description": (
        "读取任务的完整详情，包括门禁链状态、handoff 历史、事件日志。"
        "返回中的 handoff_chain 是一份按时序整理的移交链摘要（from→to→note），"
        "Reviewer 据此快速获取上一环节的完整移交说明与历史上下文，"
        "无需自行解析原始 events。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "任务 ID。",
            },
            "board": {
                "type": "string",
                "description": "可选：Hermes Kanban board slug。不填则使用 HERMES_KANBAN_BOARD/HERMES_KANBAN_DB 或当前项目推导。",
            },
        },
        "required": ["task_id"],
    },
}

KANBAN_TASK_HANDOFF_SCHEMA = {
    "name": "swarm_task_handoff",
    "description": (
        "将完成当前阶段工作的任务移交给下一个门禁的审核者（Reviewer）。"
        "Worker（Drafter）完成任务起草后调用此工具，将任务流转到审核阶段。"
        "\n\n"
        "如果有门禁链，任务进入 'in_review' 状态，等待 Reviewer 审批。"
        "如果没有任何门禁，任务直接标记为 'done'。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要移交的任务 ID。",
            },
            "note": {
                "type": "string",
                "description": "移交说明（做了什么、注意事项、需要审核的要点）。",
            },
            "claim_token": {
                "type": "string",
                "description": "认领时获得的 claim_token。用于验证操作权限。",
            },
            "decisions": {"type": "array", "items": {"type": "string"}, "description": "本阶段形成的关键决策。"},
            "constraints": {"type": "array", "items": {"type": "string"}, "description": "后续阶段必须遵守的约束。"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}, "description": "证据、材料或引用路径。"},
            "files_modified": {"type": "array", "items": {"type": "string"}, "description": "本阶段修改的文件。"},
            "unresolved_items": {"type": "array", "items": {"type": "string"}, "description": "尚未解决的问题。"},
            "required_next_actions": {"type": "array", "items": {"type": "string"}, "description": "下一处理人必须执行的动作。"},
            "board": {
                "type": "string",
                "description": "可选：Hermes Kanban board slug。不填则使用 HERMES_KANBAN_BOARD/HERMES_KANBAN_DB 或当前项目推导。",
            },
        },
        "required": ["task_id", "claim_token"],
    },
}

KANBAN_TASK_APPROVE_SCHEMA = {
    "name": "swarm_task_approve",
    "description": (
        "Reviewer 批准当前门禁。如果还有后续门禁，任务自动流转到下一个门禁。"
        "所有门禁通过后，任务标记为 'approved'（最终完成）。"
        "\n\n"
        "这是 Reviewer 的核心操作——确认 Drafter 的工作合格，允许流程继续。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要批准的任务 ID。",
            },
            "note": {
                "type": "string",
                "description": "批准说明（通过了哪些检查、备注）。",
            },
            "claim_token": {
                "type": "string",
                "description": "认领时获得的 claim_token。",
            },
            "board": {
                "type": "string",
                "description": "可选：Hermes Kanban board slug。不填则使用 HERMES_KANBAN_BOARD/HERMES_KANBAN_DB 或当前项目推导。",
            },
        },
        "required": ["task_id", "claim_token"],
    },
}

KANBAN_TASK_REJECT_SCHEMA = {
    "name": "swarm_task_reject",
    "description": (
        "Reviewer 拒绝当前门禁。任务回到上一环节的 Worker（Drafter）手中，"
        "状态变为 'rejected'。Worker 需要修改后重新 handoff。"
        "\n\n"
        "拒绝时必须提供明确的理由，以便 Worker 知道哪里需要修改。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要拒绝的任务 ID。",
            },
            "reason": {
                "type": "string",
                "description": "拒绝理由（必填）。说明哪里不符合要求，需要如何修改。",
            },
            "claim_token": {
                "type": "string",
                "description": "认领时获得的 claim_token。",
            },
            "board": {
                "type": "string",
                "description": "可选：Hermes Kanban board slug。不填则使用 HERMES_KANBAN_BOARD/HERMES_KANBAN_DB 或当前项目推导。",
            },
        },
        "required": ["task_id", "reason", "claim_token"],
    },
}

KANBAN_TASK_REVISE_SCHEMA = {
    "name": "swarm_task_revise",
    "description": (
        "Worker（Drafter）在被 Reviewer 拒绝后，完成修改并重新提交。"
        "任务状态从 'rejected' 变回 'in_review'，等待 Reviewer 再次审批。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要重新提交的任务 ID。",
            },
            "note": {
                "type": "string",
                "description": "修改说明（改了哪些地方，如何回应 Reviewer 的意见）。",
            },
            "claim_token": {
                "type": "string",
                "description": "认领时获得的 claim_token。",
            },
            "board": {
                "type": "string",
                "description": "可选：Hermes Kanban board slug。不填则使用 HERMES_KANBAN_BOARD/HERMES_KANBAN_DB 或当前项目推导。",
            },
        },
        "required": ["task_id", "claim_token"],
    },
}


# ── Worker handlers ────────────────────────────────────────────────────────────

def _find_task_db(task_id: str, project_path: str | None = None) -> tuple[sqlite3.Connection, str, sqlite3.Row | None]:
    """Open the DB for the board containing task_id. Returns (conn, board_id, task_row)."""
    # If project_path given, look there
    if project_path:
        conn, board_id = _connect(project_path)
        row = conn.execute("SELECT * FROM tasks WHERE id = ? AND board_id = ?", (task_id, board_id)).fetchone()
        if row:
            return conn, board_id, row
        conn.close()

    return None, "", None  # type: ignore[return-value]


def _resolve_task_db(task_id: str, args: dict, parent_agent=None) -> tuple[sqlite3.Connection, sqlite3.Row, str]:
    """Resolve project path and find task. Returns (conn, task_row, board_id). Raises ValueError on failure."""
    project_path = _resolve_project_path(args, parent_agent)
    if not project_path:
        raise ValueError("无法确定项目路径。请使用 project_path 或先通过 project_select 选择项目。")

    conn, board_id = _connect(project_path)
    row = conn.execute("SELECT * FROM tasks WHERE id = ? AND board_id = ?", (task_id, board_id)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"任务未找到: {task_id}")

    return conn, row, board_id


def kanban_task_claim_handler(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    if not task_id:
        return json.dumps({"success": False, "error": "task_id 为必填项。"})

    parent_agent = kwargs.get("parent_agent")
    official = _connect_official_for_task(task_id, args, parent_agent)
    if official is not None:
        kb, conn, row, board = official
        assignee = getattr(parent_agent, "name", None) or os.environ.get("HERMES_PROFILE", "worker")
        try:
            if row.status == "ready":
                claimed = kb.claim_task(conn, task_id, claimer=assignee)
            elif row.status == "review":
                claimed = kb.claim_review_task(conn, task_id, claimer=assignee)
            else:
                claimed = None
            if claimed is None:
                current = kb.get_task(conn, task_id)
                return _official_result(
                    False,
                    error=(
                        f"任务状态为 '{current.status if current else row.status}'，无法认领；"
                        "只有 ready/review 且未被认领的官方 Kanban 任务可以认领。"
                    ),
                    board={"slug": board, "source": "hermes_kanban"},
                )
            task_dict = _official_task_to_dict(claimed)
            task_dict["claim_token"] = claimed.claim_lock
            return _official_result(
                True,
                task=task_dict,
                board={"slug": board, "source": "hermes_kanban"},
                message=f"任务 '{claimed.title}' 已认领。请开始工作。",
            )
        finally:
            conn.close()

    try:
        conn, row, board_id = _resolve_task_db(task_id, args, parent_agent)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    if row["status"] not in ("todo", "in_progress", "in_review", "rejected"):
        conn.close()
        return json.dumps({"success": False, "error": f"任务状态为 '{row['status']}'，无法认领。只有 todo/in_progress/in_review/rejected 状态的任务可以认领。"})

    assignee = getattr(parent_agent, "name", None) or os.environ.get("HERMES_PROFILE", "worker")
    token = _cas_claim(conn, task_id, assignee)
    if not token:
        current = conn.execute(
            "SELECT assignee, claim_expires FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        conn.close()
        holder = current["assignee"] if current else "unknown"
        expires = current["claim_expires"] if current else 0
        return json.dumps({
            "success": False,
            "error": f"任务已被认领。当前持有者: {holder}，过期时间: {expires}",
        })

    _log_event(conn, task_id, "claimed", actor=assignee)
    updated = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    task_dict = _task_to_dict(updated)
    task_dict["claim_token"] = token  # Only returned on successful claim
    conn.close()

    return json.dumps({
        "success": True,
        "task": task_dict,
        "message": f"任务 '{updated['title']}' 已认领。请开始工作。",
    }, ensure_ascii=False)


def kanban_task_read_handler(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    if not task_id:
        return json.dumps({"success": False, "error": "task_id 为必填项。"})

    parent_agent = kwargs.get("parent_agent")
    official = _connect_official_for_task(task_id, args, parent_agent)
    if official is not None:
        kb, conn, row, board = official
        try:
            events = []
            # ``list_events_for_tasks`` only exists in some Hermes revisions.
            # The stable public API is the single-task ``list_events`` helper.
            list_task_events = getattr(kb, "list_events_for_tasks", None)
            event_rows = (
                list_task_events(conn, [task_id])
                if callable(list_task_events)
                else kb.list_events(conn, task_id)
            )
            for event in event_rows:
                events.append({
                    "id": event.id,
                    "task_id": event.task_id,
                    "kind": event.kind,
                    "payload": event.payload,
                    "created_at": event.created_at,
                    "run_id": event.run_id,
                })
            comments = [
                {
                    "id": c.id,
                    "task_id": c.task_id,
                    "author": c.author,
                    "body": c.body,
                    "created_at": c.created_at,
                }
                for c in kb.list_comments(conn, task_id)
            ]
            return _official_result(
                True,
                task=_official_task_to_dict(row),
                comments=comments,
                events=events,
                event_count=len(events),
                board={"slug": board, "source": "hermes_kanban"},
            )
        finally:
            conn.close()

    try:
        conn, row, board_id = _resolve_task_db(task_id, args, parent_agent)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    task_dict = _task_to_dict(row)

    # Include event log
    events = [
        dict(e) for e in conn.execute(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY created_at", (task_id,)
        ).fetchall()
    ]
    conn.close()

    return json.dumps({
        "success": True,
        "task": task_dict,
        "handoff_chain": _handoff_chain_digest(task_dict.get("handoff_history", [])),
        "events": events,
        "event_count": len(events),
    }, ensure_ascii=False)


def kanban_task_handoff_handler(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    note = args.get("note", "").strip()
    claim_token = args.get("claim_token", "").strip()
    handoff_payload = _structured_handoff(args, note)
    actor = getattr(kwargs.get("parent_agent"), "name", "worker")
    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))

    if not task_id or not claim_token:
        return json.dumps({"success": False, "error": "task_id 和 claim_token 为必填项。"})

    parent_agent = kwargs.get("parent_agent")
    official = _connect_official_for_task(task_id, args, parent_agent)
    if official is not None:
        kb, conn, row, board = official
        try:
            if not _official_claim_ok(row, claim_token):
                return _official_result(False, error="claim_token 无效或已过期。")
        finally:
            conn.close()
        from tools.kanban_tools import _handle_complete

        complete_args = {
            "task_id": task_id,
            "summary": note or "handoff completed",
            "metadata": {
                "swarm_action": "handoff",
                "approved_by": getattr(parent_agent, "name", None),
                "lex_handoff": handoff_payload,
            },
        }
        if board:
            complete_args["board"] = board
        out = json.loads(_handle_complete(complete_args))
        if out.get("ok"):
            if project_path:
                _persist_structured_handoff(project_path, task_id, handoff_payload, actor=actor)
            return _official_result(
                True,
                task_id=task_id,
                board={"slug": board, "source": "hermes_kanban"},
                message=out.get("message") or "已通过官方 Kanban handoff/complete 路由。",
                kanban=out,
            )
        return _official_result(False, error=out.get("error") or json.dumps(out, ensure_ascii=False), kanban=out)

    try:
        conn, row, board_id = _resolve_task_db(task_id, args, parent_agent)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    if not _cas_handoff(conn, task_id, claim_token):
        conn.close()
        return json.dumps({"success": False, "error": "claim_token 无效或已过期。请重新认领任务。"})

    if row["status"] not in ("in_progress", "rejected"):
        conn.close()
        return json.dumps({"success": False, "error": f"任务状态为 '{row['status']}'，无法移交。"})

    evidence_errors = _read_before_conclude_errors(note)
    if evidence_errors:
        conn.close()
        return json.dumps({
            "success": False,
            "error": "Read-before-conclude 证据门禁未通过，禁止移交。",
            "failed_checks": evidence_errors,
            "fix_hint": (
                "请逐一列出、解压、读取/OCR 全部相关文件，补充 File Evidence Ledger，"
                "并将缺口标为“已核实未提供 / 未核实 / 已提供但不完整”之一后再 handoff。"
            ),
        }, ensure_ascii=False)

    # ── DELIVERY_SPEC gate check (hard gate) ──
    # Validate exit criteria for the current delivery phase before allowing handoff.
    # This makes delivery validation non-bypassable — like Claude Code's plan/todo system.
    if project_path:
        try:
            from ..delivery_spec import load_delivery_spec, validate_gate_exit

            spec = load_delivery_spec(project_path)
            current_gate_name = _infer_current_gate(row, spec)
            exit_errors = validate_gate_exit(project_path, current_gate_name)

            if exit_errors:
                conn.close()
                return json.dumps({
                    "success": False,
                    "error": "交付规范检查未通过，禁止移交。",
                    "gate": current_gate_name,
                    "failed_checks": exit_errors,
                    "fix_hint": "请修复以上问题后重新调用 swarm_task_handoff。",
                }, ensure_ascii=False)

            # Also check output_required from workflow and handoff note completeness
            from hermes_cli.project_commands import _validate_handoff_envelope

            handoff_envelope = {
                "status": "completed",
                "evidence": {"note": note, "handoff_count": len(json.loads(row["handoff_history_json"])) + 1},
            }
            envelope_errors = _validate_handoff_envelope(handoff_envelope)
            if envelope_errors:
                conn.close()
                return json.dumps({
                    "success": False,
                    "error": "移交 notes 不完整。",
                    "missing_outputs": envelope_errors,
                    "fix_hint": "请补充完整的 handoff note 后再调用。",
                }, ensure_ascii=False)

        except ImportError:
            # If delivery_spec not available, fall through (soft gate — no block)
            pass
        except Exception:
            # Unexpected errors should not block delivery (preserve existing behavior)
            pass

    now = _now()
    gates = json.loads(row["gates_json"])
    history = json.loads(row["handoff_history_json"])
    history.append({
        "action": "handoff",
        "from": row["assignee"],
        "note": note,
        "structured": handoff_payload,
        "timestamp": now,
    })

    if gates and row["gate_index"] < len(gates):
        # Move to review at current gate
        current_gate = gates[row["gate_index"]]
        next_assignee = current_gate.get("target_pool", "")
        conn.execute(
            """UPDATE tasks SET status = 'in_review', assignee = ?,
               handoff_history_json = ?, claim_lock = NULL, claim_expires = NULL,
               updated_at = ? WHERE id = ?""",
            (next_assignee, json.dumps(history, ensure_ascii=False), now, task_id),
        )
        _log_event(conn, task_id, "handoff", actor=actor,
                   payload={"note": note, "structured": handoff_payload, "to": next_assignee, "gate_index": row["gate_index"]})
        msg = f"已移交给 {next_assignee} 审核（门禁 {row['gate_index'] + 1}/{len(gates)}）。"
    else:
        # No gates — mark done directly
        conn.execute(
            """UPDATE tasks SET status = 'done', handoff_history_json = ?,
               claim_lock = NULL, claim_expires = NULL, completed_at = ?,
               updated_at = ? WHERE id = ?""",
            (json.dumps(history, ensure_ascii=False), now, now, task_id),
        )
        _log_event(conn, task_id, "completed", actor=actor, payload={"note": note, "structured": handoff_payload})
        msg = "任务已完成（无门禁链）。"

    updated = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    if project_path:
        _persist_structured_handoff(project_path, task_id, handoff_payload, actor=actor)

    return json.dumps({
        "success": True,
        "task": _task_to_dict(updated),
        "message": msg,
    }, ensure_ascii=False)


KANBAN_TASK_PROGRESS_SCHEMA = {
    "name": "swarm_task_progress",
    "description": (
        "Worker 在执行长任务过程中发布阶段性进度。每完成一个子项（如一段修改、"
        "一个审阅维度）就调用一次，让 Coordinator 通过 swarm_task_poll 实时看到"
        "你做到哪里了。这不替代 handoff —— handoff 是完成后的最终交付，"
        "progress 是执行过程中的中途汇报。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "你正在执行的任务 ID。"},
            "note": {"type": "string", "description": "本次进度说明：做了什么 / 当前状态 / 下一步。"},
            "item": {"type": "string", "description": "可选：本次进度对应的子项标识（如 \"§5 利率条款\"）。"},
            "status": {"type": "string", "description": "可选：子项状态，如 ok / failed / skipped。"},
            "project_path": {"type": "string", "description": "项目根目录绝对路径。省略则用当前选中项目。"},
            "board": {
                "type": "string",
                "description": "可选：Hermes Kanban board slug。不填则使用 HERMES_KANBAN_BOARD/HERMES_KANBAN_DB 或当前项目推导。",
            },
        },
        "required": ["task_id", "note"],
    },
}


def kanban_task_progress_handler(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    note = args.get("note", "").strip()
    if not task_id or not note:
        return json.dumps({"success": False, "error": "task_id 和 note 为必填项。"})

    parent_agent = kwargs.get("parent_agent")
    official = _connect_official_for_task(task_id, args, parent_agent)
    if official is not None:
        kb, conn, row, board = official
        actor = getattr(parent_agent, "name", None) or os.environ.get("HERMES_PROFILE", "worker")
        payload = {"note": note}
        if args.get("item"):
            payload["item"] = str(args["item"]).strip()
        if args.get("status"):
            payload["status"] = str(args["status"]).strip()
        try:
            with kb.write_txn(conn):
                kb._append_event(conn, task_id, "progress", payload)  # noqa: SLF001
            kb.add_comment(conn, task_id, author=actor, body=f"[progress] {note}")
            return _official_result(
                True,
                board={"slug": board, "source": "hermes_kanban"},
                message="进度已记录。Coordinator 可通过 board/session 通知链路查看。",
            )
        finally:
            conn.close()

    try:
        conn, row, board_id = _resolve_task_db(task_id, args, parent_agent)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    actor = getattr(parent_agent, "name", "worker")
    payload = {"note": note}
    if args.get("item"):
        payload["item"] = str(args["item"]).strip()
    if args.get("status"):
        payload["status"] = str(args["status"]).strip()
    _log_event(conn, task_id, "progress", actor=actor, payload=payload)
    conn.close()

    return json.dumps({
        "success": True,
        "message": "进度已记录。Coordinator 可通过 swarm_task_poll 查看 latest_progress。",
    }, ensure_ascii=False)


def kanban_task_approve_handler(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    note = args.get("note", "").strip()
    claim_token = args.get("claim_token", "").strip()

    if not task_id or not claim_token:
        return json.dumps({"success": False, "error": "task_id 和 claim_token 为必填项。"})

    parent_agent = kwargs.get("parent_agent")
    official = _connect_official_for_task(task_id, args, parent_agent)
    if official is not None:
        kb, conn, row, board = official
        try:
            if not _official_claim_ok(row, claim_token):
                return _official_result(False, error="claim_token 无效或已过期。")
        finally:
            conn.close()
        from tools.kanban_tools import _handle_complete

        complete_args = {
            "task_id": task_id,
            "summary": note or "approved",
            "metadata": {"swarm_action": "approve", "approved_by": getattr(parent_agent, "name", None)},
        }
        if board:
            complete_args["board"] = board
        out = json.loads(_handle_complete(complete_args))
        if out.get("ok"):
            return _official_result(
                True,
                task_id=task_id,
                board={"slug": board, "source": "hermes_kanban"},
                message=out.get("message") or "官方 Kanban 任务已批准/完成。",
                kanban=out,
            )
        return _official_result(False, error=out.get("error") or json.dumps(out, ensure_ascii=False), kanban=out)

    try:
        conn, row, board_id = _resolve_task_db(task_id, args, parent_agent)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    if not _cas_handoff(conn, task_id, claim_token):
        conn.close()
        return json.dumps({"success": False, "error": "claim_token 无效或已过期。"})

    if row["status"] != "in_review":
        conn.close()
        return json.dumps({"success": False, "error": f"任务状态为 '{row['status']}'，无法批准。只有 in_review 状态的任务可以批准。"})

    evidence_errors = _read_before_conclude_errors(note)
    if evidence_errors:
        conn.close()
        return json.dumps({
            "success": False,
            "error": "Read-before-conclude 证据门禁未通过，禁止批准。",
            "failed_checks": evidence_errors,
            "fix_hint": (
                "Reviewer approve note 必须包含证据覆盖表，且不能把“未核实”表述为“未提供”。"
                "请 reject 退回补查，或在 approve note 中补充完整证据台账和三态缺口标签。"
            ),
        }, ensure_ascii=False)

    # ── DELIVERY_SPEC legal_scorecard gate (hard gate) ──
    # Before approving, validate quality via legal_scorecard.
    # Score threshold is read from DELIVERY_SPEC (default 80%).
    # This makes approval non-bypassable — like Claude Code's verification step.
    project_path = _resolve_project_path(args, kwargs.get("parent_agent"))
    if project_path:
        try:
            from ..delivery_spec import load_delivery_spec
            from hermes_cli.project_commands import legal_scorecard

            spec = load_delivery_spec(project_path)
            current_gate_name = _infer_current_gate(row, spec)
            gate_def = spec.get("gates", {}).get(current_gate_name, {})
            exit_criteria = gate_def.get("exit", [])

            # Determine min score from gate exit criteria
            min_score = 80  # default
            scorecard_required = False
            for criterion in exit_criteria:
                if criterion.startswith("legal_scorecard_min_"):
                    scorecard_required = True
                    try:
                        min_score = int(criterion.split("_")[-1])
                    except ValueError:
                        pass

            # Run scorecard if required by gate or if spec exists (belt and suspenders)
            if scorecard_required or spec.get("version", 0) > 0:
                score = legal_scorecard(project_path, strict=True)
                checks = score.get("checks", [])
                failures_list = score.get("failures", [])
                total = len(checks)
                passed_count = sum(1 for c in checks if c.get("passed"))
                score_pct = int((passed_count / total) * 100) if total > 0 else 0

                if score_pct < min_score:
                    conn.close()
                    return json.dumps({
                        "success": False,
                        "error": f"legal_scorecard 得分 {score_pct}% < {min_score}%，禁止批准。",
                        "scorecard_summary": {
                            "score_pct": score_pct,
                            "min_required": min_score,
                            "checks": checks,
                            "failures": failures_list,
                        },
                        "fix_hint": "请先修复 scorecard 中的 failures 后再调用 approve。",
                    }, ensure_ascii=False)

                if failures_list and score.get("status") == "failed":
                    conn.close()
                    return json.dumps({
                        "success": False,
                        "error": f"存在 {len(failures_list)} 个硬性失败项，禁止批准。",
                        "failures": failures_list,
                        "fix_hint": "请先修复以上失败项后再调用 approve。",
                    }, ensure_ascii=False)

        except ImportError:
            # If delivery_spec or project_commands not importable, fall through
            pass
        except Exception:
            # Unexpected errors should not block (preserve existing behavior)
            pass

    now = _now()
    history = json.loads(row["handoff_history_json"])
    actor = getattr(parent_agent, "name", "reviewer")

    history.append({
        "action": "approved",
        "by": actor,
        "note": note,
        "timestamp": now,
    })

    # Advance to next gate
    gates = json.loads(row["gates_json"])
    next_gate = None
    next_idx = row["gate_index"] + 1
    if next_idx < len(gates):
        next_gate = gates[next_idx]

    if next_gate:
        next_assignee = next_gate.get("target_pool", "")
        conn.execute(
            """UPDATE tasks SET status = 'in_review', gate_index = ?,
               assignee = ?, handoff_history_json = ?, claim_lock = NULL,
               claim_expires = NULL, updated_at = ? WHERE id = ?""",
            (next_idx, next_assignee, json.dumps(history, ensure_ascii=False), now, task_id),
        )
        _log_event(conn, task_id, "approved", actor=actor,
                   payload={"note": note, "next_gate": next_gate, "next_assignee": next_assignee})
        msg = f"已批准（门禁 {row['gate_index'] + 1}/{len(gates)}）。流转到下一门禁: {next_assignee}。"
    else:
        # All gates passed — final approval
        conn.execute(
            """UPDATE tasks SET status = 'approved', handoff_history_json = ?,
               claim_lock = NULL, claim_expires = NULL, completed_at = ?,
               updated_at = ? WHERE id = ?""",
            (json.dumps(history, ensure_ascii=False), now, now, task_id),
        )
        _log_event(conn, task_id, "approved", actor=actor,
                   payload={"note": note, "final": True})
        msg = f"全部门禁已通过（共 {len(gates)} 步）。任务最终批准！"

    updated = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()

    return json.dumps({
        "success": True,
        "task": _task_to_dict(updated),
        "message": msg,
    }, ensure_ascii=False)


def kanban_task_reject_handler(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    reason = args.get("reason", "").strip()
    claim_token = args.get("claim_token", "").strip()

    if not task_id or not reason or not claim_token:
        return json.dumps({"success": False, "error": "task_id、reason 和 claim_token 为必填项。"})

    parent_agent = kwargs.get("parent_agent")
    official = _connect_official_for_task(task_id, args, parent_agent)
    if official is not None:
        kb, conn, row, board = official
        try:
            if not _official_claim_ok(row, claim_token):
                return _official_result(False, error="claim_token 无效或已过期。")
        finally:
            conn.close()
        from tools.kanban_tools import _handle_block

        block_args = {"task_id": task_id, "reason": reason}
        if board:
            block_args["board"] = board
        out = json.loads(_handle_block(block_args))
        if out.get("ok"):
            return _official_result(
                True,
                task_id=task_id,
                board={"slug": board, "source": "hermes_kanban"},
                message=f"已拒绝/阻塞任务。理由: {reason}",
                kanban=out,
            )
        return _official_result(False, error=out.get("error") or json.dumps(out, ensure_ascii=False), kanban=out)

    try:
        conn, row, board_id = _resolve_task_db(task_id, args, parent_agent)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    if not _cas_handoff(conn, task_id, claim_token):
        conn.close()
        return json.dumps({"success": False, "error": "claim_token 无效或已过期。"})

    if row["status"] != "in_review":
        conn.close()
        return json.dumps({"success": False, "error": f"任务状态为 '{row['status']}'，无法拒绝。只有 in_review 状态的任务可以拒绝。"})

    now = _now()
    history = json.loads(row["handoff_history_json"])
    actor = getattr(parent_agent, "name", "reviewer")

    # Find the previous assignee (the drafter/worker)
    prev_assignee = None
    for h in reversed(history):
        if h.get("action") == "handoff":
            prev_assignee = h.get("from")
            break

    history.append({
        "action": "rejected",
        "by": actor,
        "reason": reason,
        "timestamp": now,
    })

    conn.execute(
        """UPDATE tasks SET status = 'rejected', assignee = ?,
           handoff_history_json = ?, claim_lock = NULL, claim_expires = NULL,
           updated_at = ? WHERE id = ?""",
        (prev_assignee, json.dumps(history, ensure_ascii=False), now, task_id),
    )
    _log_event(conn, task_id, "rejected", actor=actor,
               payload={"reason": reason, "return_to": prev_assignee})

    updated = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()

    return json.dumps({
        "success": True,
        "task": _task_to_dict(updated),
        "message": f"已拒绝并退回给 {prev_assignee or '上一环节'}。理由: {reason}",
    }, ensure_ascii=False)


def kanban_task_revise_handler(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    note = args.get("note", "").strip()
    claim_token = args.get("claim_token", "").strip()

    if not task_id or not claim_token:
        return json.dumps({"success": False, "error": "task_id 和 claim_token 为必填项。"})

    parent_agent = kwargs.get("parent_agent")
    official = _connect_official_for_task(task_id, args, parent_agent)
    if official is not None:
        kb, conn, row, board = official
        try:
            if not _official_claim_ok(row, claim_token):
                return _official_result(False, error="claim_token 无效或已过期。")
            ok = kb.block_task(conn, task_id, reason=f"revise requested: {note}")
            if not ok:
                return _official_result(False, error=f"任务状态为 '{row.status}'，无法重新提交/阻塞。")
            return _official_result(
                True,
                task_id=task_id,
                board={"slug": board, "source": "hermes_kanban"},
                message="官方 Kanban 任务已记录 revise 请求并转为 blocked，等待重新分派/人工处理。",
            )
        finally:
            conn.close()

    try:
        conn, row, board_id = _resolve_task_db(task_id, args, parent_agent)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    if not _cas_handoff(conn, task_id, claim_token):
        conn.close()
        return json.dumps({"success": False, "error": "claim_token 无效或已过期。"})

    if row["status"] != "rejected":
        conn.close()
        return json.dumps({"success": False, "error": f"任务状态为 '{row['status']}'，无法重新提交。只有 rejected 状态的任务可以 revise。"})

    now = _now()
    history = json.loads(row["handoff_history_json"])
    actor = getattr(parent_agent, "name", "drafter")

    # Find the reviewer to handoff back to
    reviewer = None
    for h in reversed(history):
        if h.get("action") == "rejected":
            reviewer = h.get("by")
            break

    history.append({
        "action": "revised",
        "by": actor,
        "note": note,
        "timestamp": now,
    })

    conn.execute(
        """UPDATE tasks SET status = 'in_review', assignee = ?,
           handoff_history_json = ?, claim_lock = NULL, claim_expires = NULL,
           updated_at = ? WHERE id = ?""",
        (reviewer, json.dumps(history, ensure_ascii=False), now, task_id),
    )
    _log_event(conn, task_id, "revised", actor=actor,
               payload={"note": note, "handoff_to": reviewer})

    updated = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()

    return json.dumps({
        "success": True,
        "task": _task_to_dict(updated),
        "message": f"修改完成，已重新提交给 {reviewer or '审核者'}。",
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRATION — at least one top-level register() for AST discovery
# ═══════════════════════════════════════════════════════════════════════════════

from tools.registry import registry, tool_error, tool_result  # noqa: E402


def _check_kanban(**kwargs) -> bool:
    """Kanban toolset is always available — no external deps beyond stdlib."""
    return True


# Coordinator tools
registry.register(
    name="swarm_board_create",
    toolset="kanban_swarm",
    schema=KANBAN_BOARD_CREATE_SCHEMA,
    handler=lambda args, **kw: kanban_board_create_handler(args, **kw),
    check_fn=_check_kanban,
    description="为项目创建 Kanban Board",
    emoji="📋",
)

registry.register(
    name="swarm_board_info",
    toolset="kanban_swarm",
    schema=KANBAN_BOARD_INFO_SCHEMA,
    handler=lambda args, **kw: kanban_board_info_handler(args, **kw),
    check_fn=_check_kanban,
    description="查看 Board 信息与列状态",
    emoji="ℹ️",
)

registry.register(
    name="swarm_task_create",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_CREATE_SCHEMA,
    handler=lambda args, **kw: kanban_task_create_handler(args, **kw),
    check_fn=_check_kanban,
    description="创建带门禁链的 Kanban 任务",
    emoji="➕",
)

registry.register(
    name="swarm_task_assign",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_ASSIGN_SCHEMA,
    handler=lambda args, **kw: kanban_task_assign_handler(args, **kw),
    check_fn=_check_kanban,
    description="分配/重新分配任务",
    emoji="👤",
)

registry.register(
    name="swarm_task_wait",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_WAIT_SCHEMA,
    handler=lambda args, **kw: kanban_task_wait_handler(args, **kw),
    check_fn=_check_kanban,
    description="[DEPRECATED] 等待任务完成（阻塞轮询）",
    emoji="⏳",
)

registry.register(
    name="swarm_workflow_compile",
    toolset="kanban_swarm",
    schema=KANBAN_WORKFLOW_COMPILE_SCHEMA,
    handler=lambda args, **kw: kanban_workflow_compile_handler(args, **kw),
    check_fn=_check_kanban,
    description="从 YAML 编译工作流为 Kanban 任务",
    emoji="⚙️",
)

registry.register(
    name="swarm_board_status",
    toolset="kanban_swarm",
    schema=KANBAN_BOARD_STATUS_SCHEMA,
    handler=lambda args, **kw: kanban_board_status_handler(args, **kw),
    check_fn=_check_kanban,
    description="获取 Board 完整状态概览",
    emoji="📊",
)

# New Coordinator tools — Plan A: Tool Layer Hardening
registry.register(
    name="swarm_task_poll",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_POLL_SCHEMA,
    handler=lambda args, **kw: kanban_task_poll_handler(args, **kw),
    check_fn=_check_kanban,
    description="非阻塞查询多个任务状态",
    emoji="📡",
)

registry.register(
    name="swarm_task_collect",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_COLLECT_SCHEMA,
    handler=lambda args, **kw: kanban_task_collect_handler(args, **kw),
    check_fn=_check_kanban,
    description="收集已完成任务的 Worker 产出",
    emoji="📦",
)

registry.register(
    name="swarm_dispatch_now",
    toolset="kanban_swarm",
    schema=KANBAN_DISPATCH_NOW_SCHEMA,
    handler=lambda args, **kw: kanban_dispatch_now_handler(args, **kw),
    check_fn=_check_kanban,
    description="手动运行一次 Kanban dispatcher",
    emoji="🚦",
)

# Worker tools
registry.register(
    name="swarm_task_claim",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_CLAIM_SCHEMA,
    handler=lambda args, **kw: kanban_task_claim_handler(args, **kw),
    check_fn=_check_kanban,
    description="Worker 认领任务（CAS 原子操作）",
    emoji="✋",
)

registry.register(
    name="swarm_task_read",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_READ_SCHEMA,
    handler=lambda args, **kw: kanban_task_read_handler(args, **kw),
    check_fn=_check_kanban,
    description="Worker 读取任务完整详情",
    emoji="📖",
)

registry.register(
    name="swarm_task_handoff",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_HANDOFF_SCHEMA,
    handler=lambda args, **kw: kanban_task_handoff_handler(args, **kw),
    check_fn=_check_kanban,
    description="Worker 移交任务给审核者",
    emoji="↗️",
)

registry.register(
    name="swarm_task_approve",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_APPROVE_SCHEMA,
    handler=lambda args, **kw: kanban_task_approve_handler(args, **kw),
    check_fn=_check_kanban,
    description="Reviewer 批准当前门禁",
    emoji="✅",
)

registry.register(
    name="swarm_task_reject",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_REJECT_SCHEMA,
    handler=lambda args, **kw: kanban_task_reject_handler(args, **kw),
    check_fn=_check_kanban,
    description="Reviewer 拒绝并退回任务",
    emoji="❌",
)

registry.register(
    name="swarm_task_revise",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_REVISE_SCHEMA,
    handler=lambda args, **kw: kanban_task_revise_handler(args, **kw),
    check_fn=_check_kanban,
    description="Worker 修改后重新提交",
    emoji="🔧",
)

registry.register(
    name="swarm_task_progress",
    toolset="kanban_swarm",
    schema=KANBAN_TASK_PROGRESS_SCHEMA,
    handler=lambda args, **kw: kanban_task_progress_handler(args, **kw),
    check_fn=_check_kanban,
    description="Worker 发布执行中的阶段性进度（供 poll 查看）",
    emoji="📣",
)
