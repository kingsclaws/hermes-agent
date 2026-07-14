"""Native legal workflow orchestration for document-focused Hermes sessions."""

from __future__ import annotations

import json
import logging
import re
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from tools.delegate_tool import delegate_task
from .legal_workflow_learning import (
    format_learning_rules,
    learn_from_findings,
    scorecard_findings,
)
from .project_management_tool import resolve_selected_project
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
    "draft": ["lex-docx-worker", "file", "project_management"],
    "revise": ["lex-docx-worker", "file", "project_management"],
    "review_content": ["lex-docx-coordinator", "file", "project_management"],
    "review_format": ["lex-docx-coordinator", "file", "project_management"],
    "review_ts": ["lex-docx-coordinator", "file", "project_management"],
    "review_xref": ["lex-docx-coordinator", "file", "project_management"],
    "review_translation": ["lex-docx-coordinator", "file", "project_management"],
}

_PROFILE_BY_TASK_TYPE: Dict[str, str] = {
    "draft": "lex-drafter",
    "revise": "lex-drafter",
    "draft_iterative": "lex-drafter",
    "review_content": "lex-reviewer-content",
    "review_format": "lex-reviewer-format",
    "review_ts": "lex-reviewer-ts",
    "review_xref": "lex-reviewer-xref",
    "review_translation": "lex-reviewer-translation",
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
        "coordinates multi-agent review bundles in code instead of relying on prompt-only choreography. "
        "Use this for lawyer-style document work: read structure/review digest, "
        "build a clause/issue map, revise bounded ranges, verify content and "
        "formatting, maintain project_facts, and snapshot/deliver through lex_git."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_type": {
                "type": "string",
                "enum": [
                    "draft",
                    "draft_iterative",
                    "plan",
                    "revise",
                    "review",
                    "review_content",
                    "review_format",
                    "review_ts",
                    "review_xref",
                    "review_translation",
                    "proofread",
                    "deliver",
                    "template_audit",
                    "template_fill",
                ],
                "description": "Native legal workflow step to run.",
            },
            "project_dir": {
                "type": "string",
                "description": "Optional explicit project root. Defaults to the active selected project or TERMINAL_CWD.",
            },
            "workflow_type": {
                "type": "string",
                "enum": ["contract_revision", "document_drafting", "translation_quality_review", "proofread_review", "project_init", "delivery_gate"],
                "description": "Workflow template to create when task_type=plan.",
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
            "bilingual_path": {
                "type": "string",
                "description": "Single bilingual DOCX for translation QA. Defaults to document_path for review_translation.",
            },
            "source_path": {
                "type": "string",
                "description": "Source-language DOCX for separate-file translation QA.",
            },
            "translation_path": {
                "type": "string",
                "description": "Translated DOCX for separate-file translation QA.",
            },
            "glossary_path": {
                "type": "string",
                "description": "Optional glossary, term sheet, or defined-term reference for translation QA.",
            },
            "sop_path": {
                "type": "string",
                "description": "Optional project/task-specific translation QA SOP file.",
            },
            "sop_overrides": {
                "type": "string",
                "description": "Optional task-specific SOP additions, exceptions, or priority changes.",
            },
            "domain_terms": {
                "type": "object",
                "description": "Optional task-specific term map for translation QA.",
            },
            "chunk_size": {
                "type": "integer",
                "description": "Paragraph chunk size for native translation QA or proofread review.",
            },
            "enable_learning": {
                "type": "boolean",
                "description": "Whether translation QA should extract reusable workflow learning rules. Default: true.",
            },
            "learning_scope": {
                "type": "string",
                "enum": ["global"],
                "description": "Learning rule scope. Default: global.",
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
            "mode": {
                "type": "string",
                "enum": ["direct", "kanban"],
                "description": (
                    "协调模式。默认是 'kanban'：在项目 Board 上创建任务，"
                    "Drafter/Reviewer 通过认领→移交→审批流程协作。"
                    "'direct' 仅用于 Kanban worker 内部执行或用户明确批准的紧急旁路。"
                ),
            },
            "allow_direct": {
                "type": "boolean",
                "description": (
                    "Emergency bypass for mode='direct'. Default false. "
                    "Only use after the user explicitly approves bypassing Kanban for this call."
                ),
            },
            "direct_reason": {
                "type": "string",
                "description": "Required human-readable reason when allow_direct=true.",
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
        project_root / ".hermes-project" / "memories" / "project_facts.md",
        project_root / "STANDARDS.md",
        project_root / "AGENTS.md",
    ):
        if path.exists():
            try:
                pieces.append(f"\n## {path.name}\n{path.read_text(encoding='utf-8')}")
            except Exception as exc:
                logger.debug("Could not read %s: %s", path, exc)
    return "\n".join(pieces).strip()


def _workflow_learning_context(workflow_type: str, learning_scope: str = "global") -> str:
    try:
        from hermes_state import SessionDB

        rules = SessionDB().list_workflow_learning_rules(
            workflow_type=workflow_type,
            scope=learning_scope,
            status="active",
            limit=80,
        )
        return format_learning_rules(rules)
    except Exception:
        return ""


def _learn_from_review_findings(
    *,
    findings: List[Dict[str, Any]],
    workflow_type: str,
    learning_scope: str,
    document_path: Optional[str],
    enabled: bool,
) -> Dict[str, Any]:
    if not enabled:
        return {"enabled": False, "findings": findings, "candidates": [], "active_rules": [], "candidate_rules": []}
    try:
        return learn_from_findings(
            findings=findings,
            workflow_type=workflow_type,
            scope=learning_scope,
            run_id=None,
            document_path=document_path,
            enabled=True,
        )
    except Exception as exc:
        return {"enabled": True, "error": str(exc), "findings": findings, "candidates": []}


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
        "\n## Mandatory Revision Verification Protocol\n"
        "Before reporting completion for any legal document revision:\n"
        "1. Use lex_scan to discover all occurrences of each old/problem term or clause pattern that the instruction changes.\n"
        "2. Read all affected ranges with lex_read before editing; do not rely on blind keyword replacement.\n"
        "3. Edit narrowly with Track Changes where possible.\n"
        "4. Run lex_revision_guard with required_absent for obsolete/problem terms and required_present for required replacement terms.\n"
        "5. If lex_revision_guard has failures, status must be failed unless every residual is explicitly listed as intentionally retained with a legal reason.\n"
        "\n## Output Contract\n"
        "Return ONLY valid JSON. No markdown fences. Use this schema exactly:\n"
        "{\n"
        '  "status": "completed|failed",\n'
        '  "summary": "short summary",\n'
        '  "modified_files": ["absolute/or-relative-path"],\n'
        '  "modified_locations": ["clause or paragraph references"],\n'
        '  "scan_queries": {"obsolete_or_problem_terms": [], "required_terms": []},\n'
        '  "revision_guard": {"ok": true, "failures": []},\n'
        '  "intentional_residuals": [],\n'
        '  "verification_passed": true,\n'
        '  "verification_report": ["checks you ran"],\n'
        '  "primary_document": "' + target.replace('"', '\\"') + '"\n'
        "}\n"
        "verification_passed must be false if revision_guard.ok is false and intentional_residuals does not fully explain every failure.\n"
    )


def _iterative_draft_contract(document_path: Optional[str], start_para: int, end_para: int) -> str:
    target = document_path or ""
    return (
        "\n## Mandatory Iterative Drafting Protocol\n"
        f"You are assigned ONLY paragraphs §{start_para}-§{end_para}. "
        "Do not edit paragraphs outside this range.\n"
        "You must work in this order:\n"
        "1. Read ONLY your assigned range with lex_read(paras=[...]); if you need orientation, first inspect lex_read(mode='review')/legal_structure from the parent context.\n"
        "2. Compare that range against the term sheet, project context, and user instructions.\n"
        "3. Decide as a lawyer whether content, defined terms, parties, amounts, dates, conditions, liability, cross-references, comments, and formatting need changes.\n"
        "4. For each paragraph/table cell that needs changes, use native lex_edit/lex_table_list only.\n"
        "5. If the instruction changes names, roles, defined terms, amounts, dates, governing concepts, or template tokens, use lex_scan to find all matching occurrences in the whole document before editing your range.\n"
        "6. After edits, read back the same paragraph range with lex_read.\n"
        "7. Run lex_revision_guard for any obsolete/problem terms and required replacement terms created by your edits.\n"
        "8. Verify that no placeholder, bracket option, wrong party, amount/date, broken definition, unresolved comment, cross-reference issue, unexplained template note, or unresolved guard failure remains in the range.\n"
        "9. If no edit is needed, explicitly say so and explain why.\n"
        "Do not use terminal/python/docx/lxml workarounds.\n\n"
        "## Output Contract\n"
        "Return ONLY valid JSON. No markdown fences. Use this schema exactly:\n"
        "{\n"
        '  "status": "completed|failed",\n'
        f'  "paragraph_range": "§{start_para}-§{end_para}",\n'
        '  "summary": "short summary",\n'
        '  "modified_files": ["absolute/or-relative-path"],\n'
        '  "modified_locations": ["§N or table references"],\n'
        '  "unchanged_locations": ["§N references reviewed but not changed"],\n'
        '  "scan_queries": {"obsolete_or_problem_terms": [], "required_terms": []},\n'
        '  "revision_guard": {"ok": true, "failures": []},\n'
        '  "intentional_residuals": [],\n'
        '  "verification_passed": true,\n'
        '  "verification_report": ["readback and checks performed"],\n'
        '  "remaining_issues": [],\n'
        '  "primary_document": "' + target.replace('"', '\\"') + '"\n'
        "}\n"
        "If verification_passed is false, remaining_issues is non-empty, or revision_guard.ok is false without fully justified intentional_residuals, status must be failed."
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


def _run_scorecard_gate(
    project_root,
    document_path: str | None,
    workflow_id: str,
    run_id: str | None,
    learning_scope: str,
    enable_learning: bool,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Run legal_scorecard as a programmatic gate and merge results into payload.

    This is the machine-audit layer from the video's architecture — it validates
    persisted harness evidence (handoff envelopes, convention profiles, review
    plans, verification records) rather than trusting agent prose.
    """
    try:
        from hermes_cli.project_commands import legal_scorecard

        sc = legal_scorecard(
            str(project_root),
            document_path=document_path,
            workflow_id=workflow_id,
            run_id=run_id,
            strict=True,
        )
        payload["scorecard"] = sc
        if not sc.get("ok"):
            payload["status"] = "failed"
            payload["verification_passed"] = False
            report = payload.get("verification_report")
            if not isinstance(report, list):
                report = []
            failures_desc = "; ".join(
                f.get("message", f.get("check", "?")) for f in sc.get("failures", [])
            )
            report.append(
                f"Scorecard gate failed [{len(sc.get('failures', []))} failure(s)]: {failures_desc}"
            )
            payload["verification_report"] = report
            gate_findings = scorecard_findings(sc, document_path=document_path)
            payload["gate_findings"] = gate_findings
            payload["gate_learning"] = learn_from_findings(
                findings=gate_findings,
                workflow_type="delivery_gate",
                scope=learning_scope,
                run_id=run_id,
                document_path=document_path,
                enabled=enable_learning,
            )
    except Exception as exc:
        payload["scorecard"] = {
            "ok": False,
            "status": "failed",
            "error": "scorecard gate raised exception",
            "detail": str(exc),
        }
        payload["status"] = "failed"
        payload["verification_passed"] = False
        report = payload.get("verification_report")
        if not isinstance(report, list):
            report = []
        report.append(f"Scorecard gate failed [exception]: {exc}")
        payload["verification_report"] = report
    return payload


def _revision_guard_failed(parsed: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(parsed, dict):
        return False
    guard = parsed.get("revision_guard")
    if not isinstance(guard, dict):
        return False
    if bool(guard.get("ok", True)):
        return False
    failures = guard.get("failures")
    if not isinstance(failures, list) or not failures:
        return True
    intentional = parsed.get("intentional_residuals")
    if not isinstance(intentional, list):
        return True
    # This is deliberately conservative: if the child claims intentional
    # residuals, it must account for at least every failed guard item.
    return len(intentional) < len(failures)


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


def _paragraph_count(document_path: str) -> int:
    from lexitool.markup import lex_read

    stats = lex_read(document_path, mode="stats")
    match = re.search(r"Paragraphs:\s*(\d+)", stats or "")
    if not match:
        raise ValueError(f"Could not determine paragraph count from lex_stats for {document_path}")
    return int(match.group(1))


def _read_para_range(document_path: str, start_para: int, end_para: int) -> str:
    from lexitool.markup import lex_read

    return lex_read(
        document_path,
        paras=list(range(start_para, end_para + 1)),
        mode="full",
        show_tc=True,
        show_format=True,
    )


def _run_iterative_drafting(
    *,
    parent_agent,
    project_root: Path,
    document_path: str,
    instructions: Optional[str],
    related_paths: List[str],
    term_sheet_path: Optional[str],
    chunk_size: int,
    learning_workflow_type: str,
    learning_scope: str,
) -> Dict[str, Any]:
    if not document_path:
        return {"error": "draft_iterative requires document_path."}

    paragraph_count = _paragraph_count(document_path)
    effective_chunk = max(1, min(int(chunk_size or 12), 40))
    project_context = _load_project_context(project_root)
    learning_context = _workflow_learning_context(learning_workflow_type, learning_scope)
    role_file = _ROLE_FILE_BY_TASK_TYPE["draft"]
    base_context = (
        _role_prompt(project_root, role_file)
        + "\n\n"
        + project_context
        + ("\n\n" + learning_context if learning_context else "")
    ).strip()

    chunks: List[Dict[str, int]] = []
    start = 1
    while start <= paragraph_count:
        end = min(start + effective_chunk - 1, paragraph_count)
        chunks.append({"start": start, "end": end})
        start = end + 1

    chunk_results: List[Dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        start_para = chunk["start"]
        end_para = chunk["end"]
        pre_read = _read_para_range(document_path, start_para, end_para)
        context = (
            base_context
            + "\n\n## Assigned Range Pre-Read\n"
            + pre_read
            + "\n\n"
            + _iterative_draft_contract(document_path, start_para, end_para)
        ).strip()
        goal = _make_goal(
            "draft_iterative",
            document_path=document_path,
            instructions=(
                (instructions or "按项目上下文、TS和法律文书制作要求处理。")
                + f"\nProcess only paragraphs §{start_para}-§{end_para}. "
                "Use read-edit-readback verification before returning."
            ),
            related_paths=related_paths,
            term_sheet_path=term_sheet_path,
        )
        delegated = _dispatch_single_child(
            parent_agent=parent_agent,
            project_root=project_root,
            workflow_id="contract_revision",
            run_id=chunk_results[-1].get("run_id") if chunk_results else None,
            node_id=f"draft_iterative_{index}",
            task_type="draft_iterative",
            goal=goal,
            context=context,
            toolsets=_TOOLSETS_BY_TASK_TYPE["draft"],
        )
        post_read = _read_para_range(document_path, start_para, end_para)
        structured = delegated.get("structured") if isinstance(delegated, dict) else None
        status = (
            str((structured or {}).get("status") or "").lower()
            if isinstance(structured, dict)
            else ""
        )
        guard_failed = _revision_guard_failed(structured if isinstance(structured, dict) else None)
        verification_passed = (
            bool((structured or {}).get("verification_passed")) and not guard_failed
            if isinstance(structured, dict)
            else False
        )
        chunk_result = {
            "chunk_index": index,
            "paragraph_range": f"§{start_para}-§{end_para}",
            "status": (
                "failed"
                if guard_failed
                else status or ("error" if delegated.get("error") or structured is None else "completed")
            ),
            "delegate_error": delegated.get("error"),
            "guard_failed": guard_failed,
            "run_id": delegated.get("run_id"),
            "structured": structured,
            "summary": delegated.get("summary"),
            "verification_passed": verification_passed,
            "parent_readback": post_read[:12000],
        }
        chunk_results.append(chunk_result)
        if delegated.get("error") or structured is None or status == "failed" or guard_failed or not verification_passed:
            return {
                "status": "failed",
                "error": "Iterative drafting stopped because a chunk failed structured verification.",
                "failed_chunk": chunk_result,
                "chunks_completed": len(chunk_results),
                "paragraph_count": paragraph_count,
                "document_path": document_path,
                "project_dir": str(project_root),
                "task_type": "draft_iterative",
            }

    return {
        "status": "completed",
        "document_path": document_path,
        "project_dir": str(project_root),
        "task_type": "draft_iterative",
        "paragraph_count": paragraph_count,
        "chunk_size": effective_chunk,
        "chunks": chunk_results,
        "verification_passed": all(bool(c.get("verification_passed")) for c in chunk_results if c.get("structured") is not None),
        "verification_report": [
            f"{c['paragraph_range']}: parent readback captured after child completion"
            for c in chunk_results
        ],
    }


def _dispatch_single_child(
    *,
    parent_agent,
    project_root: Path | None = None,
    workflow_id: str = "contract_revision",
    run_id: str | None = None,
    node_id: str | None = None,
    task_type: str,
    goal: str,
    context: str,
    toolsets: List[str],
) -> Dict[str, Any]:
    raw = delegate_task(
        goal=goal,
        context=context,
        toolsets=toolsets,
        profile=_PROFILE_BY_TASK_TYPE.get(task_type),
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
    payload = {
        "delegate_result": result,
        "structured": structured,
        "summary": summary,
        "task_type": task_type,
    }
    if project_root is not None:
        try:
            from hermes_cli.project_commands import legal_handoff_record

            handoff = structured if isinstance(structured, dict) else {
                "status": "failed" if result.get("error") else "completed",
                "summary": summary,
                "evidence": {"delegate_summary": summary[:4000]},
            }
            handoff_result = legal_handoff_record(
                str(project_root),
                workflow_id=workflow_id,
                node_id=node_id or task_type,
                handoff=handoff,
                run_id=run_id,
            )
            payload["handoff_record"] = handoff_result.get("record")
            payload["run_id"] = (handoff_result.get("record") or {}).get("run_id") or run_id
        except Exception as exc:
            payload["handoff_error"] = str(exc)
    return payload


def _build_review_subtasks(
    *,
    project_root: Path,
    document_path: Optional[str],
    instructions: Optional[str],
    related_paths: List[str],
    term_sheet_path: Optional[str],
    review_types: List[str],
    workflow_type: str,
    learning_scope: str,
) -> List[Dict[str, Any]]:
    project_context = _load_project_context(project_root)
    learning_context = _workflow_learning_context(workflow_type, learning_scope)
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
            + ("\n\n" + learning_context if learning_context else "")
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
                "profile": _PROFILE_BY_TASK_TYPE.get(review_type),
                "role": "leaf",
                "toolsets": _TOOLSETS_BY_TASK_TYPE[review_type],
            }
        )
    return tasks


def _handle_kanban_mode(
    task_type: str,
    project_root: Path,
    document_path: str | None,
    instructions: str | None,
    args: dict,
    parent_agent,
) -> str:
    """Bridge legal_orchestrate task_types to kanban swarm tasks.

    Maps high-level legal workflow types to concrete kanban tasks with gate chains.
    """
    from .kanban_toolset import (
        kanban_board_create_handler,
        kanban_task_create_handler,
    )

    project_path = str(project_root)

    # Ensure board exists
    board_result = json.loads(kanban_board_create_handler(
        {"project_path": project_path},
        parent_agent=parent_agent,
    ))
    board_id = board_result.get("board", {}).get("id", "")

    created_tasks = []

    if task_type in ("draft", "draft_iterative", "revise"):
        # Single task: Drafter → Reviewer-Content → Reviewer-Format
        gates = [
            {"type": "review", "target_pool": "hpswarm-reviewer-content"},
            {"type": "approve", "target_pool": "hpswarm-reviewer-format"},
        ]
        title = instructions or f"起草文档: {document_path or '新文档'}"
        result = json.loads(kanban_task_create_handler({
            "project_path": project_path,
            "title": title,
            "description": f"task_type={task_type}\ndocument_path={document_path}\ninstructions={instructions}",
            "assignee": "hpswarm-drafter",
            "gates": json.dumps(gates),
        }, parent_agent=parent_agent))
        if result.get("success"):
            created_tasks.append(result["task"])

    elif task_type == "plan":
        # Plan mode: compile YAML workflow if provided
        workflow_type = args.get("workflow_type", "")
        yaml_path = str(project_root / ".hermes-project" / "workflows" / f"{workflow_type}.yaml")
        if Path(yaml_path).is_file():
            from .kanban_toolset import kanban_workflow_compile_handler
            result = json.loads(kanban_workflow_compile_handler({
                "yaml_path": yaml_path,
                "project_path": project_path,
            }, parent_agent=parent_agent))
            if result.get("success"):
                created_tasks.extend(result.get("tasks", []))

    elif task_type in ("review", "proofread") or task_type in _REVIEW_TASK_TYPES:
        # Review: create tasks for each review type
        if task_type in _REVIEW_TASK_TYPES:
            review_types = [task_type]
        else:
            review_types = args.get("review_types") or [
                "review_content", "review_format", "review_ts", "review_xref",
            ]
        for rt in review_types:
            reviewer_map = {
                "review_content": "hpswarm-reviewer-content",
                "review_format": "hpswarm-reviewer-format",
                "review_ts": "hpswarm-reviewer-ts",
                "review_xref": "hpswarm-reviewer-xref",
                "review_translation": "hpswarm-reviewer-translation",
            }
            reviewer = reviewer_map.get(rt, f"hpswarm-reviewer-{rt.replace('review_', '')}")
            result = json.loads(kanban_task_create_handler({
                "project_path": project_path,
                "title": f"审阅 ({rt}): {document_path or '文档'}",
                "description": f"document_path={document_path}\ninstructions={instructions}",
                "assignee": reviewer,
                "gates": json.dumps([{"type": "review", "target_pool": "hpswarm-coordinator"}]),
            }, parent_agent=parent_agent))
            if result.get("success"):
                created_tasks.append(result["task"])

    elif task_type == "template_fill":
        result = json.loads(kanban_task_create_handler({
            "project_path": project_path,
            "title": f"模板填充/清理: {document_path or '文档'}",
            "description": (
                f"task_type=template_fill\n"
                f"document_path={document_path}\n"
                f"instructions={instructions}\n"
                "Use native lex_template_audit/lex_template_fill, then read back and hand off."
            ),
            "assignee": "hpswarm-drafter",
            "gates": json.dumps([
                {"type": "review", "target_pool": "hpswarm-reviewer-format"},
                {"type": "approve", "target_pool": "hpswarm-coordinator"},
            ]),
        }, parent_agent=parent_agent))
        if result.get("success"):
            created_tasks.append(result["task"])

    elif task_type == "deliver":
        result = json.loads(kanban_task_create_handler({
            "project_path": project_path,
            "title": f"交付检查: {document_path or project_path}",
            "description": (
                f"task_type=deliver\n"
                f"document_path={document_path}\n"
                f"instructions={instructions}\n"
                "Run lex_deliver/evidence coverage checks and report deliverable paths."
            ),
            "assignee": "hpswarm-coordinator",
            "gates": json.dumps([{"type": "approve", "target_pool": "hpswarm-coordinator"}]),
        }, parent_agent=parent_agent))
        if result.get("success"):
            created_tasks.append(result["task"])

    elif task_type == "template_audit":
        result = json.loads(kanban_task_create_handler({
            "project_path": project_path,
            "title": f"模板审计: {document_path or '文档'}",
            "description": (
                f"task_type=template_audit\n"
                f"document_path={document_path}\n"
                f"instructions={instructions}\n"
                "Run lex_template_audit and hand off structured findings."
            ),
            "assignee": "hpswarm-reviewer-format",
            "gates": json.dumps([{"type": "approve", "target_pool": "hpswarm-coordinator"}]),
        }, parent_agent=parent_agent))
        if result.get("success"):
            created_tasks.append(result["task"])

    if not created_tasks:
        return json.dumps({
            "success": False,
            "error": f"task_type={task_type} could not be mapped to a Kanban task.",
            "mode": "kanban",
            "project_path": project_path,
            "hint": (
                "Use task_type=plan/draft/revise/review/proofread/template_fill/"
                "template_audit/deliver, or call with mode='direct', allow_direct=true "
                "only after explicit user approval."
            ),
        }, ensure_ascii=False)

    return json.dumps({
        "success": True,
        "mode": "kanban",
        "board_id": board_id,
        "project_path": project_path,
        "task_type": task_type,
        "tasks_created": len(created_tasks),
        "tasks": created_tasks,
        "message": (
            f"已在 Kanban Board 上创建 {len(created_tasks)} 个任务。"
            f"Worker 可以用 swarm_task_claim 认领。"
        ),
    }, ensure_ascii=False)


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
    bilingual_path = str(args.get("bilingual_path") or "").strip() or None
    source_path = str(args.get("source_path") or "").strip() or None
    translation_path = str(args.get("translation_path") or "").strip() or None
    glossary_path = str(args.get("glossary_path") or "").strip() or None
    sop_path = str(args.get("sop_path") or "").strip() or None
    sop_overrides = str(args.get("sop_overrides") or "").strip() or None
    domain_terms = args.get("domain_terms") if isinstance(args.get("domain_terms"), dict) else {}
    enable_learning = bool(args.get("enable_learning", True))
    learning_scope = str(args.get("learning_scope") or "global").strip()
    workflow_type = str(args.get("workflow_type") or "").strip() or None
    learning_workflow_type = workflow_type or ("proofread_review" if task_type == "proofread" else "contract_revision")
    if task_type == "draft_iterative" and not workflow_type:
        learning_workflow_type = "document_drafting"
    mode = str(args.get("mode") or "kanban").strip().lower()
    if mode not in {"direct", "kanban"}:
        return tool_error("mode must be 'kanban' or 'direct'.")

    # ── Kanban bridge mode ──────────────────────────────────────────────────
    if mode == "kanban":
        return _handle_kanban_mode(
            task_type=task_type,
            project_root=project_root,
            document_path=document_path,
            instructions=instructions,
            args=args,
            parent_agent=parent_agent,
        )

    direct_allowed = bool(os.environ.get("HERMES_KANBAN_TASK"))
    if not direct_allowed:
        direct_allowed = bool(args.get("allow_direct")) and bool(str(args.get("direct_reason") or "").strip())
    if not direct_allowed:
        return tool_error(
            "legal_orchestrate defaults to Kanban. Direct mode is blocked unless "
            "this call runs inside a Kanban worker (HERMES_KANBAN_TASK) or the user "
            "explicitly approves a one-off bypass with allow_direct=true and direct_reason."
        )

    if task_type == "deliver":
        from tools.registry import registry as _registry

        if not project_root.exists():
            return tool_error(f"project_dir does not exist: {project_root}")
        return _registry.dispatch(
            "lex_deliver",
            {"project_dir": str(project_root)},
            task_id=kwargs.get("task_id"),
        )

    if task_type in ("template_audit", "template_fill"):
        from tools.registry import registry as _registry

        if task_type == "template_audit":
            if not document_path:
                return tool_error("template_audit requires document_path.")
            return _registry.dispatch(
                "lex_template_audit",
                {"path": document_path},
                task_id=kwargs.get("task_id"),
                parent_agent=parent_agent,
            )

        if task_type == "template_fill":
            if not document_path:
                return tool_error("template_fill requires document_path.")
            if not instructions:
                return tool_error("template_fill requires instructions (JSON fill_values).")
            try:
                fill_values = json.loads(instructions)
            except json.JSONDecodeError:
                return tool_error("template_fill instructions must be valid JSON with 'blanks' and 'checkboxes' keys.")
            output = str(args.get("output") or "").strip() or document_path
            manifest_json = str(args.get("manifest_json") or args.get("term_sheet_path") or "").strip()
            if not manifest_json:
                manifest_result = _registry.dispatch(
                    "lex_template_audit",
                    {"path": document_path},
                    task_id=kwargs.get("task_id"),
                    parent_agent=parent_agent,
                )
                try:
                    manifest = json.loads(manifest_result)
                except (json.JSONDecodeError, TypeError):
                    manifest_json = manifest_result
                else:
                    if isinstance(manifest, dict) and not manifest.get("error"):
                        manifest_json = json.dumps(manifest, ensure_ascii=False)
            return _registry.dispatch(
                "lex_template_fill",
                {
                    "path": document_path,
                    "manifest_json": manifest_json,
                    "fill_values_json": json.dumps(fill_values, ensure_ascii=False),
                    "output": output,
                    "tc": bool(args.get("tc", True)),
                },
                task_id=kwargs.get("task_id"),
                parent_agent=parent_agent,
            )

    if task_type == "plan":
        from tools.registry import registry as _registry

        if not workflow_type:
            plan_text = " ".join(
                text for text in (instructions, document_path, bilingual_path, source_path, translation_path) if text
            ).lower()
            if any(marker in plan_text for marker in ("translation", "翻译", "中英文", "bilingual")):
                workflow_type = "translation_quality_review"
            elif any(marker in plan_text for marker in ("proofread", "校对", "审校", "审阅", "review")):
                workflow_type = "proofread_review"
            elif any(marker in plan_text for marker in ("逐段制作", "一页一页", "draft", "制作", "起草")):
                workflow_type = "document_drafting"
            else:
                workflow_type = "contract_revision"
        plan_name_by_type = {
            "contract_revision": "法律文书修订 workflow",
            "document_drafting": "法律文书逐段制作 workflow",
            "proofread_review": "法律文书逐段校对 workflow",
            "translation_quality_review": "中英文翻译质量核对 workflow",
        }

        return _registry.dispatch(
            "legal_workflow",
            {
                "action": "create_plan",
                "name": plan_name_by_type.get(workflow_type, "法律文书 workflow"),
                "workflow_type": workflow_type,
                "project_dir": str(project_root),
                "document_path": document_path or "",
                "term_sheet_path": term_sheet_path or "",
                "bilingual_path": bilingual_path or document_path or "",
                "source_path": source_path or "",
                "translation_path": translation_path or "",
                "glossary_path": glossary_path or term_sheet_path or "",
                "sop_path": sop_path or "",
                "sop_overrides": sop_overrides or "",
                "domain_terms": domain_terms,
                "enable_learning": enable_learning,
                "learning_scope": learning_scope,
                "review_types": [
                    str(item).strip().replace("review_", "")
                    for item in (args.get("review_types") or [])
                    if str(item).strip()
                ],
                "chunk_size": int(args.get("chunk_size") or (180 if workflow_type == "proofread_review" else 120)),
                "instructions": instructions or "",
            },
            task_id=kwargs.get("task_id"),
            parent_agent=parent_agent,
        )

    if task_type == "proofread":
        from tools.registry import registry as _registry

        if not document_path:
            return tool_error("proofread requires document_path.")
        review_types = [
            str(item).strip().replace("review_", "")
            for item in (args.get("review_types") or [])
            if str(item).strip()
        ] or ["content", "format", "xref"]
        return _registry.dispatch(
            "lex_proofread",
            {
                "path": document_path or "",
                "review_types": review_types,
                "chunk_size": int(args.get("chunk_size") or 180),
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

        # Auto-route large documents to lex_proofread (split-and-parallel review).
        # Prevents the attention-degradation death march where a single reviewer
        # receives 2000+ paragraphs and manually chunks them 15 at a time.
        if document_path:
            try:
                para_count = _paragraph_count(document_path)
                if para_count > 200:
                    from tools.registry import registry as _registry

                    proofread_types = [
                        t.replace("review_", "")
                        for t in review_types
                        if t.replace("review_", "") in {"content", "format", "ts", "xref", "translation"}
                    ]
                    if not proofread_types:
                        proofread_types = ["content", "format", "xref"]
                    return _registry.dispatch(
                        "lex_proofread",
                        {
                            "path": document_path,
                            "review_types": proofread_types,
                            "chunk_size": int(args.get("chunk_size") or 300),
                        },
                        task_id=kwargs.get("task_id"),
                        parent_agent=parent_agent,
                    )
            except Exception:
                pass  # Fall through to manual review if stats fail

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
            workflow_type=learning_workflow_type,
            learning_scope=learning_scope,
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
        review_run_id: str | None = None
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
            handoff_record = None
            try:
                from hermes_cli.project_commands import legal_handoff_record

                handoff = structured if isinstance(structured, dict) else {
                    "status": "failed" if result.get("error") else "completed",
                    "summary": summary,
                    "findings": task_findings,
                    "evidence": {"delegate_summary": summary[:4000]},
                }
                handoff_result = legal_handoff_record(
                    str(project_root),
                    workflow_id="full_review",
                    node_id=review_type.replace("review_", "") or "review",
                    handoff=handoff,
                    run_id=review_run_id,
                )
                handoff_record = handoff_result.get("record")
                review_run_id = (handoff_record or {}).get("run_id") or review_run_id
            except Exception:
                handoff_record = None
            subtasks.append(
                {
                    "review_type": review_type,
                    "status": result.get("status"),
                    "run_id": review_run_id,
                    "handoff_valid": (handoff_record or {}).get("valid"),
                    "summary": (
                        (structured or {}).get("summary")
                        if isinstance(structured, dict)
                        else summary
                    ) or str(task.get("machine_summary") or ""),
                    "finding_count": len(task_findings),
                }
            )
        findings = _dedupe_findings(findings)
        learning = _learn_from_review_findings(
            findings=findings,
            workflow_type=learning_workflow_type,
            learning_scope=learning_scope,
            document_path=document_path,
            enabled=enable_learning,
        )
        payload = _run_scorecard_gate(
            project_root,
            document_path=document_path,
            workflow_id="full_review",
            run_id=review_run_id,
            learning_scope=learning_scope,
            enable_learning=enable_learning,
            payload={
                "document_path": document_path,
                "findings": findings,
                "learning": learning,
                "learning_candidates": learning.get("candidates", []),
                "project_dir": str(project_root),
                "run_id": review_run_id,
                "status": "completed",
                "subtasks": subtasks,
                "task_type": task_type,
            },
        )
        return _tool_ok(payload)

    if task_type == "review_translation":
        from tools.registry import registry as _registry

        return _registry.dispatch(
            "lex_translation_review",
            {
                "bilingual_path": bilingual_path or document_path or "",
                "source_path": source_path or "",
                "translation_path": translation_path or "",
                "glossary_path": glossary_path or term_sheet_path or "",
                "sop_path": sop_path or "",
                "sop_overrides": sop_overrides or "",
                "domain_terms": domain_terms,
                "instructions": instructions or "",
                "chunk_size": int(args.get("chunk_size") or 120),
                "source_language": "Chinese",
                "target_language": "English",
                "enable_learning": enable_learning,
                "learning_scope": learning_scope,
            },
            task_id=kwargs.get("task_id"),
            parent_agent=parent_agent,
        )

    if task_type == "swarm":
        workflow_id = str(args.get("workflow_id") or "contract_revision").strip()
        if not project_root.exists():
            return tool_error(f"project_dir does not exist: {project_root}")

        from ..kanban_legal_swarm import compile_workflow

        run = compile_workflow(
            project_dir=str(project_root),
            workflow_id=workflow_id,
            params={
                "document_path": document_path or "",
                "instructions": instructions or "",
            },
        )
        return tool_result(json.dumps(run.as_dict(), ensure_ascii=False, indent=2))

    if task_type == "draft_iterative":
        if not document_path:
            return tool_error("draft_iterative requires document_path.")
        result = _run_iterative_drafting(
            parent_agent=parent_agent,
            project_root=project_root,
            document_path=document_path,
            instructions=instructions,
            related_paths=related_paths,
            term_sheet_path=term_sheet_path,
            chunk_size=int(args.get("chunk_size") or 12),
            learning_workflow_type=learning_workflow_type or "document_drafting",
            learning_scope=learning_scope,
        )
        if result.get("error"):
            return tool_error(str(result["error"]), data=result)
        run_id = None
        for chunk in result.get("chunks") or []:
            if chunk.get("run_id"):
                run_id = chunk["run_id"]
                break
        result = _run_scorecard_gate(
            project_root,
            document_path=document_path,
            workflow_id="contract_revision",
            run_id=run_id,
            learning_scope=learning_scope,
            enable_learning=enable_learning,
            payload=result,
        )
        return _tool_ok(result)

    # Structured-template auto-detection — intercept before generic draft/revise.
    # Annotated templates (NAFMII, APLMA, CSRC, etc.) have fill-in blanks,
    # checkboxes, and colored notes.  The correct workflow has THREE phases:
    #
    #   Phase 1 (mechanical): lex_template_fill blanks + checkboxes only
    #   Phase 2 (intellectual): Drafter reads the partially-filled document,
    #           drafts custom clauses guided by the remaining annotations,
    #           verifies every edit — THIS IS WHERE THE REAL WORK HAPPENS
    #   Phase 3 (cleanup): lex_template_fill deletes annotations + guide + highlights
    #
    # Then review proceeds normally.
    if task_type in ("draft", "revise") and document_path:
        try:
            from .lex_template_tool import detect_nafmii_template

            naftype = detect_nafmii_template(document_path)
            if naftype:
                from tools.registry import registry as _registry

                manifest_json = _registry.dispatch(
                    "lex_template_audit",
                    {"path": document_path},
                    task_id=kwargs.get("task_id"),
                    parent_agent=parent_agent,
                )
                try:
                    manifest = json.loads(manifest_json)
                except (json.JSONDecodeError, TypeError):
                    manifest = {"raw": str(manifest_json)[:2000]}

                return _tool_ok({
                    "template_detected": True,
                    "template_type": naftype,
                    "document_path": document_path,
                    "manifest": manifest,
                    "workflow": [
                        {
                            "step": 1,
                            "phase": "mechanical_fill",
                            "action": "legal_orchestrate(task_type='template_fill', phases=['fill_blanks','select_checkboxes'], ...)",
                            "what_happens": "替换所有高亮空白为实际值；勾选☑选项。注释和批注保留——Drafter 需要它们作为起草指引。",
                        },
                        {
                            "step": 2,
                            "phase": "substantive_drafting",
                            "action": "delegate_task(role='drafter', ...)",
                            "what_happens": "Drafter 逐段阅读填写后的文档，根据残留的注释/批注起草自定义条款、适配标准条款、处理勾选导致的可选条款增删、交叉引用验证。完成后执行完整强制验证协议。",
                        },
                        {
                            "step": 3,
                            "phase": "cleanup",
                            "action": "legal_orchestrate(task_type='template_fill', phases=['delete_colored_notes','delete_annotations','delete_guide','strip_highlights'], ...)",
                            "what_happens": "Drafter 起草完毕后，删除所有注释、批注、使用说明、高亮——文档变为清洁版。",
                        },
                        {
                            "step": 4,
                            "phase": "review",
                            "action": "lex_proofread(path, review_type='all', nafmii_mode=True) or parallel delegate reviewer tasks",
                            "what_happens": "审阅阶段。NAFMII 模式下启用：前置机械审计（检查残留空白/注释）+ 条款感知分块 + 表格强化审查。",
                        },
                    ],
                    "next_step": (
                        "标注版模板检测到。请按以下顺序处理：\n"
                        "1. 提供 blanks 和 checkboxes 的填写值，调用 template_fill（仅 fill 和 checkbox 阶段）\n"
                        "2. 委派 Drafter 进行实质性起草（文档中的注释/批注是起草指引，此时保留）\n"
                        "3. Drafter 完成后，调用 template_fill（仅 cleanup 阶段）删除注释\n"
                        "4. 进入审阅阶段"
                    ),
                    "project_dir": str(project_root),
                    "status": "awaiting_mechanical_fill",
                    "task_type": task_type,
                })
        except Exception:
            pass  # If detection fails, fall through to normal draft

    # Auto-route large-document draft/revise to iterative drafting.
    # A single drafter receiving 2000+ paragraphs will suffer attention
    # degradation.  Iterative drafting feeds 12-paragraph chunks sequentially,
    # each with pre-read context and readback verification.
    if task_type in ("draft", "revise") and document_path:
        try:
            para_count = _paragraph_count(document_path)
            if para_count > 40:
                result = _run_iterative_drafting(
                    parent_agent=parent_agent,
                    project_root=project_root,
                    document_path=document_path,
                    instructions=instructions,
                    related_paths=related_paths,
                    term_sheet_path=term_sheet_path,
                    chunk_size=int(args.get("chunk_size") or 12),
                    learning_workflow_type=learning_workflow_type or "document_drafting",
                    learning_scope=learning_scope,
                )
                if result.get("error"):
                    return tool_error(str(result["error"]), data=result)
                run_id = None
                for chunk in result.get("chunks") or []:
                    if chunk.get("run_id"):
                        run_id = chunk["run_id"]
                        break
                result = _run_scorecard_gate(
                    project_root,
                    document_path=document_path,
                    workflow_id="contract_revision",
                    run_id=run_id,
                    learning_scope=learning_scope,
                    enable_learning=enable_learning,
                    payload=result,
                )
                return _tool_ok(result)
        except Exception:
            pass  # Document may not exist yet → fall through to single-child draft

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
        + ("\n\n" + _workflow_learning_context(learning_workflow_type, learning_scope) if learning_workflow_type else "")
        + "\n\n"
        + contract
    ).strip()
    delegated = _dispatch_single_child(
        parent_agent=parent_agent,
        project_root=project_root,
        workflow_id=learning_workflow_type,
        node_id=task_type,
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
        learning = _learn_from_review_findings(
            findings=findings,
            workflow_type=learning_workflow_type,
            learning_scope=learning_scope,
            document_path=document_path,
            enabled=enable_learning,
        )
        payload = _run_scorecard_gate(
            project_root,
            document_path=document_path,
            workflow_id=learning_workflow_type,
            run_id=delegated.get("run_id") if isinstance(delegated, dict) else None,
            learning_scope=learning_scope,
            enable_learning=enable_learning,
            payload={
                "document_path": document_path,
                "findings": findings,
                "learning": learning,
                "learning_candidates": learning.get("candidates", []),
                "project_dir": str(project_root),
                "status": (structured or {}).get("status", "completed") if isinstance(structured, dict) else "completed",
                "summary": (
                    (structured or {}).get("summary", summary)
                    if isinstance(structured, dict)
                    else summary
                ) or str((preflight or {}).get("summary") or ""),
                "task_type": task_type,
            },
        )
        return _tool_ok(payload)

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
    if task_type in ("draft", "revise") and _revision_guard_failed(payload):
        payload["status"] = "failed"
        payload["verification_passed"] = False
        report = payload.get("verification_report")
        if not isinstance(report, list):
            report = []
        report.append(
            "lex_revision_guard failed and residuals were not fully justified; parent workflow blocks completion."
        )
        payload["verification_report"] = report
    payload = _run_scorecard_gate(
        project_root,
        document_path=document_path,
        workflow_id=learning_workflow_type,
        run_id=delegated.get("run_id") if isinstance(delegated, dict) else None,
        learning_scope=learning_scope,
        enable_learning=enable_learning,
        payload=payload,
    )
    return _tool_ok(payload)


# Retired from the native tool surface.  The legal harness now enters through
# legal_workflow(action="start"), which compiles workflow runs onto kanban_swarm.
# Keep the module importable for migration/reference tests, but do not register
# legal_orchestrate: registry discovery treats top-level registry.register calls
# as tool exposure.
