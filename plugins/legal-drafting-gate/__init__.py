"""Legal runtime gate plugin.

Blocks legal document mutation tools until the project has minimum drafting
prerequisites, while allowing an explicit user-approved bypass with an audit
log. This keeps legal drafting discipline in the harness layer instead of
relying on prompt compliance.

Also enforces:
- lex-master routes project work instead of executing it directly.
- legal delivery claims include evidence coverage.
"""

from __future__ import annotations

import json
import os
import re
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

_LEX_MASTER_EXECUTION_TOOLS = {
    "lex_edit",
    "lex_format",
    "lex_list",
    "lex_section",
    "lex_doc",
    "lex_clause",
    "lex_ref",
    "lex_tc",
    "lex_comment",
    "lex_heal",
    "lex_review",
    "swarm_board_create",
    "swarm_task_create",
    "swarm_task_handoff",
    "swarm_task_approve",
    "swarm_task_reject",
    "swarm_task_collect",
    "swarm_dispatch_now",
    "project_facts",
    "project_update_task",
    "project_add_task",
    "project_delete_task",
}

_LEX_MASTER_DIRECT_READ_TOOLS = {
    "lex_master_route",
    "project_list",
    "session_search",
    # lex-master must be able to identify projects and inspect source material
    # before routing.  Keep these read-only/OCR tools available while blocking
    # direct editing and project execution tools below.
    "lex_ocr",
    "lex_read",
    "lex_scan",
    "lex_stats",
    "lex_table_list",
}

_DELIVERY_CLAIM_RE = re.compile(
    r"(已完成|完成了|已经完成|修改完成|修订完成|审阅完成|审核完成|校对完成|全部完成|"
    r"done|completed|reviewed|revised|updated)",
    re.IGNORECASE,
)
_LEGAL_WORK_RE = re.compile(
    r"(合同|协议|文书|法律|审阅|修订|修改|校对|交叉引用|定义|条款|附件|redline|"
    r"docx|lex_|lexitool|proofread|cross[- ]?reference)",
    re.IGNORECASE,
)
_DELIVERY_WORK_ACTION_RE = re.compile(
    r"(起草|制作|修改|修订|审阅|审核|校对|交付|出具|生成交付|"
    r"draft(?:ed|ing)?|revis(?:e|ed|ing)|review(?:ed|ing)?|proofread|redline|deliver(?:ed|y)?)",
    re.IGNORECASE,
)
_DOCUMENT_DELIVERY_ANCHOR_RE = re.compile(
    r"(/workingfile/|[/\\\w\u4e00-\u9fff .()\[\]-]+\.(?:docx|pdf|html|md)\b|"
    r"lex_(?:read|diff|proofread|ref|edit|tc|ocr|verify)|"
    r"§\s*\d+|第\s*\d+(?:\.\d+)?\s*条|段落|页码|页面|全文|全篇|"
    r"合同修改|协议修改|文书修改|修订文件|交付文件|交付包|报告路径)",
    re.IGNORECASE,
)
_FUTURE_OR_CONDITIONAL_COMPLETION_RE = re.compile(
    r"(完成后|完成时|完成之前|完成以后|待.{0,12}完成|等.{0,12}完成|"
    r"如果.{0,12}完成|如.{0,12}完成|完成后我会|完成后再|完成后将)",
    re.IGNORECASE,
)
_EVIDENCE_MARKERS = (
    "evidence",
    "coverage",
    "覆盖",
    "核对范围",
    "审阅范围",
    "验证",
    "读回",
    "lex_read",
    "lex_diff",
    "lex_proofread",
    "paragraph",
    "段落",
    "§",
)


def register(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("transform_llm_output", _on_transform_llm_output)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_profile_name() -> str:
    for key in ("HERMES_PROFILE", "HERMES_ACTIVE_PROFILE"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    home = str(os.environ.get("HERMES_HOME") or "")
    if "/profiles/" in home:
        return home.rstrip("/").split("/profiles/", 1)[1].split("/", 1)[0]
    return ""


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


def _write_lex_master_bypass_log(
    *,
    tool_name: str,
    args: dict,
    reason: str,
    session_id: str,
    task_id: str,
) -> None:
    log_path = Path(os.environ.get("HERMES_HOME") or "/root/.hermes/profiles/lex-master") / "master-route-bypass-log.json"
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
            "operation": str(args.get("op") or args.get("action") or ""),
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


def _lex_master_route_block_message(tool_name: str) -> str:
    return (
        "Lex-master routing gate blocked this tool call. The lex-master profile "
        "is the coordinator-of-coordinators and must not execute project work "
        f"directly with `{tool_name}`. First call `lex_master_route` with "
        "`action='resolve'` or `action='dispatch'` and route the task to the "
        "matched project coordinator session.\n\n"
        "If the user explicitly approves a one-off emergency bypass, retry with "
        "`bypass_lex_master_route_gate=true` and a non-empty `bypass_reason`. "
        "The bypass will be logged in the lex-master profile."
    )


def _lex_master_route_gate(
    *,
    tool_name: str,
    args: dict,
    task_id: str,
    session_id: str,
) -> Optional[dict]:
    if _active_profile_name() != "lex-master":
        return None
    if tool_name in _LEX_MASTER_DIRECT_READ_TOOLS:
        return None
    if tool_name not in _LEX_MASTER_EXECUTION_TOOLS and not tool_name.startswith(("lex_", "swarm_")):
        return None
    bypass = bool(args.get("bypass_lex_master_route_gate"))
    reason = str(args.get("bypass_reason") or "").strip()
    if bypass and reason:
        _write_lex_master_bypass_log(
            tool_name=tool_name,
            args=args,
            reason=reason,
            session_id=session_id,
            task_id=task_id,
        )
        return None
    return {"action": "block", "message": _lex_master_route_block_message(tool_name)}


def _on_pre_tool_call(
    tool_name: str = "",
    args: Optional[dict] = None,
    task_id: str = "",
    session_id: str = "",
    **_: Any,
) -> Optional[dict]:
    args = args if isinstance(args, dict) else {}
    route_block = _lex_master_route_gate(
        tool_name=tool_name,
        args=args,
        task_id=task_id,
        session_id=session_id,
    )
    if route_block is not None:
        return route_block
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


def _looks_like_legal_delivery(response_text: str) -> bool:
    text = response_text or ""
    if not _DELIVERY_CLAIM_RE.search(text):
        return False
    if _FUTURE_OR_CONDITIONAL_COMPLETION_RE.search(text):
        return False
    return bool(
        _LEGAL_WORK_RE.search(text)
        and _DELIVERY_WORK_ACTION_RE.search(text)
        and _DOCUMENT_DELIVERY_ANCHOR_RE.search(text)
    )


def _has_evidence_coverage(response_text: str) -> bool:
    text = response_text or ""
    lowered = text.lower()
    has_html = bool(re.search(r"[/\\\w\u4e00-\u9fff .()\[\]-]+\.html\b", text, re.IGNORECASE))
    has_docx = bool(re.search(r"[/\\\w\u4e00-\u9fff .()\[\]-]+\.docx\b", text, re.IGNORECASE))
    if not (has_html and has_docx):
        return False
    marker_count = sum(1 for marker in _EVIDENCE_MARKERS if marker.lower() in lowered)
    if marker_count >= 2:
        return True
    table_like = "|" in text and re.search(r"(文件|范围|段落|验证|status|issue|gap)", text, re.IGNORECASE)
    return bool(table_like)


def _delivery_gate_message(response_text: str) -> str:
    preview = response_text.strip()
    if len(preview) > 1200:
        preview = preview[:1197] + "..."
    return (
        "Legal delivery gate blocked the final response because it appears to "
        "claim completion of legal document work without an evidence coverage "
        "summary.\n\n"
        "Before reporting completion, provide:\n"
        "- user-facing `.html` report path and converted `.docx` report path;\n"
        "- optional `.md` working draft path for future agents;\n"
        "- source files reviewed or modified;\n"
        "- paragraph/page/section ranges covered;\n"
        "- readback or verification tools used, such as lex_read, lex_diff, lex_proofread, lex_ref;\n"
        "- unresolved gaps, failed reads/OCR, or items requiring user decision.\n\n"
        "Draft response that was blocked:\n\n"
        "```text\n"
        f"{preview}\n"
        "```"
    )


def _on_transform_llm_output(
    response_text: str = "",
    session_id: str = "",
    platform: str = "",
    **_: Any,
) -> Optional[str]:
    if not response_text:
        return None
    if not _looks_like_legal_delivery(response_text):
        return None
    if _has_evidence_coverage(response_text):
        return None
    return _delivery_gate_message(response_text)
