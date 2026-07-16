"""Read-oriented Lex WebUI API surface.

This module exposes stable, frontend-friendly HTTP endpoints for legal matter
visualisation. It deliberately aggregates existing files and SQLite stores into
simple JSON rather than making a separate WebUI parse `.hermes-project/*`
artifacts directly.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import APIRouter, HTTPException, Query

from hermes_cli import projects_db
from hermes_constants import (
    get_hermes_home,
    reset_hermes_home_override,
    set_hermes_home_override,
)

router = APIRouter(tags=["lex"])

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


def _project_root(project: dict[str, Any]) -> Optional[Path]:
    raw = project.get("path") or project.get("cwd") or project.get("primary_path")
    if not raw:
        folders = project.get("folders") or []
        primary = next((folder for folder in folders if folder.get("is_primary")), None)
        selected = primary or (folders[0] if folders else None)
        raw = selected.get("path") if selected else None
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _project_sidecar(root: Optional[Path]) -> Optional[Path]:
    if root is None:
        return None
    return root / ".hermes-project"


def _legal_board_slug(root: Path) -> str:
    name = root.name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    digest = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"legal-{slug[:48].strip('-')}-{digest}" if slug else f"legal-project-{digest}"


def _local_kanban_summary(root: Optional[Path], *, profile: Optional[str] = None) -> dict[str, Any]:
    if root is None:
        return {"exists": False, "counts": {}, "board_path": None}
    from hermes_cli import kanban_db as kb

    expected_root = str(root.resolve())
    board_slug = _legal_board_slug(root)
    canonical_path: Path | None = None
    with _profile_scope(profile):
        # Prefer persisted board metadata over reconstructing a slug. This also
        # survives slug-algorithm changes and proves the board belongs to this
        # project via its default_workdir.
        for board in kb.list_boards(include_archived=True):
            workdir = str(board.get("default_workdir") or "").strip()
            if workdir and os.path.realpath(workdir) == os.path.realpath(expected_root):
                board_slug = str(board.get("slug") or board_slug)
                db_value = str(board.get("db_path") or "").strip()
                canonical_path = Path(db_value) if db_value else None
                break
        if canonical_path is None:
            canonical_path = kb.kanban_db_path(board=board_slug)
    legacy_path = root / "kanban" / "kanban.db"
    db_path = canonical_path if canonical_path.is_file() else legacy_path
    if not db_path.is_file():
        return {"exists": False, "counts": {}, "tasks": [], "board_path": str(db_path), "board_slug": board_slug}
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
                "body",
                "block_kind",
                "last_failure_error",
                "result",
                "started_at",
                "current_step_key",
                "workflow_template_id",
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
    except Exception as exc:
        return {
            "exists": True,
            "counts": {},
            "tasks": [],
            "board_path": str(db_path),
            "board_slug": board_slug,
            "error": str(exc) or exc.__class__.__name__,
        }
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
        "board_slug": board_slug,
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


def _project_payload(project: dict[str, Any], *, profile: Optional[str] = None) -> dict[str, Any]:
    root = _project_root(project)
    sidecar = _project_sidecar(root)
    meta = _read_json(sidecar / "project-meta.json") if sidecar else {}
    state = _read_json(sidecar / "project-state.json") if sidecar else {}
    state.setdefault("status", project.get("status"))
    if project.get("init_status"):
        state.setdefault("phase", project.get("init_status"))
    context_excerpt = _read_excerpt(sidecar / "project-context.md") if sidecar else ""
    facts_excerpt = _read_excerpt(sidecar / "memories" / "project_facts.md") if sidecar else ""
    digest_entries = _source_digest_entries(sidecar)
    research_runs = _research_runs(sidecar)
    kanban = _local_kanban_summary(root, profile=profile)
    workflow = _project_stage_summary(
        meta=meta,
        state=state,
        kanban=kanban,
        research_runs=research_runs,
        digest_entries=digest_entries,
    )
    durable_context = _project_context(str(project.get("id") or ""), profile=profile)
    latest_snapshot = durable_context["latest_snapshot"]
    context_health = {
        "active_decisions": len(durable_context["decisions"]),
        "active_constraints": len(durable_context["constraints"]),
        "latest_snapshot": latest_snapshot,
        "cross_session_ready": bool(
            latest_snapshot or durable_context["decisions"] or durable_context["constraints"]
        ),
        "session_context_provider": "lcm-compatible",
    }

    return {
        "id": str(project.get("id") or ""),
        "slug": str(project.get("slug") or project.get("id") or ""),
        "name": str(project.get("name") or (root.name if root else "Legal project")),
        "description": str(
            project.get("description") or project.get("goal") or project.get("notes") or ""
        ) or None,
        "archived": bool(project.get("archived"))
        or str(project.get("status") or "").upper() == "ARCHIVED",
        "created_at": project.get("created_at"),
        "primary_path": str(root) if root else None,
        "board_slug": project.get("board_slug") or (_legal_board_slug(root) if root else None),
        "folders": project.get("folders")
        or ([{"path": str(root), "is_primary": True}] if root else []),
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
        "context_health": context_health,
        "durable_context": durable_context,
    }


def _legal_projects_db_path() -> Path:
    override = os.environ.get("HERMES_PROJECTS_DB_PATH", "").strip()
    return Path(override).expanduser() if override else get_hermes_home() / "state.db"


def _project_context(project_id: str, *, profile: Optional[str]) -> dict[str, Any]:
    """Read the durable cross-session context without importing tool modules."""
    with _profile_scope(profile):
        path = _legal_projects_db_path()
        if not path.is_file():
            return {"decisions": [], "constraints": [], "latest_snapshot": None}
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            tables = {str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            decisions = [dict(row) for row in conn.execute(
                "SELECT * FROM project_decisions WHERE project_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 50",
                (project_id,),
            ).fetchall()] if "project_decisions" in tables else []
            constraints = [dict(row) for row in conn.execute(
                "SELECT * FROM project_constraints WHERE project_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 50",
                (project_id,),
            ).fetchall()] if "project_constraints" in tables else []
            snapshot_row = conn.execute(
                "SELECT * FROM project_snapshots WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone() if "project_snapshots" in tables else None
            snapshot = dict(snapshot_row) if snapshot_row else None
            if snapshot:
                try:
                    snapshot["state"] = json.loads(snapshot.pop("state_json") or "{}")
                except Exception:
                    snapshot["state"] = {}
            return {"decisions": decisions, "constraints": constraints, "latest_snapshot": snapshot}
        except Exception:
            return {"decisions": [], "constraints": [], "latest_snapshot": None}
        finally:
            if conn is not None:
                conn.close()


def _list_legal_projects(*, profile: Optional[str]) -> list[dict[str, Any]]:
    with _profile_scope(profile):
        path = _legal_projects_db_path()
        if not path.is_file():
            return []
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects'"
            ).fetchone()
            if not exists:
                return []
            has_init_runs = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_init_runs'"
            ).fetchone()
            query = (
                """SELECT p.*,
                          (SELECT r.status FROM project_init_runs r
                            WHERE r.project_id = p.id
                            ORDER BY r.updated_at DESC LIMIT 1) AS init_status
                     FROM projects p ORDER BY p.updated_at DESC"""
                if has_init_runs
                else "SELECT p.*, NULL AS init_status FROM projects p ORDER BY p.updated_at DESC"
            )
            return [
                dict(row)
                for row in conn.execute(query).fetchall()
            ]
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass


def _list_official_projects(*, profile: Optional[str]) -> list[dict[str, Any]]:
    """Load Hermes projects as compatibility records for the Lex dashboard."""
    with _profile_scope(profile):
        with projects_db.connect_closing() as conn:
            projects = projects_db.list_projects(conn, include_archived=True)
    return [project.to_dict() for project in projects]


def _list_projects(*, profile: Optional[str]) -> list[dict[str, Any]]:
    """Merge legal and Hermes projects, preferring legal records on conflicts."""
    projects = _list_legal_projects(profile=profile)
    seen_ids = {str(project.get("id") or "") for project in projects}
    seen_paths = {
        str(root)
        for project in projects
        if (root := _project_root(project)) is not None
    }
    for project in _list_official_projects(profile=profile):
        project_id = str(project.get("id") or "")
        root = _project_root(project)
        if project_id in seen_ids or (root is not None and str(root) in seen_paths):
            continue
        projects.append(project)
        seen_ids.add(project_id)
        if root is not None:
            seen_paths.add(str(root))
    return projects


def _load_project(project_id: str, *, profile: Optional[str]) -> dict[str, Any]:
    project = next(
        (row for row in _list_projects(profile=profile) if row.get("id") == project_id),
        None,
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    return project


@router.get("/projects")
def get_lex_projects(profile: Optional[str] = Query(default=None)) -> dict[str, Any]:
    projects = _list_projects(profile=profile)
    rows = [_project_payload(project, profile=profile) for project in projects]
    return {"projects": rows, "count": len(rows)}


@router.get("/projects/{project_id}")
def get_lex_project(
    project_id: str,
    profile: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    project = _load_project(project_id, profile=profile)
    return {"project": _project_payload(project, profile=profile)}


@router.get("/projects/{project_id}/evidence")
def get_lex_project_evidence(
    project_id: str,
    profile: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    project = _load_project(project_id, profile=profile)
    payload = _project_payload(project, profile=profile)
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
    payload = _project_payload(project, profile=profile)
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
    payload = _project_payload(project, profile=profile)
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
    payload = _project_payload(project, profile=profile)
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
    payload = _project_payload(project, profile=profile)
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
        "context_health": payload["context_health"],
        "durable_context": payload["durable_context"],
    }


@router.get("/projects/{project_id}/context")
def get_lex_project_context(
    project_id: str,
    profile: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    project = _load_project(project_id, profile=profile)
    payload = _project_payload(project, profile=profile)
    return {
        "project_id": payload["id"],
        "context_health": payload["context_health"],
        **payload["durable_context"],
    }
