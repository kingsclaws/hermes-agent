"""Read-oriented Lex WebUI API surface.

This module exposes stable, frontend-friendly HTTP endpoints for legal matter
visualisation. It deliberately aggregates existing files and SQLite stores into
simple JSON rather than making a separate WebUI parse `.hermes-project/*`
artifacts directly.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import APIRouter, HTTPException, Query

from hermes_cli import projects_db
from hermes_constants import (
    reset_hermes_home_override,
    set_hermes_home_override,
)

router = APIRouter(prefix="/api/lex", tags=["lex"])

KANBAN_FLOW_ORDER = [
    "triage",
    "todo",
    "scheduled",
    "ready",
    "running",
    "blocked",
    "review",
    "done",
]


@contextmanager
def _profile_scope(profile: Optional[str]) -> Iterator[None]:
    requested = (profile or "").strip()
    if not requested or requested.lower() == "current":
        yield
        return
    from hermes_cli.web_server import _resolve_profile_dir

    profile_dir = _resolve_profile_dir(requested)
    token = set_hermes_home_override(str(profile_dir))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_excerpt(path: Path, limit: int = 2000) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _project_root(project: projects_db.Project) -> Optional[Path]:
    raw = project.primary_path or next(
        (folder.path for folder in project.folders if folder.is_primary),
        None,
    )
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _project_sidecar(root: Optional[Path]) -> Optional[Path]:
    if root is None:
        return None
    return root / ".hermes-project"


def _local_kanban_summary(root: Optional[Path]) -> dict[str, Any]:
    if root is None:
        return {"exists": False, "counts": {}, "board_path": None}
    db_path = root / "kanban" / "kanban.db"
    if not db_path.is_file():
        return {"exists": False, "counts": {}, "tasks": [], "board_path": str(db_path)}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM tasks GROUP BY status"
        ).fetchall()
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        wanted = [
            name
            for name in (
                "id",
                "title",
                "status",
                "assignee",
                "priority",
                "updated_at",
                "created_at",
                "completed_at",
                "session_id",
                "column_id",
            )
            if name in columns
        ]
        tasks: list[dict[str, Any]] = []
        if wanted:
            sql = (
                f"SELECT {', '.join(wanted)} FROM tasks "
                "ORDER BY "
                + ("updated_at DESC" if "updated_at" in columns else "rowid DESC")
                + " LIMIT 200"
            )
            tasks = [dict(row) for row in conn.execute(sql).fetchall()]
    except Exception:
        return {"exists": True, "counts": {}, "tasks": [], "board_path": str(db_path)}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    counts = {str(status): int(count) for status, count in rows}
    return {
        "exists": True,
        "counts": counts,
        "tasks": tasks,
        "board_path": str(db_path),
    }


def _source_digest_entries(sidecar: Optional[Path]) -> list[dict[str, Any]]:
    if sidecar is None:
        return []
    digests_dir = sidecar / "source-digests"
    if not digests_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(digests_dir.glob("*.source-digest.md")):
        excerpt = _read_excerpt(path, limit=1200)
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "modified_at": path.stat().st_mtime,
                "excerpt": excerpt,
            }
        )
    return items


def _research_runs(sidecar: Optional[Path]) -> list[dict[str, Any]]:
    if sidecar is None:
        return []
    db_path = sidecar / "research.db"
    if not db_path.is_file():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title, status, question, started_at, finished_at "
            "FROM research_runs ORDER BY started_at DESC"
        ).fetchall()
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return [dict(row) for row in rows]


def _research_run_detail(sidecar: Optional[Path], run_id: str) -> dict[str, Any] | None:
    if sidecar is None:
        return None
    db_path = sidecar / "research.db"
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            "SELECT * FROM research_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            return None

        def related_rows(table: str, order_by: str) -> list[dict[str, Any]]:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if exists is None:
                return []
            return [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM {table} WHERE research_run_id = ? "
                    f"ORDER BY {order_by}",
                    (run_id,),
                ).fetchall()
            ]

        questions = related_rows("research_questions", "priority ASC, id ASC")
        sources = related_rows("research_sources", "retrieved_at DESC, id ASC")
        findings = related_rows("research_findings", "confidence DESC, id ASC")
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {
        "run": dict(run),
        "questions": questions,
        "sources": sources,
        "findings": findings,
    }


def _research_status_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    latest_run: dict[str, Any] | None = None
    latest_started = float("-inf")
    for run in runs:
        status = str(run.get("status") or "unknown").strip() or "unknown"
        counts[status] = counts.get(status, 0) + 1
        try:
            started = float(run.get("started_at") or 0)
        except Exception:
            started = 0.0
        if latest_run is None or started > latest_started:
            latest_run = run
            latest_started = started
    return {
        "counts": counts,
        "latest_run": latest_run,
    }


def _kanban_flow_summary(kanban: dict[str, Any]) -> dict[str, Any]:
    counts = {
        str(name): int(value)
        for name, value in (kanban.get("counts") or {}).items()
    }
    total_tasks = sum(counts.values())
    done_tasks = counts.get("done", 0)
    current_lane = ""
    # Report the furthest active lane reached, not the oldest backlog lane.
    for lane in reversed(KANBAN_FLOW_ORDER):
        if lane == "done":
            continue
        if counts.get(lane, 0) > 0:
            current_lane = lane
            break
    if not current_lane and total_tasks and done_tasks == total_tasks:
        current_lane = "done"
    return {
        "exists": bool(kanban.get("exists")),
        "total_tasks": total_tasks,
        "done_tasks": done_tasks,
        "blocked_tasks": counts.get("blocked", 0),
        "review_tasks": counts.get("review", 0),
        "active_tasks": total_tasks - done_tasks,
        "current_lane": current_lane or None,
        "flow_order": KANBAN_FLOW_ORDER,
        "counts": counts,
    }


def _project_stage_summary(
    *,
    meta: dict[str, Any],
    state: dict[str, Any],
    kanban: dict[str, Any],
    research_runs: list[dict[str, Any]],
    digest_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    research = _research_status_summary(research_runs)
    kanban_flow = _kanban_flow_summary(kanban)
    status = _first_non_empty(
        state.get("status"),
        meta.get("status"),
    )
    phase = _first_non_empty(
        state.get("phase"),
        meta.get("phase"),
        kanban_flow.get("current_lane"),
        status,
    )
    return {
        "status": status or None,
        "phase": phase or None,
        "evidence_count": len(digest_entries),
        "research": research,
        "kanban": kanban_flow,
    }


def _project_payload(project: projects_db.Project) -> dict[str, Any]:
    root = _project_root(project)
    sidecar = _project_sidecar(root)
    meta = _read_json(sidecar / "project-meta.json") if sidecar else {}
    state = _read_json(sidecar / "project-state.json") if sidecar else {}
    context_excerpt = _read_excerpt(sidecar / "project-context.md") if sidecar else ""
    facts_excerpt = _read_excerpt(sidecar / "memories" / "project_facts.md") if sidecar else ""
    digest_entries = _source_digest_entries(sidecar)
    research_runs = _research_runs(sidecar)
    kanban = _local_kanban_summary(root)
    workflow = _project_stage_summary(
        meta=meta,
        state=state,
        kanban=kanban,
        research_runs=research_runs,
        digest_entries=digest_entries,
    )

    return {
        "id": project.id,
        "slug": project.slug,
        "name": project.name,
        "description": project.description,
        "archived": bool(project.archived),
        "created_at": project.created_at,
        "primary_path": str(root) if root else None,
        "board_slug": project.board_slug,
        "folders": [folder.to_dict() for folder in project.folders],
        "meta": meta,
        "state": state,
        "context_excerpt": context_excerpt,
        "facts_excerpt": facts_excerpt,
        "evidence": {
            "source_digest_count": len(digest_entries),
            "source_digests": digest_entries,
        },
        "research": {
            "run_count": len(research_runs),
            "runs": research_runs,
        },
        "kanban": kanban,
        "workflow": workflow,
    }


def _load_project(project_id: str, *, profile: Optional[str]) -> projects_db.Project:
    with _profile_scope(profile):
        with projects_db.connect_closing() as conn:
            project = projects_db.get_project(conn, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    return project


@router.get("/projects")
def get_lex_projects(profile: Optional[str] = Query(default=None)) -> dict[str, Any]:
    with _profile_scope(profile):
        with projects_db.connect_closing() as conn:
            projects = projects_db.list_projects(conn, include_archived=True)
    rows = [_project_payload(project) for project in projects]
    return {"projects": rows, "count": len(rows)}


@router.get("/projects/{project_id}")
def get_lex_project(
    project_id: str,
    profile: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    project = _load_project(project_id, profile=profile)
    return {"project": _project_payload(project)}


@router.get("/projects/{project_id}/evidence")
def get_lex_project_evidence(
    project_id: str,
    profile: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    project = _load_project(project_id, profile=profile)
    payload = _project_payload(project)
    return {
        "project_id": payload["id"],
        "project_name": payload["name"],
        "primary_path": payload["primary_path"],
        "evidence": payload["evidence"],
        "facts_excerpt": payload["facts_excerpt"],
    }


@router.get("/projects/{project_id}/research/runs")
def get_lex_project_research_runs(
    project_id: str,
    profile: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    project = _load_project(project_id, profile=profile)
    payload = _project_payload(project)
    return {
        "project_id": payload["id"],
        "project_name": payload["name"],
        "primary_path": payload["primary_path"],
        "research": payload["research"],
    }


@router.get("/projects/{project_id}/research/runs/{run_id}")
def get_lex_project_research_run_detail(
    project_id: str,
    run_id: str,
    profile: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    project = _load_project(project_id, profile=profile)
    payload = _project_payload(project)
    root = _project_root(project)
    detail = _research_run_detail(_project_sidecar(root), run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Unknown research run: {run_id}")
    return {
        "project_id": payload["id"],
        "project_name": payload["name"],
        "primary_path": payload["primary_path"],
        **detail,
    }


@router.get("/projects/{project_id}/kanban")
def get_lex_project_kanban(
    project_id: str,
    profile: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    project = _load_project(project_id, profile=profile)
    payload = _project_payload(project)
    return {
        "project_id": payload["id"],
        "project_name": payload["name"],
        "primary_path": payload["primary_path"],
        "kanban": payload["kanban"],
        "workflow": payload["workflow"],
    }


@router.get("/projects/{project_id}/cockpit")
def get_lex_project_cockpit(
    project_id: str,
    profile: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    project = _load_project(project_id, profile=profile)
    payload = _project_payload(project)
    return {
        "project": {
            "id": payload["id"],
            "slug": payload["slug"],
            "name": payload["name"],
            "description": payload["description"],
            "archived": payload["archived"],
            "created_at": payload["created_at"],
            "primary_path": payload["primary_path"],
            "board_slug": payload["board_slug"],
        },
        "workflow": payload["workflow"],
        "evidence": payload["evidence"],
        "research": payload["research"],
        "kanban": payload["kanban"],
        "context_excerpt": payload["context_excerpt"],
        "facts_excerpt": payload["facts_excerpt"],
    }
