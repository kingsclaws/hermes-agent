from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[2] / "plugins" / "lex-legal-tools"


def _load_project_management_module(monkeypatch):
    package = types.ModuleType("lex_legal_tools")
    package.__path__ = [str(PLUGIN_ROOT)]
    tools_package = types.ModuleType("lex_legal_tools.tools")
    tools_package.__path__ = [str(PLUGIN_ROOT / "tools")]
    monkeypatch.setitem(sys.modules, "lex_legal_tools", package)
    monkeypatch.setitem(sys.modules, "lex_legal_tools.tools", tools_package)

    name = "lex_legal_tools.tools.project_management_tool"
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_ROOT / "tools" / "project_management_tool.py",
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_project_status_reads_legacy_legal_project(monkeypatch, tmp_path):
    state_db = tmp_path / "state.db"
    with sqlite3.connect(state_db) as conn:
        conn.execute(
            """CREATE TABLE projects (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, client TEXT DEFAULT '',
                goal TEXT DEFAULT '', path TEXT NOT NULL, cwd TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'INIT', notes TEXT DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                harness_version TEXT DEFAULT '', last_harness_migration_at REAL
            )"""
        )
        conn.execute(
            """INSERT INTO projects
               (id, name, path, cwd, status, created_at, updated_at)
               VALUES ('proj_1', '颐保银团', '/matter', '/matter', 'REVIEWING', 1, 1)"""
        )

    monkeypatch.setenv("HERMES_PROJECTS_DB_PATH", str(state_db))
    module = _load_project_management_module(monkeypatch)

    result = json.loads(module.project_status_handler({"project_name": "颐保银团"}))

    assert result["success"] is True
    assert result["project_name"] == "颐保银团"
    assert result["current_status"] == "REVIEWING"

    updated = json.loads(module.project_status_handler({
        "project_name": "颐保银团",
        "status": "CLOSING",
    }))
    assert updated["success"] is True
    assert updated["previous_status"] == "REVIEWING"
    assert updated["current_status"] == "CLOSING"


def test_legal_project_store_owns_init_and_source_lifecycle(monkeypatch, tmp_path):
    state_db = tmp_path / "state.db"
    monkeypatch.setenv("HERMES_PROJECTS_DB_PATH", str(state_db))
    module = _load_project_management_module(monkeypatch)
    store = module._project_session_db()

    project_id = store.create_project(
        "Demo Matter", str(tmp_path / "matter"), "Client", "Review",
        cwd=str(tmp_path / "matter"),
    )
    project = store.get_project(project_id)
    assert project is not None
    assert project["name"] == "Demo Matter"
    assert store.get_project_by_path(str(tmp_path / "matter"))["id"] == project_id

    run_id = store.create_project_init_run(project_id, config={"workers": 2})
    source = {
        "id": "src_1",
        "path": str(tmp_path / "matter" / "agreement.docx"),
        "rel_path": "agreement.docx",
        "file_name": "agreement.docx",
        "ext": ".docx",
        "file_type": "docx",
        "priority": "core",
        "must_read_before_init": True,
    }
    assert store.upsert_project_sources(project_id, run_id, [source]) == 1
    assert store.get_project_source(
        project_id=project_id, source_id="src_1",
    )["read_status"] == "pending"

    digest_id = store.upsert_project_source_digest(
        project_id=project_id,
        source_id="src_1",
        run_id=run_id,
        read_method="lex_read",
        read_coverage="full",
        digest={"summary": "read"},
    )
    assert digest_id.startswith("sdig_")
    assert store.get_project_source(
        project_id=project_id, source_id="src_1",
    )["read_status"] == "digested"
    assert store.update_project_init_run(run_id, status="INIT_SYNTHESIS") is True
    assert store.update_project(project_id, status="READY_TO_DRAFT") is True
    assert store.get_project(project_id)["status"] == "READY_TO_DRAFT"
    store.close()


def test_legal_project_store_builds_cross_session_context_pack(monkeypatch, tmp_path):
    state_db = tmp_path / "state.db"
    monkeypatch.setenv("HERMES_PROJECTS_DB_PATH", str(state_db))
    module = _load_project_management_module(monkeypatch)
    store = module._project_session_db()
    project_id = store.create_project("Matter", str(tmp_path / "matter"))

    store.record_decision(project_id, "Governing law", "Use PRC law", source_session_id="s1")
    store.record_constraint(project_id, "Deadline", "File by Friday", source_session_id="s1")
    store.create_snapshot(
        project_id, "Draft complete; reviewer must verify schedules",
        label="handoff:task-1", state={"task_id": "task-1"}, source_session_id="s1",
    )

    pack = store.build_context_pack(project_id, objective="Review draft")
    assert pack["objective"] == "Review draft"
    assert pack["active_decisions"][0]["decision"] == "Use PRC law"
    assert pack["active_constraints"][0]["constraint_text"] == "File by Friday"
    assert pack["latest_snapshot"]["state"]["task_id"] == "task-1"
    assert store.context_health(project_id)["cross_session_ready"] is True
    store.close()


def test_legal_project_create_uses_plugin_owned_scaffold(monkeypatch, tmp_path):
    state_db = tmp_path / "state.db"
    project_dir = tmp_path / "new-matter"
    monkeypatch.setenv("HERMES_PROJECTS_DB_PATH", str(state_db))
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    module = _load_project_management_module(monkeypatch)

    result = json.loads(module.project_init_handler({
        "name": "New Matter",
        "path": str(project_dir),
        "client": "Client",
        "goal": "Review documents",
        "auto_init": False,
    }))

    assert result["success"] is True
    assert result["project"]["path"] == str(project_dir)
    assert (project_dir / "AGENTS.md").is_file()
    assert (project_dir / "STANDARDS.md").is_file()
    assert (project_dir / ".hermes-project" / "project-meta.json").is_file()
