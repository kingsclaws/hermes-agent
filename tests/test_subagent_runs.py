from unittest.mock import MagicMock

from hermes_state import SessionDB
from tools.delegate_tool import _build_child_progress_callback


def test_subagent_run_round_trip(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("sess-parent", source="cli", model="test-model")
        db.create_subagent_run(
            "sa-1234",
            parent_session_id="sess-parent",
            goal="Review document",
            status="queued",
            role="leaf",
            model="gpt-test",
            toolsets=["lex-docx", "file"],
        )
        db.add_subagent_event("sa-1234", "subagent.spawn_requested", {"goal": "Review document"})
        db.update_subagent_run(
            "sa-1234",
            status="completed",
            duration_seconds=3.5,
            tool_count=2,
            summary="Done",
        )

        run = db.get_subagent_run("sa-1234")
        assert run is not None
        assert run["parent_session_id"] == "sess-parent"
        assert run["status"] == "completed"
        assert run["toolsets"] == ["lex-docx", "file"]
        assert run["summary"] == "Done"

        runs = db.list_subagent_runs(parent_session_id="sess-parent")
        assert len(runs) == 1
        assert runs[0]["id"] == "sa-1234"

        events = db.list_subagent_events("sa-1234")
        assert [event["event_type"] for event in events] == ["subagent.spawn_requested"]
        assert events[0]["payload"]["goal"] == "Review document"
    finally:
        db.close()


def test_child_progress_callback_persists_events(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("sess-parent", source="cli", model="test-model")
        db.create_subagent_run(
            "sa-5678",
            parent_session_id="sess-parent",
            goal="Check clauses",
            status="queued",
            toolsets=["lex-docx"],
        )

        parent = MagicMock()
        parent._delegate_spinner = MagicMock()
        parent._session_db = db
        parent.tool_progress_callback = None

        cb = _build_child_progress_callback(
            0,
            "Check clauses",
            parent,
            1,
            subagent_id="sa-5678",
            depth=0,
            model="gpt-test",
            toolsets=["lex-docx"],
            role="leaf",
        )
        assert cb is not None

        cb("subagent.start", preview="Check clauses")
        cb("tool.started", "lex_read", "Agreement.docx")

        run = db.get_subagent_run("sa-5678")
        assert run is not None
        assert run["status"] == "running"
        assert run["tool_count"] == 1

        events = db.list_subagent_events("sa-5678")
        assert [event["event_type"] for event in events] == [
            "subagent.start",
            "subagent.tool",
        ]
        assert events[1]["payload"]["tool_name"] == "lex_read"
    finally:
        db.close()
