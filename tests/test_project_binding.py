from pathlib import Path

from hermes_state import SessionDB
from tools.project_management_tool import apply_project_binding, resolve_selected_project


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
