"""Project management tools for legal/document-focused Hermes sessions."""

from __future__ import annotations

import json
import logging
import os
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
            return tool_error(f"Project not found: {name}")
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
