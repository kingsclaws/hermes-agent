from types import SimpleNamespace

from agent.conversation_loop import _restore_or_build_system_prompt
from agent.tool_refresh import refresh_agent_tools_if_needed


def _tool(name, description=""):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_refresh_agent_tools_preserves_dynamic_agent_tools(monkeypatch):
    memory_manager = SimpleNamespace(
        get_all_tool_schemas=lambda: [
            {"name": "memory_fact", "description": "memory", "parameters": {"type": "object"}}
        ]
    )
    context_compressor = SimpleNamespace(
        get_tool_schemas=lambda: [
            {"name": "lcm_expand", "description": "context", "parameters": {"type": "object"}}
        ]
    )
    agent = SimpleNamespace(
        tools=[_tool("old_tool"), _tool("memory_fact"), _tool("lcm_expand")],
        valid_tool_names={"old_tool", "memory_fact", "lcm_expand"},
        enabled_toolsets=None,
        disabled_toolsets=None,
        _memory_manager=memory_manager,
        context_compressor=context_compressor,
        _cached_system_prompt="old prompt",
        _kanban_worker_guidance="",
        session_id="s1",
    )

    monkeypatch.setattr(
        "model_tools.get_tool_definitions",
        lambda **_: [_tool("new_native_tool")],
    )

    changed = refresh_agent_tools_if_needed(agent)

    assert changed is True
    assert agent.valid_tool_names == {"new_native_tool", "memory_fact", "lcm_expand"}
    assert agent._cached_system_prompt is None
    assert agent._force_system_prompt_rebuild_once is True


def test_refresh_agent_tools_noops_when_schema_fingerprint_is_same(monkeypatch):
    agent = SimpleNamespace(
        tools=[_tool("same_tool", "same")],
        valid_tool_names={"same_tool"},
        enabled_toolsets=None,
        disabled_toolsets=None,
        _memory_manager=None,
        context_compressor=None,
        _cached_system_prompt="cached",
        _kanban_worker_guidance="",
        session_id="s1",
    )
    monkeypatch.setattr(
        "model_tools.get_tool_definitions",
        lambda **_: [_tool("same_tool", "same")],
    )

    changed = refresh_agent_tools_if_needed(agent)

    assert changed is False
    assert agent._cached_system_prompt == "cached"
    assert not getattr(agent, "_force_system_prompt_rebuild_once", False)


def test_forced_tool_refresh_rebuilds_system_prompt_instead_of_db_restore():
    class FakeDB:
        def __init__(self):
            self.updated = None

        def get_session(self, _session_id):
            return {"system_prompt": "old stored prompt"}

        def update_system_prompt(self, _session_id, system_prompt):
            self.updated = system_prompt

    db = FakeDB()
    agent = SimpleNamespace(
        session_id="s1",
        _session_db=db,
        _cached_system_prompt=None,
        _force_system_prompt_rebuild_once=True,
        _build_system_prompt=lambda _system_message=None: "fresh prompt",
        model="test",
        platform="tui",
    )

    _restore_or_build_system_prompt(agent, None, [{"role": "user", "content": "hi"}])

    assert agent._cached_system_prompt == "fresh prompt"
    assert db.updated == "fresh prompt"
    assert agent._force_system_prompt_rebuild_once is False
