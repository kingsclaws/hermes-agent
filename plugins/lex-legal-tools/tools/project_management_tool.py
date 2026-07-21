#!/usr/bin/env python3
"""
Project Management Tools — native chat-available legal project lifecycle.

Five tools:
  project_init     Create a new project (dirs + scaffolding + DB)
  legal_project_list  List all registered legal projects
  project_select   Switch active project context
  project_context  Read or update project context
  project_status   Read or set project phase
"""

from __future__ import annotations

import json
import os
import hashlib
import subprocess
import time
import uuid
from pathlib import Path
import re


def _project_session_db():
    """Return the plugin-owned legal project store plus session access."""
    from .project_store import LegalProjectStore

    db_path = os.environ.get("HERMES_PROJECTS_DB_PATH", "").strip()
    if not db_path:
        try:
            from hermes_cli.profiles import get_active_profile_name
            from hermes_constants import get_default_hermes_root

            if get_active_profile_name() == "lex-master":
                db_path = str(get_default_hermes_root() / "state.db")
        except Exception:
            db_path = ""

    return LegalProjectStore(db_path=Path(db_path)) if db_path else LegalProjectStore()


# ── Schemas ──────────────────────────────────────────────────────────────────

PROJECT_INIT_SCHEMA = {
    "name": "project_init",
    "description": (
        "Create a new HPSwarm legal project. Creates the project directory with "
        "Coordinator identity (AGENTS.md), legal SOP (STANDARDS.md), sub-agent "
        "role templates, and registers it in the project database."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Project name (e.g. '弘郡二期'). Used as directory name if path not specified.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Absolute path for the project directory. If omitted, "
                    "uses current working directory / <name>."
                ),
            },
            "client": {
                "type": "string",
                "description": "Client name (e.g. '中信金资')",
            },
            "goal": {
                "type": "string",
                "description": (
                    "Project goal or task description (e.g. '起草担保合同纠纷诉状')"
                ),
            },
        },
        "required": ["name"],
    },
}

PROJECT_CREATE_SCHEMA = {
    "name": "legal_project_create",
    "description": (
        "Create/register a legal project and immediately start the native init "
        "workflow: scan source materials, write .hermes-project mirrors, and "
        "create Kanban source-digest/synthesis tasks. Use this when the user "
        "says to create a project for an existing matter directory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Project name."},
            "path": {"type": "string", "description": "Existing or new project directory."},
            "client": {"type": "string", "description": "Client name, if known."},
            "goal": {"type": "string", "description": "Matter objective or immediate task."},
            "matter_type": {"type": "string", "description": "Optional matter type, e.g. financing, M&A, amendment, DD."},
            "auto_init": {"type": "boolean", "description": "Start init workflow immediately. Default true."},
            "max_parallel_agents": {"type": "integer", "description": "Desired init worker parallelism. Default 5."},
            "max_parallel_ocr": {"type": "integer", "description": "Desired OCR parallelism. Default 2."},
            "reader_pool": {"type": "string", "description": "Assignee profile for file digest tasks. Default hpswarm-reviewer-content."},
            "synthesis_pool": {"type": "string", "description": "Assignee profile for synthesis task. Default lex-coordinator."},
            "max_core_tasks": {"type": "integer", "description": "Max core read tasks to create this run. Default 50."},
            "bind_session": {"type": "boolean", "description": "Bind the current session as the project's coordinator session. Default true."},
        },
        "required": ["name"],
    },
}

PROJECT_REGISTER_SCHEMA = {
    "name": "project_register",
    "description": (
        "Register an existing project directory without necessarily starting "
        "the init reading workflow. Use this for migration/history import or "
        "when the user says '只注册一下/先别读'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Project name."},
            "path": {"type": "string", "description": "Existing project directory."},
            "client": {"type": "string", "description": "Client name, if known."},
            "goal": {"type": "string", "description": "Matter objective."},
            "no_init": {"type": "boolean", "description": "Do not start init workflow. Default true."},
        },
        "required": ["name", "path"],
    },
}

PROJECT_INIT_START_SCHEMA = {
    "name": "project_init_start",
    "description": (
        "Start or restart the legal project initialization workflow for a "
        "registered project. Scans materials, records source inventory in "
        "state.db, mirrors outputs under .hermes-project, and creates Kanban "
        "tasks for per-file digests plus coordinator synthesis."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_name": {"type": "string", "description": "Project name or ID. Optional if a project is selected."},
            "project_path": {"type": "string", "description": "Project path for direct resolution."},
            "max_parallel_agents": {"type": "integer", "description": "Desired init worker parallelism. Default 5."},
            "max_parallel_ocr": {"type": "integer", "description": "Desired OCR parallelism. Default 2."},
            "reader_pool": {"type": "string", "description": "Assignee profile for file digest tasks. Default hpswarm-reviewer-content."},
            "synthesis_pool": {"type": "string", "description": "Assignee profile for synthesis task. Default lex-coordinator."},
            "max_core_tasks": {"type": "integer", "description": "Max core/supporting read tasks to create this run. Default 50."},
        },
        "required": [],
    },
}

PROJECT_SOURCE_DIGEST_SCHEMA = {
    "name": "project_source_digest",
    "description": (
        "Submit a structured digest for one project-init source file after "
        "reading/OCRing it. This is the required write-back step for "
        "init.source_digest Kanban tasks before kanban_complete is allowed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_name": {"type": "string", "description": "Project name or ID. Optional when source_id is globally unique."},
            "project_path": {"type": "string", "description": "Project path, if project_name is unavailable."},
            "run_id": {"type": "string", "description": "Project init run id."},
            "source_id": {"type": "string", "description": "Source id from the init task body/source inventory."},
            "source_path": {"type": "string", "description": "Absolute or project-relative source path."},
            "read_method": {"type": "string", "description": "Actual tool/method used, e.g. lex_read, lex_ocr, spreadsheet_inspection."},
            "read_coverage": {
                "type": "string",
                "enum": ["full", "partial", "failed"],
                "description": "Whether the source was read completely. Use failed only after trying the correct native tool.",
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Confidence in the extracted content.",
            },
            "summary": {"type": "string", "description": "Concise digest of the source's substantive content."},
            "evidence_ledger": {
                "type": "array",
                "description": "Concrete pages/paragraphs/sheets/sections read and relied on.",
                "items": {"type": "string"},
            },
            "key_facts": {
                "type": "array",
                "description": "Candidate project facts discovered in this source.",
                "items": {"type": "string"},
            },
            "open_questions": {
                "type": "array",
                "description": "Questions or missing information raised by this source.",
                "items": {"type": "string"},
            },
            "issues": {
                "type": "array",
                "description": "Read failures, conflicts, outdated material flags, or drafting risks.",
                "items": {"type": "string"},
            },
        },
        "required": ["read_coverage", "summary", "evidence_ledger"],
    },
}

PROJECT_LIST_SCHEMA = {
    "name": "legal_project_list",
    "description": (
        "List all registered HPSwarm legal projects. Shows name, client, "
        "status, path, and project ID for each."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": (
                    "Optional filter by status: INIT, DRAFTING, REVIEWING, "
                    "REVISING, FINAL, DELIVERED, ARCHIVED"
                ),
            },
        },
        "required": [],
    },
}

PROJECT_SELECT_SCHEMA = {
    "name": "project_select",
    "description": (
        "Select a project as the active working context. When selected, "
        "the project's AGENTS.md is loaded and project context is injected "
        "into the system prompt on subsequent turns."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Project name or ID to switch to.",
            },
        },
        "required": ["project_name"],
    },
}

PROJECT_CONTEXT_SCHEMA = {
    "name": "project_context",
    "description": (
        "Read or update the current project context. Use action='read' to view "
        "project context (metadata, goal, background). Use action='update' to "
        "modify a specific field (goal, client, notes)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Project name or ID. If omitted, uses the currently selected project.",
            },
            "action": {
                "type": "string",
                "enum": ["read", "update"],
                "description": "'read' to view context, 'update' to modify a field.",
            },
            "field": {
                "type": "string",
                "enum": ["goal", "client", "notes", "name"],
                "description": "Field to update (only for action='update').",
            },
            "value": {
                "type": "string",
                "description": "New value for the field (only for action='update').",
            },
        },
        "required": ["action"],
    },
}

PROJECT_STATUS_SCHEMA = {
    "name": "project_status",
    "description": (
        "Read or update a project's phase. Lex legal flow: "
        "CREATED → INIT_READING → PARTY_ENRICHMENT → INIT_SYNTHESIS → "
        "READY_TO_DRAFT → DRAFTING → REVIEWING → CLOSING."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Project name or ID. If omitted, uses the currently selected project.",
            },
            "status": {
                "type": "string",
                "enum": [
                    "CREATED", "INIT", "INIT_READING", "PARTY_ENRICHMENT",
                    "INIT_SYNTHESIS", "READY_TO_DRAFT", "DRAFTING",
                    "REVIEWING", "REVISING", "FINAL", "DELIVERED", "CLOSING",
                    "ARCHIVED",
                ],
                "description": "New status to set. If omitted, returns the current status.",
            },
        },
        "required": [],
    },
}

LEX_MASTER_ROUTE_SCHEMA = {
    "name": "lex_master_route",
    "description": (
        "Lex-master routing tool. Use this before doing any project-specific "
        "legal work from the lex-master profile. It lists registered projects, "
        "resolves a user request to the right project/coordinator session, and "
        "can dispatch the request to that coordinator by resuming the project "
        "session under the default/root profile. lex-master should coordinate "
        "and report routing status, not directly edit project documents."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_projects", "resolve", "dispatch", "status"],
                "description": (
                    "list_projects: show shared registry projects. resolve: find "
                    "best project/coordinator session. dispatch: asynchronously "
                    "send task to that coordinator session. status: inspect a "
                    "previous dispatch route_id."
                ),
            },
            "query": {
                "type": "string",
                "description": "Project hint or natural-language request, e.g. '魔方投资银团变更'.",
            },
            "project_id": {
                "type": "string",
                "description": "Optional exact project id. Takes precedence over query.",
            },
            "task": {
                "type": "string",
                "description": "Task/instruction to send to the project coordinator for action=dispatch.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional exact coordinator session id. If omitted, the best project session is chosen.",
            },
            "route_id": {
                "type": "string",
                "description": "Route id returned by dispatch; used by action=status.",
            },
            "async": {
                "type": "boolean",
                "description": "Dispatch asynchronously. Default true.",
            },
        },
        "required": ["action"],
    },
}


# ── Internal helpers ─────────────────────────────────────────────────────────

# Module-level cache for the currently selected project in this session.
_active_project_name: str | None = None
_active_project_path: str | None = None


def _ensure_legal_harness_files(
    project_dir: str, *, project_name: str = "", client: str = "", goal: str = "",
) -> dict:
    """Create project bootstrap files without depending on Hermes core."""
    root = Path(project_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    sidecar = root / ".hermes-project"
    memories = sidecar / "memories"
    memories.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    created: list[str] = []

    meta_path = sidecar / "project-meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    meta.update({
        "name": project_name or meta.get("name") or root.name,
        "client": client if client != "" else meta.get("client", ""),
        "goal": goal if goal != "" else meta.get("goal", ""),
        "cwd": str(root),
        "management_dir": str(root),
        "updated": now,
        "toolsets": ["lexitool"],
    })
    meta.setdefault("created", now)
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )

    defaults = {
        root / "AGENTS.md": (
            f"# {meta['name']} Coordinator\n\n"
            "Use Lex legal tools and Kanban review gates. Read every source "
            "before concluding, preserve Track Changes, and verify deliverables.\n"
        ),
        root / "STANDARDS.md": (
            "# Legal Work Standards\n\n"
            "1. Lock the authoritative source and scope before editing.\n"
            "2. Maintain a file evidence ledger; filenames are not evidence.\n"
            "3. Preserve Track Changes and run delivery verification.\n"
            "4. Do not report completion while evidence or review gates are open.\n"
        ),
        sidecar / "project-context.md": (
            f"# Project Context: {meta['name']}\n\n"
            f"- Client: {meta.get('client') or 'TBD'}\n"
            f"- Goal: {meta.get('goal') or 'TBD'}\n"
            f"- Path: `{root}`\n"
        ),
        sidecar / "project-facts.json": json.dumps(
            {"facts": [], "updated_at": now}, ensure_ascii=False, indent=2,
        ) + "\n",
        memories / "project_facts.md": "# Project Facts\n\nNo confirmed facts recorded.\n",
    }
    for path, content in defaults.items():
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            created.append(str(path))
    return {"ok": True, "project_dir": str(root), "created": created}


def _resolve_cwd() -> str:
    """Best-effort working directory for path resolution."""
    return os.environ.get("TERMINAL_CWD", os.getcwd())


def _resolve_project(name_or_id: str):
    """Look up a project by name or ID. Returns dict or None."""
    db = _project_session_db()
    return db.get_project(name_or_id)


def _shared_project_db_path() -> Path:
    """Root/default project registry used by lex-master cross-profile routing."""
    configured = os.environ.get("HERMES_PROJECTS_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    try:
        from hermes_constants import get_default_hermes_root

        return get_default_hermes_root() / "state.db"
    except Exception:
        return Path("/root/.hermes/state.db")


def _ensure_coordinator_column(db) -> None:
    """Install the plugin-owned session binding column when first needed."""
    def migrate(conn):
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "coordinator_for" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN coordinator_for TEXT")

    db._execute_write(migrate)


def _route_dir() -> Path:
    try:
        from hermes_constants import get_hermes_home

        base = get_hermes_home()
    except Exception:
        base = Path.home() / ".hermes"
    path = base / "master-routes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _norm_text(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def _score_project(project: dict, query: str) -> int:
    q = _norm_text(query)
    if not q:
        return 0
    fields = [
        project.get("id"),
        project.get("name"),
        project.get("client"),
        project.get("goal"),
        project.get("path"),
        project.get("cwd"),
        project.get("notes"),
    ]
    score = 0
    for field in fields:
        text = _norm_text(field)
        if not text:
            continue
        if q == text:
            score += 100
        elif q in text:
            score += 40
        elif text in q and len(text) >= 2:
            score += 35
        else:
            for token in [t for t in q.replace("/", " ").split() if len(t) >= 2]:
                if token in text:
                    score += 5
    return score


def _session_last_activity(row: dict) -> float:
    for key in ("ended_at", "started_at"):
        try:
            value = row.get(key)
            if value is not None:
                return float(value)
        except Exception:
            pass
    return 0.0


def _project_candidates(db, query: str = "", project_id: str = "") -> list[dict]:
    projects = db.list_projects()
    if project_id:
        return [p for p in projects if p.get("id") == project_id]
    if not query:
        return sorted(projects, key=lambda p: p.get("updated_at") or p.get("created_at") or 0, reverse=True)
    scored = [(p, _score_project(p, query)) for p in projects]
    return [p for p, score in sorted(scored, key=lambda item: item[1], reverse=True) if score > 0]


def _session_score(session: dict, project: dict) -> int:
    score = 0
    if session.get("project_id") == project.get("id"):
        score += 100
    project_path = str(project.get("path") or project.get("cwd") or "")
    session_cwd = str(session.get("project_cwd") or "")
    if project_path and session_cwd and (session_cwd.startswith(project_path) or project_path.startswith(session_cwd)):
        score += 50
    title = _norm_text(session.get("title"))
    pname = _norm_text(project.get("name"))
    if pname and title and pname in title:
        score += 30
    if session.get("parent_session_id"):
        score -= 20
    if session.get("end_reason") in {"compression", "resumed_other"}:
        score -= 10
    score += min(int(_session_last_activity(session) // 1_000_000), 9999)
    return score


def _coordinator_title(project_name: str) -> str:
    """Canonical title for a project's coordinator session."""
    return f"[{project_name}] coordinator"


def _find_coordinator_session(db, project: dict, session_id: str = "") -> dict | None:
    if session_id:
        try:
            return db.get_session(session_id)
        except Exception:
            return None

    project_id = project.get("id", "")

    # Primary: sessions.coordinator_for == project_id
    # This column survives compression (propagated to new session automatically).
    if project_id:
        try:
            _ensure_coordinator_column(db)
            with db._lock:
                row = db._conn.execute(
                    "SELECT id FROM sessions WHERE coordinator_for = ? "
                    "AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
            if row:
                return db.get_session(row[0])
        except Exception:
            pass

    # Fallback: heuristic scoring (legacy, for projects created before coordinator_for)
    sessions = db.list_sessions_rich(limit=500)
    scored = [(s, _session_score(s, project)) for s in sessions]
    scored = [(s, score) for s, score in scored if score > 0]
    if not scored:
        return None
    return sorted(scored, key=lambda item: item[1], reverse=True)[0][0]


def _bind_coordinator_session(project_id: str | None, session_id: str, project_name: str = "") -> bool:
    """Bind session to project via coordinator_for column (survives compression)."""
    if not project_id or not session_id:
        return False
    try:
        db = _project_session_db()
        _ensure_coordinator_column(db)

        def bind(conn):
            cursor = conn.execute(
                "UPDATE sessions SET coordinator_for = ? WHERE id = ?",
                (project_id, session_id),
            )
            return cursor.rowcount > 0

        return bool(db._execute_write(bind))
    except Exception:
        return False


def _dispatch_to_session(*, session_id: str, task: str, project: dict, route_id: str, async_mode: bool) -> dict:
    route_path = _route_dir() / f"{route_id}.json"
    log_path = _route_dir() / f"{route_id}.log"
    prompt = (
        "[lex-master route]\n"
        f"Project: {project.get('name')} ({project.get('id')})\n"
        f"Project path: {project.get('path') or project.get('cwd') or ''}\n"
        f"Route ID: {route_id}\n"
        "You are the project coordinator session for this matter. Do not treat "
        "this as a fresh unrelated request. Continue the existing project context, "
        "use project facts/kanban/harness as appropriate.\n\n"
        "## Project binding — do this first\n\n"
        f'Call project_bind_session(project_name="{project.get("name")}") '
        "to register yourself as the coordinator for this project. "
        "Do NOT use memory to record project binding — it is unreliable.\n\n"
        "## Handoff protocol — read carefully\n\n"
        "After ALL kanban workflow tasks have completed (all gates passed, all "
        "reviewer feedback addressed, deliverable ready), you MUST call:\n\n"
        f'    lex_master_route(action="report", route_id="{route_id}",\n'
        '        status="done"|"blocked"|"failed",\n'
        '        summary="One-sentence what was accomplished.",\n'
        '        details="Full findings / deliverables / file paths.")\n\n'
        "IMPORTANT: Do NOT try to respond directly to this message with a final "
        "answer. The legal delivery gate will block direct responses that lack "
        "evidence coverage details. Instead, ALWAYS use lex_master_route(action="
        '"report") to report back — this bypasses the delivery gate.\n\n'
        "Call it ONCE when the full workflow finishes, not after each sub-task.\n\n"
        f"User request from lex-master:\n{task.strip()}\n"
    )
    cmd = ["hermes", "-p", "default", "--resume", session_id, "-z", prompt]
    record = {
        "route_id": route_id,
        "created_at": time.time(),
        "project_id": project.get("id"),
        "project_name": project.get("name"),
        "session_id": session_id,
        "task": task,
        "command": cmd,
        "log_path": str(log_path),
        "status": "starting",
    }
    route_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Update project-session binding
    _bind_coordinator_session(project.get("id"), session_id, project.get("name", ""))

    if async_mode:
        with log_path.open("ab") as log:
            proc = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env={**os.environ, "HERMES_PROJECTS_DB_PATH": str(_shared_project_db_path())},
            )
        record.update({"status": "dispatched", "pid": proc.pid})
        route_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return record

    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=1800,
        env={**os.environ, "HERMES_PROJECTS_DB_PATH": str(_shared_project_db_path())},
    )
    log_path.write_text((completed.stdout or "") + (completed.stderr or ""), encoding="utf-8")
    record.update({"status": "completed" if completed.returncode == 0 else "failed", "returncode": completed.returncode})
    route_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def resolve_selected_project(parent_agent=None, session_id: str | None = None):
    """Return the currently selected project dict when one can be inferred.

    This is intentionally read-only and best-effort. It lets orchestration tools
    resolve project context without depending on CLI-only state.
    """
    db = _project_session_db()

    if _active_project_name:
        project = db.get_project(_active_project_name)
        if project:
            return project

    for attr in ("_selected_project_id", "selected_project_id"):
        value = getattr(parent_agent, attr, None) if parent_agent is not None else None
        if value:
            project = db.get_project(str(value))
            if project:
                return project

    for attr in ("_selected_project_cwd", "selected_project_cwd"):
        value = getattr(parent_agent, attr, None) if parent_agent is not None else None
        if value:
            project = db.get_project_by_path(str(value))
            if project:
                return project

    if session_id:
        try:
            session = db.get_session(session_id)
        except Exception:
            session = None
        if session:
            project_id = session.get("project_id")
            if project_id:
                project = db.get_project(project_id)
                if project:
                    return project
            project_cwd = session.get("project_cwd")
            if project_cwd:
                project = db.get_project_by_path(project_cwd)
                if project:
                    return project

    if _active_project_path:
        project = db.get_project_by_path(_active_project_path)
        if project:
            return project

    return None


def build_selected_project_prompt_context(parent_agent=None, session_id: str | None = None) -> str:
    """Return a compact prompt block for the active/bound legal project."""
    project = resolve_selected_project(parent_agent, session_id=session_id)
    if not project:
        return ""

    project_path = str(project.get("path") or project.get("cwd") or "").strip()
    root = Path(project_path).expanduser() if project_path else None
    sidecar = root / ".hermes-project" if root else None

    lines = [
        "[Active legal project context]",
        f"Project: {project.get('name') or project.get('id') or ''}",
    ]
    if project.get("client"):
        lines.append(f"Client: {project.get('client')}")
    if project.get("status"):
        lines.append(f"Status: {project.get('status')}")
    if project_path:
        lines.append(f"Directory: {project_path}")
    if project.get("goal"):
        lines.append(f"Goal: {_compact_text(str(project.get('goal')), 1200)}")
    if project.get("notes"):
        lines.append(f"Notes: {_compact_text(str(project.get('notes')), 1200)}")

    if sidecar and sidecar.exists():
        artifact_paths = {
            "project_context": sidecar / "project-context.md",
            "project_facts": sidecar / "memories" / "project_facts.md",
            "init_impression": sidecar / "init-impression.md",
            "missing_info_list": sidecar / "missing-info-list.md",
            "source_inventory": sidecar / "source-inventory.md",
        }
        existing = [f"{name}={path}" for name, path in artifact_paths.items() if path.exists()]
        if existing:
            lines.extend(["", "Live matter artifacts:", *existing])

        for label, rel in (
            ("Project context snapshot", artifact_paths["project_context"]),
            ("Project facts snapshot", artifact_paths["project_facts"]),
            ("Init impression", artifact_paths["init_impression"]),
            ("Missing info list", artifact_paths["missing_info_list"]),
        ):
            text = _read_prompt_excerpt(rel, limit=5000)
            if text:
                lines.extend(["", f"## {label}", text])

        source_summary = _source_inventory_summary(artifact_paths["source_inventory"])
        if source_summary:
            lines.extend(["", "## Source inventory summary", source_summary])

    lines.extend(
        [
            "",
            "Project-aware session rules:",
            "1. Treat this matter as the default project unless the user explicitly switches projects.",
            "2. Use project_facts as the living fact ledger and keep it updated when facts are confirmed, corrected, or superseded.",
            "3. Treat init artifacts as live working files, not archival notes.",
            "4. Before legal drafting/review, reconcile the task against project facts, init impression, and missing-info list.",
        ]
    )
    return "\n".join(line for line in lines if line is not None).strip()


def _read_prompt_excerpt(path: Path, *, limit: int = 5000) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        return _compact_text(path.read_text(encoding="utf-8", errors="replace"), limit)
    except Exception:
        return ""


def _compact_text(text: str, limit: int) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _source_inventory_summary(path: Path) -> str:
    raw = _read_prompt_excerpt(path, limit=4000)
    if not raw:
        return ""
    lines = [line.rstrip() for line in raw.splitlines() if line.strip()]
    if len(lines) <= 18:
        return "\n".join(lines)
    head = lines[:10]
    tail = lines[-6:]
    return "\n".join(head + ["...", *tail])


def _resolve_project_path(name_or_id: str, given_path: str | None) -> str:
    """Resolve an absolute path for a new project."""
    if given_path and Path(given_path).is_absolute():
        return given_path
    base = _resolve_cwd()
    if given_path:
        return str(Path(base) / given_path)
    return str(Path(base) / (name_or_id))


_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", "node_modules",
    "kanban", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}
_SKIP_SUFFIXES = {".tmp", ".bak", ".ds_store", ".lock"}
_SUPPORTED_SOURCE_EXTS = {
    ".docx", ".doc", ".pdf", ".xlsx", ".xls", ".csv", ".md", ".txt", ".html",
    ".htm", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".zip", ".rar",
    ".7z",
}
_CORE_KEYWORDS = {
    "合同", "协议", "补充协议", "贷款", "抵押", "质押", "保证", "担保",
    "term sheet", "termsheet", "ts", "批复", "授信", "额度", "会议纪要",
    "营业执照", "章程", "身份证", "授权", "股权", "产权", "产证", "评估",
    "反馈", "修订", "清单", "issue", "sop", "项目概述", "说明",
}
_PARTY_KEYWORDS = {"营业执照", "章程", "身份证", "授权", "法定代表", "股东", "工商"}
_ASSET_KEYWORDS = {"产权", "产证", "抵押", "质押", "担保", "评估", "资产"}
_TERM_KEYWORDS = {"term sheet", "termsheet", "ts", "批复", "授信", "额度", "会议纪要"}
_FEEDBACK_KEYWORDS = {"反馈", "修订", "清单", "issue", "bug-report", "sop"}


def _source_id(project_id: str, rel_path: str) -> str:
    digest = hashlib.sha1(f"{project_id}:{rel_path}".encode("utf-8")).hexdigest()[:16]
    return f"src_{digest}"


def _read_method_for_ext(ext: str, name: str) -> str:
    ext = ext.lower()
    if ext in {".docx", ".doc"}:
        return "lex_read"
    if ext == ".pdf":
        return "pdf_text_then_lex_ocr"
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        return "lex_ocr"
    if ext in {".xlsx", ".xls", ".csv"}:
        return "spreadsheet_inspection"
    if ext in {".md", ".txt", ".html", ".htm"}:
        return "read_file"
    if ext in {".zip", ".rar", ".7z"}:
        return "archive_extract_then_inventory"
    return "manual"


def _classify_source(path: Path, rel_path: str) -> dict:
    lower = rel_path.lower()
    ext = path.suffix.lower()
    keyword_hits = [kw for kw in _CORE_KEYWORDS if kw in lower]
    file_type = "other"
    if any(kw in lower for kw in _PARTY_KEYWORDS):
        file_type = "party_material"
    elif any(kw in lower for kw in _ASSET_KEYWORDS):
        file_type = "security_or_asset_material"
    elif any(kw in lower for kw in _TERM_KEYWORDS):
        file_type = "commercial_terms_or_approval"
    elif any(kw in lower for kw in _FEEDBACK_KEYWORDS):
        file_type = "prior_version_or_feedback"
    elif ext in {".docx", ".doc"} or "合同" in lower or "协议" in lower:
        file_type = "transaction_document"
    elif ext in {".md", ".html", ".htm", ".txt"}:
        file_type = "project_note"
    elif ext in {".zip", ".rar", ".7z"}:
        file_type = "archive"

    priority = "supporting"
    must_read = False
    reason = "supported material"
    if keyword_hits:
        priority = "core"
        must_read = True
        reason = "matched core legal-material keywords: " + ", ".join(sorted(keyword_hits)[:6])
    elif ext in {".zip", ".rar", ".7z"}:
        priority = "supporting"
        reason = "archive; should be extracted/triaged if relevant"
    elif ext not in _SUPPORTED_SOURCE_EXTS:
        priority = "ignored"
        reason = "unsupported extension"

    return {
        "file_type": file_type,
        "priority": priority,
        "reason": reason,
        "must_read_before_init": must_read,
        "read_method": _read_method_for_ext(ext, path.name),
    }


def _scan_project_sources(project_id: str, project_path: str, run_id: str) -> list[dict]:
    root = Path(project_path).expanduser().resolve()
    sources: list[dict] = []
    if not root.is_dir():
        return sources
    for path in sorted(root.rglob("*")):
        try:
            if not path.is_file():
                continue
            rel = path.relative_to(root)
        except Exception:
            continue
        parts = set(rel.parts)
        if parts & _SKIP_DIRS:
            continue
        if rel.parts and rel.parts[0] == ".hermes-project":
            # The sidecar is generated output. Avoid recursively treating
            # init mirrors as new source materials.
            continue
        if path.name.startswith("~$") or path.name.lower() in {".ds_store", "thumbs.db"}:
            continue
        ext = path.suffix.lower()
        if ext in _SKIP_SUFFIXES:
            continue
        if ext and ext not in _SUPPORTED_SOURCE_EXTS:
            continue
        rel_path = rel.as_posix()
        cls = _classify_source(path, rel_path)
        try:
            stat = path.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            size = 0
            mtime = 0.0
        sources.append({
            "id": _source_id(project_id, rel_path),
            "run_id": run_id,
            "path": str(path),
            "rel_path": rel_path,
            "file_name": path.name,
            "ext": ext,
            "size_bytes": size,
            "mtime": mtime,
            "sha1": "",
            "read_status": "pending" if cls["priority"] != "ignored" else "ignored",
            **cls,
        })
    return sources


def _write_project_init_mirrors(project: dict, run_id: str, sources: list[dict]) -> dict:
    root = Path(project.get("path") or project.get("cwd") or "").expanduser().resolve()
    sidecar = root / ".hermes-project"
    sidecar.mkdir(parents=True, exist_ok=True)
    (sidecar / "source-digests").mkdir(exist_ok=True)
    (sidecar / "ocr").mkdir(exist_ok=True)
    (sidecar / "facts").mkdir(exist_ok=True)
    (sidecar / "workflows").mkdir(exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    profile_md = sidecar / "project-profile.md"
    if not profile_md.is_file():
        profile_md.write_text(
            "\n".join([
                f"# {project.get('name') or root.name}",
                "",
                f"- Project ID: `{project.get('id')}`",
                f"- Client: {project.get('client') or 'TBD'}",
                f"- Goal: {project.get('goal') or 'TBD'}",
                f"- Status: {project.get('status') or 'CREATED'}",
                f"- Path: `{root}`",
                f"- Created/updated: {now}",
                "",
                "## Matter Profile",
                "",
                "<!-- Coordinator maintains this during init. -->",
            ]) + "\n",
            encoding="utf-8",
        )

    inventory_path = Path(_write_project_source_inventory_mirror(project, run_id, sources))

    impression_path = sidecar / "init-impression.md"
    if not impression_path.is_file():
        impression_path.write_text(
            "\n".join([
                "# Project Init Impression",
                "",
                f"> Init run `{run_id}` has started. Coordinator synthesis should update this file after source digests complete.",
                "",
                "## 1. Executive Summary",
                "",
                "TBD",
                "",
                "## 2. Transaction Structure",
                "",
                "TBD",
                "",
                "## 3. Parties",
                "",
                "TBD",
                "",
                "## 4. Core Commercial Terms",
                "",
                "TBD",
                "",
                "## 5. Document Set",
                "",
                "TBD",
                "",
                "## 6. Source Evidence",
                "",
                "TBD",
                "",
                "## 7. Drafting Implications",
                "",
                "TBD",
                "",
                "## 8. Risk Flags",
                "",
                "TBD",
                "",
                "## 9. Open Questions",
                "",
                "TBD",
                "",
                "## 10. Recommended Next Workflow",
                "",
                "TBD",
            ]) + "\n",
            encoding="utf-8",
        )

    missing_path = sidecar / "missing-info-list.md"
    if not missing_path.is_file():
        missing_path.write_text(
            "# Missing Info List\n\n<!-- Coordinator records missing core materials / failed reads / conflicts here. -->\n",
            encoding="utf-8",
        )

    run_log = sidecar / "workflows" / f"init-run-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.md"
    run_log.write_text(
        f"# Init Run {run_id}\n\n"
        f"- Started: {now}\n"
        f"- Project: {project.get('name')} ({project.get('id')})\n"
        f"- Source count: {len(sources)}\n"
        f"- Core count: {sum(1 for s in sources if s.get('priority') == 'core')}\n",
        encoding="utf-8",
    )
    return {
        "sidecar": str(sidecar),
        "source_inventory": str(inventory_path),
        "init_impression": str(impression_path),
        "missing_info_list": str(missing_path),
        "run_log": str(run_log),
    }


def _write_project_source_inventory_mirror(project: dict, run_id: str, sources: list[dict]) -> str:
    root = Path(project.get("path") or project.get("cwd") or "").expanduser().resolve()
    sidecar = root / ".hermes-project"
    sidecar.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    priority_order = {"core": 0, "supporting": 1, "backlog": 2, "ignored": 3}
    sorted_sources = sorted(
        sources,
        key=lambda s: (priority_order.get(s.get("priority"), 9), s.get("rel_path", "")),
    )
    lines = [
        "# Source Inventory",
        "",
        f"- Init run: `{run_id}`",
        f"- Updated: {now}",
        f"- Total files: {len(sorted_sources)}",
        f"- Core files: {sum(1 for s in sorted_sources if s.get('priority') == 'core')}",
        f"- Digested files: {sum(1 for s in sorted_sources if s.get('read_status') == 'digested')}",
        "",
        "| Priority | Must Read | Type | Read Method | Status | Source ID | File | Reason |",
        "|----------|-----------|------|-------------|--------|-----------|------|--------|",
    ]
    for src in sorted_sources:
        lines.append(
            "| {priority} | {must} | {typ} | {method} | {status} | `{sid}` | `{file}` | {reason} |".format(
                priority=src.get("priority", ""),
                must="yes" if src.get("must_read_before_init") else "no",
                typ=src.get("file_type", ""),
                method=src.get("read_method", ""),
                status=src.get("read_status", ""),
                sid=src.get("id", ""),
                file=src.get("rel_path", "").replace("|", "\\|"),
                reason=str(src.get("reason", "")).replace("|", "\\|"),
            )
        )
    inventory_path = sidecar / "source-inventory.md"
    inventory_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(inventory_path)


def _safe_digest_filename(rel_path: str, source_id: str) -> str:
    stem = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in rel_path)
    stem = stem.strip(" ._") or source_id
    if len(stem) > 120:
        stem = stem[:100].rstrip(" ._-") + "-" + hashlib.sha1(rel_path.encode()).hexdigest()[:10]
    return f"{stem}.source-digest.md"


def _write_project_source_digest_mirror(
    project: dict,
    source: dict,
    digest_id: str,
    digest: dict,
) -> str:
    root = Path(project.get("path") or project.get("cwd") or "").expanduser().resolve()
    digests_dir = root / ".hermes-project" / "source-digests"
    digests_dir.mkdir(parents=True, exist_ok=True)
    out_path = digests_dir / _safe_digest_filename(source.get("rel_path", ""), source.get("id", "source"))
    evidence = digest.get("evidence_ledger") or []
    facts = digest.get("key_facts") or []
    questions = digest.get("open_questions") or []
    issues = digest.get("issues") or []
    lines = [
        f"# Source Digest: {source.get('rel_path')}",
        "",
        f"- Digest ID: `{digest_id}`",
        f"- Source ID: `{source.get('id')}`",
        f"- Init run: `{source.get('run_id') or digest.get('run_id') or ''}`",
        f"- Read method: {digest.get('read_method') or source.get('read_method') or ''}",
        f"- Coverage: {digest.get('read_coverage') or ''}",
        f"- Confidence: {digest.get('confidence') or ''}",
        "",
        "## Summary",
        "",
        str(digest.get("summary") or "").strip(),
        "",
        "## File Evidence Ledger",
        "",
    ]
    lines.extend([f"- {item}" for item in evidence] or ["- TBD"])
    lines.extend(["", "## Candidate Project Facts", ""])
    lines.extend([f"- {item}" for item in facts] or ["- None recorded"])
    lines.extend(["", "## Open Questions", ""])
    lines.extend([f"- {item}" for item in questions] or ["- None recorded"])
    lines.extend(["", "## Issues / Conflicts", ""])
    lines.extend([f"- {item}" for item in issues] or ["- None recorded"])
    lines.extend(["", "## Raw Digest JSON", "", "```json", json.dumps(digest, ensure_ascii=False, indent=2), "```", ""])
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)


def _sync_source_digest_project_facts(project_dir: str, source: dict, digest: dict) -> list[dict]:
    facts_path = Path(project_dir) / ".hermes-project" / "project-facts.json"
    facts_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        facts_data = json.loads(facts_path.read_text(encoding="utf-8"))
    except Exception:
        facts_data = {"facts": []}
    records = facts_data.setdefault("facts", [])
    updates: list[dict] = []
    rel_path = str(source.get("rel_path") or source.get("path") or "").strip() or "source"

    def add(category: str, key: str, value, status: str, tags: list[str]) -> None:
        record = {
            "category": category, "key": key, "value": value,
            "source": "project_source_digest",
            "confidence": "high" if category == "init_reading" else "medium",
            "status": status, "tags": ["project_init", "source_digest", *tags],
        }
        records[:] = [item for item in records if item.get("key") != key]
        records.append(record)
        updates.append(record)

    read_method = str(digest.get("read_method") or source.get("read_method") or "").strip()
    if read_method:
        add("init_reading", f"{rel_path}.read_method", read_method, "confirmed", [])
    coverage = str(digest.get("read_coverage") or "").strip()
    if coverage:
        add("init_reading", f"{rel_path}.read_coverage", coverage, "confirmed", [])
    for index, raw in enumerate(digest.get("key_facts") or [], start=1):
        if value := str(raw).strip():
            add(
                "init_source_fact", f"{rel_path}.fact.{index}",
                {"source_file": rel_path, "fact": value}, "confirmed",
                ["candidate_fact"],
            )
    for index, raw in enumerate(digest.get("open_questions") or [], start=1):
        if value := str(raw).strip():
            add(
                "init_open_question", f"{rel_path}.question.{index}",
                {"source_file": rel_path, "question": value},
                "needs_confirmation", ["open_question"],
            )
    for index, raw in enumerate(digest.get("issues") or [], start=1):
        if value := str(raw).strip():
            add(
                "init_issue", f"{rel_path}.issue.{index}",
                {"source_file": rel_path, "issue": value},
                "needs_confirmation", ["issue"],
            )
    facts_data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    facts_path.write_text(
        json.dumps(facts_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return updates


def _resolve_project_for_digest(args: dict, db) -> tuple[dict | None, dict | None, str]:
    project = None
    project_name = str(args.get("project_name") or "").strip()
    project_path = str(args.get("project_path") or "").strip()
    source_id = str(args.get("source_id") or "").strip()
    source_path = str(args.get("source_path") or "").strip()
    run_id = str(args.get("run_id") or "").strip()

    if project_name:
        project = db.get_project(project_name)
    if project is None and project_path:
        project = db.get_project_by_path(project_path)
    if project is None and source_path and Path(source_path).is_absolute():
        # Prefer the longest registered project path containing the source.
        src_real = Path(source_path).expanduser().resolve()
        candidates = []
        for candidate in db.list_projects():
            root = Path(candidate.get("path") or candidate.get("cwd") or "").expanduser()
            try:
                root_real = root.resolve()
                src_real.relative_to(root_real)
            except Exception:
                continue
            candidates.append((len(str(root_real)), candidate))
        if candidates:
            project = sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]
    if project is None:
        project = resolve_selected_project(None, os.environ.get("HERMES_SESSION_ID"))
    if project is None:
        return None, None, "No project resolved. Pass project_name/project_path/source_path."

    source = None
    if source_id:
        source = db.get_project_source(project_id=project["id"], source_id=source_id)
    if source is None and source_path:
        root = Path(project.get("path") or project.get("cwd") or "").expanduser().resolve()
        p = Path(source_path).expanduser()
        rel_path = None
        if p.is_absolute():
            try:
                rel_path = p.resolve().relative_to(root).as_posix()
            except Exception:
                source = db.get_project_source(project_id=project["id"], path=str(p.resolve()))
        else:
            rel_path = p.as_posix()
        if source is None and rel_path:
            source = db.get_project_source(project_id=project["id"], rel_path=rel_path)
    if source is None:
        return project, None, "No source resolved. Pass source_id or source_path from source-inventory."
    if run_id and source.get("run_id") and source.get("run_id") != run_id:
        return project, None, f"Source belongs to run {source.get('run_id')}, not {run_id}."
    return project, source, ""


def _create_project_init_kanban_graph(
    project: dict,
    run_id: str,
    sources: list[dict],
    *,
    reader_pool: str,
    synthesis_pool: str,
    max_core_tasks: int,
) -> dict:
    from hermes_cli import kanban_db as kb
    from .kanban_toolset import _official_board

    project_path = str(Path(project.get("path") or project.get("cwd") or "").resolve())
    meta = _official_board(project_path, title=project.get("name") or Path(project_path).name)
    conn = kb.connect(board=meta["slug"])
    created: list[dict] = []
    try:
        core_sources = [
            s for s in sources
            if s.get("priority") == "core" and s.get("read_status") != "ignored"
        ][: max(1, int(max_core_tasks or 50))]
        digest_task_ids: list[str] = []
        for src in core_sources:
            marker = {
                "project_id": project.get("id"),
                "run_id": run_id,
                "source_id": src.get("id"),
                "source_path": src.get("path"),
                "rel_path": src.get("rel_path"),
            }
            body = (
                "## Project Init Source Digest Task\n\n"
                f"Project: {project.get('name')} ({project.get('id')})\n"
                f"Init run: {run_id}\n"
                f"Source id: {src.get('id')}\n"
                f"Project path: {project_path}\n"
                f"File: {src.get('path')}\n"
                f"File type: {src.get('file_type')}\n"
                f"Required read method: {src.get('read_method')}\n\n"
                "Read this file completely using the required native legal tools. "
                "For scanned PDFs/images, use lex_ocr. Do not infer from filename. "
                "Then call `project_source_digest` with source_id, read_coverage, "
                "summary, evidence_ledger, key_facts, open_questions, and issues. "
                "`kanban_complete` is blocked until that native digest record exists. "
                "Your final completion summary must include a File Evidence Ledger.\n\n"
                "<LEX_PROJECT_INIT_SOURCE_JSON>\n"
                + json.dumps(marker, ensure_ascii=False)
                + "\n</LEX_PROJECT_INIT_SOURCE_JSON>"
            )
            tid = kb.create_task(
                conn,
                title=f"init.source_digest: {src.get('rel_path')}",
                body=body,
                assignee=reader_pool,
                created_by="project_init_start",
                workspace_kind="dir",
                workspace_path=project_path,
                priority=100 if src.get("must_read_before_init") else 50,
                idempotency_key=f"project-init:{project.get('id')}:{run_id}:digest:{src.get('id')}",
                session_id=os.environ.get("HERMES_SESSION_ID") or None,
                board=meta["slug"],
            )
            digest_task_ids.append(tid)
            created.append({"id": tid, "kind": "source_digest", "source": src.get("rel_path"), "assignee": reader_pool})

        body = (
            "## Project Init Synthesis Task\n\n"
            f"Project: {project.get('name')} ({project.get('id')})\n"
            f"Init run: {run_id}\n"
            f"Project path: {project_path}\n\n"
            "After all source_digest tasks complete, synthesize project-profile, "
            "project_facts, init-impression, missing-info-list, and the short user "
            "completion report. Do not enter DRAFTING without explicit user authorization."
        )
        synth_id = kb.create_task(
            conn,
            title="init.synthesis: project impression and facts",
            body=body,
            assignee=synthesis_pool,
            created_by="project_init_start",
            workspace_kind="dir",
            workspace_path=project_path,
            priority=10,
            parents=digest_task_ids,
            idempotency_key=f"project-init:{project.get('id')}:{run_id}:synthesis",
            session_id=os.environ.get("HERMES_SESSION_ID") or None,
            board=meta["slug"],
        )
        created.append({"id": synth_id, "kind": "init_synthesis", "assignee": synthesis_pool, "parents": digest_task_ids})
    finally:
        conn.close()

    return {"board": meta, "tasks": created}


# ── Tool handlers ────────────────────────────────────────────────────────────

def _register_or_update_project(name: str, path: str, client: str, goal: str) -> tuple[str, dict]:
    db = _project_session_db()
    project_dir = Path(path).expanduser().resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    existing = db.get_project_by_path(str(project_dir))
    if existing:
        project_id = existing["id"]
        db.update_project(
            project_id,
            name=name or existing.get("name") or project_dir.name,
            client=client if client is not None else existing.get("client", ""),
            goal=goal if goal is not None else existing.get("goal", ""),
            path=str(project_dir),
            cwd=str(project_dir),
        )
    else:
        project_id = db.create_project(
            name or project_dir.name,
            str(project_dir),
            client or "",
            goal or "",
            cwd=str(project_dir),
        )

    _ensure_legal_harness_files(
        str(project_dir),
        project_name=name or project_dir.name,
        client=client or "",
        goal=goal or "",
    )
    project = db.get_project(project_id)

    session_id = os.environ.get("HERMES_SESSION_ID")
    if session_id:
        try:
            db.set_session_project(session_id, project_id, project_cwd=str(project_dir))
        except Exception:
            pass
    return project_id, project or {
        "id": project_id,
        "name": name or project_dir.name,
        "path": str(project_dir),
        "cwd": str(project_dir),
        "client": client or "",
        "goal": goal or "",
        "status": "INIT",
    }


def project_create_handler(args: dict, **kwargs) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return json.dumps({"success": False, "error": "name is required"}, ensure_ascii=False)
    path = _resolve_project_path(name, args.get("path"))
    client = str(args.get("client") or "").strip()
    goal = str(args.get("goal") or "").strip()
    matter_type = str(args.get("matter_type") or "").strip()
    if matter_type and goal:
        goal = f"{goal}\nMatter type: {matter_type}"
    elif matter_type:
        goal = f"Matter type: {matter_type}"

    try:
        project_id, project = _register_or_update_project(name, path, client, goal)
        global _active_project_name, _active_project_path
        _active_project_name = project["name"]
        _active_project_path = project["path"]

        init_result = None
        if args.get("auto_init", True):
            init_args = {
                "project_name": project_id,
                "max_parallel_agents": args.get("max_parallel_agents", 5),
                "max_parallel_ocr": args.get("max_parallel_ocr", 2),
                "reader_pool": args.get("reader_pool") or "hpswarm-reviewer-content",
                "synthesis_pool": args.get("synthesis_pool") or "lex-coordinator",
                "max_core_tasks": args.get("max_core_tasks", 50),
            }
            init_result = json.loads(project_init_start_handler(init_args, **kwargs))

        # Bind current session as coordinator (default true)
        bind_info = None
        if args.get("bind_session", True):
            session_id = os.environ.get("HERMES_SESSION_ID", "")
            if session_id:
                bound = _bind_coordinator_session(project_id, session_id, project["name"])
                if bound:
                    # Best-effort readability only; coordinator_for is the durable binding.
                    try:
                        db = _project_session_db()
                        db.set_session_title(session_id, f"[{project['name']}] coordinator")
                    except Exception:
                        pass
                bind_info = {"session_id": session_id, "bound": bool(bound)}

        return json.dumps({
            "success": True,
            "project_id": project_id,
            "project": project,
            "auto_init": bool(args.get("auto_init", True)),
            "init": init_result,
            "bind_session": bind_info,
            "message": (
                f"Project '{project['name']}' registered at {project['path']}."
                + (f" Session {session_id} bound as coordinator." if bind_info and bind_info.get("bound") else "")
                + (" Init workflow started." if init_result and init_result.get("success") else "")
            ),
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def project_register_handler(args: dict, **kwargs) -> str:
    name = str(args.get("name") or "").strip()
    path = str(args.get("path") or "").strip()
    if not name or not path:
        return json.dumps({"success": False, "error": "name and path are required"}, ensure_ascii=False)
    client = str(args.get("client") or "").strip()
    goal = str(args.get("goal") or "").strip()
    try:
        project_id, project = _register_or_update_project(name, path, client, goal)
        global _active_project_name, _active_project_path
        _active_project_name = project["name"]
        _active_project_path = project["path"]
        result = {
            "success": True,
            "project_id": project_id,
            "project": project,
            "no_init": bool(args.get("no_init", True)),
            "message": f"Project '{project['name']}' registered without starting init.",
        }
        if not args.get("no_init", True):
            result["init"] = json.loads(project_init_start_handler({"project_name": project_id}, **kwargs))
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def project_init_start_handler(args: dict, **kwargs) -> str:
    db = _project_session_db()
    project_name = str(args.get("project_name") or "").strip()
    project_path = str(args.get("project_path") or "").strip()
    project = None
    if project_name:
        project = db.get_project(project_name)
    if project is None and project_path:
        project = db.get_project_by_path(project_path)
    if project is None:
        project = resolve_selected_project(kwargs.get("parent_agent"), os.environ.get("HERMES_SESSION_ID"))
    if project is None:
        return json.dumps({
            "success": False,
            "error": "No project resolved. Pass project_name/project_path or select a project first.",
        }, ensure_ascii=False)

    project_root = Path(project.get("path") or project.get("cwd") or "").expanduser().resolve()
    if not project_root.is_dir():
        return json.dumps({"success": False, "error": f"project path not found: {project_root}"}, ensure_ascii=False)

    max_parallel_agents = int(args.get("max_parallel_agents") or 5)
    max_parallel_ocr = int(args.get("max_parallel_ocr") or 2)
    reader_pool = str(args.get("reader_pool") or "hpswarm-reviewer-content").strip()
    synthesis_pool = str(args.get("synthesis_pool") or "lex-coordinator").strip()
    max_core_tasks = int(args.get("max_core_tasks") or 50)

    run_id = db.create_project_init_run(
        project["id"],
        status="INIT_READING",
        config={
            "max_parallel_agents": max_parallel_agents,
            "max_parallel_ocr": max_parallel_ocr,
            "reader_pool": reader_pool,
            "synthesis_pool": synthesis_pool,
            "max_core_tasks": max_core_tasks,
        },
    )
    sources = _scan_project_sources(project["id"], str(project_root), run_id)
    db.upsert_project_sources(project["id"], run_id, sources)
    db.update_project(project["id"], status="INIT_READING")
    project = db.get_project(project["id"]) or project

    mirrors = _write_project_init_mirrors(project, run_id, sources)
    kanban = _create_project_init_kanban_graph(
        project,
        run_id,
        sources,
        reader_pool=reader_pool,
        synthesis_pool=synthesis_pool,
        max_core_tasks=max_core_tasks,
    )

    core_count = sum(1 for s in sources if s.get("priority") == "core")
    supporting_count = sum(1 for s in sources if s.get("priority") == "supporting")
    ignored_count = sum(1 for s in sources if s.get("priority") == "ignored")
    summary = (
        f"Init run {run_id}: scanned {len(sources)} sources; "
        f"core={core_count}, supporting={supporting_count}, ignored={ignored_count}; "
        f"kanban_tasks={len(kanban.get('tasks', []))}."
    )
    db.update_project_init_run(run_id, summary=summary)

    return json.dumps({
        "success": True,
        "project": {
            "id": project.get("id"),
            "name": project.get("name"),
            "path": project.get("path"),
            "status": "INIT_READING",
        },
        "init_run_id": run_id,
        "source_counts": {
            "total": len(sources),
            "core": core_count,
            "supporting": supporting_count,
            "ignored": ignored_count,
        },
        "mirrors": mirrors,
        "kanban": kanban,
        "message": (
            "Project init started. Core materials have source_digest Kanban tasks; "
            "init.synthesis will run after those complete. Drafting remains locked "
            "until explicit user authorization."
        ),
    }, ensure_ascii=False)


def project_source_digest_handler(args: dict, **kwargs) -> str:
    db = _project_session_db()
    project, source, error = _resolve_project_for_digest(args, db)
    if error:
        return json.dumps({"success": False, "error": error}, ensure_ascii=False)
    assert project is not None and source is not None
    try:
        _ensure_legal_harness_files(str(project.get("path") or project.get("cwd") or ""))
    except Exception:
        pass

    coverage = str(args.get("read_coverage") or "").strip().lower()
    if coverage not in {"full", "partial", "failed"}:
        return json.dumps({
            "success": False,
            "error": "read_coverage must be one of: full, partial, failed",
        }, ensure_ascii=False)
    confidence = str(args.get("confidence") or "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    summary = str(args.get("summary") or "").strip()
    if not summary:
        return json.dumps({"success": False, "error": "summary is required"}, ensure_ascii=False)
    evidence = args.get("evidence_ledger") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    if not isinstance(evidence, list) or not any(str(item).strip() for item in evidence):
        return json.dumps({
            "success": False,
            "error": "evidence_ledger must contain at least one concrete read/OCR evidence item",
        }, ensure_ascii=False)

    def _list_field(name: str) -> list[str]:
        value = args.get(name) or []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return [str(value)]
        return [str(item).strip() for item in value if str(item).strip()]

    read_method = str(args.get("read_method") or source.get("read_method") or "").strip()
    run_id = str(args.get("run_id") or source.get("run_id") or "").strip()
    digest = {
        "project_id": project["id"],
        "project_name": project.get("name"),
        "run_id": run_id,
        "source_id": source["id"],
        "source_path": source.get("path"),
        "rel_path": source.get("rel_path"),
        "read_method": read_method,
        "read_coverage": coverage,
        "confidence": confidence,
        "summary": summary,
        "evidence_ledger": _list_field("evidence_ledger"),
        "key_facts": _list_field("key_facts"),
        "open_questions": _list_field("open_questions"),
        "issues": _list_field("issues"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        digest_id = db.upsert_project_source_digest(
            project_id=project["id"],
            source_id=source["id"],
            run_id=run_id or None,
            read_method=read_method,
            read_coverage=coverage,
            confidence=confidence,
            digest=digest,
            created_by=os.environ.get("HERMES_PROFILE") or os.environ.get("USER") or "agent",
        )
        mirror_path = _write_project_source_digest_mirror(project, source, digest_id, digest)
        fact_updates = _sync_source_digest_project_facts(str(project.get("path") or project.get("cwd") or ""), source, digest)
        try:
            from .legal_workflow_learning import learn_from_findings, project_init_findings

            init_learning = learn_from_findings(
                findings=project_init_findings(source=source, digest=digest),
                workflow_type="project_init",
                scope="project",
                run_id=run_id or None,
                document_path=str(source.get("path") or ""),
                enabled=True,
            )
        except Exception as exc:
            init_learning = {"enabled": True, "error": str(exc), "candidates": []}
        # Refresh source inventory status without touching other init mirrors.
        sources = db.list_project_sources(project["id"], run_id=run_id or None)
        if sources:
            _write_project_source_inventory_mirror(project, run_id or source.get("run_id") or "", sources)
        return json.dumps({
            "success": True,
            "digest_id": digest_id,
            "project_id": project["id"],
            "source_id": source["id"],
            "run_id": run_id,
            "read_coverage": coverage,
            "fact_updates": fact_updates,
            "init_learning": init_learning,
            "mirror_path": mirror_path,
            "message": "Source digest recorded. kanban_complete may now close this init.source_digest task if evidence gate also passes.",
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def project_init_handler(args: dict, **kwargs) -> str:
    """Create a new project from chat."""
    return project_create_handler(
        {**args, "auto_init": args.get("auto_init", True)}, **kwargs,
    )


def project_list_handler(args: dict, **kwargs) -> str:
    """List all registered projects."""
    db = _project_session_db()
    status = args.get("status")
    projects = db.list_projects(status)

    result = []
    for p in projects:
        result.append(
            {
                "id": p["id"],
                "name": p["name"],
                "client": p.get("client", ""),
                "goal": p.get("goal", ""),
                "path": p["path"],
                "status": p["status"],
                "created_at": p["created_at"],
                "updated_at": p["updated_at"],
            }
        )

    if not result:
        return json.dumps(
            {
                "projects": [],
                "message": (
                    "No projects registered. Create one with the project_init tool, "
                    "or run: hermes project init /path/to/dir --name 'Project Name'"
                ),
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {"projects": result, "count": len(result)}, ensure_ascii=False
    )


def project_select_handler(args: dict, **kwargs) -> str:
    """Select a project as the active context."""
    project_name = args.get("project_name", "").strip()
    if not project_name:
        return json.dumps({"success": False, "error": "project_name is required"})

    project = _resolve_project(project_name)
    if not project:
        return json.dumps(
            {
                "success": False,
                "error": f"Project not found: {project_name}",
                "hint": "Use legal_project_list to see registered legal projects.",
            }
        )

    project_path = project["path"]
    if not Path(project_path).is_dir():
        return json.dumps(
            {
                "success": False,
                "error": f"Project directory missing: {project_path}",
                "hint": "The project was registered but the directory no longer exists.",
            }
        )

    global _active_project_name, _active_project_path
    _active_project_name = project["name"]
    _active_project_path = project_path

    parent_agent = kwargs.get("parent_agent")
    session_id = os.environ.get("HERMES_SESSION_ID", "").strip() or getattr(parent_agent, "session_id", "")
    if parent_agent is not None:
        try:
            parent_agent._selected_project_id = project["id"]
            parent_agent.selected_project_id = project["id"]
            parent_agent._selected_project_cwd = project_path
            parent_agent.selected_project_cwd = project_path
            parent_agent._selected_project_name = project["name"]
            parent_agent._cached_system_prompt = None
        except Exception:
            pass
    if session_id:
        try:
            db = _project_session_db()
            db.set_session_project(session_id, project["id"], project_cwd=project_path)
        except Exception:
            pass

    return json.dumps(
        {
            "success": True,
            "project": {
                "id": project["id"],
                "name": project["name"],
                "status": project["status"],
                "path": project_path,
                "goal": project.get("goal", ""),
                "client": project.get("client", ""),
            },
            "message": (
                f"Now working on project '{project['name']}' ({project_path}). "
                "AGENTS.md and project context will be loaded on the next turn. "
                f"Status: {project['status']}."
            ),
        },
        ensure_ascii=False,
    )


def project_context_handler(args: dict, **kwargs) -> str:
    """Read or update project context."""
    project_name = args.get("project_name") or _active_project_name
    if not project_name:
        return json.dumps(
            {
                "success": False,
                "error": (
                    "No project specified and no project selected. "
                    "Use project_select first or pass project_name."
                ),
            }
        )

    project = _resolve_project(project_name)
    if not project:
        return json.dumps(
            {"success": False, "error": f"Project not found: {project_name}"}
        )

    action = args.get("action", "read")

    if action == "read":
        project_path = Path(project["path"])
        context_md = project_path / ".hermes-project" / "project-context.md"
        context_text = ""
        if context_md.is_file():
            context_text = context_md.read_text(encoding="utf-8")

        return json.dumps(
            {
                "success": True,
                "project": {
                    "id": project["id"],
                    "name": project["name"],
                    "client": project.get("client", ""),
                    "goal": project.get("goal", ""),
                    "status": project["status"],
                    "notes": project.get("notes", ""),
                    "path": project["path"],
                },
                "context_md": context_text,
            },
            ensure_ascii=False,
        )

    elif action == "update":
        field = args.get("field")
        value = args.get("value")
        if not field or value is None:
            return json.dumps(
                {"success": False, "error": "field and value are required for update"}
            )

        valid_fields = {"goal", "client", "notes", "name"}
        if field not in valid_fields:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Invalid field: {field}. Valid: {', '.join(sorted(valid_fields))}",
                }
            )

        db = _project_session_db()
        db.update_project(project["id"], **{field: value})

        # Also update project-meta.json on disk
        meta_path = (
            Path(project["path"]) / ".hermes-project" / "project-meta.json"
        )
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta[field] = value
                meta_path.write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False) + "\n"
                )
            except Exception:
                pass

        return json.dumps(
            {
                "success": True,
                "field": field,
                "value": value,
                "message": f"Updated project '{project['name']}' field '{field}'.",
            },
            ensure_ascii=False,
        )

    return json.dumps({"success": False, "error": f"Unknown action: {action}"})


def project_status_handler(args: dict, **kwargs) -> str:
    """Read or update project status."""
    project_name = args.get("project_name") or _active_project_name

    if not project_name:
        return json.dumps(
            {
                "success": False,
                "error": "No project specified and no project selected.",
                "valid_statuses": ["CREATED", "INIT_READING", "PARTY_ENRICHMENT", "INIT_SYNTHESIS", "READY_TO_DRAFT", "DRAFTING", "REVIEWING", "CLOSING"],
            }
        )

    project = _resolve_project(project_name)
    if not project:
        return json.dumps(
            {"success": False, "error": f"Project not found: {project_name}"}
        )

    new_status = args.get("status")

    if not new_status:
        return json.dumps(
            {
                "success": True,
                "project_name": project["name"],
                "current_status": project["status"],
                "valid_statuses": ["CREATED", "INIT", "INIT_READING", "PARTY_ENRICHMENT", "INIT_SYNTHESIS", "READY_TO_DRAFT", "DRAFTING", "REVIEWING", "REVISING", "FINAL", "DELIVERED", "CLOSING", "ARCHIVED"],
            }
        )

    valid = {
        "CREATED", "INIT", "INIT_READING", "PARTY_ENRICHMENT",
        "INIT_SYNTHESIS", "READY_TO_DRAFT", "DRAFTING", "REVIEWING",
        "REVISING", "FINAL", "DELIVERED", "CLOSING", "ARCHIVED",
    }
    new_status = new_status.upper()
    if new_status not in valid:
        return json.dumps(
            {
                "success": False,
                "error": f"Invalid status: {new_status}",
                "valid_statuses": sorted(valid),
            }
        )

    db = _project_session_db()
    previous = project["status"]
    db.update_project(project["id"], status=new_status)

    # Update project-meta.json
    meta_path = Path(project["path"]) / ".hermes-project" / "project-meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["status"] = new_status
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False) + "\n"
            )
        except Exception:
            pass

    return json.dumps(
        {
            "success": True,
            "project_name": project["name"],
            "previous_status": previous,
            "current_status": new_status,
            "message": f"Status changed: {previous} → {new_status}",
        },
        ensure_ascii=False,
    )


def lex_master_route_handler(args: dict, **kwargs) -> str:
    """Route lex-master requests to the right project coordinator session."""
    action = str(args.get("action") or "").strip()
    db_path = _shared_project_db_path()
    try:
        from .project_store import LegalProjectStore

        db = LegalProjectStore(db_path=db_path)
    except Exception as exc:
        return json.dumps(
            {
                "success": False,
                "error": f"Cannot open shared project registry: {exc}",
                "db_path": str(db_path),
            },
            ensure_ascii=False,
        )

    if action == "list_projects":
        projects = _project_candidates(db)
        return json.dumps(
            {
                "success": True,
                "db_path": str(db_path),
                "projects": [
                    {
                        "id": p.get("id"),
                        "name": p.get("name"),
                        "client": p.get("client"),
                        "goal": p.get("goal"),
                        "status": p.get("status"),
                        "path": p.get("path") or p.get("cwd"),
                        "updated_at": p.get("updated_at"),
                    }
                    for p in projects
                ],
                "count": len(projects),
            },
            ensure_ascii=False,
        )

    if action == "status":
        route_id = str(args.get("route_id") or "").strip()
        if not route_id:
            return json.dumps({"success": False, "error": "route_id is required for status"}, ensure_ascii=False)
        route_path = _route_dir() / f"{route_id}.json"
        if not route_path.is_file():
            return json.dumps({"success": False, "error": f"route not found: {route_id}"}, ensure_ascii=False)
        try:
            record = json.loads(route_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return json.dumps({"success": False, "error": f"cannot read route: {exc}"}, ensure_ascii=False)
        pid = record.get("pid")
        if pid:
            try:
                os.kill(int(pid), 0)
                record["process_alive"] = True
            except OSError:
                record["process_alive"] = False
        log_path = Path(record.get("log_path") or "")
        if log_path.is_file():
            try:
                text = log_path.read_text(encoding="utf-8", errors="ignore")
                record["log_tail"] = text[-4000:]
            except Exception:
                pass
        # Also check for a coordinator report file
        report_path = _route_dir() / f"{route_id}.report.json"
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                record["report"] = report
            except Exception:
                pass
        return json.dumps({"success": True, "route": record}, ensure_ascii=False)

    if action == "report":
        route_id = str(args.get("route_id") or "").strip()
        if not route_id:
            return json.dumps({"success": False, "error": "route_id is required for report"}, ensure_ascii=False)
        route_path = _route_dir() / f"{route_id}.json"
        if not route_path.is_file():
            return json.dumps({"success": False, "error": f"route not found: {route_id}"}, ensure_ascii=False)
        summary = str(args.get("summary") or args.get("result") or "").strip()
        status_report = str(args.get("status") or "done").strip()
        report = {
            "route_id": route_id,
            "status": status_report or "done",
            "summary": summary,
            "reported_at": time.time(),
            "details": str(args.get("details") or "")[:2000],
        }
        report_path = _route_dir() / f"{route_id}.report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return json.dumps({
            "success": True,
            "message": "Report saved. lex-master can now read it via lex_master_route(action='status', route_id='...').",
            "report": report,
        }, ensure_ascii=False)

    if action not in {"resolve", "dispatch"}:
        return json.dumps(
            {"success": False, "error": "action must be list_projects, resolve, dispatch, status, or report"},
            ensure_ascii=False,
        )

    query = str(args.get("query") or args.get("task") or "").strip()
    project_id = str(args.get("project_id") or "").strip()
    candidates = _project_candidates(db, query=query, project_id=project_id)
    if not candidates:
        return json.dumps(
            {
                "success": False,
                "error": "No matching project found.",
                "db_path": str(db_path),
                "query": query,
                "hint": "Call lex_master_route(action='list_projects') and ask the user to choose a project.",
            },
            ensure_ascii=False,
        )

    project = candidates[0]
    session = _find_coordinator_session(db, project, str(args.get("session_id") or "").strip())
    response = {
        "success": True,
        "action": action,
        "db_path": str(db_path),
        "project": {
            "id": project.get("id"),
            "name": project.get("name"),
            "client": project.get("client"),
            "goal": project.get("goal"),
            "status": project.get("status"),
            "path": project.get("path") or project.get("cwd"),
        },
        "coordinator_session": (
            {
                "id": session.get("id"),
                "title": session.get("title"),
                "source": session.get("source"),
                "project_id": session.get("project_id"),
                "project_cwd": session.get("project_cwd"),
                "started_at": session.get("started_at"),
                "ended_at": session.get("ended_at"),
                "end_reason": session.get("end_reason"),
                "message_count": session.get("message_count"),
            }
            if session
            else None
        ),
        "other_candidates": [
            {"id": p.get("id"), "name": p.get("name"), "path": p.get("path") or p.get("cwd")}
            for p in candidates[1:5]
        ],
    }

    if action == "resolve":
        if not session:
            response["warning"] = "Project resolved, but no coordinator session was found."
        return json.dumps(response, ensure_ascii=False)

    task = str(args.get("task") or "").strip()
    if not task:
        response.update({"success": False, "error": "task is required for dispatch"})
        return json.dumps(response, ensure_ascii=False)
    if not session:
        response.update(
            {
                "success": False,
                "error": "No coordinator session found for project; ask user whether to create a project session first.",
            }
        )
        return json.dumps(
            response,
            ensure_ascii=False,
        )

    route_id = f"route_{uuid.uuid4().hex[:12]}"
    async_mode = bool(args.get("async", True))
    try:
        route_record = _dispatch_to_session(
            session_id=session["id"],
            task=task,
            project=project,
            route_id=route_id,
            async_mode=async_mode,
        )
    except Exception as exc:
        response.update({"success": False, "error": f"dispatch failed: {exc}"})
        return json.dumps(response, ensure_ascii=False)
    response["route"] = route_record
    response["message"] = (
        "Task dispatched to the project coordinator session. lex-master should now report the route_id "
        "and wait for or poll status instead of doing the project work itself."
    )
    return json.dumps(response, ensure_ascii=False)


# ── Registry registration (discovered by AST scanner) ────────────────────────

from tools.registry import registry, tool_error, tool_result  # noqa: E402

registry.register(
    name="legal_project_create",
    toolset="project_management",
    schema=PROJECT_CREATE_SCHEMA,
    handler=lambda args, **kw: project_create_handler(args, **kw),
    emoji="📁",
)

registry.register(
    name="project_register",
    toolset="project_management",
    schema=PROJECT_REGISTER_SCHEMA,
    handler=lambda args, **kw: project_register_handler(args, **kw),
    emoji="🗂️",
)

registry.register(
    name="project_init_start",
    toolset="project_management",
    schema=PROJECT_INIT_START_SCHEMA,
    handler=lambda args, **kw: project_init_start_handler(args, **kw),
    emoji="🚦",
)

registry.register(
    name="project_source_digest",
    toolset="project_management",
    schema=PROJECT_SOURCE_DIGEST_SCHEMA,
    handler=lambda args, **kw: project_source_digest_handler(args, **kw),
    emoji="📑",
)

registry.register(
    name="project_init",
    toolset="project_management",
    schema=PROJECT_INIT_SCHEMA,
    handler=lambda args, **kw: project_init_handler(args, **kw),
    emoji="📁",
)

registry.register(
    name="legal_project_list",
    toolset="project_management",
    schema=PROJECT_LIST_SCHEMA,
    handler=lambda args, **kw: project_list_handler(args, **kw),
    emoji="📋",
)

registry.register(
    name="project_select",
    toolset="project_management",
    schema=PROJECT_SELECT_SCHEMA,
    handler=lambda args, **kw: project_select_handler(args, **kw),
    emoji="🎯",
)

registry.register(
    name="project_context",
    toolset="project_management",
    schema=PROJECT_CONTEXT_SCHEMA,
    handler=lambda args, **kw: project_context_handler(args, **kw),
    emoji="📝",
)

registry.register(
    name="project_status",
    toolset="project_management",
    schema=PROJECT_STATUS_SCHEMA,
    handler=lambda args, **kw: project_status_handler(args, **kw),
    emoji="🔄",
)

registry.register(
    name="lex_master_route",
    toolset="project_management",
    schema=LEX_MASTER_ROUTE_SCHEMA,
    handler=lambda args, **kw: lex_master_route_handler(args, **kw),
    emoji="🧭",
)

# ── project_bind_session ─────────────────────────────────────────────────

PROJECT_BIND_SESSION_SCHEMA = {
    "name": "project_bind_session",
    "description": (
        "Bind the current session as the coordinator session for a project. "
        "After binding, all future dispatches to this project will use this session. "
        "Call this when you are the project coordinator and want to ensure "
        "lex-master routes tasks to YOUR session (not a random one). "
        "Also sets the session title to include the project name for readability."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Project name or partial match.",
            },
        },
        "required": ["project_name"],
    },
}


def project_bind_session_handler(args: dict, **kwargs) -> str:
    """Bind current session as the coordinator session for a project."""
    project_name = str(args.get("project_name") or "").strip()
    if not project_name:
        return json.dumps({"success": False, "error": "project_name is required"})

    session_id = os.environ.get("HERMES_SESSION_ID", "")
    if not session_id:
        return json.dumps(
            {"success": False, "error": "HERMES_SESSION_ID not set — cannot determine current session."}
        )

    db = _project_session_db()
    project = _resolve_project(project_name)
    if not project:
        return json.dumps(
            {"success": False, "error": f"Project not found: {project_name}",
             "hint": "Use legal_project_list to see registered legal projects."}
        )

    bound = _bind_coordinator_session(project["id"], session_id, project["name"])
    if not bound:
        return json.dumps(
            {
                "success": False,
                "error": f"Session not found or could not be bound: {session_id}",
                "project_id": project["id"],
                "project_name": project["name"],
            },
            ensure_ascii=False,
        )

    # Best-effort readability only; coordinator_for is the durable binding.
    try:
        current_title = db.get_session_title(session_id) or ""
        new_title = f"[{project['name']}] coordinator"
        if current_title != new_title:
            db.set_session_title(session_id, new_title)
    except Exception:
        pass

    parent_agent = kwargs.get("parent_agent")
    if parent_agent is not None:
        try:
            project_path = str(project.get("path") or project.get("cwd") or "")
            parent_agent._selected_project_id = project["id"]
            parent_agent.selected_project_id = project["id"]
            parent_agent._selected_project_cwd = project_path
            parent_agent.selected_project_cwd = project_path
            parent_agent._selected_project_name = project["name"]
            parent_agent._cached_system_prompt = None
        except Exception:
            pass

    return json.dumps(
        {
            "success": True,
            "project_id": project["id"],
            "project_name": project["name"],
            "session_id": session_id,
            "message": (
                f"Session {session_id} is now the coordinator for '{project['name']}'. "
                f"Future dispatches will use this session."
            ),
        },
        ensure_ascii=False,
    )


registry.register(
    name="project_bind_session",
    toolset="project_management",
    schema=PROJECT_BIND_SESSION_SCHEMA,
    handler=lambda args, **kw: project_bind_session_handler(args, **kw),
    emoji="🔗",
)
