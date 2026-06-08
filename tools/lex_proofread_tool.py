"""
lex_proofread — Split-and-Parallel-Review Tool

Splits a .docx document into logical chunks at heading boundaries, then
delegates each chunk to a profile-aware reviewer sub-agent. Aggregates
all findings into a unified proofread report.

This guarantees every paragraph is read by exactly one reviewer, eliminating
the attention-degradation problem of single-agent full-document review.
"""
from __future__ import annotations

import json
import re
from typing import Any

from tools.registry import registry, tool_error, tool_result


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
        "  xref        — Cross-reference and defined-term audit\n"
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
        parent_agent=parent_agent,
    )


# ── Core ───────────────────────────────────────────────────────────────────── #

def lex_proofread(
    path: str,
    review_type: str = "content",
    review_types: list[str] | None = None,
    chunk_size: int = 300,
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

    # 3. Compute chunks
    chunks = _compute_chunks(structure, total_paras, max(chunk_size, 50))

    if not chunks:
        return tool_error("Could not determine document structure for chunking")

    # 4. Resolve profiles
    requested_types = [str(v).strip() for v in (review_types or []) if str(v).strip()]
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

    # 5. Build batch tasks
    tasks: list[dict[str, Any]] = []
    for i, (start, end, label) in enumerate(chunks):
        for profile in profiles:
            # Determine which review type this profile belongs to
            rt = _profile_to_review_type(profile)
            rt_label = REVIEW_TYPE_LABEL.get(rt, rt)
            tasks.append({
                "goal": (
                    f"Proofread {path} paragraphs §{start}-§{end} ({label}). "
                    f"Review type: {rt_label}. "
                    f"Read your section with lex_read(path='{path}', paras=[{start},{end}]). "
                    f"Report EVERY issue found — legal errors, inconsistencies, "
                    f"formatting problems, missing clauses, incorrect references, "
                    f"typos. Be thorough and precise. Structure your response as "
                    f"a numbered list of findings."
                ),
                "profile": profile,
            })

    # 6. Delegate
    from tools.delegate_tool import delegate_task

    result_json = delegate_task(tasks=tasks, parent_agent=parent_agent)

    # 7. Aggregate
    return _aggregate_results(result_json, chunks, profiles, path, review_label)


# ── Chunk computation ──────────────────────────────────────────────────────── #

def _compute_chunks(
    structure_text: str,
    total_paras: int,
    max_size: int,
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

    # Group sections into chunks respecting max_size.
    # Sections smaller than max_size are merged together; a section larger
    # than max_size is force-split at fixed boundaries.
    chunks: list[tuple[int, int, str]] = []
    i = 0
    while i < len(sections):
        sec_start, sec_end, sec_level, sec_text = sections[i]

        # If this single section exceeds max_size, split it internally
        if sec_end - sec_start + 1 > max_size:
            pos = sec_start
            part = 1
            while pos <= sec_end:
                sub_end = min(pos + max_size - 1, sec_end)
                chunks.append((pos, sub_end, f"{sec_text} ({part})"))
                pos = sub_end + 1
                part += 1
            i += 1
            continue

        # Try to merge consecutive sections into one chunk
        chunk_start = sec_start
        chunk_end = sec_end
        chunk_label = sec_text
        i += 1

        while i < len(sections):
            n_start, n_end, n_level, n_text = sections[i]
            if n_end - chunk_start + 1 > max_size:
                break
            chunk_end = n_end
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
