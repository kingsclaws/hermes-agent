from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

from hermes_cli import kanban_db as kb


PLUGIN_ROOT = Path(__file__).parents[2] / "plugins" / "lex-legal-tools"


def _load_kanban_toolset(monkeypatch):
    package = types.ModuleType("lex_legal_tools")
    package.__path__ = [str(PLUGIN_ROOT)]
    tools_package = types.ModuleType("lex_legal_tools.tools")
    tools_package.__path__ = [str(PLUGIN_ROOT / "tools")]
    monkeypatch.setitem(sys.modules, "lex_legal_tools", package)
    monkeypatch.setitem(sys.modules, "lex_legal_tools.tools", tools_package)

    name = "lex_legal_tools.tools.kanban_toolset"
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_ROOT / "tools" / "kanban_toolset.py",
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_collect_completed_official_task_without_legacy_receipt_api(
    monkeypatch, tmp_path,
):
    kanban_home = tmp_path / "hermes-home"
    project_path = tmp_path / "matter"
    project_path.mkdir()
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(kanban_home))
    monkeypatch.setenv("HERMES_SESSION_ID", "session_coordinator")

    module = _load_kanban_toolset(monkeypatch)
    board = module._official_board(str(project_path), title="Matter")
    conn = kb.connect(board=board["slug"])
    try:
        task_id = kb.create_task(
            conn,
            title="Review evidence",
            assignee="reviewer",
            workspace_kind="dir",
            workspace_path=str(project_path),
        )
        assert kb.complete_task(
            conn,
            task_id,
            summary="Evidence review complete",
            metadata={"artifacts": [str(project_path / "review.docx")]},
        )
    finally:
        conn.close()

    result = json.loads(module.kanban_task_collect_handler({
        "task_id": task_id,
        "project_path": str(project_path),
    }))

    assert result["success"] is True, result
    assert result["task_id"] == task_id
    assert result["status"] == "done"
    assert result["summary"] == "Evidence review complete"
    assert result["output"]["files_touched"] == [str(project_path / "review.docx")]
