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


def test_project_management_toolset_exposes_init_tools():
    import tools.project_management_tool  # noqa: F401
    from tools.registry import registry
    from toolsets import TOOLSETS

    registered = set(registry.get_all_tool_names())
    assert {"project_create", "project_register", "project_init_start"} <= registered
    assert {"project_create", "project_register", "project_init_start"} <= set(
        TOOLSETS["project_management"]["tools"]
    )
