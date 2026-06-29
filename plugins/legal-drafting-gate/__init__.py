"""Legal drafting gate plugin.

Blocks legal document mutation tools until the project has minimum drafting
prerequisites, while allowing an explicit user-approved bypass with an audit
log. This keeps legal drafting discipline in the harness layer instead of
relying on prompt compliance.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


_MUTATION_TOOLS = {
    "lex_edit",
    "lex_format",
    "lex_list",
    "lex_section",
    "lex_doc",
    "lex_clause",
    "lex_ref",
    "lex_tc",
    "lex_comment",
}

_READ_ONLY_OPS = {
    "lex_ref": {"list", "audit", "resolve", "term_format_audit"},
    "lex_tc": {"list"},
    "lex_comment": {"list"},
    "lex_doc": {"stats", "inspect", "read"},
    "lex_clause": {"extract", "compare"},
}

_REQUIRED_FILES = {
    "style_memo": Path(".hermes-project/drafting/style-memo.md"),
    "revision_plan": Path(".hermes-project/drafting/revision-plan.md"),
}


def register(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _looks_like_docx_path(value: str) -> bool:
    return value.endswith((".docx", ".DOCX")) or value.startswith("/workingfile/")


def _iter_string_values(value: Any):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _iter_string_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_string_values(nested)
    elif isinstance(value, str):
        yield value


def _project_root_from_args(args: dict) -> Optional[Path]:
    for key in ("project_dir", "project_path", "cwd"):
        raw = str(args.get(key) or "").strip()
        if raw:
            p = Path(raw).expanduser()
            if p.exists():
                return p.resolve()
    for key in ("path", "document_path", "docx_path", "file_path"):
        raw = str(args.get(key) or "").strip()
        if raw:
            root = _project_root_from_path(raw)
            if root:
                return root
    for value in _iter_string_values(args):
        if _looks_like_docx_path(value):
            root = _project_root_from_path(value)
            if root:
                return root
    return None


def _project_root_from_path(raw: str) -> Optional[Path]:
    try:
        p = Path(raw).expanduser()
    except Exception:
        return None
    candidates = [p if p.is_dir() else p.parent, *p.parents]
    for candidate in candidates:
        if (candidate / ".hermes-project").is_dir():
            return candidate.resolve()
    if str(p).startswith("/workingfile/"):
        parts = p.parts
        if len(parts) >= 3:
            return (Path(parts[0]) / parts[1] / parts[2]).resolve()
    return None


def _is_mutation(tool_name: str, args: dict) -> bool:
    if tool_name not in _MUTATION_TOOLS:
        return False
    read_only = _READ_ONLY_OPS.get(tool_name)
    if read_only is None:
        return True
    op = str(args.get("op") or args.get("action") or "").strip()
    return op not in read_only


def _missing_prereqs(project_root: Path) -> list[tuple[str, Path]]:
    missing: list[tuple[str, Path]] = []
    for key, rel in _REQUIRED_FILES.items():
        path = project_root / rel
        if not path.is_file() or not path.read_text(encoding="utf-8", errors="ignore").strip():
            missing.append((key, path))
    return missing


def _is_gate_scope(project_root: Path) -> bool:
    """Gate legal projects mounted under /workingfile.

    Tests use a temp path containing /workingfile/ to exercise the same path
    inference without writing into the host mount.
    """

    root = str(project_root)
    return root.startswith("/workingfile/") or "/workingfile/" in root


def _write_bypass_log(
    *,
    project_root: Path,
    tool_name: str,
    args: dict,
    missing: list[tuple[str, Path]],
    reason: str,
    session_id: str,
    task_id: str,
) -> None:
    log_path = project_root / ".hermes-project" / "drafting" / "gate-bypass-log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {}
    except Exception:
        data = {}
    entries = data.setdefault("bypasses", [])
    entries.append(
        {
            "timestamp": _now(),
            "tool_name": tool_name,
            "document_path": str(args.get("path") or args.get("document_path") or ""),
            "operation": str(args.get("op") or args.get("action") or ""),
            "missing": [{"key": key, "path": str(path)} for key, path in missing],
            "reason": reason,
            "session_id": session_id,
            "task_id": task_id,
        }
    )
    log_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _block_message(project_root: Path, missing: list[tuple[str, Path]]) -> str:
    missing_lines = "\n".join(f"- {key}: {path}" for key, path in missing)
    return (
        "Legal drafting gate blocked this document mutation. Before editing a "
        "legal document, create the minimum drafting prerequisites:\n"
        f"{missing_lines}\n\n"
        "Required workflow: read the template, create a style memo, create a "
        "revision/drafting plan, then call lex_edit again.\n\n"
        "If the user explicitly approves bypassing this gate for this specific "
        "edit, ask for confirmation first, then retry the same tool call with "
        "bypass_legal_drafting_gate=true and a non-empty bypass_reason. The "
        "bypass will be logged at "
        f"{project_root / '.hermes-project' / 'drafting' / 'gate-bypass-log.json'}."
    )


def _on_pre_tool_call(
    tool_name: str = "",
    args: Optional[dict] = None,
    task_id: str = "",
    session_id: str = "",
    **_: Any,
) -> Optional[dict]:
    args = args if isinstance(args, dict) else {}
    if not _is_mutation(tool_name, args):
        return None
    project_root = _project_root_from_args(args)
    if project_root is None:
        return None
    if not _is_gate_scope(project_root):
        return None
    missing = _missing_prereqs(project_root)
    if not missing:
        return None

    bypass = bool(args.get("bypass_legal_drafting_gate"))
    reason = str(args.get("bypass_reason") or "").strip()
    if bypass and reason:
        _write_bypass_log(
            project_root=project_root,
            tool_name=tool_name,
            args=args,
            missing=missing,
            reason=reason,
            session_id=session_id,
            task_id=task_id,
        )
        return None

    return {"action": "block", "message": _block_message(project_root, missing)}
