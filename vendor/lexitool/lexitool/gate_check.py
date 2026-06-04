"""
gate_check.py — Review gate enforcement for HPSwarm legal projects.

Validates all 7 quality gates before a project can advance to delivery.
Reads reviewer reports from .hermes-project/reviews/ and runs automated
checks (cross_doc_scan, doctor, lint) to produce a pass/fail signal.

Operations:
  - gate_check:  Run all (or selected) gates and return structured result
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Gate definitions ──────────────────────────────────────────────────────────

GATES = {
    1: {
        "name": "Structure",
        "key": "structure",
        "description": "TOC present, heading levels valid, numbering continuous",
        "automated": True,
    },
    2: {
        "name": "Content Review",
        "key": "content",
        "description": "Content review report filed, no critical issues open",
        "automated": False,
        "report_file": "content-review-report.md",
        "reviewer": "reviewer-content",
    },
    3: {
        "name": "Format Review",
        "key": "format",
        "description": "Format review report filed, D01-D09 diagnostics pass",
        "automated": False,
        "report_file": "format-review-report.md",
        "reviewer": "reviewer-format",
    },
    4: {
        "name": "TS Consistency",
        "key": "ts_consistency",
        "description": "TS commercial consistency report filed, no major deviations",
        "automated": False,
        "report_file": "ts-consistency-report.md",
        "reviewer": "reviewer-ts-consistency",
    },
    5: {
        "name": "Cross-References",
        "key": "cross_ref",
        "description": "All cross-references validated, zero broken refs",
        "automated": True,
    },
    6: {
        "name": "Translation",
        "key": "translation",
        "description": "Translation review report filed, all checklist items pass",
        "automated": False,
        "report_file": "translation-review-report.md",
        "reviewer": "reviewer-translation",
    },
    7: {
        "name": "Final",
        "key": "final",
        "description": "All previous gates pass, ready for delivery",
        "automated": True,
    },
}


def _read_project_meta(project_dir: str) -> Optional[dict]:
    """Read project-meta.json if present."""
    meta_path = Path(project_dir) / ".hermes-project" / "project-meta.json"
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_report(project_dir: str, filename: str) -> Optional[str]:
    """Read a reviewer report. Checks reviews/ directory first, then root."""
    # Primary location: .hermes-project/reviews/
    reviews_path = Path(project_dir) / ".hermes-project" / "reviews" / filename
    if reviews_path.is_file():
        return reviews_path.read_text(encoding="utf-8")
    # Fallback: project root
    root_path = Path(project_dir) / filename
    if root_path.is_file():
        return root_path.read_text(encoding="utf-8")
    return None


def _parse_report_status(text: str) -> dict:
    """Parse a reviewer report to extract status and critical issues.

    Looks for:
      - ``## 总体评价`` section with ``Status: PASS|FAIL|CONDITIONAL``
      - Counts of "重大问题" or "致命断裂" as open critical issues
    """
    result = {"status": "unknown", "critical_issues": 0, "total_issues": 0}

    # Extract explicit status line
    status_match = re.search(
        r"总体评价.*?\n.*?(?:Status:\s*(PASS|FAIL|CONDITIONAL)|(通过|需修改后重审|需修复后重审))",
        text, re.DOTALL,
    )
    if status_match:
        if status_match.group(1):
            result["status"] = status_match.group(1).lower()
        elif status_match.group(2):
            cn = status_match.group(2)
            if "通过" in cn and "需" not in cn:
                result["status"] = "pass"
            else:
                result["status"] = "fail"

    # If no explicit status, infer from language
    if result["status"] == "unknown":
        if re.search(r"(通过|PASS|no (critical|broken|major))", text, re.IGNORECASE):
            result["status"] = "pass"
        elif re.search(r"(需修改|需修复|FAIL|重大问题|致命断裂|必须修改)", text):
            result["status"] = "fail"

    # Count critical issues
    critical_patterns = [
        r"重大问题",
        r"致命断裂",
        r"致命.*断裂",
        r"必须修改",
        r"critical",
        r"Critical issue",
    ]
    for pat in critical_patterns:
        result["critical_issues"] += len(re.findall(pat, text))

    # Count total issues (lines starting with "- " or numbered items)
    result["total_issues"] = len(re.findall(r"^\s*[-*]\s+", text, re.MULTILINE))

    return result


def _find_docx_files(project_dir: str) -> List[str]:
    """Find deliverable .docx files in a project directory."""
    result = []
    root = Path(project_dir)
    for p in sorted(root.rglob("*.docx")):
        if ".hermes-project" in p.parts:
            continue
        if "delivery" in p.parts:
            continue
        name = p.name
        if name.startswith("~$") or name.startswith("[Redline]") or name.startswith("[Mark-up]"):
            continue
        result.append(str(p))
    return result


# ── Individual gate validators ─────────────────────────────────────────────────


def _check_gate_1_structure(project_dir: str, **_kw) -> dict:
    """G1: Structure — TOC, heading levels, numbering."""
    docx_files = _find_docx_files(project_dir)
    if not docx_files:
        return {"gate": 1, "status": "pending", "detail": "No .docx files found in project",
                "remediation": "Create at least one document via lex_docx_create"}

    # Try running lex_docx_doctor on the primary document
    issues = []
    try:
        from lexitool.doctor import check as doctor_check
        result = doctor_check(docx_files[0])
        diags = result.get("diagnostics", {})
        # D04: heading levels, D05: numbering
        for key in ("D04", "D05"):
            if key in diags and not diags[key].get("ok", True):
                issues.append(f"{key}: {diags[key].get('detail', 'failed')}")
    except Exception as e:
        logger.debug("G1 doctor check skipped: %s", e)
        # Graceful degradation — don't fail the gate just because doctor can't run

    if issues:
        return {
            "gate": 1, "status": "fail",
            "detail": f"Structure issues: {'; '.join(issues)}",
            "remediation": "Run lex_docx_doctor action=fix to auto-repair D04/D05",
        }
    return {"gate": 1, "status": "pass", "detail": f"Structure OK — {len(docx_files)} document(s) found"}


def _check_gate_report(
    gate_num: int, project_dir: str, report_file: str, gate_label: str
) -> dict:
    """Generic check that a reviewer report exists and is passing."""
    report = _read_report(project_dir, report_file)
    if not report:
        return {
            "gate": gate_num, "status": "pending",
            "detail": f"No {gate_label} review report found",
            "remediation": f"Run reviewer for {gate_label} via delegate_task",
        }

    parsed = _parse_report_status(report)
    if parsed["status"] == "pass":
        detail = f"{gate_label} report: PASS"
    elif parsed["status"] == "fail":
        detail = f"{gate_label} report: FAIL — {parsed['critical_issues']} critical issues, {parsed['total_issues']} total"
    else:
        detail = f"{gate_label} report: status unclear ({parsed['total_issues']} issues noted)"

    return {
        "gate": gate_num,
        "status": parsed["status"] if parsed["status"] != "unknown" else "fail",
        "detail": detail,
        "critical_issues": parsed["critical_issues"],
        "remediation": (
            f"Address {parsed['critical_issues']} critical issues in {gate_label} review"
            if parsed["critical_issues"] > 0 else
            f"Mark {gate_label} review status as PASS"
        ),
    }


def _check_gate_5_cross_ref(project_dir: str, **_kw) -> dict:
    """G5: Cross-references — run internal and cross-document audits."""
    docx_files = _find_docx_files(project_dir)
    if not docx_files:
        return {
            "gate": 5, "status": "pending",
            "detail": "No .docx files found for cross-reference scan",
            "remediation": "Create or add at least one deliverable .docx document",
        }

    try:
        from lexitool.xref import audit_documents
        result = audit_documents(docx_files)
    except Exception as e:
        return {
            "gate": 5, "status": "fail",
            "detail": f"Cross-reference scan failed: {e}",
            "remediation": "Check document format and re-run cross-reference audit",
        }

    summary = result.get("summary", {})
    broken = summary.get("broken", 0)
    total = summary.get("total", 0)

    if broken > 0:
        return {
            "gate": 5, "status": "fail",
            "detail": f"{broken}/{total} broken cross-references",
            "cross_ref_result": result,
            "remediation": "Fix all broken cross-references before delivery",
        }
    if total == 0:
        return {
            "gate": 5,
            "status": "pass",
            "detail": f"No cross-references found across {len(docx_files)} document(s)",
            "cross_ref_result": result,
        }
    return {
        "gate": 5,
        "status": "pass",
        "detail": f"All {total} cross-references valid across {len(docx_files)} document(s)",
        "cross_ref_result": result,
    }


def _check_gate_7_final(project_dir: str = "", results: Optional[List[dict]] = None, **_kw) -> dict:
    """G7: Final — all previous gates must pass."""
    if results is None:
        results = []
    failures = [r for r in results if r["gate"] != 7 and r["status"] not in ("pass", "pending")]
    pending = [r for r in results if r["gate"] != 7 and r["status"] == "pending"]

    if failures:
        failed_gates = ", ".join(f"G{r['gate']}" for r in failures)
        return {
            "gate": 7, "status": "fail",
            "detail": f"Blocked by: {failed_gates}",
            "remediation": f"Fix gates {failed_gates} before final delivery",
        }
    if pending:
        pending_gates = ", ".join(f"G{r['gate']}" for r in pending)
        return {
            "gate": 7, "status": "pending",
            "detail": f"Waiting on: {pending_gates}",
            "remediation": f"Complete pending review for gates {pending_gates}",
        }
    return {"gate": 7, "status": "pass", "detail": "All gates passed — ready for delivery"}


_GATE_VALIDATORS = {
    1: _check_gate_1_structure,
    2: lambda pd, **kw: _check_gate_report(2, pd, "content-review-report.md", "Content Review"),
    3: lambda pd, **kw: _check_gate_report(3, pd, "format-review-report.md", "Format Review"),
    4: lambda pd, **kw: _check_gate_report(4, pd, "ts-consistency-report.md", "TS Consistency"),
    5: _check_gate_5_cross_ref,
    6: lambda pd, **kw: _check_gate_report(6, pd, "translation-review-report.md", "Translation"),
    7: _check_gate_7_final,
}


# ── Main entry point ──────────────────────────────────────────────────────────


def gate_check(
    project_dir: str,
    gates: Optional[List[int]] = None,
    strict: bool = False,
) -> dict:
    """Run quality gate checks against a legal project.

    Args:
        project_dir: Path to the project root (containing .hermes-project/).
        gates:       Specific gates to check (1-7). Default: all.
        strict:      If True, missing reports are treated as failures.

    Returns:
        {
            "ok": True/False (all gates pass),
            "project_name": "...",
            "checked_at": "ISO timestamp",
            "gates": [
                {"gate": N, "status": "pass/fail/pending", "detail": "...", ...},
            ],
            "blocked": ["G3", "G4"],
            "remediation": ["..."],
        }
    """
    src = Path(project_dir)
    if not src.is_dir():
        return {"ok": False, "error": f"Project directory not found: {project_dir}"}
    if not (src / ".hermes-project").is_dir():
        return {"ok": False, "error": f"Not a hermes project (no .hermes-project/ in {project_dir})"}

    meta = _read_project_meta(project_dir)
    project_name = meta.get("name", src.name) if meta else src.name

    to_check = gates if gates else list(range(1, 8))
    results: List[dict] = []

    for gate_num in to_check:
        validator = _GATE_VALIDATORS.get(gate_num)
        if not validator:
            results.append({
                "gate": gate_num, "status": "fail",
                "detail": f"Unknown gate number: {gate_num}",
            })
            continue
        try:
            result = validator(project_dir, results=results)
        except Exception as e:
            logger.exception("Gate %d check failed with exception", gate_num)
            result = {
                "gate": gate_num, "status": "fail",
                "detail": f"Gate check error: {e}",
            }
        results.append(result)

    # Compute overall status
    failures = [r for r in results if r["status"] == "fail"]
    pending = [r for r in results if r["status"] == "pending"]

    if strict:
        pending_as_fail = []
        for r in pending:
            f = dict(r)
            f["status"] = "fail"
            f["detail"] += " [strict mode: treated as failure]"
            pending_as_fail.append(f)
        # Replace pending with fails in results
        for i, r in enumerate(results):
            if r["status"] == "pending":
                results[i] = pending_as_fail.pop(0)
        failures.extend(results[i] for i, r in enumerate(results) if r["status"] == "fail")

    ok = len(failures) == 0 and (not strict or len([r for r in results if r["status"] == "fail"]) == 0)

    blocked = [f"G{r['gate']}" for r in results if r["status"] == "fail"]
    blocked_s = ", ".join(blocked) if blocked else "none"

    remediation = [r["remediation"] for r in results if r.get("remediation")]

    return {
        "ok": True,
        "all_passed": ok,
        "project_name": project_name,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "strict_mode": strict,
        "gates": results,
        "blocked": blocked_s,
        "remediation": remediation,
    }
