from __future__ import annotations

import json
import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

_PLUGIN_API = (
    Path(__file__).parents[2]
    / "plugins"
    / "lex-legal-tools"
    / "dashboard"
    / "plugin_api.py"
)
_SPEC = importlib.util.spec_from_file_location("test_lex_plugin_api", _PLUGIN_API)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
router = _MODULE.router

API_PREFIX = "/api/plugins/lex-legal-tools"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_lex_api_projects_and_evidence(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli import projects_db

    project_root = tmp_path / "matter-a"
    project_root.mkdir(parents=True)

    with projects_db.connect_closing() as conn:
        pid = projects_db.create_project(
            conn,
            name="Matter A",
            folders=[str(project_root)],
            description="Legal matter",
            board_slug="matter-a",
        )

    sidecar = project_root / ".hermes-project"
    _write(sidecar / "project-meta.json", json.dumps({"status": "INIT_READING"}))
    _write(sidecar / "project-state.json", json.dumps({"phase": "reviewing_sources"}))
    _write(sidecar / "project-context.md", "# Context\n\nImportant matter context.")
    _write(sidecar / "memories" / "project_facts.md", "# Facts\n\nKnown facts.")
    _write(
        sidecar / "source-digests" / "loan-agreement.source-digest.md",
        "# Source Digest\n\n## Summary\n\nRead the facility agreement.\n",
    )

    app = FastAPI()
    app.include_router(router, prefix=API_PREFIX)
    client = TestClient(app)

    listing = client.get(f"{API_PREFIX}/projects")
    assert listing.status_code == 200
    body = listing.json()
    assert body["count"] == 1
    assert body["projects"][0]["id"] == pid
    assert body["projects"][0]["meta"]["status"] == "INIT_READING"
    assert body["projects"][0]["evidence"]["source_digest_count"] == 1

    evidence = client.get(f"{API_PREFIX}/projects/{pid}/evidence")
    assert evidence.status_code == 200
    evidence_body = evidence.json()
    assert evidence_body["evidence"]["source_digest_count"] == 1
    assert "Known facts." in evidence_body["facts_excerpt"]


def test_lex_api_research_and_kanban(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli import projects_db

    project_root = tmp_path / "matter-b"
    project_root.mkdir(parents=True)

    with projects_db.connect_closing() as conn:
        pid = projects_db.create_project(
            conn,
            name="Matter B",
            folders=[str(project_root)],
            board_slug="matter-b",
        )

    sidecar = project_root / ".hermes-project"
    sidecar.mkdir(parents=True, exist_ok=True)

    research_db = sidecar / "research.db"
    import sqlite3

    conn = sqlite3.connect(str(research_db))
    conn.execute(
        "CREATE TABLE research_runs (id TEXT PRIMARY KEY, title TEXT, status TEXT, question TEXT, started_at REAL, finished_at REAL)"
    )
    conn.execute(
        "INSERT INTO research_runs (id, title, status, question, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("res_1", "HK law check", "collecting", "What is current HK position?", 1.0, None),
    )
    conn.commit()
    conn.close()

    kanban_dir = project_root / "kanban"
    kanban_dir.mkdir(parents=True, exist_ok=True)
    kanban_db = kanban_dir / "kanban.db"
    conn = sqlite3.connect(str(kanban_db))
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT)")
    conn.executemany(
        "INSERT INTO tasks (id, status) VALUES (?, ?)",
        [("t1", "todo"), ("t2", "review"), ("t3", "review")],
    )
    conn.commit()
    conn.close()

    app = FastAPI()
    app.include_router(router, prefix=API_PREFIX)
    client = TestClient(app)

    research = client.get(f"{API_PREFIX}/projects/{pid}/research/runs")
    assert research.status_code == 200
    research_body = research.json()
    assert research_body["research"]["run_count"] == 1
    assert research_body["research"]["runs"][0]["title"] == "HK law check"

    kanban = client.get(f"{API_PREFIX}/projects/{pid}/kanban")
    assert kanban.status_code == 200
    kanban_body = kanban.json()
    assert kanban_body["kanban"]["exists"] is True
    assert kanban_body["kanban"]["counts"]["review"] == 2
    assert len(kanban_body["kanban"]["tasks"]) == 3

    detail = client.get(f"{API_PREFIX}/projects/{pid}/research/runs/res_1")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["run"]["id"] == "res_1"

    cockpit = client.get(f"{API_PREFIX}/projects/{pid}/cockpit")
    assert cockpit.status_code == 200
    cockpit_body = cockpit.json()
    assert cockpit_body["project"]["id"] == pid
    assert cockpit_body["workflow"]["kanban"]["current_lane"] == "review"
    assert cockpit_body["workflow"]["kanban"]["review_tasks"] == 2
    assert cockpit_body["workflow"]["research"]["counts"]["collecting"] == 1


def test_lex_api_merges_legal_and_official_projects(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli import projects_db

    official_root = tmp_path / "official-matter"
    official_root.mkdir()
    with projects_db.connect_closing() as conn:
        official_id = projects_db.create_project(
            conn,
            name="Official Matter",
            folders=[str(official_root)],
        )

    legal_root = tmp_path / "legal-matter"
    legal_root.mkdir()
    state_db = home / "state.db"
    state_db.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    conn = sqlite3.connect(state_db)
    conn.execute(
        """CREATE TABLE projects (
               id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
               cwd TEXT DEFAULT '', status TEXT NOT NULL, goal TEXT DEFAULT '',
               notes TEXT DEFAULT '', created_at REAL NOT NULL,
               updated_at REAL NOT NULL
           )"""
    )
    conn.execute(
        """INSERT INTO projects
               (id, name, path, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("proj_legal", "Legal Matter", str(legal_root), "ACTIVE", 1.0, 2.0),
    )
    conn.commit()
    conn.close()

    app = FastAPI()
    app.include_router(router, prefix=API_PREFIX)
    body = TestClient(app).get(f"{API_PREFIX}/projects").json()

    assert body["count"] == 2
    assert {project["id"] for project in body["projects"]} == {
        "proj_legal",
        official_id,
    }
