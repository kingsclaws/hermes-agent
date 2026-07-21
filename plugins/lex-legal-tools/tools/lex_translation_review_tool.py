"""Native bilingual legal translation review workflow."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .legal_workflow_learning import (
    export_learning_sop as _generic_export_learning_sop,
    format_learning_rules as _generic_format_learning_rules,
    learn_from_findings as _generic_learn_from_findings,
    learning_candidates_from_findings as _generic_learning_candidates_from_findings,
    learning_sop_markdown as _generic_learning_sop_markdown,
)
from tools.registry import registry, tool_error, tool_result


LEX_TRANSLATION_REVIEW_SCHEMA = {
    "name": "lex_translation_review",
    "description": (
        "Run a native bilingual legal translation QA workflow. It chunks the "
        "source/translation or bilingual DOCX, delegates visible reviewer tasks, "
        "and aggregates structured findings for omissions, mistranslations, "
        "defined terms, numbers, cross-references, and legal-effect shifts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "bilingual_path": {
                "type": "string",
                "description": "Single DOCX containing Chinese and English text to compare.",
            },
            "source_path": {
                "type": "string",
                "description": "Source-language DOCX when source and translation are separate documents.",
            },
            "translation_path": {
                "type": "string",
                "description": "Translated DOCX when source and translation are separate documents.",
            },
            "glossary_path": {
                "type": "string",
                "description": "Optional glossary, term sheet, or defined-term reference file.",
            },
            "sop_path": {
                "type": "string",
                "description": "Optional project/task-specific translation QA SOP markdown/text file.",
            },
            "sop_overrides": {
                "type": "string",
                "description": "Optional task-specific SOP additions, exceptions, or priority changes.",
            },
            "domain_terms": {
                "type": "object",
                "description": "Optional task-specific term map, e.g. {'本合同': 'this Agreement'}.",
            },
            "instructions": {
                "type": "string",
                "description": "User-specific review scope, clause range, language priority, or edit policy.",
            },
            "chunk_size": {
                "type": "integer",
                "description": "Maximum paragraph count per review task. Default: 120.",
            },
            "source_language": {
                "type": "string",
                "description": "Source language label. Default: Chinese.",
            },
            "target_language": {
                "type": "string",
                "description": "Target language label. Default: English.",
            },
            "enable_learning": {
                "type": "boolean",
                "description": "Whether to extract reusable workflow learning rules. Default: true.",
            },
            "learning_scope": {
                "type": "string",
                "enum": ["global"],
                "description": "Where learning rules are stored. Default: global.",
            },
            "workflow_run_id": {
                "type": "string",
                "description": "Optional legal workflow run id to associate learning with.",
            },
        },
    },
}


def _handle_translation_review(args: dict, **kwargs) -> str:
    return lex_translation_review(
        bilingual_path=_clean(args.get("bilingual_path")),
        source_path=_clean(args.get("source_path")),
        translation_path=_clean(args.get("translation_path")),
        glossary_path=_clean(args.get("glossary_path")),
        sop_path=_clean(args.get("sop_path")),
        sop_overrides=_clean(args.get("sop_overrides")),
        domain_terms=args.get("domain_terms") if isinstance(args.get("domain_terms"), dict) else None,
        instructions=_clean(args.get("instructions")),
        chunk_size=int(args.get("chunk_size") or 120),
        source_language=_clean(args.get("source_language")) or "Chinese",
        target_language=_clean(args.get("target_language")) or "English",
        enable_learning=bool(args.get("enable_learning", True)),
        learning_scope=_clean(args.get("learning_scope")) or "global",
        workflow_run_id=_clean(args.get("workflow_run_id")),
        parent_agent=kwargs.get("parent_agent"),
    )


def lex_translation_review(
    *,
    bilingual_path: str | None = None,
    source_path: str | None = None,
    translation_path: str | None = None,
    glossary_path: str | None = None,
    sop_path: str | None = None,
    sop_overrides: str | None = None,
    domain_terms: dict[str, Any] | None = None,
    instructions: str | None = None,
    chunk_size: int = 120,
    source_language: str = "Chinese",
    target_language: str = "English",
    enable_learning: bool = True,
    learning_scope: str = "global",
    workflow_run_id: str | None = None,
    parent_agent: Any = None,
) -> str:
    if parent_agent is None:
        return tool_error("lex_translation_review requires parent agent context.")

    if not bilingual_path and not (source_path and translation_path):
        return tool_error(
            "Provide either bilingual_path, or both source_path and translation_path."
        )

    primary_path = bilingual_path or source_path
    if not primary_path:
        return tool_error("No review document path was provided.")

    from lexitool.markup import lex_read
    from tools.delegate_tool import delegate_task

    try:
        structure = lex_read(primary_path, mode="structure")
        stats = lex_read(primary_path, mode="stats")
    except Exception as exc:
        return tool_error(f"lex_read failed for {primary_path}: {exc}")

    total_paras = _parse_total_paragraphs(stats)
    if total_paras <= 0:
        return tool_error(f"Could not determine paragraph count for {primary_path}.")

    global_rules = _load_active_learning_rules(
        workflow_type="translation_quality_review",
        scope=learning_scope,
    )
    chunks = _compute_review_chunks(structure, total_paras, max(chunk_size, 40))
    tasks = [
        _build_task(
            index=index,
            start=start,
            end=end,
            label=label,
            bilingual_path=bilingual_path,
            source_path=source_path,
            translation_path=translation_path,
            glossary_path=glossary_path,
            sop_text=_load_sop(sop_path),
            sop_overrides=sop_overrides,
            domain_terms=domain_terms or {},
            global_rules=global_rules,
            instructions=instructions,
            source_language=source_language,
            target_language=target_language,
        )
        for index, (start, end, label) in enumerate(chunks, start=1)
    ]

    raw_results = delegate_task(tasks=tasks, role="leaf", parent_agent=parent_agent)
    return _aggregate_results(
        raw_results=raw_results,
        chunks=chunks,
        bilingual_path=bilingual_path,
        source_path=source_path,
        translation_path=translation_path,
        glossary_path=glossary_path,
        sop_path=sop_path,
        sop_overrides=sop_overrides,
        domain_terms=domain_terms or {},
        source_language=source_language,
        target_language=target_language,
        enable_learning=enable_learning,
        learning_scope=learning_scope,
        workflow_run_id=workflow_run_id,
    )


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _load_sop(path: str | None) -> str:
    if not path:
        return ""
    try:
        sop_path = Path(path).expanduser()
        if not sop_path.is_file():
            return f"[SOP file not found: {path}]"
        text = sop_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"[Could not read SOP file {path}: {exc}]"
    return text[:12000]


def _load_active_learning_rules(*, workflow_type: str, scope: str) -> list[dict[str, Any]]:
    try:
        from hermes_state import SessionDB

        return SessionDB().list_workflow_learning_rules(
            workflow_type=workflow_type,
            scope=scope,
            status="active",
            limit=80,
        )
    except Exception:
        return []


def _parse_total_paragraphs(stats: str) -> int:
    match = re.search(r"Paragraphs:\s*(\d+)", stats or "")
    return int(match.group(1)) if match else 0


def _compute_review_chunks(
    structure: str,
    total_paras: int,
    max_size: int,
) -> list[tuple[int, int, str]]:
    try:
        from .lex_proofread_tool import _compute_chunks

        chunks = _compute_chunks(structure, total_paras, max_size)
        if chunks:
            return chunks
    except Exception:
        pass

    return [
        (start, min(start + max_size - 1, total_paras), f"§{start}-§{min(start + max_size - 1, total_paras)}")
        for start in range(1, total_paras + 1, max_size)
    ]


def _build_task(
    *,
    index: int,
    start: int,
    end: int,
    label: str,
    bilingual_path: str | None,
    source_path: str | None,
    translation_path: str | None,
    glossary_path: str | None,
    sop_text: str,
    sop_overrides: str | None,
    domain_terms: dict[str, Any],
    global_rules: list[dict[str, Any]],
    instructions: str | None,
    source_language: str,
    target_language: str,
) -> dict[str, Any]:
    if bilingual_path:
        read_block = (
            f"Read the bilingual document with native lex_read only:\n"
            f"lex_read(path='{bilingual_path}', paras=[{start},{end}])\n"
            "Treat Chinese as controlling unless the user instructions say otherwise."
        )
    else:
        read_block = (
            "Read the corresponding source and translation ranges with native lex_read only:\n"
            f"lex_read(path='{source_path}', paras=[{start},{end}])\n"
            f"lex_read(path='{translation_path}', paras=[{start},{end}])\n"
            "If paragraph alignment is imperfect, use headings and clause numbers to align before reviewing."
        )

    glossary_block = (
        f"\nReference glossary/term source: {glossary_path}. Read it when terminology or defined terms are relevant."
        if glossary_path
        else ""
    )
    domain_terms_block = (
        "\nTask-specific domain terms:\n"
        + json.dumps(domain_terms, ensure_ascii=False, indent=2)
        if domain_terms
        else ""
    )
    sop_block = (
        "\nProject/task-specific SOP excerpt. Apply it only as a supplement to the baseline rules; "
        "do not assume project-specific examples are universal:\n"
        f"{sop_text}"
        if sop_text
        else ""
    )
    override_block = (
        "\nTask-specific SOP overrides/additions. These override the baseline where they conflict:\n"
        f"{sop_overrides.strip()}"
        if sop_overrides
        else ""
    )
    learning_block = _format_learning_rules(global_rules)
    instruction_block = f"\nUser instructions: {instructions.strip()}" if instructions else ""

    goal = f"""
Bilingual legal translation QA task #{index}: paragraphs §{start}-§{end} ({label}).

{read_block}
{glossary_block}
{domain_terms_block}
{sop_block}
{override_block}
{learning_block}
{instruction_block}

Baseline legal translation QA rules:
1. Determine the controlling language from user instructions; if unspecified, treat the source language as controlling.
2. Preserve legal effect over literal phrasing. Flag any shift in obligation, condition, discretion, timing, amount, remedy, liability, termination, consent, or notice mechanics.
3. Check defined terms and party names for consistent translation, capitalization, formatting, and singular/plural usage.
4. Check cross-references, clause/schedule/table labels, dates, numbers, currencies, percentages, URLs, and notice periods.
5. Separate substantive translation issues from style/format issues. Do not bury a major omission under a formatting note.
6. Produce pending-confirmation items when the correct legal wording requires user/client confirmation.

Mandatory workflow:
1. Build a brief {source_language} ↔ {target_language} alignment for the reviewed range.
2. Check every sentence for omission, addition, mistranslation, ambiguity, and legal-effect shift.
3. Check defined terms, party names, facility names, clause references, schedules, numbers, dates, currency, percentages, notice periods, conditions, obligations, discretion, negation, and modal verbs.
4. Check drafting quality: capitalization of defined English terms, spacing, punctuation, bilingual bracket style, and obvious Chinese typos.
5. Do not edit the document. Report issues only, unless the parent task explicitly asks for edits.

Return ONLY valid JSON. No markdown fences. Use this schema exactly:
{{
  "status": "completed",
  "range": "§{start}-§{end}",
  "summary": "short result",
  "findings": [
    {{
      "severity": "critical|major|minor|info",
      "paragraph": "§123",
      "clause": "clause heading if known",
      "issue_type": "major_omission|omission|addition|mistranslation|legal_effect|defined_term|party_name|number_date_currency|cross_reference|schedule_table|grammar_spelling|style_format|source_typo|pending_confirmation",
      "source_text": "controlling source wording",
      "translation_text": "current translation wording",
      "finding": "what is wrong",
      "suggestion": "specific corrected wording"
    }}
  ],
  "no_issue_ranges": ["§x-§y"]
}}
If there are no issues, return findings as [] and list the reviewed no_issue_ranges.
""".strip()

    return {
        "goal": goal,
        "profile": "lex-reviewer-translation",
        "toolsets": ["lex-docx", "file", "project_management"],
    }


def _aggregate_results(
    *,
    raw_results: str,
    chunks: list[tuple[int, int, str]],
    bilingual_path: str | None,
    source_path: str | None,
    translation_path: str | None,
    glossary_path: str | None,
    sop_path: str | None,
    sop_overrides: str | None,
    domain_terms: dict[str, Any],
    source_language: str,
    target_language: str,
    enable_learning: bool,
    learning_scope: str,
    workflow_run_id: str | None,
) -> str:
    try:
        data = json.loads(raw_results)
    except Exception:
        return tool_result(
            {
                "status": "completed",
                "warning": "delegate_task returned non-JSON output",
                "raw_results": raw_results,
            }
        )

    findings: list[dict[str, Any]] = []
    subtasks: list[dict[str, Any]] = []
    for index, result in enumerate(data.get("results") or []):
        summary = str(result.get("summary") or result.get("error") or "")
        parsed = _extract_json(summary)
        task_findings = _normalize_findings(parsed.get("findings") if parsed else [])
        findings.extend(task_findings)
        subtasks.append(
            {
                "index": index + 1,
                "status": result.get("status"),
                "summary": (parsed or {}).get("summary") or summary[:500],
                "finding_count": len(task_findings),
                "range": (parsed or {}).get("range") or _chunk_range(chunks, index),
            }
        )

    findings = _dedupe_findings(findings)
    learning = (
        _learn_from_findings(
            findings=findings,
            workflow_type="translation_quality_review",
            scope=learning_scope,
            run_id=workflow_run_id,
            document_path=bilingual_path or source_path or translation_path,
        )
        if enable_learning
        else {"enabled": False, "candidates": [], "active_rules": [], "candidate_rules": []}
    )
    payload = {
            "status": "completed",
            "workflow_type": "translation_quality_review",
            "bilingual_path": bilingual_path,
            "source_path": source_path,
            "translation_path": translation_path,
            "glossary_path": glossary_path,
            "sop_path": sop_path,
            "sop_overrides": sop_overrides,
            "domain_terms": domain_terms,
            "source_language": source_language,
            "target_language": target_language,
            "chunks": [
                {"start": start, "end": end, "label": label}
                for start, end, label in chunks
            ],
            "subtasks": subtasks,
            "findings": findings,
            "finding_count": len(findings),
            "delegate_summary": {
                "total_duration_seconds": data.get("total_duration_seconds"),
                "task_count": len(data.get("results") or []),
            },
            "report": _markdown_report(findings, subtasks),
            "learning": learning,
            "learning_candidates": learning.get("candidates", []),
        }
    return tool_result(payload)


def _extract_json(text: str) -> dict[str, Any] | None:
    stripped = (text or "").strip()
    candidates = []
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _format_learning_rules(rules: list[dict[str, Any]]) -> str:
    return _generic_format_learning_rules(rules)


def _learn_from_findings(
    *,
    findings: list[dict[str, Any]],
    workflow_type: str,
    scope: str,
    run_id: str | None,
    document_path: str | None,
) -> dict[str, Any]:
    return _generic_learn_from_findings(
        findings=findings,
        workflow_type=workflow_type,
        scope=scope,
        run_id=run_id,
        document_path=document_path,
        enabled=True,
    )


def _learning_candidates_from_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _generic_learning_candidates_from_findings(
        findings,
        workflow_type="translation_quality_review",
    )


def _export_learning_sop(*, workflow_type: str, scope: str, db) -> str | None:
    return _generic_export_learning_sop(workflow_type=workflow_type, scope=scope, db=db)


def _learning_sop_markdown(
    workflow_type: str,
    scope: str,
    active: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> str:
    return _generic_learning_sop_markdown(workflow_type, scope, active, candidates)


def _normalize_findings(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "severity": str(item.get("severity") or "info").lower(),
                "paragraph": str(item.get("paragraph") or "").strip(),
                "clause": str(item.get("clause") or "").strip(),
                "issue_type": str(item.get("issue_type") or "translation").strip(),
                "source_text": str(item.get("source_text") or "").strip(),
                "translation_text": str(item.get("translation_text") or "").strip(),
                "finding": str(item.get("finding") or "").strip(),
                "suggestion": str(item.get("suggestion") or "").strip(),
            }
        )
    return normalized


def _dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen = set()
    for item in findings:
        key = (
            item.get("paragraph"),
            item.get("issue_type"),
            item.get("source_text"),
            item.get("translation_text"),
            item.get("finding"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _chunk_range(chunks: list[tuple[int, int, str]], index: int) -> str:
    if index >= len(chunks):
        return ""
    start, end, _label = chunks[index]
    return f"§{start}-§{end}"


def _markdown_report(findings: list[dict[str, Any]], subtasks: list[dict[str, Any]]) -> str:
    lines = [
        "# Translation Quality Review",
        "",
        f"Subtasks: {len(subtasks)}",
        f"Findings: {len(findings)}",
        "",
    ]
    if not findings:
        lines.append("No translation issues were reported by delegated reviewers.")
        return "\n".join(lines)

    lines.extend(
        [
            "| severity | paragraph | issue_type | finding | suggestion |",
            "|---|---|---|---|---|",
        ]
    )
    for item in findings:
        lines.append(
            "| {severity} | {paragraph} | {issue_type} | {finding} | {suggestion} |".format(
                severity=_md(item.get("severity")),
                paragraph=_md(item.get("paragraph")),
                issue_type=_md(item.get("issue_type")),
                finding=_md(item.get("finding")),
                suggestion=_md(item.get("suggestion")),
            )
        )
    return "\n".join(lines)


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _check_lexitool() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("lexitool") is not None
    except Exception:
        return False


registry.register(
    name="lex_translation_review",
    toolset="lexitool",
    schema=LEX_TRANSLATION_REVIEW_SCHEMA,
    handler=_handle_translation_review,
    check_fn=_check_lexitool,
    emoji="🌐",
)
