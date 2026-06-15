"""Runtime tool-surface refresh for long-lived AIAgent instances."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger("run_agent")


def _tool_defs_fingerprint(tools: List[Dict[str, Any]] | None) -> str:
    """Return a stable fingerprint for tool names and schemas."""
    normalized = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if isinstance(fn, dict):
            normalized.append(fn)
    normalized.sort(key=lambda item: str(item.get("name") or ""))
    try:
        return json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return repr(normalized)


def _append_agent_level_tools(agent, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Re-apply non-registry tools that are appended during agent init.

    Registry tool refresh must not drop memory-provider or context-engine tools,
    because those are generated from live provider/compressor state rather than
    from tools.registry.
    """
    result = list(tools or [])
    existing = {
        item.get("function", {}).get("name")
        for item in result
        if isinstance(item, dict)
    }

    if getattr(agent, "_memory_manager", None) and (
        getattr(agent, "enabled_toolsets", None) is None
        or "memory" in (getattr(agent, "enabled_toolsets", None) or [])
    ):
        try:
            for schema in agent._memory_manager.get_all_tool_schemas():
                name = schema.get("name", "")
                if name and name not in existing:
                    result.append({"type": "function", "function": schema})
                    existing.add(name)
        except Exception as exc:
            logger.debug("memory tool refresh failed: %s", exc)

    agent._context_engine_tool_names = set(getattr(agent, "_context_engine_tool_names", set()) or set())
    if (
        getattr(agent, "context_compressor", None)
        and (
            getattr(agent, "enabled_toolsets", None) is None
            or "context_engine" in (getattr(agent, "enabled_toolsets", None) or [])
        )
    ):
        try:
            refreshed_context_names = set()
            for schema in agent.context_compressor.get_tool_schemas():
                name = schema.get("name", "")
                if not name:
                    continue
                refreshed_context_names.add(name)
                if name not in existing:
                    result.append({"type": "function", "function": schema})
                    existing.add(name)
            agent._context_engine_tool_names = refreshed_context_names
        except Exception as exc:
            logger.debug("context-engine tool refresh failed: %s", exc)

    return result


def refresh_agent_tools_if_needed(agent, *, force: bool = False) -> bool:
    """Refresh a live agent's tool schemas without resetting conversation state.

    Returns True when the effective tool surface changed. A change invalidates
    the cached system prompt so tool-aware guidance is rebuilt next turn.
    """
    try:
        from agent.prompt_builder import KANBAN_GUIDANCE
        from model_tools import get_tool_definitions

        refreshed = get_tool_definitions(
            enabled_toolsets=getattr(agent, "enabled_toolsets", None),
            disabled_toolsets=getattr(agent, "disabled_toolsets", None),
            quiet_mode=True,
        )
        refreshed = _append_agent_level_tools(agent, refreshed)
        new_fingerprint = _tool_defs_fingerprint(refreshed)
        old_fingerprint = getattr(agent, "_tool_definitions_fingerprint", None)
        if old_fingerprint is None:
            old_fingerprint = _tool_defs_fingerprint(getattr(agent, "tools", None))

        if not force and new_fingerprint == old_fingerprint:
            agent._tool_definitions_fingerprint = old_fingerprint
            return False

        agent.tools = refreshed
        agent.valid_tool_names = {
            item["function"]["name"]
            for item in refreshed
            if isinstance(item, dict)
            and isinstance(item.get("function"), dict)
            and item["function"].get("name")
        }
        agent._tool_definitions_fingerprint = new_fingerprint
        agent._kanban_worker_guidance = (
            KANBAN_GUIDANCE if "kanban_show" in agent.valid_tool_names else ""
        )
        agent._cached_system_prompt = None
        agent._force_system_prompt_rebuild_once = True
        logger.info(
            "Refreshed tool surface for session %s: %d tools",
            getattr(agent, "session_id", ""),
            len(refreshed),
        )
        return True
    except Exception as exc:
        logger.debug("tool refresh failed for live agent: %s", exc, exc_info=True)
        return False


__all__ = ["refresh_agent_tools_if_needed", "_tool_defs_fingerprint"]
