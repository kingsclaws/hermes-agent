import json

from hermes_cli import kanban_db as kb
from tools.legal_evidence_gate import (
    read_before_conclude_errors as _read_before_conclude_errors,
)
from tools.kanban_tools import _handle_complete
from tools.kanban_toolset import (
    kanban_task_approve_handler,
    kanban_task_collect_handler,
    kanban_task_create_handler,
    kanban_task_read_handler,
)


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


def test_read_before_conclude_blocks_missing_claim_without_evidence():
    errors = _read_before_conclude_errors("社保凭证未提供，zip中未见。")

    assert errors
    assert any("File Evidence Ledger" in err or "文件证据台账" in err for err in errors)
    assert any("已核实未提供" in err for err in errors)


def test_read_before_conclude_allows_verified_evidence_ledger():
    note = """
    ## File Evidence Ledger
    文件名 | 工具 | 实际内容摘要 | 对应清单问题编号 | 结论
    完税证明.pdf | lex_ocr | 显示社保缴纳记录 | Q29 | 已提供但不完整

    跟进项：社保缴纳通知已核实未提供。
    """

    assert _read_before_conclude_errors(note) == []


def test_read_before_conclude_does_not_block_non_missing_delivery_note():
    assert _read_before_conclude_errors("已完成合同格式审阅，无需修订。") == []


def test_kanban_complete_blocks_missing_claim_before_db_connect():
    out = _handle_complete({
        "task_id": "tsk_missing_evidence",
        "summary": "社保凭证未提供，zip中未见。",
    })
    data = json.loads(out)

    assert data["error"]
    assert "Read-before-conclude" in data["error"]
    assert "File Evidence Ledger" in data["error"]


def test_swarm_task_approve_routes_to_official_board_from_worker_env(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    project = tmp_path / "legal project"
    project.mkdir()
    board = "legal-demo"
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", board)

    conn = kb.connect(board=board)
    try:
        task_id = kb.create_task(
            conn,
            title="Review response",
            assignee="hpswarm-coordinator",
            workspace_kind="dir",
            workspace_path=str(project),
            initial_status="running",
            session_id="coord-session",
        )
        conn.execute(
            "UPDATE tasks SET claim_lock = ?, claim_expires = ? WHERE id = ?",
            ("claim-123", 9999999999, task_id),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", "claim-123")
    out = kanban_task_approve_handler({
        "task_id": task_id,
        "claim_token": "claim-123",
        "project_path": str(project),
        "note": "Reviewer checked the task and approves completion.",
    })
    data = json.loads(out)

    assert data["success"] is True
    assert data["board"]["slug"] == board

    conn = kb.connect(board=board)
    try:
        task = kb.get_task(conn, task_id)
        events = kb.list_events_for_tasks(conn, [task_id])
    finally:
        conn.close()

    assert task is not None
    assert task.status == "done"
    assert any(e.kind == "completed" for e in events)


def test_swarm_task_read_routes_to_official_board_from_project_path(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    project = tmp_path / "legal project"
    project.mkdir()
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))

    board = "legal-legal-project-test"
    conn = kb.connect(board=board)
    try:
        task_id = kb.create_task(
            conn,
            title="Official board task",
            assignee="hpswarm-reviewer-content",
            workspace_kind="dir",
            workspace_path=str(project),
        )
    finally:
        conn.close()

    out = kanban_task_read_handler({
        "task_id": task_id,
        "board": board,
        "project_path": str(project),
    })
    data = json.loads(out)

    assert data["success"] is True
    assert data["task"]["id"] == task_id
    assert data["board"]["slug"] == board


def test_swarm_task_collect_records_terminal_receipt_once(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    project = tmp_path / "legal-project"
    project.mkdir()
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setenv("HERMES_SESSION_ID", "coord-session-collect")

    create_out = kanban_task_create_handler({
        "project_path": str(project),
        "title": "Collect once",
        "description": "Verify receipt dedupe.",
        "assignee": "hpswarm-reviewer-content",
    })
    create_data = json.loads(create_out)
    assert create_data["success"] is True
    task_id = create_data["task"]["id"]
    board = create_data["board"]["slug"]

    conn = kb.connect(board=board)
    try:
        assert kb.complete_task(conn, task_id, summary="worker finished") is True
    finally:
        conn.close()

    first = json.loads(kanban_task_collect_handler({
        "task_id": task_id,
        "project_path": str(project),
    }))
    second = json.loads(kanban_task_collect_handler({
        "task_id": task_id,
        "project_path": str(project),
    }))

    assert first["success"] is True
    assert second["success"] is True

    conn = kb.connect(board=board)
    try:
        events = kb.list_events(conn, task_id)
    finally:
        conn.close()

    collected = [
        e for e in events
        if e.kind == "collected"
        and isinstance(e.payload, dict)
        and e.payload.get("session_id") == "coord-session-collect"
        and e.payload.get("status") == "done"
    ]
    assert len(collected) == 1
