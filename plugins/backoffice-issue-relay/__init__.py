"""Backoffice Issue Relay plugin.

Captures technical failures encountered during legal work and writes a redacted
JSON issue into shared storage. The legal session can continue, while the
maintainer side gets a durable, reproducible maintenance queue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

_FALLBACK_ISSUE_DIR = Path("/workingfile/.lex-hermes-backoffice/issues")

_LEX_TOOL_NAMES = {
    "lex_read",
    "lex_edit",
    "lex_stats",
    "lex_tc",
    "lex_ocr",
    "lex_ref",
    "lex_heal",
    "lex_review",
}

_WORKFLOW_TOOL_PREFIXES = (
    "swarm_",
    "project_",
    "legal_",
)

_SENSITIVE_KEYS = {
    "content",
    "document_text",
    "html",
    "markdown",
    "message",
    "messages",
    "new_text",
    "old_text",
    "paragraph",
    "paragraphs",
    "prompt",
    "query",
    "replacement",
    "result",
    "text",
    "value",
}

_PATH_KEYS = {
    "docx_path",
    "document_path",
    "file",
    "file_path",
    "output",
    "output_docx",
    "path",
    "pdf_path",
    "target_path",
    "template_path",
}

_TECH_ERROR_PATTERNS: Tuple[Tuple[str, str, str], ...] = (
    (
        "ocr",
        "high",
        r"401 Client Error: Unauthorized|MinerU|MINERU|192\.168\.11\.5|9987|Connection refused",
    ),
    (
        "environment",
        "high",
        r"ModuleNotFoundError|ImportError|No module named|command not found|exit 127",
    ),
    (
        "lexitool",
        "high",
        r"Traceback|unexpected keyword argument|AttributeError|TypeError|ValueError: .*docx|lxml|openxml|zipfile",
    ),
    (
        "permission",
        "high",
        r"Permission denied|Errno 13|Operation not permitted",
    ),
    (
        "workflow",
        "medium",
        r"stale gateway|gateway_state|dispatcher|worker.*claim|kanban|claim lock",
    ),
)

_ORDINARY_USER_ERRORS = (
    "File not found",
    "No such file",
    "not found",
    "targets is required",
    "Invalid op",
    "missing required",
)


def register(ctx: Any) -> None:
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
    ctx.register_hook("kanban_task_blocked", _on_kanban_task_blocked)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_result(result: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    if isinstance(result, dict):
        return result, json.dumps(result, ensure_ascii=False, default=str)
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed, result
        except Exception:
            pass
        return None, result
    return None, str(result)


def _is_candidate_tool(tool_name: str) -> bool:
    return (
        tool_name in _LEX_TOOL_NAMES
        or tool_name.startswith("lex_")
        or tool_name.startswith(_WORKFLOW_TOOL_PREFIXES)
    )


def _error_text_from_result(result: Any) -> str:
    parsed, text = _parse_result(result)
    if parsed:
        for key in ("error", "stderr", "exception", "message"):
            value = parsed.get(key)
            if value:
                return str(value)
        if parsed.get("ok") is False or parsed.get("success") is False:
            return text
    return text


def _classify(tool_name: str, result: Any) -> Optional[Tuple[str, str, str]]:
    if not _is_candidate_tool(tool_name):
        return None

    parsed, raw_text = _parse_result(result)
    error_text = _error_text_from_result(result).strip()
    has_error_flag = bool(
        parsed
        and (
            parsed.get("error")
            or parsed.get("ok") is False
            or parsed.get("success") is False
        )
    )
    if not has_error_flag and "[error]" not in raw_text and "Traceback" not in raw_text:
        return None

    category = "lexitool" if tool_name.startswith("lex_") else "workflow"
    severity = "medium"
    for candidate_category, candidate_severity, pattern in _TECH_ERROR_PATTERNS:
        if re.search(pattern, error_text, flags=re.IGNORECASE | re.DOTALL):
            category = candidate_category
            severity = candidate_severity
            break
    else:
        if any(marker.lower() in error_text.lower() for marker in _ORDINARY_USER_ERRORS):
            return None

    summary = _summarize_error(tool_name, category, error_text)
    return category, severity, summary


def _summarize_error(tool_name: str, category: str, error_text: str) -> str:
    cleaned = " ".join(error_text.split())
    if len(cleaned) > 180:
        cleaned = cleaned[:177] + "..."
    if not cleaned:
        cleaned = "tool returned an error"
    return f"{category}: {tool_name} failed: {cleaned}"


def _redact_args(value: Any, key: str = "") -> Any:
    key_l = key.lower()
    if isinstance(value, dict):
        return {str(k): _redact_args(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        if key_l in _SENSITIVE_KEYS:
            return {"redacted": True, "type": "list", "items": len(value)}
        return [_redact_args(v, key) for v in value[:20]]
    if isinstance(value, str):
        if key_l in _SENSITIVE_KEYS:
            return {"redacted": True, "type": "str", "length": len(value)}
        if key_l in _PATH_KEYS or _looks_like_path(value):
            return value
        if len(value) > 300:
            return {"redacted": True, "type": "str", "length": len(value)}
        return value
    return value


def _looks_like_path(value: str) -> bool:
    return value.startswith(("/", "~/")) or value.endswith((".docx", ".pdf", ".md", ".html", ".json"))


def _iter_string_values(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for nested in value.values():
            yield from _iter_string_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_string_values(nested)
    elif isinstance(value, str):
        yield value


def _artifact_paths(args: Any) -> list[str]:
    paths: list[str] = []
    for value in _iter_string_values(args):
        if _looks_like_path(value):
            paths.append(value)
    return sorted(set(paths))


def _project_root_from_args(args: Any) -> Optional[Path]:
    for value in _iter_string_values(args):
        if not _looks_like_path(value):
            continue
        try:
            p = Path(value).expanduser()
        except Exception:
            continue
        candidates = [p if p.is_dir() else p.parent, *p.parents]
        for candidate in candidates:
            if (candidate / ".hermes-project").is_dir():
                return candidate
        if str(p).startswith("/workingfile/"):
            parts = p.parts
            if len(parts) >= 3:
                return Path(parts[0]) / parts[1] / parts[2]
    return None


def _issue_dir(project_root: Optional[Path]) -> Path:
    if project_root is not None:
        return project_root / ".hermes-project" / "backoffice" / "issues"
    return _FALLBACK_ISSUE_DIR


def _fingerprint(category: str, tool_name: str, error: str, project_path: str) -> str:
    normalized = " ".join(error.split())[:500]
    seed = json.dumps(
        {
            "category": category,
            "tool_name": tool_name,
            "error": normalized,
            "project_path": project_path,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _record_issue(
    *,
    tool_name: str,
    args: Optional[Dict[str, Any]],
    result: Any,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: Optional[int] = None,
    category_override: Optional[str] = None,
    severity_override: Optional[str] = None,
    summary_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    args = args if isinstance(args, dict) else {}
    if category_override:
        category, severity, summary = (
            category_override,
            severity_override or "medium",
            summary_override or f"{category_override}: {tool_name} blocked",
        )
        error_text = _error_text_from_result(result)
    else:
        classified = _classify(tool_name, result)
        if classified is None:
            return None
        category, severity, summary = classified
        error_text = _error_text_from_result(result)

    project_root = _project_root_from_args(args)
    project_path = str(project_root) if project_root is not None else ""
    fingerprint = _fingerprint(category, tool_name, error_text, project_path)
    issue_dir = _issue_dir(project_root)

    with _LOCK:
        existing = sorted(issue_dir.glob(f"*_{fingerprint}.json")) if issue_dir.exists() else []
        if existing:
            path = existing[0]
            try:
                issue = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                issue = {}
            issue["last_seen_at"] = _now()
            issue["occurrences"] = int(issue.get("occurrences") or 1) + 1
            issue.setdefault("status", "open")
            _atomic_write_json(path, issue)
            issue["_path"] = str(path)
            return issue

        issue_id = f"bo_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{fingerprint}"
        path = issue_dir / f"{issue_id}_{fingerprint}.json"
        issue = {
            "id": issue_id,
            "created_at": _now(),
            "last_seen_at": _now(),
            "occurrences": 1,
            "fingerprint": fingerprint,
            "project_id": "",
            "project_path": project_path,
            "session_id": session_id or "",
            "profile": os.environ.get("HERMES_PROFILE", "") or os.environ.get("HERMES_ACTIVE_PROFILE", ""),
            "surface": os.environ.get("HERMES_SURFACE", "") or os.environ.get("HERMES_PLATFORM", ""),
            "category": category,
            "severity": severity,
            "summary": summary,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id or "",
            "task_id": task_id or "",
            "duration_ms": duration_ms,
            "tool_args_redacted": _redact_args(args),
            "error": " ".join(error_text.split())[:1200],
            "repro_steps": [
                f"Run tool {tool_name} with the redacted arguments shown in this issue.",
                "Use artifact_paths for source files; do not infer document content from redacted fields.",
            ],
            "artifact_paths": _artifact_paths(args),
            "status": "open",
            "fix_commit": "",
            "fixed_image_digest": "",
        }
        _atomic_write_json(path, issue)
        issue["_path"] = str(path)
        return issue


def _append_issue_note(result: Any, issue: Dict[str, Any]) -> str:
    note = {
        "id": issue.get("id"),
        "path": issue.get("_path"),
        "status": issue.get("status", "open"),
        "summary": issue.get("summary"),
    }
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except Exception:
            return result + f"\n\n[backoffice_issue] {json.dumps(note, ensure_ascii=False)}"
        if isinstance(parsed, dict):
            parsed["backoffice_issue"] = note
            return json.dumps(parsed, ensure_ascii=False)
    if isinstance(result, dict):
        copied = dict(result)
        copied["backoffice_issue"] = note
        return json.dumps(copied, ensure_ascii=False)
    return str(result) + f"\n\n[backoffice_issue] {json.dumps(note, ensure_ascii=False)}"


def _on_post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: Optional[int] = None,
    **_: Any,
) -> None:
    try:
        _record_issue(
            tool_name=tool_name,
            args=args,
            result=result,
            task_id=task_id,
            session_id=session_id,
            tool_call_id=tool_call_id,
            duration_ms=duration_ms,
        )
    except Exception as exc:
        logger.debug("backoffice issue relay post_tool_call failed: %s", exc)


def _on_transform_tool_result(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: Optional[int] = None,
    **_: Any,
) -> Optional[str]:
    try:
        issue = _record_issue(
            tool_name=tool_name,
            args=args,
            result=result,
            task_id=task_id,
            session_id=session_id,
            tool_call_id=tool_call_id,
            duration_ms=duration_ms,
        )
        if not issue:
            return None
        return _append_issue_note(result, issue)
    except Exception as exc:
        logger.debug("backoffice issue relay transform_tool_result failed: %s", exc)
        return None


def _on_kanban_task_blocked(task_id: str = "", reason: str = "", **kwargs: Any) -> None:
    reason_text = reason or json.dumps(kwargs, ensure_ascii=False, default=str)
    if not re.search(r"tool|lexitool|ocr|gateway|dispatcher|worker|permission|import|module|容器|权限", reason_text, re.IGNORECASE):
        return
    try:
        _record_issue(
            tool_name="kanban_task_blocked",
            args={"task_id": task_id, "reason": reason_text},
            result={"error": reason_text},
            task_id=task_id,
            category_override="workflow",
            severity_override="medium",
            summary_override=_summarize_error("kanban_task_blocked", "workflow", reason_text),
        )
    except Exception as exc:
        logger.debug("backoffice issue relay kanban hook failed: %s", exc)
