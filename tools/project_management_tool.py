#!/usr/bin/env python3
"""
Project Management Tools — native chat-available legal project lifecycle.

Five tools:
  project_init     Create a new project (dirs + scaffolding + DB)
  project_list     List all registered projects
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


def _project_session_db():
    """Return the SessionDB used as the shared project registry."""
    from hermes_state import SessionDB

    db_path = os.environ.get("HERMES_PROJECTS_DB_PATH", "").strip()
    if not db_path:
        try:
            from hermes_cli.profiles import get_active_profile_name
            from hermes_constants import get_default_hermes_root

            if get_active_profile_name() == "lex-master":
                db_path = str(get_default_hermes_root() / "state.db")
        except Exception:
            db_path = ""

    return SessionDB(db_path=Path(db_path)) if db_path else SessionDB()


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
    "name": "project_create",
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

PROJECT_LIST_SCHEMA = {
    "name": "project_list",
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


def _find_coordinator_session(db, project: dict, session_id: str = "") -> dict | None:
    if session_id:
        try:
            return db.get_session(session_id)
        except Exception:
            return None
    sessions = db.list_sessions_rich(limit=500)
    scored = [(s, _session_score(s, project)) for s in sessions]
    scored = [(s, score) for s, score in scored if score > 0]
    if not scored:
        return None
    return sorted(scored, key=lambda item: item[1], reverse=True)[0][0]


def _dispatch_to_session(*, session_id: str, task: str, project: dict, route_id: str, async_mode: bool) -> dict:
    route_path = _route_dir() / f"{route_id}.json"
    log_path = _route_dir() / f"{route_id}.log"
    prompt = (
        "[lex-master route]\n"
        f"Project: {project.get('name')} ({project.get('id')})\n"
        f"Project path: {project.get('path') or project.get('cwd') or ''}\n"
        "You are the project coordinator session for this matter. Do not treat "
        "this as a fresh unrelated request. Continue the existing project context, "
        "use project facts/kanban/harness as appropriate, and report progress/results "
        "back in this session.\n\n"
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
        "",
        "| Priority | Must Read | Type | Read Method | Status | File | Reason |",
        "|----------|-----------|------|-------------|--------|------|--------|",
    ]
    for src in sorted_sources:
        lines.append(
            "| {priority} | {must} | {typ} | {method} | {status} | `{file}` | {reason} |".format(
                priority=src.get("priority", ""),
                must="yes" if src.get("must_read_before_init") else "no",
                typ=src.get("file_type", ""),
                method=src.get("read_method", ""),
                status=src.get("read_status", ""),
                file=src.get("rel_path", "").replace("|", "\\|"),
                reason=str(src.get("reason", "")).replace("|", "\\|"),
            )
        )
    inventory_path = sidecar / "source-inventory.md"
    inventory_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

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
        f"- Source count: {len(sorted_sources)}\n"
        f"- Core count: {sum(1 for s in sorted_sources if s.get('priority') == 'core')}\n",
        encoding="utf-8",
    )
    return {
        "sidecar": str(sidecar),
        "source_inventory": str(inventory_path),
        "init_impression": str(impression_path),
        "missing_info_list": str(missing_path),
        "run_log": str(run_log),
    }


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
    from tools.kanban_toolset import _official_board

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
            body = (
                "## Project Init Source Digest Task\n\n"
                f"Project: {project.get('name')} ({project.get('id')})\n"
                f"Init run: {run_id}\n"
                f"Project path: {project_path}\n"
                f"File: {src.get('path')}\n"
                f"File type: {src.get('file_type')}\n"
                f"Required read method: {src.get('read_method')}\n\n"
                "Read this file completely using the required native legal tools. "
                "For scanned PDFs/images, use lex_ocr. Do not infer from filename. "
                "Write a structured source_digest markdown file under "
                f"`{project_path}/.hermes-project/source-digests/` and complete "
                "with a File Evidence Ledger plus suggested_project_facts."
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
    from hermes_cli.project_commands import _ensure_legal_harness_files

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

        return json.dumps({
            "success": True,
            "project_id": project_id,
            "project": project,
            "auto_init": bool(args.get("auto_init", True)),
            "init": init_result,
            "message": (
                f"Project '{project['name']}' registered at {project['path']}."
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


def project_init_handler(args: dict, **kwargs) -> str:
    """Create a new project from chat."""
    # Backwards-compatible alias: historically project_init created
    # scaffolding only. Lex now treats chat project creation as
    # create/register + auto init.
    if args.get("path") and Path(str(args.get("path"))).exists():
        return project_create_handler({**args, "auto_init": args.get("auto_init", True)}, **kwargs)
    name = args.get("name", "").strip()
    if not name:
        return json.dumps({"success": False, "error": "project name is required"})

    path = _resolve_project_path(name, args.get("path"))
    client = args.get("client", "") or "（待补充）"
    goal = args.get("goal", "") or "（待补充）"

    try:
        from hermes_cli.project_commands import _create_scaffolding, _register_in_db

        project_dir = Path(path)
        _create_scaffolding(project_dir, name, client, goal)
        project_id = _register_in_db(name, str(project_dir), client, goal)

        global _active_project_name, _active_project_path
        _active_project_name = name
        _active_project_path = str(project_dir)

        # Auto-create kanban board for the new project
        kanban_msg = ""
        try:
            from tools.kanban_toolset import kanban_board_create_handler
            result = json.loads(kanban_board_create_handler(
                {"project_path": str(project_dir), "title": name},
            ))
            if result.get("success"):
                kanban_msg = " Kanban Board 已自动创建。"
        except Exception:
            pass

        # Auto-bind current session to the newly created project
        bind_msg = ""
        try:
            session_id = os.environ.get("HERMES_SESSION_ID")
            if session_id:
                from hermes_state import SessionDB
                db = SessionDB()
                try:
                    db.set_session_project(session_id, project_id,
                                           project_cwd=str(project_dir))
                    bind_msg = " Current session bound to project."
                finally:
                    db.close()
        except Exception:
            pass

        return json.dumps(
            {
                "success": True,
                "project_id": project_id,
                "name": name,
                "client": client,
                "goal": goal,
                "path": str(project_dir),
                "status": "INIT",
                "message": (
                    f"Project '{name}' created at {project_dir}. "
                    "AGENTS.md is loaded automatically — Coordinator is ready. "
                    "Use swarm_task_create to delegate work to Drafter and Reviewers."
                    + kanban_msg
                ),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})


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
                "hint": "Use project_list to see registered projects.",
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
        from hermes_state import SessionDB

        db = SessionDB(db_path=db_path)
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
        return json.dumps({"success": True, "route": record}, ensure_ascii=False)

    if action not in {"resolve", "dispatch"}:
        return json.dumps(
            {"success": False, "error": "action must be list_projects, resolve, dispatch, or status"},
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
    name="project_create",
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
    name="project_init",
    toolset="project_management",
    schema=PROJECT_INIT_SCHEMA,
    handler=lambda args, **kw: project_init_handler(args, **kw),
    emoji="📁",
)

registry.register(
    name="project_list",
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
