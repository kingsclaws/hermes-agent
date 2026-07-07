"""lex_heal — Self-healing for lexitool via Claude Code CLI.

When lexitool tools fail with internal errors (bugs, not usage mistakes),
the agent calls lex_heal to:
1. Spawn Claude Code to diagnose and fix the lexitool source
2. Hot-reload all lexitool tools without restart
3. Optionally append the fix pattern to the SOP for future reference
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.registry import invalidate_check_fn_cache, registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# Reuse the same check as lexitool_tool — lexitool must be importable
_LEXITOOL_PATH = Path("/root/.hermes/tools/lexitool")
if str(_LEXITOOL_PATH.parent) not in sys.path:
    sys.path.insert(0, str(_LEXITOOL_PATH.parent))


def _check_lexitool():
    """Return True when lexitool is importable."""
    import importlib.util
    try:
        return importlib.util.find_spec("lexitool") is not None
    except (ImportError, ValueError):
        return False


def _resolve_lexitool_source_dir() -> str | None:
    """Find the lexitool source directory.

    Tries the CI image path first, then the lex-hermes image path.
    """
    candidates = [
        "/opt/hermes/vendor/lexitool",
        "/opt/lexitool",
    ]
    for d in candidates:
        if os.path.isdir(d):
            return d
    return None


def _resolve_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


LEX_HEAL_SCHEMA = {
    "name": "lex_heal",
    "description": (
        "Self-healing: invoke Claude Code to fix failing lexitool code. "
        "Use this when lexitool tools (lex_read, lex_edit, etc.) repeatedly "
        "return errors that look like internal bugs (KeyError, AttributeError, "
        "unexpected None, parsing failures on valid documents). Claude Code "
        "will diagnose the failure in the lexitool source, apply a minimal fix, "
        "and the tools will be hot-reloaded automatically.\n\n"
        "IMPORTANT: Only use after confirming the error is internal to lexitool, "
        "not a usage mistake (wrong path, bad target syntax, etc.)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "failure_description": {
                "type": "string",
                "description": (
                    "What is failing: tool name, error message, expected behavior. "
                    "Example: 'lex_read(paras=[5]) returns KeyError: paragraphs for "
                    "files with tracked changes in tables. lex_stats works fine.'"
                ),
            },
            "max_turns": {
                "type": "integer",
                "description": "Maximum Claude Code turns for the fix attempt (default: 10).",
            },
            "update_sop": {
                "type": "boolean",
                "description": "Append the fix pattern to the lexitool SOP after successful fix (default: true).",
            },
        },
        "required": ["failure_description"],
    },
}


_FIX_PROMPT_TEMPLATE = """\
The hermes-agent lexitool tools have a bug. Diagnose and fix it in the lexitool
Python library source code in the current directory.

Failure report from the agent:
{failure_description}

Steps:
1. Read the relevant source files to understand the failure
2. Identify the root cause — be precise about which file and line
3. Apply a MINIMAL, targeted fix — change as few lines as possible
4. Verify the fix: `python3 -c "import lexitool; ..."` using the exact call
   that was failing

RULES:
- Only edit files in the current directory tree
- Do NOT refactor, rename, or reorganize code unrelated to the bug
- Do NOT change public APIs or add new features
- Keep backward compatibility
- If the bug is in markup.py, check whether openxml_package.py is also affected"""


def _handle_lex_heal(args: dict, **kwargs) -> str:
    failure_description = args["failure_description"]
    max_turns = args.get("max_turns", 10)
    update_sop = args.get("update_sop", True)

    source_dir = _resolve_lexitool_source_dir()
    if source_dir is None:
        return tool_error(
            "Could not find lexitool source directory. "
            "Expected /opt/hermes/vendor/lexitool/ or /opt/lexitool/."
        )

    # Check that claude CLI is available
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        return tool_error(
            "Claude Code CLI not found. Install it with: "
            "npm install -g @anthropic-ai/claude-code"
        )

    # Build the prompt
    prompt = _FIX_PROMPT_TEMPLATE.format(failure_description=failure_description)

    # Build the command
    cmd = [
        claude_bin,
        "-p", prompt,
        "--allowedTools", "Read,Edit,Write,Bash",
        "--max-turns", str(max_turns),
        "--output-format", "json",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
    ]

    # Inherit the parent's environment (includes ANTHROPIC_API_KEY)
    env = os.environ.copy()

    logger.info("lex_heal: spawning Claude Code in %s (max_turns=%d)", source_dir, max_turns)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max_turns * 120,
            env=env,
            cwd=source_dir,
        )
    except subprocess.TimeoutExpired:
        return tool_error(
            f"Claude Code timed out after {max_turns * 120}s. "
            "Try increasing max_turns or simplifying the failure description."
        )
    except FileNotFoundError:
        return tool_error(f"Claude Code binary not found at: {claude_bin}")
    except Exception as e:
        return tool_error(f"Failed to spawn Claude Code: {e}")

    # Parse Claude Code JSON output
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if result.returncode != 0 and not stdout:
        return tool_error(
            f"Claude Code exited with code {result.returncode}. "
            f"stderr: {stderr[:500]}"
        )

    try:
        cc_result = json.loads(stdout)
    except json.JSONDecodeError:
        # Maybe there's useful output even if not valid JSON
        return tool_error(
            f"Claude Code returned non-JSON output (exit {result.returncode}). "
            f"stdout preview: {stdout[:500]}"
        )

    subtype = cc_result.get("subtype", "unknown")
    result_text = cc_result.get("result", "")
    num_turns = cc_result.get("num_turns", 0)
    cost_usd = cc_result.get("total_cost_usd", 0)

    if subtype != "success":
        return tool_error(
            f"Claude Code fix attempt did not succeed (subtype={subtype}). "
            f"Turns: {num_turns}, cost: ${cost_usd:.4f}. "
            f"Result: {result_text[:500]}"
        )

    # ── Hot reload lexitool tools ──
    try:
        from tools.lexitool_tool import reload_lexitool_tools
        reload_info = reload_lexitool_tools()
    except Exception as e:
        reload_info = {"error": str(e)}
        logger.warning("lex_heal: hot-reload failed: %s", e)

    # ── SOP update ──
    sop_result = None
    if update_sop:
        sop_result = _append_fix_to_sop(
            failure_description=failure_description,
            result_text=result_text,
            cost_usd=cost_usd,
            num_turns=num_turns,
            reload_info=reload_info,
        )

    response = {
        "ok": True,
        "message": "Lexitool fix applied and tools hot-reloaded.",
        "cost_usd": cost_usd,
        "turns": num_turns,
        "result_preview": result_text[:300],
        "reload": reload_info,
    }
    if sop_result:
        response["sop_updated"] = sop_result

    return json.dumps(response, ensure_ascii=False, default=str)


def _append_fix_to_sop(
    failure_description: str,
    result_text: str,
    cost_usd: float,
    num_turns: int,
    reload_info: dict,
) -> str | None:
    """Append a fix pattern entry to the lexitool SOP file.

    Returns a status message string, or None if skipped.
    """
    sop_path = Path(".hermes-project/roles/lex-editor.md")
    if not sop_path.exists():
        logger.info("lex_heal: SOP file not found at %s, skipping update", sop_path)
        return None

    # Derive a one-line summary from the result text
    summary_line = result_text.split("\n")[0] if result_text else "fix applied"
    summary_line = summary_line[:120]

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    entry = f"""
### Fix Pattern {date_str}: {summary_line}

**Failure**: {failure_description[:300]}
**Fix**: {result_text[:500]}
**Cost**: ${cost_usd:.4f} USD, {num_turns} turns
**Reload**: {json.dumps(reload_info, default=str)}
"""

    try:
        existing = sop_path.read_text(encoding="utf-8")

        section_header = "## Self-Healing Fix Patterns"
        if section_header not in existing:
            # Append the section header first
            existing = existing.rstrip() + f"\n\n{section_header}\n"

        # Append the entry
        updated = existing.rstrip() + entry
        sop_path.write_text(updated, encoding="utf-8")
        return f"Appended fix pattern to {sop_path}"
    except Exception as e:
        logger.warning("lex_heal: failed to update SOP: %s", e)
        return None



# ── Registration ──

_TOOLS = [
    ("lex_heal", "lexitool", LEX_HEAL_SCHEMA, _handle_lex_heal),
]

for _name, _toolset, _schema, _handler in _TOOLS:
    registry.register(
        name=_name,
        toolset=_toolset,
        schema=_schema,
        handler=_handler,
        check_fn=_check_lexitool,
        description=_schema.get("description", ""),
        emoji="",
    )


def reload_lex_heal() -> dict:
    """Hot-reload the lex_heal tool by re-importing this module."""
    import importlib
    import tools.lex_heal_tool

    before = set(registry.get_tool_names_for_toolset("lexitool"))
    for name in list(before):
        if name == "lex_heal":
            registry.deregister(name)

    importlib.reload(tools.lex_heal_tool)
    invalidate_check_fn_cache()

    after = set(registry.get_tool_names_for_toolset("lexitool"))
    return {
        "deregistered": list(before & {"lex_heal"}),
        "reregistered": list(after - before),
        "total_lexitool_tools": len(after),
    }
