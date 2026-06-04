"""Native legal workflow orchestration for document-focused Hermes sessions."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.delegate_tool import delegate_task
from tools.project_management_tool import resolve_selected_project
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)


_ROLE_FILE_BY_TASK_TYPE: Dict[str, str] = {
    "draft": "drafter.md",
    "revise": "drafter.md",
    "review_content": "reviewer-content.md",
    "review_format": "reviewer-format.md",
    "review_ts": "reviewer-ts-consistency.md",
    "review_xref": "reviewer-cross-ref.md",
    "review_translation": "reviewer-translation.md",
}

_TOOLSETS_BY_TASK_TYPE: Dict[str, List[str]] = {
    "draft": ["lex-docx", "file", "project_management"],
    "revise": ["lex-docx", "file", "project_management"],
    "review_content": ["lex-docx", "file", "project_management"],
    "review_format": ["lex-docx", "file", "project_management"],
    "review_ts": ["lex-docx", "file", "project_management"],
    "review_xref": ["lex-docx", "file", "project_management"],
    "review_translation": ["lex-docx", "file", "project_management"],
}

_REVIEW_TASK_TYPES = {
    "review_content",
    "review_format",
    "review_ts",
    "review_xref",
    "review_translation",
}


LEGAL_ORCHESTRATE_SCHEMA = {
    "name": "legal_orchestrate",
    "description": (
        "Native legal workflow orchestrator. Routes legal drafting and review tasks "
        "to the correct subagent role, enforces structured reviewer outputs, and "
        "coordinates multi-agent review bundles in code instead of relying on prompt-only choreography."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_type": {
                "type": "string",
                "enum": [
                    "draft",
                    "revise",
                    "review",
                    "review_content",
                    "review_format",
                    "review_ts",
                    "review_xref",
                    "review_translation",
                    "deliver",
                ],
                "description": "Native legal workflow step to run.",
            },
            "project_dir": {
                "type": "string",
                "description": "Optional explicit project root. Defaults to the active selected project or TERMINAL_CWD.",
            },
            "document_path": {
                "type": "string",
                "description": "Primary legal document path for drafting/review/revision.",
            },
            "instructions": {
                "type": "string",
                "description": "Concrete drafting/review instructions from the user.",
            },
            "related_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional supporting files for cross-reference or translation review.",
            },
            "term_sheet_path": {
                "type": "string",
                "description": "Optional term sheet path for TS-consistency review.",
            },
            "review_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "review_content",
                        "review_format",
                        "review_ts",
                        "review_xref",
                        "review_translation",
                    ],
                },
                "description": "For task_type=review, override the default review bundle with an explicit reviewer set.",
            },
        },
        "required": ["task_type"],
    },
}


def _tool_ok(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _resolve_project_root(args: dict, parent_agent=None) -> Path:
    raw = str(args.get("project_dir") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    selected = resolve_selected_project(parent_agent, session_id=getattr(parent_agent, "session_id", None))
    if selected and selected.get("cwd"):
        return Path(str(selected["cwd"])).expanduser().resolve()
    env_cwd = Path(str((getattr(parent_agent, "_selected_project_cwd", None) or "") or "")).expanduser()
    if str(env_cwd) and env_cwd.exists():
        return env_cwd.resolve()
    return Path.cwd().resolve()


def _role_prompt(project_root: Path, role_file: str) -> str:
    role_path = project_root / ".hermes-project" / "roles" / role_file
    if role_path.exists():
        return role_path.read_text(encoding="utf-8")
    return f"You are the specialist legal worker for role file {role_file}."


def _load_project_context(project_root: Path) -> str:
    pieces: List[str] = []
    for path in (
        project_root / ".hermes-project" / "project-context.md",
        project_root / "STANDARDS.md",
        project_root / "AGENTS.md",
    ):
        if path.exists():
            try:
                pieces.append(f"\n## {path.name}\n{path.read_text(encoding='utf-8')}")
            except Exception as exc:
                logger.debug("Could not read %s: %s", path, exc)
    return "\n".join(pieces).strip()


def _review_contract(review_type: str, document_path: str) -> str:
    return (
        "\n## Output Contract\n"
        "Return ONLY valid JSON. No markdown fences. Use this schema exactly:\n"
        "{\n"
        '  "status": "completed|failed",\n'
        '  "summary": "short summary",\n'
        '  "findings": [\n'
        "    {\n"
        f'      "review_type": "{review_type}",\n'
        '      "severity": "critical|major|minor|info",\n'
        f'      "document_path": "{document_path}",\n'
        '      "location": "paragraph/clause/table reference",\n'
        '      "title": "short issue title",\n'
        '      "finding": "what is wrong",\n'
        '      "suggestion": "specific fix"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Every reviewer comment must be expressed as a structured finding. "
        "If there are no issues, return findings as []."
    )


def _draft_contract(document_path: Optional[str]) -> str:
    target = document_path or ""
    return (
        "\n## Output Contract\n"
        "Return ONLY valid JSON. No markdown fences. Use this schema exactly:\n"
        "{\n"
        '  "status": "completed|failed",\n'
        '  "summary": "short summary",\n'
        '  "modified_files": ["absolute/or-relative-path"],\n'
        '  "modified_locations": ["clause or paragraph references"],\n'
        '  "verification_passed": true,\n'
        '  "verification_report": ["checks you ran"],\n'
        '  "primary_document": "' + target.replace('"', '\\"') + '"\n'
        "}\n"
    )


def _extract_json_payload(text: str) -> Optional[Dict[str, Any]]:
    if not text or not isinstance(text, str):
        return None
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidate = text[start : end + 1]
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _fallback_finding(review_type: str, document_path: Optional[str], summary: str) -> Dict[str, Any]:
    return {
        "document_path": document_path or "",
        "finding": summary.strip() or "Reviewer returned an unstructured summary.",
        "location": "",
        "review_type": review_type,
        "severity": "info",
        "suggestion": "Re-run review with stricter JSON formatting.",
        "title": "Unstructured reviewer output",
    }


def _normalize_findings(
    parsed: Optional[Dict[str, Any]],
    *,
    review_type: str,
    document_path: Optional[str],
    raw_summary: str,
) -> List[Dict[str, Any]]:
    findings = parsed.get("findings") if isinstance(parsed, dict) else None
    if not isinstance(findings, list):
        if raw_summary.strip():
            return [_fallback_finding(review_type, document_path, raw_summary)]
        return []
    normalized: List[Dict[str, Any]] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "document_path": str(item.get("document_path") or document_path or ""),
                "finding": str(item.get("finding") or "").strip(),
                "location": str(item.get("location") or "").strip(),
                "review_type": str(item.get("review_type") or review_type),
                "severity": str(item.get("severity") or "info").lower(),
                "suggestion": str(item.get("suggestion") or "").strip(),
                "title": str(item.get("title") or "").strip(),
            }
        )
    return normalized


def _make_goal(
    task_type: str,
    *,
    document_path: Optional[str],
    instructions: Optional[str],
    related_paths: List[str],
    term_sheet_path: Optional[str],
) -> str:
    pieces = [f"Legal workflow task: {task_type}."]
    if document_path:
        pieces.append(f"Primary document: {document_path}.")
    if term_sheet_path:
        pieces.append(f"Term sheet: {term_sheet_path}.")
    if related_paths:
        pieces.append("Supporting files: " + ", ".join(related_paths) + ".")
    if instructions:
        pieces.append("Instructions: " + instructions.strip())
    return "\n".join(pieces)


def _dispatch_single_child(
    *,
    parent_agent,
    task_type: str,
    goal: str,
    context: str,
    toolsets: List[str],
) -> Dict[str, Any]:
    raw = delegate_task(
        goal=goal,
        context=context,
        toolsets=toolsets,
        role="leaf",
        parent_agent=parent_agent,
    )
    parsed = json.loads(raw)
    results = parsed.get("results") or []
    if not results:
        return {"error": parsed.get("error") or "delegate_task returned no results."}
    result = results[0]
    summary = str(result.get("summary") or result.get("error") or "")
    structured = _extract_json_payload(summary)
    return {
        "delegate_result": result,
        "structured": structured,
        "summary": summary,
        "task_type": task_type,
    }


def _build_review_subtasks(
    *,
    project_root: Path,
    document_path: Optional[str],
    instructions: Optional[str],
    related_paths: List[str],
    term_sheet_path: Optional[str],
    review_types: List[str],
) -> List[Dict[str, Any]]:
    project_context = _load_project_context(project_root)
    tasks: List[Dict[str, Any]] = []
    for review_type in review_types:
        role_file = _ROLE_FILE_BY_TASK_TYPE[review_type]
        context = (
            _role_prompt(project_root, role_file)
            + "\n\n"
            + project_context
            + "\n\n"
            + _review_contract(review_type, document_path or "")
        ).strip()
        tasks.append(
            {
                "context": context,
                "goal": _make_goal(
                    review_type,
                    document_path=document_path,
                    instructions=instructions,
                    related_paths=related_paths,
                    term_sheet_path=term_sheet_path,
                ),
                "role": "leaf",
                "toolsets": _TOOLSETS_BY_TASK_TYPE[review_type],
            }
        )
    return tasks


def _handle_legal_orchestrate(args: dict, **kwargs) -> str:
    parent_agent = kwargs.get("parent_agent")
    if parent_agent is None:
        return tool_error("legal_orchestrate requires a parent agent context.")

    task_type = str(args.get("task_type") or "").strip().lower()
    if not task_type:
        return tool_error("task_type is required")

    project_root = _resolve_project_root(args, parent_agent=parent_agent)
    document_path = str(args.get("document_path") or "").strip() or None
    instructions = str(args.get("instructions") or "").strip() or None
    related_paths = [str(p).strip() for p in (args.get("related_paths") or []) if str(p).strip()]
    term_sheet_path = str(args.get("term_sheet_path") or "").strip() or None

    if task_type == "deliver":
        from tools.registry import registry as _registry

        if not project_root.exists():
            return tool_error(f"project_dir does not exist: {project_root}")
        return _registry.dispatch(
            "lex_deliver",
            {"project_dir": str(project_root)},
            task_id=kwargs.get("task_id"),
        )

    if task_type == "review":
        review_types = [str(v).strip() for v in (args.get("review_types") or []) if str(v).strip()]
        if not review_types:
            review_types = ["review_content", "review_format"]
            if term_sheet_path:
                review_types.append("review_ts")
            if related_paths:
                review_types.append("review_xref")
        invalid = [name for name in review_types if name not in _REVIEW_TASK_TYPES]
        if invalid:
            return tool_error(f"Unknown review_types: {', '.join(invalid)}")
        tasks = _build_review_subtasks(
            project_root=project_root,
            document_path=document_path,
            instructions=instructions,
            related_paths=related_paths,
            term_sheet_path=term_sheet_path,
            review_types=review_types,
        )
        batch = json.loads(
            delegate_task(
                tasks=tasks,
                role="leaf",
                parent_agent=parent_agent,
            )
        )
        if batch.get("error"):
            return tool_error(batch["error"])
        findings: List[Dict[str, Any]] = []
        subtasks: List[Dict[str, Any]] = []
        for review_type, result in zip(review_types, batch.get("results") or []):
            summary = str(result.get("summary") or result.get("error") or "")
            structured = _extract_json_payload(summary)
            task_findings = _normalize_findings(
                structured,
                review_type=review_type,
                document_path=document_path,
                raw_summary=summary,
            )
            findings.extend(task_findings)
            subtasks.append(
                {
                    "review_type": review_type,
                    "status": result.get("status"),
                    "summary": (structured or {}).get("summary") if isinstance(structured, dict) else summary,
                    "finding_count": len(task_findings),
                }
            )
        return _tool_ok(
            {
                "document_path": document_path,
                "findings": findings,
                "project_dir": str(project_root),
                "status": "completed",
                "subtasks": subtasks,
                "task_type": task_type,
            }
        )

    if task_type not in _ROLE_FILE_BY_TASK_TYPE:
        return tool_error(f"Unknown task_type: {task_type}")

    role_file = _ROLE_FILE_BY_TASK_TYPE[task_type]
    project_context = _load_project_context(project_root)
    if task_type in _REVIEW_TASK_TYPES:
        contract = _review_contract(task_type, document_path or "")
    else:
        contract = _draft_contract(document_path)

    context = (_role_prompt(project_root, role_file) + "\n\n" + project_context + "\n\n" + contract).strip()
    delegated = _dispatch_single_child(
        parent_agent=parent_agent,
        task_type=task_type,
        goal=_make_goal(
            task_type,
            document_path=document_path,
            instructions=instructions,
            related_paths=related_paths,
            term_sheet_path=term_sheet_path,
        ),
        context=context,
        toolsets=_TOOLSETS_BY_TASK_TYPE[task_type],
    )
    if delegated.get("error"):
        return tool_error(str(delegated["error"]))

    structured = delegated.get("structured")
    summary = str(delegated.get("summary") or "")
    if task_type in _REVIEW_TASK_TYPES:
        findings = _normalize_findings(
            structured if isinstance(structured, dict) else None,
            review_type=task_type,
            document_path=document_path,
            raw_summary=summary,
        )
        return _tool_ok(
            {
                "document_path": document_path,
                "findings": findings,
                "project_dir": str(project_root),
                "status": (structured or {}).get("status", "completed") if isinstance(structured, dict) else "completed",
                "summary": (structured or {}).get("summary", summary) if isinstance(structured, dict) else summary,
                "task_type": task_type,
            }
        )

    payload = structured if isinstance(structured, dict) else {
        "modified_files": [document_path] if document_path else [],
        "modified_locations": [],
        "primary_document": document_path,
        "status": "completed",
        "summary": summary,
        "verification_passed": False,
        "verification_report": [],
    }
    payload["project_dir"] = str(project_root)
    payload["task_type"] = task_type
    return _tool_ok(payload)


registry.register(
    name="legal_orchestrate",
    toolset="legal_orchestration",
    schema=LEGAL_ORCHESTRATE_SCHEMA,
    handler=_handle_legal_orchestrate,
    description=LEGAL_ORCHESTRATE_SCHEMA["description"],
)
