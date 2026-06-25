from tools.registry import discover_builtin_tools, registry
from toolsets import resolve_toolset


def test_retired_legal_orchestrate_is_not_registered():
    imported = discover_builtin_tools()

    assert "tools.legal_orchestration_tool" not in imported
    assert "legal_orchestrate" not in registry._tools


def test_legal_toolsets_expose_workflow_and_kanban_execution_layer():
    discover_builtin_tools()

    for toolset in ("legal_orchestration", "lex-docx", "lex-docx-coordinator"):
        tools = set(resolve_toolset(toolset))
        assert "legal_orchestrate" not in tools
        assert "legal_workflow" in tools
        assert "swarm_workflow_compile" in tools
        assert "swarm_task_poll" in tools
        assert "swarm_task_progress" in tools

