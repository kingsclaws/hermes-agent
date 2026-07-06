"""Shared workflow-learning helpers for legal workflows."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


LOW_RISK_AUTO_ISSUE_TYPES = {
    "cross_reference",
    "defined_term",
    "delivery_artifact",
    "evidence_ledger",
    "party_name",
    "read_coverage",
    "review_format",
    "review_xref",
    "schedule_table",
    "source_reading",
    "style_format",
}


LEARNING_CATEGORY_BY_ISSUE_TYPE = {
    "cross_reference": "reference_format",
    "defined_term": "terminology",
    "delivery_artifact": "delivery",
    "evidence_ledger": "verification",
    "party_name": "terminology",
    "read_coverage": "source_reading",
    "review_content": "candidate_substantive",
    "review_format": "format",
    "review_translation": "candidate_language",
    "review_ts": "candidate_ts_consistency",
    "review_xref": "reference_format",
    "schedule_table": "format",
    "scorecard_gate": "verification",
    "source_reading": "source_reading",
    "source_risk": "candidate_source_risk",
    "style_format": "format",
}


WORKFLOW_LABELS = {
    "contract_revision": "Contract revision",
    "delivery_gate": "Delivery gate",
    "document_drafting": "Document drafting",
    "project_init": "Project init",
    "proofread_review": "Proofread review",
    "translation_quality_review": "Translation QA",
}


def format_learning_rules(rules: list[dict[str, Any]]) -> str:
    if not rules:
        return "No active reusable workflow rules yet."

    lines = ["Active reusable workflow rules:"]
    for item in rules[:12]:
        title = str(item.get("title") or "Untitled rule").strip()
        rule_text = str(item.get("rule_text") or "").strip()
        category = str(item.get("category") or "general").strip()
        lines.append(f"- [{category}] {title}: {rule_text}")
    return "\n".join(lines)


def learn_from_findings(
    *,
    findings: list[dict[str, Any]],
    workflow_type: str,
    scope: str = "global",
    run_id: str | None = None,
    document_path: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "findings": findings or []}

    candidates = learning_candidates_from_findings(findings or [], workflow_type=workflow_type)
    if not candidates:
        return {
            "enabled": True,
            "findings": findings or [],
            "candidates": [],
            "active_rules": [],
            "candidate_rules": [],
            "summary": "No reusable workflow learning extracted.",
        }

    try:
        from hermes_state import SessionDB

        db = SessionDB()
        active_rules: list[dict[str, Any]] = []
        candidate_rules: list[dict[str, Any]] = []
        for item in candidates:
            row = db.upsert_workflow_learning_rule(
                workflow_type=workflow_type,
                scope=scope,
                category=item["category"],
                rule_key=item["rule_key"],
                title=item["title"],
                rule_text=item["rule_text"],
                source_issue_type=item["source_issue_type"],
                source_run_id=run_id,
                source_document_path=document_path,
                status="active" if item["auto_active"] else "candidate",
                confidence=float(item["confidence"]),
                auto_merged=bool(item["auto_active"]),
            )
            if item["auto_active"]:
                active_rules.append(row)
            else:
                candidate_rules.append(row)

        event_id = db.create_workflow_learning_event(
            workflow_type=workflow_type,
            scope=scope,
            run_id=run_id,
            document_path=document_path,
            summary=(
                f"Extracted {len(candidates)} learning candidates; "
                f"{len(active_rules)} active, {len(candidate_rules)} pending."
            ),
            candidate_count=len(candidate_rules),
            active_count=len(active_rules),
            payload={"candidates": candidates, "findings": findings or []},
        )
        export_learning_sop(workflow_type=workflow_type, scope=scope, db=db)
        return {
            "enabled": True,
            "event_id": event_id,
            "findings": findings or [],
            "candidates": candidates,
            "active_rules": active_rules,
            "candidate_rules": candidate_rules,
            "summary": (
                f"Extracted {len(candidates)} learning candidates; "
                f"{len(active_rules)} active, {len(candidate_rules)} pending."
            ),
        }
    except Exception as exc:
        return {
            "enabled": True,
            "error": str(exc),
            "findings": findings or [],
            "candidates": candidates,
            "active_rules": [],
            "candidate_rules": [],
        }


def learning_candidates_from_findings(
    findings: list[dict[str, Any]],
    *,
    workflow_type: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in findings:
        issue_type = _coerce_issue_type(item)
        source = _coerce_text(item, "source_text", "source_value", "source")
        current = _coerce_text(item, "translation_text", "current_text", "current_value", "current")
        suggestion = _coerce_text(item, "suggestion", "expected", "recommended_fix")
        finding = _coerce_text(item, "finding", "title", "message", "summary")
        if not suggestion and not finding:
            continue

        category = LEARNING_CATEGORY_BY_ISSUE_TYPE.get(issue_type, "candidate_substantive")
        auto_active = issue_type in LOW_RISK_AUTO_ISSUE_TYPES and bool(suggestion or finding)
        title = _learning_title(workflow_type, issue_type, source, current, suggestion, finding)
        rule_text = _learning_rule_text(issue_type, source, current, suggestion, finding, auto_active)
        key_material = "|".join(
            [
                workflow_type,
                category,
                issue_type,
                _norm_for_key(current or source or finding),
                _norm_for_key(suggestion or finding),
            ]
        )
        candidates.append(
            {
                "auto_active": auto_active,
                "category": category,
                "confidence": 0.85 if auto_active else 0.45,
                "rule_key": hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:24],
                "rule_text": rule_text,
                "source_issue_type": issue_type,
                "title": title,
            }
        )
    return _dedupe_candidates(candidates)


def scorecard_findings(scorecard: dict[str, Any] | None, *, document_path: str | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not isinstance(scorecard, dict):
        return findings
    for failure in scorecard.get("failures") or []:
        if not isinstance(failure, dict):
            continue
        check = str(failure.get("check") or "scorecard_gate").strip() or "scorecard_gate"
        message = str(failure.get("message") or failure.get("detail") or check).strip()
        if not message:
            continue
        issue_type = {
            "delivery_artifacts": "delivery_artifact",
            "report_paths": "delivery_artifact",
            "verification_report": "evidence_ledger",
        }.get(check, "scorecard_gate")
        findings.append(
            {
                "document_path": document_path or "",
                "finding": message,
                "issue_type": issue_type,
                "location": str(failure.get("location") or "").strip(),
                "review_type": "delivery_gate",
                "severity": "major",
                "suggestion": str(failure.get("expected") or "").strip(),
                "title": f"Delivery gate: {check}",
            }
        )
    return findings


def project_init_findings(
    *,
    source: dict[str, Any] | None,
    digest: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    source = source or {}
    digest = digest or {}
    rel_path = str(digest.get("rel_path") or source.get("rel_path") or source.get("path") or "").strip()
    read_method = str(digest.get("read_method") or source.get("read_method") or "").strip()
    coverage = str(digest.get("read_coverage") or "").strip()
    findings: list[dict[str, Any]] = [
        {
            "issue_type": "evidence_ledger",
            "title": f"Project init evidence ledger: {rel_path or 'source file'}",
            "finding": "Project init source digests should always preserve read method, coverage, confidence, and a file evidence ledger.",
            "suggestion": "Record read method, read coverage, confidence, and a File Evidence Ledger before concluding on a source file.",
            "review_type": "project_init",
            "severity": "info",
            "location": rel_path,
        }
    ]
    if read_method:
        findings.append(
            {
                "issue_type": "source_reading",
                "title": f"Project init reading path: {rel_path or read_method}",
                "finding": f"Source {rel_path or 'file'} was processed via {read_method}. Reuse the best native read path for similar inputs.",
                "suggestion": f"For similar source files, prefer {read_method} and explicitly record any read limits.",
                "review_type": "project_init",
                "severity": "info",
                "location": rel_path,
                "current_value": read_method,
            }
        )
    if coverage:
        findings.append(
            {
                "issue_type": "read_coverage",
                "title": f"Project init coverage label: {rel_path or 'source file'}",
                "finding": f"Source digests should label coverage as {coverage}, not silently overclaim completeness.",
                "suggestion": "Always record read_coverage as full, partial, or failed, and explain any gap in the evidence ledger.",
                "review_type": "project_init",
                "severity": "info",
                "location": rel_path,
                "current_value": coverage,
            }
        )
    for raw in digest.get("open_questions") or []:
        question = str(raw).strip()
        if not question:
            continue
        findings.append(
            {
                "issue_type": "source_risk",
                "title": f"Project init open question: {rel_path or 'source file'}",
                "finding": question,
                "review_type": "project_init",
                "severity": "medium",
                "location": rel_path,
            }
        )
    for raw in digest.get("issues") or []:
        issue = str(raw).strip()
        if not issue:
            continue
        findings.append(
            {
                "issue_type": "source_risk",
                "title": f"Project init issue: {rel_path or 'source file'}",
                "finding": issue,
                "review_type": "project_init",
                "severity": "major",
                "location": rel_path,
            }
        )
    return findings


def export_learning_sop(*, workflow_type: str, scope: str, db) -> str | None:
    try:
        rules = db.list_workflow_learning_rules(
            workflow_type=workflow_type,
            scope=scope,
            limit=200,
        )
    except Exception:
        return None
    active = [item for item in rules if str(item.get("status")) == "active"]
    candidates = [item for item in rules if str(item.get("status")) == "candidate"]
    root = Path.home() / ".hermes" / "workflow-learning"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{workflow_type}.{scope}.md"
    path.write_text(
        learning_sop_markdown(workflow_type, scope, active, candidates),
        encoding="utf-8",
    )
    return str(path)


def learning_sop_markdown(
    workflow_type: str,
    scope: str,
    active_rules: list[dict[str, Any]],
    candidate_rules: list[dict[str, Any]],
) -> str:
    label = WORKFLOW_LABELS.get(workflow_type, workflow_type)
    lines = [
        f"# {label} Learning SOP",
        "",
        f"- Workflow type: `{workflow_type}`",
        f"- Scope: `{scope}`",
        "",
        "## Active reusable rules",
        "",
    ]
    if active_rules:
        for item in active_rules:
            lines.append(f"- {item.get('title')}: {item.get('rule_text')}")
    else:
        lines.append("- None yet.")
    lines.extend(["", "## Candidate rules pending approval", ""])
    if candidate_rules:
        for item in candidate_rules:
            lines.append(f"- {item.get('title')}: {item.get('rule_text')}")
    else:
        lines.append("- None pending.")
    lines.append("")
    return "\n".join(lines)


def _coerce_issue_type(item: dict[str, Any]) -> str:
    for key in ("issue_type", "review_type", "check"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return "candidate_substantive"


def _coerce_text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _learning_title(
    workflow_type: str,
    issue_type: str,
    source: str,
    current: str,
    suggestion: str,
    finding: str,
) -> str:
    label = {
        "cross_reference": "Cross-reference wording",
        "defined_term": "Defined term consistency",
        "delivery_artifact": "Delivery artifact checklist",
        "evidence_ledger": "Evidence ledger discipline",
        "party_name": "Party name consistency",
        "read_coverage": "Coverage disclosure discipline",
        "review_content": "Substantive review pattern",
        "review_format": "Style and format",
        "review_ts": "TS consistency pattern",
        "review_xref": "Cross-reference review pattern",
        "schedule_table": "Schedule/table wording",
        "scorecard_gate": "Delivery gate pattern",
        "source_reading": "Source reading pattern",
        "source_risk": "Project-init risk pattern",
        "style_format": "Style and format",
    }.get(issue_type, f"{WORKFLOW_LABELS.get(workflow_type, 'Workflow')} candidate")
    anchor = suggestion or current or source or finding
    return f"{label}: {_short(anchor, 80)}"


def _learning_rule_text(
    issue_type: str,
    source: str,
    current: str,
    suggestion: str,
    finding: str,
    auto_active: bool,
) -> str:
    prefix = "Reusable low-risk rule" if auto_active else "Candidate rule requiring human review"
    if current and suggestion:
        return f"{prefix}: when encountering `{current}`, prefer `{suggestion}`. Rationale: {finding}"
    if source and suggestion:
        return f"{prefix}: for source/input `{source}`, prefer `{suggestion}` where context matches. Rationale: {finding}"
    if suggestion:
        return f"{prefix}: {suggestion}. Rationale: {finding}"
    return f"{prefix}: {finding}"


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen = set()
    for item in candidates:
        key = item["rule_key"]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _norm_for_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()[:240]


def _short(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
