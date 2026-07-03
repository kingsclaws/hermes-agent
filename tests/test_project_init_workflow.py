import json
from pathlib import Path


def test_project_create_starts_init_workflow(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "default")
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    project_dir = tmp_path / "Demo Matter"
    project_dir.mkdir()
    (project_dir / "Term Sheet - demo.md").write_text("贷款金额 1亿元\n", encoding="utf-8")
    (project_dir / "营业执照.pdf").write_bytes(b"%PDF-1.4 fake")

    import hermes_state

    hermes_state.DEFAULT_DB_PATH = home / "state.db"

    from tools import project_management_tool as pm

    out = json.loads(pm.project_create_handler({
        "name": "Demo Matter",
        "path": str(project_dir),
        "client": "Client A",
        "goal": "init test",
        "auto_init": True,
        "max_core_tasks": 1,
    }))

    assert out["success"] is True
    assert out["init"]["success"] is True
    assert out["init"]["source_counts"]["core"] >= 2
    assert len(out["init"]["kanban"]["tasks"]) == 2
    assert (project_dir / ".hermes-project" / "source-inventory.md").is_file()
    assert (project_dir / ".hermes-project" / "init-impression.md").is_file()

    from hermes_state import SessionDB

    db = SessionDB(db_path=home / "state.db")
    sources = db.list_project_sources(
        out["project_id"],
        run_id=out["init"]["init_run_id"],
    )
    assert len(sources) >= 2
    assert any(src["priority"] == "core" for src in sources)


def test_project_create_auto_binds_current_session(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "default")
    monkeypatch.setenv("HERMES_SESSION_ID", "coord-create")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    project_dir = tmp_path / "Bound Matter"
    project_dir.mkdir()
    (project_dir / "项目概述.md").write_text("初始化资料\n", encoding="utf-8")

    import hermes_state

    hermes_state.DEFAULT_DB_PATH = home / "state.db"

    from hermes_state import SessionDB
    from tools import project_management_tool as pm

    db = SessionDB(db_path=home / "state.db")
    db.create_session("coord-create", source="cli", model="test-model")
    db.close()

    out = json.loads(pm.project_create_handler({
        "name": "Bound Matter",
        "path": str(project_dir),
        "client": "Client B",
        "goal": "bind test",
        "auto_init": False,
    }))

    assert out["success"] is True
    assert out["bind_session"] == {"session_id": "coord-create", "bound": True}

    db = SessionDB(db_path=home / "state.db")
    try:
        assert db.get_session("coord-create")["coordinator_for"] == out["project_id"]
        assert db.get_project_coordinator_session(out["project_id"])["id"] == "coord-create"
    finally:
        db.close()


def test_project_management_toolset_exposes_init_tools():
    import tools.project_management_tool  # noqa: F401
    from tools.registry import registry
    from toolsets import TOOLSETS

    registered = set(registry.get_all_tool_names())
    assert {"project_create", "project_register", "project_init_start", "project_source_digest"} <= registered
    assert {"project_create", "project_register", "project_init_start", "project_source_digest"} <= set(
        TOOLSETS["project_management"]["tools"]
    )


def test_project_source_digest_records_db_and_mirror(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "default")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    project_dir = tmp_path / "Digest Matter"
    project_dir.mkdir()
    (project_dir / "Term Sheet.md").write_text("贷款金额 2亿元\n", encoding="utf-8")

    import hermes_state

    hermes_state.DEFAULT_DB_PATH = home / "state.db"

    from hermes_state import SessionDB
    from tools import project_management_tool as pm

    db = SessionDB(db_path=home / "state.db")
    project_id = db.create_project("Digest Matter", str(project_dir), cwd=str(project_dir))
    run_id = db.create_project_init_run(project_id)
    sources = pm._scan_project_sources(project_id, str(project_dir), run_id)
    db.upsert_project_sources(project_id, run_id, sources)
    source = next(src for src in sources if src["priority"] == "core")

    out = json.loads(pm.project_source_digest_handler({
        "project_name": project_id,
        "run_id": run_id,
        "source_id": source["id"],
        "read_method": "read_file",
        "read_coverage": "full",
        "confidence": "high",
        "summary": "Term Sheet records a RMB 200m loan.",
        "evidence_ledger": ["Term Sheet.md full file read via read_file"],
        "key_facts": ["贷款金额：人民币2亿元"],
    }))

    assert out["success"] is True
    digest = db.get_project_source_digest(
        project_id=project_id,
        source_id=source["id"],
        run_id=run_id,
    )
    assert digest is not None
    assert digest["read_coverage"] == "full"
    updated_source = db.get_project_source(project_id=project_id, source_id=source["id"])
    assert updated_source["read_status"] == "digested"
    assert Path(out["mirror_path"]).is_file()
