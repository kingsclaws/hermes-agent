"""
lex_proofread — Split-and-Parallel-Review Tool

Splits a .docx document into logical chunks at heading boundaries, then
delegates each chunk to a profile-aware reviewer sub-agent. Aggregates
all findings into a unified proofread report.

This guarantees every paragraph is read by exactly one reviewer, eliminating
the attention-degradation problem of single-agent full-document review.

NAFMII mode (nafmii_mode=True):
  - Pre-audit mechanical check (blanks, notes, annotations) before LLM dispatch
  - Clause-aware chunking at NAFMII standard clause boundaries
  - Enhanced table-review instructions for repayment schedules, party tables etc.
"""

from __future__ import annotations

import json
import re
from typing import Any

from tools.registry import registry, tool_error, tool_result


# ── NAFMII standard clause pattern ──────────────────────────────────────────── #

# NAFMII syndicated loan agreements follow a predictable clause order.
# Recognising these patterns lets us chunk at semantic boundaries rather than
# arbitrary heading levels.  Each entry is (priority, regex, clause_label).
# Higher priority = chunk at this boundary before generic H2 boundaries.
_NAFMII_CLAUSE_PATTERNS: list[tuple[int, str, str]] = [
    (10, r"第[一二三四五六七八九十百\d]+条\s*(定义|释义|解释)", "定义与解释"),
    (10, r"第[一二三四五六七八九十百\d]+条\s*(贷款|授信|融资)", "贷款/授信条款"),
    (9,  r"第[一二三四五六七八九十百\d]+条\s*(利率|利息|利率与计息)", "利率条款"),
    (9,  r"第[一二三四五六七八九十百\d]+条\s*(提款|放款|提款先决条件|先决条件)", "提款条款"),
    (9,  r"第[一二三四五六七八九十百\d]+条\s*(还款|偿还|还本)", "还款条款"),
    (9,  r"第[一二三四五六七八九十百\d]+条\s*(担保|保证|抵押|质押)", "担保条款"),
    (9,  r"第[一二三四五六七八九十百\d]+条\s*(违约|违约事件|违约责任)", "违约责任"),
    (8,  r"第[一二三四五六七八九十百\d]+条\s*(陈述|保证|陈述与保证)", "陈述与保证"),
    (8,  r"第[一二三四五六七八九十百\d]+条\s*(承诺|约定事项|财务承诺)", "承诺条款"),
    (8,  r"第[一二三四五六七八九十百\d]+条\s*(争议|管辖|法律适用|争议解决)", "争议解决"),
    (8,  r"第[一二三四五六七八九十百\d]+条\s*(费用|税费|杂费)", "费用条款"),
    (7,  r"第[一二三四五六七八九十百\d]+条\s*(转让|变更|债权转让)", "转让条款"),
    (7,  r"第[一二三四五六七八九十百\d]+条\s*(通知|送达)", "通知与送达"),
    (7,  r"第[一二三四五六七八九十百\d]+条\s*(生效|附则|其他|杂项)", "生效与附则"),
    (7,  r"第[一二三四五六七八九十百\d]+条\s*(账户|资金监管|账户监管)", "账户条款"),
    (6,  r"第[一二三四五六七八九十百\d]+条\s*(提前还款|提前偿还|提前)", "提前还款"),
    (6,  r"第[一二三四五六七八九十百\d]+条\s*(保险|投保)", "保险条款"),
    (6,  r"第[一二三四五六七八九十百\d]+条\s*(信息披露|信息|报告)", "信息披露"),
    (5,  r"第[一二三四五六七八九十百\d]+条\s*(银团|代理行|牵头行|安排行)", "银团条款"),
    (5,  r"第[一二三四五六七八九十百\d]+条\s*(循环|额度|授信额度)", "额度条款"),
    (4,  r"第[一二三四五六七八九十百\d]+条\s*(分红|利润分配|分配)", "分红限制"),
    (4,  r"第[一二三四五六七八九十百\d]+条\s*(用途|资金用途|贷款用途)", "贷款用途"),
]

# Paragraphs containing repayment schedule / fee schedule / party info tables
_TABLE_HEAVY_PATTERNS = [
    "还款计划", "还款安排", "还款时间表", "还本付息",
    "当事人", "借款人信息", "贷款人信息", "银团成员",
    "费用表", "费率表", "收费",
    "提款计划", "放款安排",
]


# ── Review type → Profile mapping ──────────────────────────────────────────── #

REVIEW_TYPE_PROFILES: dict[str, list[str]] = {
    "content":     ["lex-reviewer-content"],
    "format":      ["lex-reviewer-format"],
    "ts":          ["lex-reviewer-ts"],
    "xref":        ["lex-reviewer-xref"],
    "translation": ["lex-reviewer-translation"],
}

REVIEW_TYPE_LABEL: dict[str, str] = {
    "content":     "Content Review",
    "format":      "Format Review",
    "ts":          "Term Sheet Consistency",
    "xref":        "Cross-Reference Audit",
    "translation": "Translation Quality",
}


# ── Schema ─────────────────────────────────────────────────────────────────── #

LEX_PROOFREAD_SCHEMA = {
    "name": "lex_proofread",
    "description": (
        "Split a legal document into logical chunks at heading boundaries, "
        "then delegate each chunk to profile-aware reviewer sub-agents for "
        "parallel review. Aggregates all findings into a unified proofread "
        "report.\n\n"
        "Use this for ANY document over ~200 paragraphs. It guarantees that "
        "every paragraph is read by exactly one reviewer, preventing the "
        "attention-degradation problem of single-agent full-document review.\n\n"
        "review_type options:\n"
        "  content     — Legal substance, clause completeness, obligations\n"
        "  format      — Font, spacing, numbering, layout\n"
        "  ts          — Term sheet consistency cross-check\n"
        "  xref        — Cross-reference and defined-term audit "
        "(includes machine preflight: xref_audit + cross_doc_scan)\n"
        "  translation — Bilingual CN↔EN accuracy\n"
        "  all         — All five review types in parallel"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file to proofread.",
            },
            "review_type": {
                "type": "string",
                "enum": ["content", "format", "ts", "xref", "translation", "all"],
                "description": "Type of review. Default: content.",
            },
            "review_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["content", "format", "ts", "xref", "translation"],
                },
                "description": "Multiple review types to run. Overrides review_type when provided.",
            },
            "chunk_size": {
                "type": "integer",
                "description": "Max paragraphs per chunk. Default: 300.",
            },
            "nafmii_mode": {
                "type": "boolean",
                "description": (
                    "Enable NAFMII-aware optimizations: pre-audit mechanical check "
                    "(blanks/notes/annotations), clause-aware chunking at known "
                    "NAFMII clause boundaries, and enhanced table-review instructions."
                ),
            },
            "related_docs": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Additional .docx paths for cross-reference validation. "
                    "When review_type includes 'xref', these docs are scanned "
                    "for cross-document references (《DocName》第X条 patterns). "
                    "If omitted, auto-discovers .docx files in the project directory."
                ),
            },
        },
        "required": ["path"],
    },
}


# ── Handler ────────────────────────────────────────────────────────────────── #

def _handle_proofread(args: dict, **kwargs) -> str:
    """Handler registered with the tool registry. Receives parent_agent via
    the special-case path in invoke_tool()."""
    parent_agent = kwargs.get("parent_agent")
    return lex_proofread(
        path=args["path"],
        review_type=args.get("review_type", "content"),
        review_types=args.get("review_types"),
        chunk_size=args.get("chunk_size", 300),
        nafmii_mode=bool(args.get("nafmii_mode")),
        related_docs=args.get("related_docs"),
        parent_agent=parent_agent,
    )


# ── Core ───────────────────────────────────────────────────────────────────── #

def lex_proofread(
    path: str,
    review_type: str = "content",
    review_types: list[str] | None = None,
    chunk_size: int = 300,
    nafmii_mode: bool = False,
    related_docs: list[str] | None = None,
    parent_agent: Any = None,
) -> str:
    """Split document and parallel-review via delegate_task."""

    if parent_agent is None:
        return tool_error("lex_proofread requires parent agent context")

    # 1. Read structure + stats
    from lexitool.markup import lex_read

    structure = lex_read(path, mode="structure")
    stats = lex_read(path, mode="stats")

    # 2. Parse total paragraphs from stats
    total_match = re.search(r"Paragraphs:\s*(\d+)", stats)
    total_paras = int(total_match.group(1)) if total_match else 0
    if total_paras == 0:
        return tool_error("Document appears to be empty (0 paragraphs)")

    # 2.5 NAFMII pre-audit: mechanical checks before LLM dispatch
    pre_audit_report: dict[str, Any] = {}
    if nafmii_mode:
        pre_audit_report = _pre_audit_check(path)

    # 2.6 XRef preflight: machine scan before LLM dispatch
    # Runs xref_audit (single-doc) + cross_doc_scan (multi-doc) so reviewers
    # receive verified ground-truth data instead of hunting blind.
    requested_types = [str(v).strip() for v in (review_types or []) if str(v).strip()]
    xref_preflight: dict[str, Any] = {}
    xref_requested = (
        "xref" in requested_types
        or review_type in ("xref", "all")
        or (not requested_types and review_type == "xref")
    )
    if xref_requested:
        xref_preflight = _xref_preflight(
            primary_path=path,
            related_docs=related_docs,
        )

    # 3. Compute chunks (NAFMII clause-aware if enabled)
    chunks = _compute_chunks(
        structure, total_paras, max(chunk_size, 50),
        nafmii_mode=nafmii_mode,
    )

    if not chunks:
        return tool_error("Could not determine document structure for chunking")

    # 4. Resolve profiles
    if requested_types:
        profiles = []
        for item in requested_types:
            profiles.extend(REVIEW_TYPE_PROFILES.get(item, []))
        if not profiles:
            profiles = REVIEW_TYPE_PROFILES["content"]
        review_label = ",".join(requested_types)
    elif review_type == "all":
        profiles = []
        for plist in REVIEW_TYPE_PROFILES.values():
            profiles.extend(plist)
        review_label = "all"
    else:
        profiles = REVIEW_TYPE_PROFILES.get(review_type, ["lex-reviewer-content"])
        review_label = review_type

    # 5. Build batch tasks (NAFMII-enhanced review prompts if enabled)
    tasks: list[dict[str, Any]] = []
    for i, (start, end, label) in enumerate(chunks):
        for profile in profiles:
            rt = _profile_to_review_type(profile)
            rt_label = REVIEW_TYPE_LABEL.get(rt, rt)

            goal = _build_review_goal(
                path=path,
                start=start,
                end=end,
                label=label,
                review_type=rt,
                rt_label=rt_label,
                nafmii_mode=nafmii_mode,
                xref_preflight=xref_preflight if rt == "xref" else None,
            )

            tasks.append({
                "goal": goal,
                "profile": profile,
            })

    # 6. Delegate
    from tools.delegate_tool import delegate_task

    result_json = delegate_task(tasks=tasks, parent_agent=parent_agent)

    # 7. Aggregate
    return _aggregate_results(
        result_json, chunks, profiles, path, review_label,
        pre_audit_report=pre_audit_report if nafmii_mode else None,
        xref_preflight=xref_preflight if xref_requested else None,
    )


# ── Pre-audit (NAFMII mode) ────────────────────────────────────────────────── #


def _pre_audit_check(path: str) -> dict[str, Any]:
    """Run lex_template_audit before LLM dispatch.  Pure OOXML scan, zero LLM cost.

    Catches mechanical issues (residual blanks, notes, annotations) instantly
    so the coordinator can decide whether to fix-and-retry or proceed with review.
    """
    try:
        from .lex_template_tool import _handle_template_audit
        result_json = _handle_template_audit({"path": path})
        manifest = json.loads(result_json)
    except Exception as e:
        return {"error": str(e), "status": "audit_failed"}

    issues: list[str] = []

    blanks = manifest.get("blanks_count", 0)
    notes = manifest.get("colored_notes_count", 0)
    annotations = manifest.get("annotations_count", 0)
    guides = manifest.get("guide_paras_count", 0)

    if blanks > 0:
        issues.append(f"{blanks} residual fill-in blanks (yellow highlights) still present")
    if notes > 0:
        issues.append(f"{notes} colored notes still present — cleanup phase may be incomplete")
    if annotations > 0:
        issues.append(f"{annotations} bracketed annotations still present")
    if guides > 0:
        issues.append(f"{guides} usage-guide paragraphs still present")

    return {
        "blanks": blanks,
        "colored_notes": notes,
        "annotations": annotations,
        "guide_paras": guides,
        "issues": issues,
        "clean": len(issues) == 0,
        "status": "clean" if len(issues) == 0 else "mechanical_issues_found",
    }


# ── XRef preflight (machine scan before LLM dispatch) ───────────────────────── #


def _xref_preflight(
    primary_path: str,
    related_docs: list[str] | None = None,
) -> dict[str, Any]:
    """Run machine xref audit before dispatching LLM reviewers.

    Returns structured findings that are fed directly into each xref reviewer's
    context as ground truth. Reviewers verify the machine findings and add any
    missing issues, rather than hunting for references from scratch.

    Includes:
      - xref_audit: single-document dead-ref + unreferenced-clause detection
      - cross_doc_scan: multi-document 《DocName》第X条 validation
    """
    try:
        from lexitool import xref
    except Exception as e:
        return {"error": str(e), "status": "xref_unavailable"}

    # Single-document audit
    try:
        single_audit = xref.xref_audit(primary_path)
    except Exception as e:
        return {"error": f"xref_audit failed: {e}", "status": "audit_failed"}

    # Cross-document scan (if multiple docs available)
    cross_result = None
    all_docs = [primary_path]
    if related_docs:
        all_docs.extend(related_docs)

    if len(all_docs) >= 2:
        try:
            cross_result = xref.cross_doc_scan(all_docs)
        except Exception:
            cross_result = None

    # Build structured findings
    findings: list[dict[str, Any]] = []

    # Internal dead refs
    for entry in single_audit.get("dead_refs") or []:
        if isinstance(entry, dict):
            findings.append({
                "type": "dead_internal_ref",
                "para": entry.get("para"),
                "ref_text": entry.get("ref_text", ""),
                "clause_num": entry.get("clause_num", ""),
                "context": entry.get("context", ""),
                "severity": "major",
                "message": (
                    f"§{entry.get('para')}: Reference '{entry.get('ref_text')}' "
                    f"points to non-existent clause {entry.get('clause_num')}"
                ),
            })

    # Cross-document broken refs
    if isinstance(cross_result, dict):
        for entry in cross_result.get("broken_refs") or []:
            if isinstance(entry, dict):
                target_doc = entry.get("target_doc", "")
                clause = entry.get("clause", "")
                findings.append({
                    "type": "broken_cross_ref",
                    "source_para": entry.get("source_para"),
                    "target_doc": target_doc,
                    "clause": clause,
                    "match": entry.get("match", ""),
                    "reason": entry.get("reason", ""),
                    "severity": "major",
                    "message": (
                        f"§{entry.get('source_para')}: Cross-reference "
                        f"'《{target_doc}》第{clause}条' — {entry.get('reason', 'broken')}"
                    ),
                })

    # Unreferenced clauses (info-level — clauses that exist but are never cited)
    for cn in single_audit.get("unreferenced_clauses") or []:
        findings.append({
            "type": "unreferenced_clause",
            "clause_num": cn,
            "severity": "info",
            "message": f"Clause {cn} exists but is never referenced by any other clause",
        })

    cross_summary = cross_result.get("summary", {}) if isinstance(cross_result, dict) else {}

    return {
        "status": "complete",
        "document_path": primary_path,
        "docs_scanned": len(all_docs),
        "single_doc_audit": single_audit,
        "cross_doc_scan": cross_result,
        "findings": findings,
        "summary": (
            f"{single_audit.get('total_references', 0)} internal refs: "
            f"{len(single_audit.get('valid_refs', []))} valid, "
            f"{len(single_audit.get('dead_refs', []))} dead, "
            f"{len(single_audit.get('unreferenced_clauses', []))} unreferenced. "
            f"Cross-doc: {cross_summary.get('total', 0)} refs, "
            f"{cross_summary.get('broken', 0)} broken."
        ),
    }


# ── NAFMII clause-aware chunking ───────────────────────────────────────────── #


def _nafmii_clause_score(heading_text: str) -> tuple[int, str]:
    """Return (priority, clause_label) if the heading matches a NAFMII clause."""
    for priority, pattern, label in _NAFMII_CLAUSE_PATTERNS:
        if re.search(pattern, heading_text):
            return (priority, label)
    return (0, "")


def _chunk_has_tables(section_text: str) -> bool:
    """Check if a section label or content mentions table-heavy clauses."""
    for pattern in _TABLE_HEAVY_PATTERNS:
        if pattern in section_text:
            return True
    return False


# ── Review goal builder ─────────────────────────────────────────────────────── #


def _build_review_goal(
    path: str,
    start: int,
    end: int,
    label: str,
    review_type: str,
    rt_label: str,
    nafmii_mode: bool = False,
    xref_preflight: dict[str, Any] | None = None,
) -> str:
    """Build a review goal, optionally enhanced for NAFMII chunks and xref preflight."""
    base = (
        f"Proofread {path} paragraphs §{start}-§{end} ({label}). "
        f"Review type: {rt_label}. "
        f"Read your section with lex_read(path='{path}', paras=[{start},{end}], show_format=true). "
        f"Report EVERY issue found — legal errors, inconsistencies, "
        f"formatting problems, missing clauses, incorrect references, "
        f"typos. Be thorough and precise. Structure your response as "
        f"a numbered list of findings."
    )

    extras: list[str] = []

    # XRef preflight: feed machine findings as ground truth
    if xref_preflight and xref_preflight.get("status") == "complete":
        findings = xref_preflight.get("findings", [])
        dead_refs = [f for f in findings if f.get("type") == "dead_internal_ref"]
        broken_xref = [f for f in findings if f.get("type") == "broken_cross_ref"]
        unreferenced = [f for f in findings if f.get("type") == "unreferenced_clause"]

        preflight_lines = [
            "MACHINE XREF PREFLIGHT — verified ground truth. Use these as a baseline.",
            "You MUST verify each finding and add any additional issues you discover.",
            "",
        ]
        if dead_refs:
            preflight_lines.append(f"DEAD INTERNAL REFS ({len(dead_refs)}):")
            for f in dead_refs[:50]:
                preflight_lines.append(f"  §{f.get('para')}: {f.get('ref_text')} → clause {f.get('clause_num')} NOT FOUND")
            if len(dead_refs) > 50:
                preflight_lines.append(f"  ... and {len(dead_refs) - 50} more")

        if broken_xref:
            preflight_lines.append(f"\nBROKEN CROSS-DOC REFS ({len(broken_xref)}):")
            for f in broken_xref[:30]:
                preflight_lines.append(f"  §{f.get('source_para')}: 《{f.get('target_doc')}》第{f.get('clause')}条 — {f.get('reason', 'broken')}")
            if len(broken_xref) > 30:
                preflight_lines.append(f"  ... and {len(broken_xref) - 30} more")

        if unreferenced:
            preflight_lines.append(f"\nUNREFERENCED CLAUSES ({len(unreferenced)}):")
            preflight_lines.append(f"  Clauses that exist but are never cited: {', '.join(str(f.get('clause_num', '')) for f in unreferenced[:20])}")
            if len(unreferenced) > 20:
                preflight_lines.append(f"  ... and {len(unreferenced) - 20} more")

        preflight_lines.append(f"\nSUMMARY: {xref_preflight.get('summary', '')}")
        extras.append("\n".join(preflight_lines))

    if nafmii_mode:
        if review_type == "content":
            extras.append(
                "NAFMII-specific checks for this section:\n"
                "- Are all defined terms used consistently?\n"
                "- Are checkbox-selected options reflected in clause text (not just ☑)?\n"
                "- Are unselected option clauses fully removed?\n"
                "- Does clause numbering follow NAFMII convention (第X条)?\n"
                "- Are cross-references to other clauses accurate?"
            )

        if review_type == "format":
            extras.append(
                "NAFMII format checks:\n"
                "- Clause headings: 黑体, 14pt, bold, centered\n"
                "- Body text: 宋体, 11.5pt, justified, 1.5 line spacing\n"
                "- Numbering: 第X条 (level 1) → X.X (level 2) → (X) (level 3)\n"
                "- Check that numbering is continuous across this section"
            )

        if review_type == "xref":
            extras.append(
                "NAFMII cross-reference checks for this section:\n"
                "- Are all 第X条 references within the standard NAFMII clause numbering scheme?\n"
                "- Are 附件 (schedule/appendix) references accurate and do the attachments exist?\n"
                "- Are defined terms in 第1条 consistently used throughout this section?\n"
                "- Are table cross-references (e.g., '详见下表') pointing to real tables?\n"
                "- Check for residual 说明版 cross-refs that should have been resolved during drafting"
            )

        if _chunk_has_tables(label):
            extras.append(
                "TABLE INTENSIVE SECTION — tables are critical here:\n"
                "- Verify every table has consistent column count across all rows\n"
                "- Check merged cells: vertically-merged cells should display the correct "
                "carry-forward value, not orphan text from adjacent rows\n"
                "- Verify numeric totals (repayment amounts, fee totals) are self-consistent\n"
                "- Check table header row is complete and matches column content"
            )

    if extras:
        return base + "\n\n" + "\n\n".join(extras)
    return base


# ── Chunk computation ──────────────────────────────────────────────────────── #

def _compute_chunks(
    structure_text: str,
    total_paras: int,
    max_size: int,
    nafmii_mode: bool = False,
) -> list[tuple[int, int, str]]:
    """Parse heading structure and split document into logical chunks.

    Args:
        structure_text: Output of lex_read(mode="structure")
        total_paras: Total paragraph count from stats
        max_size: Maximum paragraphs per chunk

    Returns:
        List of (start_para, end_para, section_label) tuples
    """
    # Parse "§N [H?] text" or "§N [TOC?] text" lines
    # TOC headings are flattened to one level below their number so they
    # sort correctly alongside H1/H2 headings.
    headings: list[tuple[int, int, str]] = []  # (para, level, text)
    for line in structure_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Match [H1], [H2], ... — standard heading styles
        m = re.match(r"§(\d+)\s+\[H(\d+)\]\s+(.*)", line)
        if m:
            para = int(m.group(1))
            level = int(m.group(2))
            text = m.group(3).strip()[:80]
            headings.append((para, level, text))
            continue
        # Match [TOC1], [TOC2], ... — TOC field-based headings
        m = re.match(r"§(\d+)\s+\[TOC(\d+)\]\s+(.*)", line)
        if m:
            para = int(m.group(1))
            level = int(m.group(2)) + 1  # TOC1 → level 2 (like H2)
            text = m.group(3).strip()[:80]
            headings.append((para, level, text))
            continue
        # Match [?] — unclassifiable heading (treat as H2)
        m = re.match(r"§(\d+)\s+\[\?\]\s+(.*)", line)
        if m:
            para = int(m.group(1))
            text = m.group(2).strip()[:80]
            headings.append((para, 2, text))

    if not headings:
        # No headings — create fixed-size chunks
        chunks = []
        for start in range(1, total_paras + 1, max_size):
            end = min(start + max_size - 1, total_paras)
            chunks.append((start, end, f"§{start}-§{end}"))
        return chunks

    # Build sections: each heading spans from its paragraph to the next heading - 1
    sections: list[tuple[int, int, int, str]] = []  # (start, end, level, label)
    for i, (para, level, text) in enumerate(headings):
        if i + 1 < len(headings):
            end = headings[i + 1][0] - 1
        else:
            end = total_paras
        if end >= para:
            sections.append((para, end, level, text))

    if not sections:
        return []

    # Pre-heading content: paragraphs before the first heading
    if sections[0][0] > 1:
        sections.insert(0, (1, sections[0][0] - 1, 0, "Preamble"))

    # Annotate sections with NAFMII clause scores (only in nafmii_mode)
    section_clause_scores: list[int] = []
    section_clause_labels: list[str] = []
    if nafmii_mode:
        for _start, _end, _level, _text in sections:
            priority, clause_label = _nafmii_clause_score(_text)
            section_clause_scores.append(priority)
            section_clause_labels.append(clause_label if clause_label else _text)
        # Reduce max_size for NAFMII — clauses are typically 50-150 paras
        effective_max = min(max_size, 200)
    else:
        section_clause_scores = [0] * len(sections)
        section_clause_labels = [text for _, _, _, text in sections]
        effective_max = max_size

    # Group sections into chunks respecting max_size.
    # In NAFMII mode, prefer to start new chunks at high-priority clause
    # boundaries rather than merging them into the previous chunk.
    chunks: list[tuple[int, int, str]] = []
    i = 0
    while i < len(sections):
        sec_start, sec_end, sec_level, sec_text = sections[i]

        # If this single section exceeds max_size, split it internally
        if sec_end - sec_start + 1 > effective_max:
            pos = sec_start
            part = 1
            while pos <= sec_end:
                sub_end = min(pos + effective_max - 1, sec_end)
                chunks.append((pos, sub_end, f"{sec_text} ({part})"))
                pos = sub_end + 1
                part += 1
            i += 1
            continue

        # Try to merge consecutive sections into one chunk
        chunk_start = sec_start
        chunk_end = sec_end
        # Use NAFMII clause label if available, otherwise heading text
        chunk_label = section_clause_labels[i] if nafmii_mode and section_clause_scores[i] > 0 else sec_text
        i += 1

        while i < len(sections):
            n_start, n_end, n_level, n_text = sections[i]
            if n_end - chunk_start + 1 > effective_max:
                break
            # NAFMII mode: don't merge across a high-priority clause boundary
            if nafmii_mode and section_clause_scores[i] >= 8:
                break
            chunk_end = n_end
            # Update label to the clause label if it's higher priority
            if nafmii_mode and section_clause_scores[i] > 0 and section_clause_scores[i] < 8:
                if not any(label in chunk_label for label in ["定义", "贷款", "利率", "还款", "担保", "违约", "提款"]):
                    chunk_label = section_clause_labels[i]
            i += 1

        chunks.append((chunk_start, chunk_end, chunk_label))

    # Post-process: merge tiny tail chunks (< 20 paras) into the previous chunk
    MIN_CHUNK = 20
    merged: list[tuple[int, int, str]] = []
    for i, (start, end, label) in enumerate(chunks):
        size = end - start + 1
        if size < MIN_CHUNK and merged:
            # Absorb into previous chunk (may exceed max_size slightly)
            prev_start, prev_end, prev_label = merged.pop()
            merged.append((prev_start, end, prev_label))
        else:
            merged.append((start, end, label))

    return merged


# ── Result aggregation ─────────────────────────────────────────────────────── #

def _aggregate_results(
    result_json: str,
    chunks: list[tuple[int, int, str]],
    profiles: list[str],
    path: str,
    review_type: str,
    pre_audit_report: dict[str, Any] | None = None,
    xref_preflight: dict[str, Any] | None = None,
) -> str:
    """Combine child findings into a unified proofread report."""
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        return tool_error("Failed to parse delegation results")

    results = data.get("results", [])
    total_duration = data.get("total_duration_seconds", 0)

    n_profiles = len(profiles)
    n_chunks = len(chunks)

    lines = [
        f"# Proofread Report: {path}",
        f"",
        f"**Review type:** {review_type}",
        f"**Profiles:** {', '.join(profiles)}",
        f"**Chunks:** {n_chunks} ({n_chunks * n_profiles} total review tasks)",
        f"**Duration:** {total_duration:.0f}s",
        f"",
    ]

    # Pre-audit section (NAFMII mode)
    if pre_audit_report:
        lines.append("## Pre-Audit (Mechanical Check)")
        lines.append("")
        if pre_audit_report.get("clean"):
            lines.append("No residual mechanical issues found. Clean.")
        else:
            lines.append("**Mechanical issues found (resolve before or during review):**")
            for issue in pre_audit_report.get("issues", []):
                lines.append(f"- {issue}")
            lines.append("")
            lines.append("| Check | Count |")
            lines.append("|-------|-------|")
            lines.append(f"| Blanks | {pre_audit_report.get('blanks', '?')} |")
            lines.append(f"| Colored Notes | {pre_audit_report.get('colored_notes', '?')} |")
            lines.append(f"| Annotations | {pre_audit_report.get('annotations', '?')} |")
            lines.append(f"| Guide Paras | {pre_audit_report.get('guide_paras', '?')} |")
        lines.append("")

    # XRef preflight section
    if xref_preflight and xref_preflight.get("status") == "complete":
        lines.append("## XRef Preflight (Machine Audit)")
        lines.append("")
        findings = xref_preflight.get("findings", [])
        dead_refs = [f for f in findings if f.get("type") == "dead_internal_ref"]
        broken_xref = [f for f in findings if f.get("type") == "broken_cross_ref"]
        unreferenced = [f for f in findings if f.get("type") == "unreferenced_clause"]

        if not dead_refs and not broken_xref and not unreferenced:
            lines.append("All internal and cross-document references validated. No issues found.")
        else:
            lines.append(f"**{len(dead_refs)} dead internal refs, {len(broken_xref)} broken cross-doc refs, {len(unreferenced)} unreferenced clauses**")
            lines.append("")
            if dead_refs:
                lines.append("### Dead Internal References (Machine-Detected)")
                lines.append("")
                lines.append("| Para | Reference | Target Clause | Context |")
                lines.append("|------|-----------|---------------|---------|")
                for f in dead_refs[:30]:
                    para = f.get("para", "?")
                    ref = f.get("ref_text", "")
                    cn = f.get("clause_num", "")
                    ctx = str(f.get("context", ""))[:60]
                    lines.append(f"| §{para} | {ref} | {cn} | {ctx} |")
                lines.append("")
            if broken_xref:
                lines.append("### Broken Cross-Document References (Machine-Detected)")
                lines.append("")
                lines.append("| Source Para | Target Doc | Clause | Reason |")
                lines.append("|-------------|------------|--------|--------|")
                for f in broken_xref[:30]:
                    para = f.get("source_para", "?")
                    doc = f.get("target_doc", "")
                    clause = f.get("clause", "")
                    reason = str(f.get("reason", ""))[:80]
                    lines.append(f"| §{para} | {doc} | {clause} | {reason} |")
                lines.append("")
            if unreferenced:
                lines.append(f"**Unreferenced Clauses:** {', '.join(str(f.get('clause_num', '')) for f in unreferenced[:20])}")
                lines.append("")
        lines.append(f"**Summary:** {xref_preflight.get('summary', '')}")
        lines.append("")

    # Map task_index → (chunk_idx, profile)
    completed = 0
    failed = 0

    for chunk_idx, (start, end, label) in enumerate(chunks):
        section_label = f"§{start}-§{end} {label}"
        lines.append(f"## {section_label}")
        lines.append("")

        for prof_idx, profile in enumerate(profiles):
            task_index = chunk_idx * n_profiles + prof_idx
            rt = _profile_to_review_type(profile)
            rt_label = REVIEW_TYPE_LABEL.get(rt, rt)

            if task_index < len(results):
                r = results[task_index]
                status = r.get("status", "unknown")
                summary = r.get("summary", "(no findings)")

                if status == "completed":
                    completed += 1
                    lines.append(f"### {rt_label} ({profile})")
                    lines.append("")
                    lines.append(summary)
                    lines.append("")
                elif status == "failed":
                    failed += 1
                    error_msg = r.get("error", "Unknown error")
                    lines.append(f"### {rt_label} ({profile}) — FAILED")
                    lines.append(f"Error: {error_msg}")
                    lines.append("")
                else:
                    failed += 1
                    lines.append(f"### {rt_label} ({profile}) — {status.upper()}")
                    lines.append("")
            else:
                failed += 1
                lines.append(f"### {rt_label} ({profile}) — MISSING")
                lines.append("")

    # Summary footer
    lines.append("---")
    lines.append(f"**Completed:** {completed}/{n_chunks * n_profiles}")
    if failed:
        lines.append(f"**Failed/Incomplete:** {failed}")

    return "\n".join(lines)


def _profile_to_review_type(profile: str) -> str:
    """Map a profile name back to its review type."""
    for rt, plist in REVIEW_TYPE_PROFILES.items():
        if profile in plist:
            return rt
    return "content"


# ── Registration ───────────────────────────────────────────────────────────── #

def _check_lexitool() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("lexitool") is not None
    except Exception:
        return False


registry.register(
    name="lex_proofread",
    toolset="lexitool",
    schema=LEX_PROOFREAD_SCHEMA,
    handler=_handle_proofread,
    check_fn=_check_lexitool,
    emoji="🔍",
)
