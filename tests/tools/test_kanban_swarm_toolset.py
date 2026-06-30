import json

from hermes_cli import kanban_db as kb
from tools.kanban_toolset import kanban_task_create_handler


def test_swarm_task_create_subscribes_origin_session_and_requires_file_reading(monkeypatch, tmp_path):
    """Legal swarm tasks must wake the coordinator and forbid filename-only review."""
    home = tmp_path / ".hermes"
    home.mkdir()
    project = tmp_path / "legal-project"
    project.mkdir()
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setenv("HERMES_SESSION_ID", "coord-session-1")

    out = kanban_task_create_handler({
        "project_path": str(project),
        "title": "Review C03 response files",
        "description": "Check Q29 against the response package.",
        "assignee": "hpswarm-reviewer-content",
    })
    data = json.loads(out)

    assert data["success"] is True
    task_id = data["task"]["id"]
    board = data["board"]["slug"]

    conn = kb.connect(board=board)
    try:
        task = kb.get_task(conn, task_id)
        subs = kb.list_notify_subs(conn, task_id)
    finally:
        conn.close()

    assert task is not None
    assert "Mandatory Legal File-Reading Protocol" in (task.body or "")
    assert "Filename matching is not evidence" in (task.body or "")
    assert "File Evidence Ledger" in (task.body or "")
    assert any(
        sub["platform"] == "session"
        and sub["chat_id"] == "coord-session-1"
        and sub["thread_id"] == ""
        for sub in subs
    )
