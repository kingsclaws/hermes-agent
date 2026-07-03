from pathlib import Path

from hermes_state import SessionDB
from tools.project_management_tool import (
    apply_project_binding,
    project_bind_session_handler,
    resolve_selected_project,
)


class _StubAgent:
    def __init__(self, db, session_id: str):
        self._session_db = db
        self.session_id = session_id
        self.terminal_cwd = None
        self.cwd = None


def test_session_project_binding_round_trip(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("sess-1", source="cli", model="test-model")
        project_root = tmp_path / "deal-a"
        project_root.mkdir()
        project_id = db.create_project("Deal A", str(project_root), "Client A", "Review agreement")
        agent = _StubAgent(db, "sess-1")

        apply_project_binding(agent, db.get_project(project_id))

        session = db.get_session("sess-1")
        resolved = resolve_selected_project(agent)
        assert session["project_id"] == project_id
        assert session["project_cwd"] == str(project_root)
        assert resolved["id"] == project_id
        assert resolved["cwd"] == str(project_root)
    finally:
        db.close()


def test_session_project_lookup_without_agent_attrs(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("sess-2", source="cli", model="test-model")
        project_root = tmp_path / "deal-b"
        project_root.mkdir()
        project_id = db.create_project("Deal B", str(project_root), "Client B", "Draft SPA")
        db.set_session_project("sess-2", project_id, str(project_root))

        resolved = resolve_selected_project(None, db=db, session_id="sess-2")
        assert resolved is not None
        assert resolved["id"] == project_id
        assert resolved["cwd"] == str(project_root)
    finally:
        db.close()


def test_delete_project_with_associated_sessions(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("sess-3", source="cli", model="test-model")
        project_root = tmp_path / "deal-c"
        project_root.mkdir()
        project_id = db.create_project("Deal C", str(project_root), "Client C", "Delete test")
        db.set_session_project("sess-3", project_id, str(project_root))

        summary = db.delete_project("Deal C", delete_sessions=True, sessions_dir=tmp_path / "sessions")

        assert summary is not None
        assert summary["deleted_sessions"] == 1
        assert db.get_project("Deal C") is None
        assert db.get_session("sess-3") is None
    finally:
        db.close()


def test_delete_session_orphans_subagent_runs(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("sess-4", source="cli", model="test-model")
        db._conn.execute(
            """
            INSERT INTO subagent_runs (id, parent_session_id, goal, status, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("run-1", "sess-4", "test", "completed", 1.0),
        )
        db._conn.commit()

        assert db.delete_session("sess-4", sessions_dir=tmp_path / "sessions") is True

        row = db._conn.execute(
            "SELECT parent_session_id FROM subagent_runs WHERE id = ?",
            ("run-1",),
        ).fetchone()
        assert row["parent_session_id"] is None
    finally:
        db.close()


def test_project_bind_session_sets_coordinator_for(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    try:
        db.create_session("coord-1", source="cli", model="test-model")
        project_root = tmp_path / "deal-d"
        project_root.mkdir()
        project_id = db.create_project("Deal D", str(project_root), "Client D", "Coordinate")

        monkeypatch.setenv("HERMES_PROJECTS_DB_PATH", str(db_path))
        monkeypatch.setenv("HERMES_SESSION_ID", "coord-1")

        import json

        result = json.loads(project_bind_session_handler({"project_name": "Deal D"}))

        assert result["success"] is True
        assert result["project_id"] == project_id
        session = db.get_session("coord-1")
        assert session["coordinator_for"] == project_id
        assert db.get_project_coordinator_session(project_id)["id"] == "coord-1"
    finally:
        db.close()


def test_project_bind_session_replaces_old_coordinator(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    try:
        db.create_session("coord-old", source="cli", model="test-model")
        db.create_session("coord-new", source="cli", model="test-model")
        project_root = tmp_path / "deal-e"
        project_root.mkdir()
        project_id = db.create_project("Deal E", str(project_root), "Client E", "Coordinate")
        assert db.bind_project_coordinator(project_id, "coord-old") is True

        monkeypatch.setenv("HERMES_PROJECTS_DB_PATH", str(db_path))
        monkeypatch.setenv("HERMES_SESSION_ID", "coord-new")

        import json

        result = json.loads(project_bind_session_handler({"project_name": "Deal E"}))

        assert result["success"] is True
        assert db.get_session("coord-old")["coordinator_for"] is None
        assert db.get_session("coord-new")["coordinator_for"] == project_id
        assert db.get_project_coordinator_session(project_id)["id"] == "coord-new"
    finally:
        db.close()
