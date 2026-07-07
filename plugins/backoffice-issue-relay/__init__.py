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
import sys
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
    "execute_code",
    "terminal",
    "terminal_exec",
    "run_shell_command",
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
        "high",
        r"Workspace empty|Source files? not found|workingfile|worker.*workspace|workspace.*empty",
    ),
    (
        "workflow",
        "medium",
        r"stale gateway|gateway_state|dispatcher|worker.*claim|kanban|claim lock|hpswarm-coordinator",
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

_SOURCE_CANDIDATES_BY_TOOL = {
    "lex_ocr": [
        "tools/lexitool_tool.py",
        "vendor/lexitool/lexitool/ocr.py",
        "vendor/lexitool/lexitool/ocr_cli.py",
    ],
    "lex_edit": [
        "tools/lexitool_tool.py",
        "vendor/lexitool/lexitool/edit_ops.py",
        "vendor/lexitool/lexitool/openxml_opc.py",
        "vendor/lexitool/lexitool/tc_utils.py",
    ],
    "lex_read": [
        "tools/lexitool_tool.py",
        "vendor/lexitool/lexitool/markup.py",
    ],
    "lex_stats": [
        "tools/lexitool_tool.py",
        "vendor/lexitool/lexitool/markup.py",
    ],
    "lex_tc": [
        "tools/lexitool_tool.py",
        "vendor/lexitool/lexitool/tc_utils.py",
        "vendor/lexitool/lexitool/markup.py",
    ],
    "lex_ref": [
        "tools/lexitool_tool.py",
        "vendor/lexitool/lexitool/markup.py",
        "vendor/lexitool/lexitool/edit_ops.py",
    ],
    "kanban_task_blocked": [
        "hermes_cli/kanban_db.py",
        "tools/kanban_toolset.py",
        "plugins/kanban",
    ],
    "terminal": [
        "tools/terminal_tool.py",
        "tools/environments/docker.py",
        "hermes_cli/kanban_db.py",
    ],
    "execute_code": [
        "tools/python_tool.py",
        "tools/terminal_tool.py",
        "hermes_cli/kanban_db.py",
    ],
}

_SOURCE_CANDIDATES_BY_CATEGORY = {
    "environment": [
        "Dockerfile.patch",
        "hermes_cli/container_boot.py",
        "hermes_cli/service_manager.py",
    ],
    "permission": [
        "Dockerfile.patch",
        "hermes_cli/container_boot.py",
    ],
    "workflow": [
        "tools/legal_workflow_tool.py",
        "tools/legal_orchestration_tool.py",
        "tools/kanban_toolset.py",
        "hermes_cli/kanban_db.py",
    ],
}


def register(ctx: Any) -> None:
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
    ctx.register_hook("kanban_task_blocked", _on_kanban_task_blocked)
    ctx.register_hook("kanban_task_completed", _on_kanban_task_completed)
    ctx.register_hook("kanban_task_claimed", _on_kanban_task_lifecycle)
    ctx.register_hook("kanban_task_review_claimed", _on_kanban_task_lifecycle)
    ctx.register_hook("kanban_task_review_requested", _on_kanban_task_lifecycle)
    ctx.register_hook("kanban_task_review_gate_approved", _on_kanban_task_lifecycle)
    ctx.register_hook("kanban_task_unblocked", _on_kanban_task_lifecycle)


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
        or (
            bool(os.environ.get("HERMES_KANBAN_TASK"))
            and tool_name in {"terminal", "terminal_exec", "execute_code", "run_shell_command"}
        )
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


_QUOTED_PATH_IN_TEXT_RE = re.compile(
    r"(?P<quote>['\"`])(?P<path>(?:/workingfile|/workspace|/data/projects|/tmp|~)/.+?)(?P=quote)"
)
_PATH_IN_TEXT_RE = re.compile(
    r"(?P<path>(?:/workingfile|/workspace|/data/projects|/tmp|~)/[^\s'\"`<>]+)"
)


def _extract_paths_from_text(value: str) -> list[str]:
    paths: list[str] = []
    for match in _QUOTED_PATH_IN_TEXT_RE.finditer(value):
        path = match.group("path").rstrip(".,;:，。；：)")
        if path:
            paths.append(path)
    for match in _PATH_IN_TEXT_RE.finditer(value):
        path = match.group("path").rstrip(".,;:，。；：)")
        if path and not any(path == existing or path.startswith(existing + "/") for existing in paths):
            paths.append(path)
    return paths


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
        else:
            paths.extend(_extract_paths_from_text(value))
    return sorted(set(paths))


def _project_root_from_args(args: Any) -> Optional[Path]:
    for value in _iter_string_values(args):
        candidates_text = [value] if _looks_like_path(value) else []
        if not candidates_text:
            candidates_text.extend(_extract_paths_from_text(value))
        for candidate_text in candidates_text:
            try:
                p = Path(candidate_text).expanduser()
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


_AUTOFIX_LOCK = threading.Lock()
_AUTOFIX_RUNNING = False

# Category → Claude skill mapping
_SKILL_FOR_CATEGORY = {
    "bug":       "/diagnose",                    # crash / wrong output → fix
    "lexitool":  "/diagnose",                    # tool parameter errors → fix
    "workflow":  "/diagnose",                    # workflow execution errors → fix
    "feature":   "/to-prd",                      # new tool / feature → write PRD
    "sop":       "/to-issues",                   # process / pattern → create workflow tickets
    "arch":      "/to-issues",                   # design / structure → create issues
    "perf":      "/diagnose",                    # performance regression → fix
    "security":  "/diagnose",                    # security issue → fix
}


def _trigger_autofix(issue: Dict[str, Any]) -> None:
    """Spawn background autofix with the right skill for the issue type."""
    global _AUTOFIX_RUNNING
    category = str(issue.get("category", "")).lower()
    severity = str(issue.get("severity", "")).lower()

    # Auto-fix: bugs & errors immediately; features/SOPs queue for review
    auto_categories = ("bug", "lexitool", "workflow", "perf", "security")
    if category not in _SKILL_FOR_CATEGORY:
        return
    if category not in auto_categories and severity != "high":
        return  # features/SOPs: only auto-fix if high severity

    with _AUTOFIX_LOCK:
        if _AUTOFIX_RUNNING:
            return
        _AUTOFIX_RUNNING = True

    skill = _SKILL_FOR_CATEGORY.get(category, "/diagnose")

    def _run():
        global _AUTOFIX_RUNNING
        try:
            import subprocess, time
            time.sleep(5)
            issue_id = issue.get("id", "")
            subprocess.run(
                ["/opt/hermes/scripts/backoffice-autofix.sh",
                 "--issue", issue_id, "--max-fixes", "1", "--skill", skill],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=600,
            )
        except Exception:
            pass
        finally:
            with _AUTOFIX_LOCK:
                _AUTOFIX_RUNNING = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()


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


def _repo_roots() -> list[Path]:
    candidates = [
        Path("/opt/hermes"),
        Path("/opt/lex-hermes"),
        Path("/root/.hermes/hermes-agent"),
        Path.cwd(),
    ]
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            roots.append(candidate)
    return roots


def _source_candidates(tool_name: str, category: str) -> list[Dict[str, Any]]:
    relatives = list(_SOURCE_CANDIDATES_BY_TOOL.get(tool_name, []))
    relatives.extend(_SOURCE_CANDIDATES_BY_CATEGORY.get(category, []))
    if tool_name.startswith("lex_") and "tools/lexitool_tool.py" not in relatives:
        relatives.insert(0, "tools/lexitool_tool.py")

    results: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for rel in relatives:
        if rel in seen:
            continue
        seen.add(rel)
        found = [str(root / rel) for root in _repo_roots() if (root / rel).exists()]
        results.append({"relative": rel, "paths": found, "exists": bool(found)})
    return results


def _runtime_context() -> Dict[str, Any]:
    return {
        "cwd": os.getcwd(),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "hermes_profile": os.environ.get("HERMES_PROFILE", "") or os.environ.get("HERMES_ACTIVE_PROFILE", ""),
        "hermes_surface": os.environ.get("HERMES_SURFACE", "") or os.environ.get("HERMES_PLATFORM", ""),
        "hermes_bundled_plugins": os.environ.get("HERMES_BUNDLED_PLUGINS", ""),
        "hermes_home": os.environ.get("HERMES_HOME", ""),
        "kanban_board": os.environ.get("HERMES_KANBAN_BOARD", ""),
        "kanban_db": os.environ.get("HERMES_KANBAN_DB", ""),
    }


def _maintainer_prompt(issue: Dict[str, Any]) -> str:
    report_path = issue.get("report_path") or ""
    lines = [
        "Read this Lex-Hermes backoffice report and fix the underlying harness/tooling defect.",
        f"Report: {report_path}",
        f"Issue JSON: {issue.get('issue_path', '') or issue.get('_path', '')}",
        f"Category: {issue.get('category', '')}",
        f"Tool: {issue.get('tool_name', '')}",
        f"Summary: {issue.get('summary', '')}",
        "",
        "Expected workflow:",
        "1. Reproduce or explain why reproduction is impossible from the redacted data.",
        "2. Read the listed source_candidates and any relevant container logs.",
        "3. Fix the code, add/adjust tests, hot-copy into lex-hermes if needed.",
        "4. Commit, push, build & push the image when the fix is verified.",
        "5. Update the issue JSON with status/fix_commit/fixed_image_digest.",
    ]
    return "\n".join(lines)


def _write_markdown_report(issue_path: Path, issue: Dict[str, Any]) -> Path:
    report_path = issue_path.with_suffix(".REPORT.md")
    source_lines = []
    for item in issue.get("source_candidates", []):
        paths = item.get("paths") or []
        if paths:
            source_lines.append(f"- `{item.get('relative')}`")
            for path in paths:
                source_lines.append(f"  - `{path}`")
        else:
            source_lines.append(f"- `{item.get('relative')}` (not found in known roots)")

    args_json = json.dumps(issue.get("tool_args_redacted", {}), ensure_ascii=False, indent=2, sort_keys=True)
    runtime_json = json.dumps(issue.get("diagnostic_context", {}), ensure_ascii=False, indent=2, sort_keys=True)
    artifacts = issue.get("artifact_paths") or []
    artifact_lines = "\n".join(f"- `{path}`" for path in artifacts) if artifacts else "- None recorded"

    body = f"""# Lex-Hermes Backoffice Report

## Summary

- Issue: `{issue.get('id', '')}`
- Status: `{issue.get('status', '')}`
- Category: `{issue.get('category', '')}`
- Severity: `{issue.get('severity', '')}`
- Tool: `{issue.get('tool_name', '')}`
- Session: `{issue.get('session_id', '')}`
- Project: `{issue.get('project_path', '') or 'unknown'}`
- Created: `{issue.get('created_at', '')}`
- Last seen: `{issue.get('last_seen_at', '')}`
- Occurrences: `{issue.get('occurrences', '')}`

{issue.get('summary', '')}

## Observed Error

```text
{issue.get('error', '')}
```

## Redacted Tool Arguments

```json
{args_json}
```

## Artifact Paths

{artifact_lines}

## Source Candidates

{chr(10).join(source_lines) if source_lines else '- None inferred'}

## Runtime Context

```json
{runtime_json}
```

## Reproduction Notes

{chr(10).join(f'- {step}' for step in issue.get('repro_steps', []))}

## Maintainer Prompt

```text
{issue.get('maintainer_prompt', '')}
```
"""
    report_path.write_text(body, encoding="utf-8")
    return report_path


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
            issue.setdefault("diagnostic_context", _runtime_context())
            issue.setdefault("source_candidates", _source_candidates(tool_name, issue.get("category", category)))
            issue["issue_path"] = str(path)
            issue["report_path"] = str(path.with_suffix(".REPORT.md"))
            issue["maintainer_prompt"] = _maintainer_prompt(issue)
            _atomic_write_json(path, issue)
            _write_markdown_report(path, issue)
            issue["_path"] = str(path)
            return issue

        issue_id = f"bo_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{fingerprint}"
        path = issue_dir / f"{issue_id}_{fingerprint}.json"
        issue = {
            "id": issue_id,
            "issue_path": str(path),
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
            "source_candidates": _source_candidates(tool_name, category),
            "diagnostic_context": _runtime_context(),
            "status": "open",
            "fix_commit": "",
            "fixed_image_digest": "",
        }
        issue["report_path"] = str(path.with_suffix(".REPORT.md"))
        issue["maintainer_prompt"] = _maintainer_prompt(issue)
        _atomic_write_json(path, issue)
        _write_markdown_report(path, issue)
        _trigger_autofix(issue)
        issue["_path"] = str(path)
        return issue


def _append_issue_note(result: Any, issue: Dict[str, Any]) -> str:
    note = {
        "id": issue.get("id"),
        "path": issue.get("_path"),
        "report_path": issue.get("report_path"),
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


def _normalize_kanban_payload(
    payload: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    task_id = str(kwargs.get("task_id") or "")
    data = dict(kwargs)
    return {
        "event": data.get("event", "unknown"),
        "task_id": task_id,
        "task_title": data.get("task_title", ""),
        "task_status": data.get("task_status", ""),
        "assignee": data.get("assignee"),
        "data": data,
    }


def _on_kanban_task_completed(payload: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
    """Kanban task completed — check workflow graph for next steps."""
    payload = _normalize_kanban_payload(payload, **kwargs)
    task_id = str(payload.get("task_id", ""))
    title = str(payload.get("task_title", ""))
    logger.info("backoffice: task completed id=%s title=%s", task_id, title)
    # Write a lightweight completion record — no full issue needed
    _write_task_event("completed", payload)

def _on_kanban_task_lifecycle(payload: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
    """Generic lifecycle event — log for audit trail."""
    payload = _normalize_kanban_payload(payload, **kwargs)
    task_id = str(payload.get("task_id", ""))
    event = str(payload.get("event", "unknown"))
    logger.info("backoffice: task lifecycle event=%s id=%s", event, task_id)

def _write_task_event(event: str, payload: Dict[str, Any]) -> None:
    """Write a lightweight task event record for the audit trail."""
    try:
        task_id = str(payload.get("task_id", ""))
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "event": event, "task_id": task_id,
            "task_title": payload.get("task_title", ""),
            "timestamp": now,
        }
        path = BACKOFFICE_DIR / f"task_{event}_{task_id}_{now.replace(':','-')}.json"
        _atomic_write_json(path, record)
    except Exception:
        pass

def _on_kanban_task_blocked(
    payload: Optional[Dict[str, Any]] = None,
    task_id: str = "",
    reason: str = "",
    **kwargs: Any,
) -> None:
    payload = _normalize_kanban_payload(payload, task_id=task_id, reason=reason, **kwargs)
    task_id = str(payload.get("task_id") or task_id or "")
    data = payload.get("data")
    if isinstance(data, dict):
        reason = str(data.get("reason") or reason or "")
    reason = reason or json.dumps(payload, ensure_ascii=False, default=str)
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
