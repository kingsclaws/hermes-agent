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
        "Read or update a project's phase. HPSwarm flow: "
        "INIT → DRAFTING → REVIEWING → REVISING → FINAL → DELIVERED."
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
                "enum": ["INIT", "DRAFTING", "REVIEWING", "REVISING", "FINAL", "DELIVERED"],
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


# ── Tool handlers ────────────────────────────────────────────────────────────

def project_init_handler(args: dict, **kwargs) -> str:
    """Create a new project from chat."""
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
                "valid_statuses": ["INIT", "DRAFTING", "REVIEWING", "REVISING", "FINAL", "DELIVERED"],
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
                "valid_statuses": ["INIT", "DRAFTING", "REVIEWING", "REVISING", "FINAL", "DELIVERED"],
            }
        )

    valid = {"INIT", "DRAFTING", "REVIEWING", "REVISING", "FINAL", "DELIVERED"}
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
