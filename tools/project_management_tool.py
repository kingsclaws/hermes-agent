"""Project management tools for legal/document-focused Hermes sessions."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


def _get_db(parent_agent=None):
    db = getattr(parent_agent, "_session_db", None) if parent_agent is not None else None
    if db is not None:
        return db
    from hermes_state import SessionDB

    return SessionDB()


def apply_project_binding(parent_agent, project: Dict[str, Any], *, persist: bool = True) -> None:
    """Apply a project selection to the current agent/session."""
    project_cwd = str(project.get("cwd") or project.get("path") or "").strip()
    project_id = str(project.get("id") or "").strip() or None

    setattr(parent_agent, "_selected_project_id", project_id)
    setattr(parent_agent, "_selected_project_cwd", project_cwd or None)
    setattr(parent_agent, "terminal_cwd", project_cwd or getattr(parent_agent, "terminal_cwd", None))
    setattr(parent_agent, "cwd", project_cwd or getattr(parent_agent, "cwd", None))

    if project_cwd:
        os.environ["TERMINAL_CWD"] = project_cwd
        os.environ["HERMES_ACTIVE_PROJECT_CWD"] = project_cwd
    if project_id:
        os.environ["HERMES_ACTIVE_PROJECT_ID"] = project_id

    if persist and getattr(parent_agent, "_session_db", None) is not None and getattr(parent_agent, "session_id", None):
        try:
            parent_agent._session_db.set_session_project(parent_agent.session_id, project_id, project_cwd or None)
        except Exception as exc:
            logger.debug("Could not persist project binding for %s: %s", parent_agent.session_id, exc)


def resolve_selected_project(parent_agent=None, *, db=None, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Resolve the currently selected project for a session/agent."""
    if parent_agent is not None:
        project_id = getattr(parent_agent, "_selected_project_id", None)
        project_cwd = getattr(parent_agent, "_selected_project_cwd", None)
        db = db or getattr(parent_agent, "_session_db", None)
        if db is not None and project_id:
            project = db.get_project(project_id)
            if project:
                project = dict(project)
                if project_cwd:
                    project["cwd"] = project_cwd
                return project
        if project_cwd:
            return {"cwd": project_cwd, "id": project_id, "path": project_cwd}
        session_id = session_id or getattr(parent_agent, "session_id", None)

    if db is None and session_id:
        db = _get_db(parent_agent)
    if db is not None and session_id:
        try:
            return db.get_session_project(session_id)
        except Exception as exc:
            logger.debug("Could not resolve session project %s: %s", session_id, exc)
    return None


PROJECT_SELECT_SCHEMA = {
    "name": "project_select",
    "description": (
        "Select a registered project as the active working context for this session. "
        "Updates the session's project binding and working directory so later turns "
        "load the project's context files and legal workflow metadata."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Project name, id, or exact path as stored in the projects database.",
            }
        },
        "required": ["name"],
    },
}

PROJECT_CREATE_SCHEMA = {
    "name": "project_create",
    "description": (
        "Create/register a legal project in the native Hermes project registry. "
        "Use this when the user asks to create a project from an existing folder. "
        "Do not write ~/.hermes/state.db manually."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Project display name, e.g. 颐保银团.",
            },
            "path": {
                "type": "string",
                "description": "Existing project directory path, e.g. /workingfile/140. 颐保银团.",
            },
            "client": {
                "type": "string",
                "description": "Optional client or lead party.",
            },
            "goal": {
                "type": "string",
                "description": "Optional project goal/description.",
            },
            "status": {
                "type": "string",
                "description": "Project status. Default: INIT.",
            },
            "select": {
                "type": "boolean",
                "description": "Select the project as active after creation. Default: true.",
            },
        },
        "required": ["name", "path"],
    },
}

PROJECT_DELETE_SCHEMA = {
    "name": "project_delete",
    "description": (
        "Delete a project from the native Hermes project registry. By default "
        "it removes the project row and associated Hermes sessions, but does "
        "not delete source files. Set delete_files=true only when the user "
        "explicitly asks to remove the project directory from disk."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Project name, id, or exact path.",
            },
            "delete_sessions": {
                "type": "boolean",
                "description": "Delete sessions associated with this project. Default: true.",
            },
            "delete_files": {
                "type": "boolean",
                "description": "Also delete the project directory from disk. Default: false.",
            },
            "confirm": {
                "type": "boolean",
                "description": "Required true to execute deletion.",
            },
        },
        "required": ["name", "confirm"],
    },
}

PROJECT_STATUS_SCHEMA = {
    "name": "project_status",
    "description": "Show the current active project or a named project from the project registry.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Optional project name or id. Omit to inspect the active project for this session.",
            }
        },
    },
}

PROJECT_CONTEXT_SCHEMA = {
    "name": "project_context",
    "description": "Return the active project's legal project-context.md and related metadata.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Optional project name or id. Omit to inspect the active project for this session.",
            }
        },
    },
}

PROJECT_LIST_SCHEMA = {
    "name": "project_list",
    "description": "List registered projects, optionally filtered by status.",
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Optional project status filter such as INIT, DRAFTING, REVIEWING, REVISING, FINAL, DELIVERED, ARCHIVED.",
            }
        },
    },
}


def _handle_project_create(args: dict, **kwargs) -> str:
    parent_agent = kwargs.get("parent_agent")
    db = _get_db(parent_agent)
    should_close = db is not getattr(parent_agent, "_session_db", None)

    name = str(args.get("name") or "").strip()
    path_raw = str(args.get("path") or "").strip()
    if not name:
        return tool_error("name is required")
    if not path_raw:
        return tool_error("path is required")

    path = str(Path(path_raw).expanduser().resolve())
    if not Path(path).exists():
        return tool_error(f"Project path does not exist: {path}")
    if not Path(path).is_dir():
        return tool_error(f"Project path is not a directory: {path}")

    client = args.get("client")
    goal = args.get("goal")
    status = str(args.get("status") or "INIT").strip() or "INIT"
    select_after_create = args.get("select", True)

    try:
        existing = db.get_project(name) or db.get_project(path)
        if existing:
            project_id = existing.get("id")
            updates = {
                "name": name,
                "path": path,
                "status": status,
            }
            if client is not None:
                updates["client"] = client
            if goal is not None:
                updates["goal"] = goal
            db.update_project(project_id, **updates)
            project = db.get_project(project_id) or {**existing, **updates}
            created = False
        else:
            project_id = db.create_project(
                name=name,
                path=path,
                client=client,
                goal=goal,
                status=status,
            )
            project = db.get_project(project_id) or {
                "id": project_id,
                "name": name,
                "path": path,
                "client": client,
                "goal": goal,
                "status": status,
            }
            created = True

        if select_after_create:
            if parent_agent is not None:
                apply_project_binding(parent_agent, project, persist=True)
            elif kwargs.get("session_id"):
                db.set_session_project(kwargs["session_id"], project.get("id"), project.get("path"))

        return tool_result(
            {
                "created": created,
                "active": bool(select_after_create),
                "id": project.get("id"),
                "name": project.get("name"),
                "path": project.get("path"),
                "client": project.get("client"),
                "goal": project.get("goal"),
                "status": project.get("status"),
            }
        )
    finally:
        if should_close:
            db.close()


def _handle_project_delete(args: dict, **kwargs) -> str:
    parent_agent = kwargs.get("parent_agent")
    db = _get_db(parent_agent)
    should_close = db is not getattr(parent_agent, "_session_db", None)

    name = str(args.get("name") or "").strip()
    if not name:
        return tool_error("name is required")
    if args.get("confirm") is not True:
        return tool_error("confirm=true is required to delete a project")

    delete_sessions = args.get("delete_sessions", True)
    delete_files = args.get("delete_files", False)
    sessions_dir = Path(os.environ.get("HERMES_SESSIONS_DIR") or Path.home() / ".hermes" / "sessions")

    try:
        project = db.get_project(name)
        if not project:
            return tool_error(f"Project not found: {name}")

        project_id = project.get("id")
        project_path = project.get("path")
        summary = db.delete_project(
            name,
            delete_sessions=bool(delete_sessions),
            sessions_dir=sessions_dir,
        )
        if summary is None:
            return tool_error(f"Project not found: {name}")

        deleted_files = False
        file_error = None
        if delete_files:
            try:
                if not project_path:
                    file_error = "Project path is empty; no files deleted."
                else:
                    path = Path(str(project_path)).expanduser().resolve()
                    if path.exists():
                        shutil.rmtree(path)
                        deleted_files = True
            except Exception as exc:
                file_error = str(exc)

        if parent_agent is not None and getattr(parent_agent, "_selected_project_id", None) == project_id:
            setattr(parent_agent, "_selected_project_id", None)
            setattr(parent_agent, "_selected_project_cwd", None)
            os.environ.pop("HERMES_ACTIVE_PROJECT_ID", None)
            os.environ.pop("HERMES_ACTIVE_PROJECT_CWD", None)

        return tool_result(
            {
                "deleted": True,
                "id": project_id,
                "name": project.get("name"),
                "path": project_path,
                "deleted_sessions": summary.get("deleted_sessions", 0),
                "deleted_session_ids": summary.get("deleted_session_ids", []),
                "unbound_sessions": summary.get("unbound_sessions", 0),
                "delete_files": bool(delete_files),
                "deleted_files": deleted_files,
                "file_error": file_error,
            }
        )
    finally:
        if should_close:
            db.close()


def _handle_project_select(args: dict, **kwargs) -> str:
    parent_agent = kwargs.get("parent_agent")
    db = _get_db(parent_agent)
    name = str(args.get("name") or "").strip()
    if not name:
        return tool_error("name is required")
    should_close = db is not getattr(parent_agent, "_session_db", None)
    try:
        project = db.get_project(name)
        if not project:
            return tool_error(f"Project not found: {name}. Use project_create to register an existing project directory.")
        if parent_agent is not None:
            apply_project_binding(parent_agent, project, persist=True)
        elif kwargs.get("session_id"):
            db.set_session_project(kwargs["session_id"], project.get("id"), project.get("path"))
        payload = {
            "active": True,
            "cwd": project.get("path"),
            "id": project.get("id"),
            "name": project.get("name"),
            "status": project.get("status"),
        }
        return tool_result(payload)
    finally:
        if should_close:
            db.close()


def _resolve_project_from_args(args: dict, **kwargs) -> tuple[Optional[Dict[str, Any]], Optional[Any]]:
    parent_agent = kwargs.get("parent_agent")
    db = _get_db(parent_agent)
    name = str(args.get("name") or "").strip()
    if name:
        return db.get_project(name), db
    return resolve_selected_project(parent_agent, db=db, session_id=kwargs.get("session_id")), db


def _handle_project_status(args: dict, **kwargs) -> str:
    project, db = _resolve_project_from_args(args, **kwargs)
    should_close = db is not getattr(kwargs.get("parent_agent"), "_session_db", None)
    try:
        if not project:
            return tool_error("No active project selected.")
        return tool_result(
            {
                "client": project.get("client"),
                "cwd": project.get("cwd") or project.get("path"),
                "goal": project.get("goal"),
                "id": project.get("id"),
                "name": project.get("name"),
                "path": project.get("path"),
                "status": project.get("status"),
            }
        )
    finally:
        if should_close and db is not None:
            db.close()


def _handle_project_context(args: dict, **kwargs) -> str:
    project, db = _resolve_project_from_args(args, **kwargs)
    should_close = db is not getattr(kwargs.get("parent_agent"), "_session_db", None)
    try:
        if not project:
            return tool_error("No active project selected.")
        project_dir = Path(str(project.get("cwd") or project.get("path") or "")).resolve()
        context_path = project_dir / ".hermes-project" / "project-context.md"
        standards_path = project_dir / "STANDARDS.md"
        result = {
            "cwd": str(project_dir),
            "id": project.get("id"),
            "name": project.get("name"),
            "project_context_path": str(context_path),
            "standards_path": str(standards_path),
        }
        if context_path.exists():
            result["project_context"] = context_path.read_text(encoding="utf-8")
        if standards_path.exists():
            result["standards"] = standards_path.read_text(encoding="utf-8")
        return tool_result(result)
    finally:
        if should_close and db is not None:
            db.close()


def _handle_project_list(args: dict, **kwargs) -> str:
    parent_agent = kwargs.get("parent_agent")
    db = _get_db(parent_agent)
    should_close = db is not getattr(parent_agent, "_session_db", None)
    try:
        projects = db.list_projects(args.get("status"))
        slim = [
            {
                "client": project.get("client"),
                "goal": project.get("goal"),
                "id": project.get("id"),
                "name": project.get("name"),
                "path": project.get("path"),
                "status": project.get("status"),
            }
            for project in projects
        ]
        return json.dumps({"projects": slim}, ensure_ascii=False)
    finally:
        if should_close:
            db.close()


registry.register(
    name="project_create",
    toolset="project_management",
    schema=PROJECT_CREATE_SCHEMA,
    handler=_handle_project_create,
    description=PROJECT_CREATE_SCHEMA["description"],
)

registry.register(
    name="project_delete",
    toolset="project_management",
    schema=PROJECT_DELETE_SCHEMA,
    handler=_handle_project_delete,
    description=PROJECT_DELETE_SCHEMA["description"],
)

registry.register(
    name="project_select",
    toolset="project_management",
    schema=PROJECT_SELECT_SCHEMA,
    handler=_handle_project_select,
    description=PROJECT_SELECT_SCHEMA["description"],
)

registry.register(
    name="project_status",
    toolset="project_management",
    schema=PROJECT_STATUS_SCHEMA,
    handler=_handle_project_status,
    description=PROJECT_STATUS_SCHEMA["description"],
)

registry.register(
    name="project_context",
    toolset="project_management",
    schema=PROJECT_CONTEXT_SCHEMA,
    handler=_handle_project_context,
    description=PROJECT_CONTEXT_SCHEMA["description"],
)

registry.register(
    name="project_list",
    toolset="project_management",
    schema=PROJECT_LIST_SCHEMA,
    handler=_handle_project_list,
    description=PROJECT_LIST_SCHEMA["description"],
)
