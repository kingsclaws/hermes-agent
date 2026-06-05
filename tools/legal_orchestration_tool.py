"""Native legal workflow orchestration for document-focused Hermes sessions."""

from __future__ import annotations

import json
import logging
import re
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

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

_XREF_REVIEW_AUTO_EXCLUDE_DIRS = {
    ".git",
    ".hermes-project",
    ".venv",
    "__pycache__",
    "delivery",
    "deliverables",
    "node_modules",
    "venv",
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
                    "plan",
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


def _resolve_path_like(value: Optional[str], *, project_root: Path) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (project_root / path).resolve()
    else:
        path = path.resolve()
    return str(path)


def _dedupe_paths(paths: Sequence[str]) -> List[str]:
    ordered: List[str] = []
    seen = set()
    for item in paths:
        key = str(item).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _discover_project_docx_paths(project_root: Path) -> List[str]:
    if not project_root.exists():
        return []
    docx_paths: List[str] = []
    for path in project_root.rglob("*.docx"):
        parts = {part.lower() for part in path.parts}
        if parts & _XREF_REVIEW_AUTO_EXCLUDE_DIRS:
            continue
        docx_paths.append(str(path.resolve()))
    return sorted(docx_paths)


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


def _dedupe_findings(findings: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: List[Dict[str, Any]] = []
    seen = set()
    for item in findings:
        key = (
            str(item.get("review_type") or ""),
            str(item.get("document_path") or ""),
            str(item.get("location") or ""),
            str(item.get("title") or ""),
            str(item.get("finding") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _xref_internal_finding(document_path: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    para = entry.get("para")
    location = f"Paragraph {para}" if para else ""
    ref_text = str(entry.get("ref_text") or entry.get("clause_num") or "cross-reference")
    target_para = entry.get("target_para")
    return {
        "document_path": document_path,
        "finding": (
            f"{ref_text} does not resolve to an existing clause in the document. "
            f"Context: {str(entry.get('context') or '').strip()}"
        ).strip(),
        "location": location,
        "review_type": "review_xref",
        "severity": "major",
        "suggestion": "Correct the clause number or add the missing target heading.",
        "title": f"Dead internal cross-reference: {ref_text}",
        "target_para": target_para,
    }


def _xref_cross_doc_finding(entry: Dict[str, Any]) -> Dict[str, Any]:
    source_path = str(entry.get("source") or "")
    para = entry.get("source_para")
    target_doc = str(entry.get("target_doc") or "")
    clause = str(entry.get("clause") or "")
    location = f"Paragraph {para}" if para else ""
    return {
        "document_path": source_path,
        "finding": str(entry.get("reason") or "").strip(),
        "location": location,
        "review_type": "review_xref",
        "severity": "major",
        "suggestion": "Fix the target document name or referenced clause so the citation resolves.",
        "title": f"Broken cross-document reference: 《{target_doc}》第{clause}条",
        "target_doc": target_doc,
    }


def _run_xref_preflight(
    *,
    project_root: Path,
    document_path: Optional[str],
    related_paths: Sequence[str],
) -> Dict[str, Any]:
    primary = _resolve_path_like(document_path, project_root=project_root)
    if not primary:
        return {
            "document_path": None,
            "error": "review_xref requires document_path.",
            "findings": [],
            "ok": False,
        }

    try:
        from lexitool import xref
    except Exception as exc:
        return {
            "document_path": primary,
            "error": f"lexitool.xref is unavailable: {exc}",
            "findings": [],
            "ok": False,
        }

    related_abs = [
        resolved
        for resolved in (
            _resolve_path_like(path, project_root=project_root) for path in related_paths
        )
        if resolved
    ]
    project_docs = _discover_project_docx_paths(project_root)
    cross_docs = _dedupe_paths([primary] + related_abs + project_docs)

    single_doc = xref.xref_audit(primary)
    cross_doc = None
    if len(cross_docs) >= 2:
        cross_doc = xref.cross_doc_scan(cross_docs)

    findings: List[Dict[str, Any]] = []
    for entry in single_doc.get("dead_refs") or []:
        if isinstance(entry, dict):
            findings.append(_xref_internal_finding(primary, entry))
    if isinstance(cross_doc, dict):
        for entry in cross_doc.get("broken_refs") or []:
            if isinstance(entry, dict):
                findings.append(_xref_cross_doc_finding(entry))

    summary_bits = [str(single_doc.get("summary") or "").strip()]
    if isinstance(cross_doc, dict) and cross_doc.get("summary"):
        summary = cross_doc["summary"]
        if isinstance(summary, dict):
            summary_bits.append(
                "Cross-document refs: "
                f"{summary.get('valid', 0)} valid, {summary.get('broken', 0)} broken"
            )
        else:
            summary_bits.append(str(summary).strip())

    return {
        "document_path": primary,
        "cross_doc_scan": cross_doc,
        "docs_scanned": cross_docs,
        "findings": _dedupe_findings(findings),
        "ok": not findings and bool(single_doc.get("ok", True)) and (cross_doc is None or bool(cross_doc.get("ok", True))),
        "single_doc_audit": single_doc,
        "summary": "; ".join(bit for bit in summary_bits if bit),
    }


def _xref_review_appendix(preflight: Dict[str, Any]) -> str:
    payload = {
        "document_path": preflight.get("document_path"),
        "docs_scanned": preflight.get("docs_scanned") or [],
        "findings": preflight.get("findings") or [],
        "single_doc_audit": preflight.get("single_doc_audit") or {},
        "cross_doc_scan": preflight.get("cross_doc_scan") or {},
        "summary": preflight.get("summary") or "",
    }
    return (
        "\n## Cross-Reference Preflight\n"
        "Treat the following machine scan as ground truth input. "
        "Verify it, add any missing issues, but do not ignore broken references already detected.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


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
        preflight = None
        extra_context = ""
        if review_type == "review_xref":
            preflight = _run_xref_preflight(
                project_root=project_root,
                document_path=document_path,
                related_paths=related_paths,
            )
            extra_context = _xref_review_appendix(preflight)
        context = (
            _role_prompt(project_root, role_file)
            + "\n\n"
            + project_context
            + extra_context
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
                "review_type": review_type,
                "machine_error": (preflight or {}).get("error"),
                "machine_findings": (preflight or {}).get("findings", []),
                "machine_summary": (preflight or {}).get("summary", ""),
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

    if task_type == "plan":
        from tools.registry import registry as _registry

        return _registry.dispatch(
            "legal_workflow",
            {
                "action": "create_plan",
                "name": "法律文书修订 workflow",
                "project_dir": str(project_root),
                "document_path": document_path or "",
                "term_sheet_path": term_sheet_path or "",
                "instructions": instructions or "",
            },
            task_id=kwargs.get("task_id"),
            parent_agent=parent_agent,
        )

    if task_type == "review":
        review_types = [str(v).strip() for v in (args.get("review_types") or []) if str(v).strip()]
        if not review_types:
            review_types = ["review_content", "review_format", "review_xref"]
            if term_sheet_path:
                review_types.append("review_ts")
        invalid = [name for name in review_types if name not in _REVIEW_TASK_TYPES]
        if invalid:
            return tool_error(f"Unknown review_types: {', '.join(invalid)}")
        if "review_xref" in review_types and not document_path:
            return tool_error("review_xref requires document_path.")
        tasks = _build_review_subtasks(
            project_root=project_root,
            document_path=document_path,
            instructions=instructions,
            related_paths=related_paths,
            term_sheet_path=term_sheet_path,
            review_types=review_types,
        )
        xref_task_error = next(
            (
                str(task.get("machine_error") or "").strip()
                for task in tasks
                if task.get("review_type") == "review_xref" and str(task.get("machine_error") or "").strip()
            ),
            "",
        )
        if xref_task_error:
            return tool_error(xref_task_error)
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
        for task, result in zip_longest(tasks, batch.get("results") or [], fillvalue={}):
            if not task:
                continue
            review_type = str(task.get("review_type") or "")
            summary = str(result.get("summary") or result.get("error") or "")
            structured = _extract_json_payload(summary)
            task_findings = _normalize_findings(
                structured,
                review_type=review_type,
                document_path=document_path,
                raw_summary=summary,
            )
            task_findings = _dedupe_findings(list(task.get("machine_findings") or []) + task_findings)
            findings.extend(task_findings)
            subtasks.append(
                {
                    "review_type": review_type,
                    "status": result.get("status"),
                    "summary": (
                        (structured or {}).get("summary")
                        if isinstance(structured, dict)
                        else summary
                    ) or str(task.get("machine_summary") or ""),
                    "finding_count": len(task_findings),
                }
            )
        findings = _dedupe_findings(findings)
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
    preflight = None
    extra_context = ""
    if task_type == "review_xref":
        preflight = _run_xref_preflight(
            project_root=project_root,
            document_path=document_path,
            related_paths=related_paths,
        )
        if preflight.get("error"):
            return tool_error(str(preflight["error"]))
        extra_context = _xref_review_appendix(preflight)
    if task_type in _REVIEW_TASK_TYPES:
        contract = _review_contract(task_type, document_path or "")
    else:
        contract = _draft_contract(document_path)

    context = (
        _role_prompt(project_root, role_file)
        + "\n\n"
        + project_context
        + extra_context
        + "\n\n"
        + contract
    ).strip()
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
        if task_type == "review_xref":
            findings = _dedupe_findings(list((preflight or {}).get("findings") or []) + findings)
        return _tool_ok(
            {
                "document_path": document_path,
                "findings": findings,
                "project_dir": str(project_root),
                "status": (structured or {}).get("status", "completed") if isinstance(structured, dict) else "completed",
                "summary": (
                    (structured or {}).get("summary", summary)
                    if isinstance(structured, dict)
                    else summary
                ) or str((preflight or {}).get("summary") or ""),
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
