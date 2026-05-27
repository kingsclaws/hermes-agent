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
from pathlib import Path


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


# ── Internal helpers ─────────────────────────────────────────────────────────

# Module-level cache for the currently selected project in this session.
_active_project_name: str | None = None
_active_project_path: str | None = None


def _resolve_cwd() -> str:
    """Best-effort working directory for path resolution."""
    return os.environ.get("TERMINAL_CWD", os.getcwd())


def _resolve_project(name_or_id: str):
    """Look up a project by name or ID. Returns dict or None."""
    from hermes_state import SessionDB

    db = SessionDB()
    return db.get_project(name_or_id)


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
                    "Use delegate_task to spawn Drafter and Reviewers."
                ),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})


def project_list_handler(args: dict, **kwargs) -> str:
    """List all registered projects."""
    from hermes_state import SessionDB

    db = SessionDB()
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
    from hermes_state import SessionDB

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

        db = SessionDB()
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
    from hermes_state import SessionDB

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

    db = SessionDB()
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
