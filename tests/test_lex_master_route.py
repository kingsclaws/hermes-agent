from __future__ import annotations

import json

from hermes_state import SessionDB
from tools.project_management_tool import lex_master_route_handler


def test_lex_master_route_resolves_project_coordinator_session(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    project_id = db.create_project(
        "魔方投资银团变更",
        "/workingfile/138. 魔方投资",
        client="Bank SinoPac",
        goal="2026 amendment",
    )
    db.create_session("sess_mofang", "cli")
    db.set_session_title("sess_mofang", "魔方投资银团变更")
    db.set_session_project("sess_mofang", project_id, "/workingfile/138. 魔方投资")
    monkeypatch.setenv("HERMES_PROJECTS_DB_PATH", str(db_path))

    result = json.loads(
        lex_master_route_handler(
            {"action": "resolve", "query": "请处理魔方投资银团变更的Share Mortgage"}
        )
    )

    assert result["success"] is True
    assert result["project"]["id"] == project_id
    assert result["coordinator_session"]["id"] == "sess_mofang"


def test_lex_master_route_prefers_bound_coordinator_for(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    project_id = db.create_project(
        "魔方投资银团变更",
        "/workingfile/138. 魔方投资",
        client="Bank SinoPac",
        goal="2026 amendment",
    )
    db.create_session("sess_heuristic", "cli")
    db.set_session_title("sess_heuristic", "魔方投资银团变更")
    db.set_session_project("sess_heuristic", project_id, "/workingfile/138. 魔方投资")
    db.create_session("sess_bound", "cli")
    db.bind_project_coordinator(project_id, "sess_bound")
    monkeypatch.setenv("HERMES_PROJECTS_DB_PATH", str(db_path))

    result = json.loads(
        lex_master_route_handler(
            {"action": "resolve", "query": "请处理魔方投资银团变更的Share Mortgage"}
        )
    )

    assert result["success"] is True
    assert result["project"]["id"] == project_id
    assert result["coordinator_session"]["id"] == "sess_bound"


def test_lex_master_route_dispatch_requires_task(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    project_id = db.create_project("apollo", "/workingfile/134. 复星 - Project Apollo")
    db.create_session("sess_apollo", "cli")
    db.set_session_title("sess_apollo", "apollo")
    db.set_session_project("sess_apollo", project_id, "/workingfile/134. 复星 - Project Apollo")
    monkeypatch.setenv("HERMES_PROJECTS_DB_PATH", str(db_path))

    result = json.loads(lex_master_route_handler({"action": "dispatch", "query": "apollo"}))

    assert result["success"] is False
    assert result["error"] == "task is required for dispatch"
    assert result["coordinator_session"]["id"] == "sess_apollo"
