"""lexitool — Atomic Word Document Manipulation for AI Agents.

Consolidates ~30 lex_docx tools into 10 focused tools:
  lex_read    — Read document content with inline format markup
  lex_stats   — Document statistics and diagnostics
  lex_edit    — Atomic text edits with optional TC tracking
  lex_tc      — Track Changes: list, accept, or reject insertions/deletions
  lex_format  — Apply formatting to ranges
  lex_list    — Bullet and numbered list management
  lex_ref     — Bookmarks and cross-references
  lex_section — Page/section/column layout
  lex_doc     — Document-level operations (create, clean, TOC, merge)
  lex_clause  — Clause-level operations (split, extract, insert, compare)
  lex_corpus  — Multi-document corpus indexing and search
  lex_diff    — Document comparison with tracked-changes redline (quicompare)
  lex_xref_audit — Cross-reference audit: find dead links and unreferenced clauses
"""
from __future__ import annotations

import json
import logging
import re
import sys
import inspect
from pathlib import Path

from tools.registry import invalidate_check_fn_cache, registry, tool_error, tool_result

logger = logging.getLogger(__name__)



def _check_lexitool():
    """Return True when lexitool is importable."""
    import importlib.util
    try:
        return importlib.util.find_spec("lexitool") is not None
    except (ImportError, ValueError):
        return False


def _resolve_path(path: str) -> str:
    """Resolve a path, expanding ~ and making absolute."""
    return str(Path(path).expanduser().resolve())


def _lex_scan_core(path: str, query: str, *, regex: bool = False,
                   case_sensitive: bool = True, include_hf: bool = True,
                   max_results: int = 200, context_chars: int = 80) -> dict:
    """Internal scan wrapper used by lex_scan and lex_bulk_scan."""
    from lexitool.scan import scan_text
    return scan_text(path, query, regex=regex, case_sensitive=case_sensitive,
                     include_headers_footers=include_hf,
                     max_results=max_results, context_chars=context_chars)


def _normalize_para_range(value):
    """Normalize paragraph range to (lo, hi) tuple or None.

    Accepts: 24, "24", "24-24", [24,24], ["24","24"], "24,26", [24,26]
    """
    if value is None:
        return None

    # List/array: [24, 24] or ["24", "24"] or [24, 26]
    if isinstance(value, list):
        if len(value) == 1:
            lo = hi = int(value[0])
            return (lo, hi)
        if len(value) == 2:
            lo, hi = int(value[0]), int(value[1])
            return (min(lo, hi), max(lo, hi))
        return None

    # Scalar: 24
    if isinstance(value, (int, float)):
        lo = int(value)
        return (lo, lo)

    # String formats
    if isinstance(value, str) and value.strip():
        s = value.strip()
        # "24,26"
        if "," in s and not s.startswith("["):
            parts = s.split(",")
            if len(parts) == 2:
                try:
                    lo, hi = int(parts[0].strip()), int(parts[1].strip())
                    return (min(lo, hi), max(lo, hi))
                except ValueError:
                    pass
        # "24-26"
        if "-" in s and not s.startswith("-"):
            parts = s.split("-")
            if len(parts) == 2:
                try:
                    lo, hi = int(parts[0].strip()), int(parts[1].strip())
                    return (min(lo, hi), max(lo, hi))
                except ValueError:
                    pass
        # "24" or " 24 "
        try:
            lo = int(s)
            return (lo, lo)
        except ValueError:
            pass

    return None


def _supports_kwargs(func) -> tuple[set[str], bool]:
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return set(), True
    supported = set(sig.parameters)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    return supported, has_var_kw


def _insert_paragraph_block_compat(
    insert_paragraph_block,
    path: str,
    after_para: int,
    paragraphs: list[dict],
    *,
    tc: bool,
    author: str,
    sz: int,
    default_format: dict,
    inherit_format: bool,
    skip_empty: bool,
    output: str,
):
    """Call lexitool insert_paragraph_block across editable-install mismatches.

    Long-running Hermes processes may import a newer tool wrapper while the
    editable lexitool package still resolves to an older function signature.
    Filter unsupported kwargs instead of failing the user's document edit.
    """
    supported, has_var_kw = _supports_kwargs(insert_paragraph_block)

    call_paragraphs = paragraphs
    skipped_empty = 0
    if skip_empty and "skip_empty" not in supported and not has_var_kw:
        call_paragraphs = []
        for pg in paragraphs:
            text = str(pg.get("text", ""))
            if not text.strip() and not pg.get("page_break_before"):
                skipped_empty += 1
                continue
            call_paragraphs.append(pg)

    kwargs = {
        "tc": tc,
        "author": author,
        "sz": sz,
        "default_format": default_format,
        "inherit_format": inherit_format,
        "skip_empty": skip_empty,
        "output": output,
    }
    if not has_var_kw:
        kwargs = {key: value for key, value in kwargs.items() if key in supported}

    try:
        res = insert_paragraph_block(path, after_para, call_paragraphs, **kwargs)
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        legacy_kwargs = {
            key: value
            for key, value in {
                "tc": tc,
                "author": author,
                "sz": sz,
                "output": output,
            }.items()
            if has_var_kw or key in supported or not supported
        }
        res = insert_paragraph_block(path, after_para, call_paragraphs, **legacy_kwargs)

    if skipped_empty and hasattr(res, "message") and skipped_empty and "skipped" not in res.message:
        res.message += f"; skipped {skipped_empty} empty paragraphs"
    return res


# ── 1. lex_read ──────────────────────────────────────────────────────────────

LEX_READ_SCHEMA = {
    "name": "lex_read",
    "description": (
        "Read a .docx file and return its content as annotated text with inline "
        "format markup. This is the PRIMARY tool for understanding document content. "
        "Always call this FIRST before editing. For legal review/revision, prefer "
        "mode='review' for a whole-document dashboard, mode='legal_structure' for "
        "the legal outline, then full targeted paragraph reads before each edit.\n\n"
        "Format tags you will see in output:\n"
        "  [b]bold text[/b]  [i]italic[/i]  [u]underline[/u]  [s]strikethrough[/s]\n"
        "  [font:宋体,12pt]text[/font]  [color:#FF0000]text[/color]\n"
        "  [highlight:yellow]text[/highlight]\n"
        "  [ins]tracked added text[/ins]  [del]tracked deleted text[/del]\n"
        "  [bullet:0] list item  [num:0] numbered item\n"
        "  [bookmark:name]text[/bookmark]  [page-break]  [section-break:next]\n"
        "  [spacing:1.5]  [indent:2ch]  [align:center]\n\n"
        "Paragraphs are prefixed with §N (1-indexed). Use these § numbers "
        "as targets for lex_edit. Do not use shell/python-docx to read Word files "
        "when this tool is available."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "paras": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Specific paragraph numbers (1-indexed). Omit for all.",
            },
            "mode": {
                "type": "string",
                "enum": ["full", "structure", "legal_structure", "review", "stats", "headers_footers"],
                "description": (
                    "full=all content with markup; structure=Word headings only; "
                    "legal_structure=Word headings plus Chinese legal-style clauses; "
                    "review=legal review dashboard with outline, TC hotspots, comments, and next reads; "
                    "stats=counts only; headers_footers=only Word section header/footer text. Default: full."
                ),
            },
            "show_tc": {
                "oneOf": [
                    {"type": "boolean"},
                    {"type": "string", "enum": ["all", "final", "original"]},
                ],
                "description": (
                    "Track Changes display mode. "
                    "true or 'all'=show [ins]/[del] markup (default). "
                    "'final'=accept all revisions: show insertions as plain text, hide deletions. "
                    "'original'=reject all revisions: show deletions as plain text, hide insertions. "
                    "false=no TC markup."
                ),
            },
            "show_format": {
                "type": "boolean",
                "description": "Include format tags ([b], [font:...], etc.). Default: false for lightweight reads. Set true when you need to inspect formatting details.",
            },
            "include_headers_footers": {
                "type": "boolean",
                "description": "Append Word section header/footer text to full reads. Default: true.",
            },
            "include_comments": {
                "type": "boolean",
                "description": (
                    "Append inline [comment:author: text] markers at paragraph level. "
                    "Reads word/comments.xml to resolve comment references to their "
                    "target paragraphs. Default: false."
                ),
            },
        },
        "required": ["path"],
    },
}


def _handle_read(args: dict, **kwargs) -> str:
    from lexitool.markup import lex_read
    path = _resolve_path(args["path"])
    show_tc = args.get("show_tc", True)
    # Normalize: accept string "final"/"original" or boolean
    if isinstance(show_tc, str):
        show_tc = show_tc.lower()
        if show_tc in ("true", "all"):
            show_tc = True
        elif show_tc == "false":
            show_tc = False
        # "final", "original" pass through as strings
    result = lex_read(
        path=path,
        paras=args.get("paras"),
        mode=args.get("mode", "full"),
        show_tc=show_tc,
        show_format=args.get("show_format", False),
        include_headers_footers=args.get("include_headers_footers", True),
        include_comments=args.get("include_comments", False),
    )
    return tool_result(result)


# ── 2. lex_stats ──────────────────────────────────────────────────────────────

LEX_STATS_SCHEMA = {
    "name": "lex_stats",
    "description": (
        "Get document statistics and diagnostics. Returns paragraph count, word "
        "count, section count, fonts used, Track Changes count, and potential "
        "formatting issues. Call this when you need a quick overview before "
        "detailed review."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
        },
        "required": ["path"],
    },
}


def _handle_stats(args: dict, **kwargs) -> str:
    from lexitool.markup import lex_read
    path = _resolve_path(args["path"])
    result = lex_read(path=path, mode="stats")
    return tool_result(result)


LEX_TABLE_LIST_SCHEMA = {
    "name": "lex_table_list",
    "description": (
        "List all body-level tables in a .docx with stable table_index, "
        "location, dimensions, and content preview. Use before table edits "
        "instead of guessing table_index after document changes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "preview_rows": {
                "type": "integer",
                "description": "Number of leading rows to preview per table. Default: 3.",
            },
            "max_cell_chars": {
                "type": "integer",
                "description": "Maximum characters per preview cell. Default: 80.",
            },
        },
        "required": ["path"],
    },
}


def _handle_table_list(args: dict, **kwargs) -> str:
    from lexitool.edit_ops import list_tables

    path = _resolve_path(args["path"])
    result = list_tables(
        path,
        preview_rows=int(args.get("preview_rows", 3)),
        max_cell_chars=int(args.get("max_cell_chars", 80)),
    )
    return tool_result(result)


LEX_SCAN_SCHEMA = {
    "name": "lex_scan",
    "description": (
        "Scan an entire .docx for exact text or regex matches and return every "
        "affected paragraph/table-cell with context. Use this before legal "
        "revision to discover all affected clauses, and after editing to verify "
        "no unintended residual text remains. Default view='final' ignores "
        "track-deleted text so residual checks do not count already-deleted "
        "material."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the .docx file."},
            "query": {"type": "string", "description": "Exact text or regex to find."},
            "regex": {"type": "boolean", "description": "Treat query as regex. Default: false."},
            "case_sensitive": {"type": "boolean", "description": "Case-sensitive matching. Default: true."},
            "flexible_whitespace": {
                "type": "boolean",
                "description": (
                    "For exact-text scans, treat spaces/NBSP/newlines as equivalent "
                    "runs so 'Security Trustee' also matches 'Security  Trustee'. "
                    "Default: true."
                ),
            },
            "view": {
                "type": "string",
                "enum": ["final", "original", "all"],
                "description": "Track Changes view for scanning. Default: final.",
            },
            "include_tables": {
                "type": "boolean",
                "description": "Also scan body-level table cells. Default: true.",
            },
            "include_headers_footers": {
                "type": "boolean",
                "description": (
                    "Also scan Word header/footer parts and return part_path/kind/ref_types "
                    "targets for replace_header_footer. Default: true."
                ),
            },
            "context_chars": {
                "type": "integer",
                "description": "Characters of context around each match. Default: 80.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum result rows to return. Default: 200.",
            },
        },
        "required": ["path", "query"],
    },
}


def _handle_scan(args: dict, **kwargs) -> str:
    from lexitool.scan import scan_text

    path = _resolve_path(args["path"])
    result = scan_text(
        path,
        str(args.get("query", "")),
        regex=bool(args.get("regex", False)),
        case_sensitive=bool(args.get("case_sensitive", True)),
        flexible_whitespace=bool(args.get("flexible_whitespace", True)),
        view=str(args.get("view", "final") or "final"),
        include_tables=bool(args.get("include_tables", True)),
        include_headers_footers=bool(args.get("include_headers_footers", True)),
        context_chars=int(args.get("context_chars", 80) or 80),
        max_results=int(args.get("max_results", 200) or 200),
    )
    return tool_result(result)


LEX_REVISION_GUARD_SCHEMA = {
    "name": "lex_revision_guard",
    "description": (
        "Run document-wide residual checks for legal revision tasks. Use after "
        "editing but before reporting completion. It verifies that specified "
        "old/problem terms are absent in final Track Changes view and that "
        "specified required terms are present. Returns every failing paragraph "
        "and table-cell so the agent must resolve or explicitly justify each "
        "residual before delivery."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the .docx file."},
            "required_absent": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Terms/phrases that must have zero final-view matches after "
                    "revision, e.g. old party names or obsolete defined terms."
                ),
            },
            "required_present": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Terms/phrases that must appear at least once after revision, "
                    "e.g. new party names or replacement defined terms."
                ),
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Case-sensitive matching. Default: true.",
            },
            "flexible_whitespace": {
                "type": "boolean",
                "description": (
                    "Treat spaces/NBSP/newlines as equivalent runs. Default: true."
                ),
            },
            "include_tables": {
                "type": "boolean",
                "description": "Also scan body-level table cells. Default: true.",
            },
            "context_chars": {
                "type": "integer",
                "description": "Characters of context around each match. Default: 80.",
            },
            "max_results_per_query": {
                "type": "integer",
                "description": "Maximum result rows per query. Default: 100.",
            },
        },
        "required": ["path"],
    },
}


def _handle_revision_guard(args: dict, **kwargs) -> str:
    from lexitool.scan import scan_text

    path = _resolve_path(args["path"])
    required_absent = [
        str(item).strip()
        for item in (args.get("required_absent") or [])
        if str(item).strip()
    ]
    required_present = [
        str(item).strip()
        for item in (args.get("required_present") or [])
        if str(item).strip()
    ]
    if not required_absent and not required_present:
        return tool_error("lex_revision_guard requires required_absent and/or required_present.")

    common = {
        "regex": False,
        "case_sensitive": bool(args.get("case_sensitive", True)),
        "flexible_whitespace": bool(args.get("flexible_whitespace", True)),
        "view": "final",
        "include_tables": bool(args.get("include_tables", True)),
        "context_chars": int(args.get("context_chars", 80) or 80),
        "max_results": int(args.get("max_results_per_query", 100) or 100),
    }

    checks = []
    failures = []
    for query in required_absent:
        scan = scan_text(path, query, **common)
        count = int(scan.get("total_matches", 0) or 0)
        check = {
            "query": query,
            "expectation": "absent",
            "passed": count == 0,
            "matches": count,
            "paragraph_targets": scan.get("paragraph_targets", []),
            "table_targets": scan.get("table_targets", []),
            "sample_results": scan.get("results", [])[:10],
        }
        checks.append(check)
        if not check["passed"]:
            failures.append(check)

    for query in required_present:
        scan = scan_text(path, query, **common)
        count = int(scan.get("total_matches", 0) or 0)
        check = {
            "query": query,
            "expectation": "present",
            "passed": count > 0,
            "matches": count,
            "paragraph_targets": scan.get("paragraph_targets", []),
            "table_targets": scan.get("table_targets", []),
            "sample_results": scan.get("results", [])[:10],
        }
        checks.append(check)
        if not check["passed"]:
            failures.append(check)

    passed = not failures
    return tool_result(
        {
            "ok": passed,
            "path": path,
            "view": "final",
            "checks": checks,
            "failures": failures,
            "failure_count": len(failures),
            "next_step": (
                "Revision guard passed; proceed to readback/gate checks."
                if passed
                else "Do not report completion. Read every failing target with lex_read, "
                "then edit or explicitly document why the residual is intentional."
            ),
        }
    )


# ── 3. lex_edit ───────────────────────────────────────────────────────────────

LEX_EDIT_SCHEMA = {
    "name": "lex_edit",
    "description": (
        "Atomically edit text in a .docx file. Supports paragraph-level, "
        "table-level, and block-level operations with optional Track Changes.\n\n"
        "Legal drafting rule: edit only after reading the relevant § range with "
        "lex_scan/lex_read. For cross-document term revisions, first call "
        "lex_scan to discover ALL occurrences, then read affected ranges, then "
        "edit. Prefer narrow paragraph/table-cell edits. Avoid broad keyword "
        "replacement unless the task is explicitly mechanical and the affected "
        "ranges have been reviewed. After every material edit, read back the same "
        "range and verify content, formatting, parties, amounts, dates, defined "
        "terms, placeholders, cross-references, and comments.\n\n"
        "## Paragraph-level ops (require 'target')\n"
        "Target syntax:\n"
        "  §3           = entire paragraph 3\n"
        "  §3:5-10      = characters 5-10 in paragraph 3\n"
        "  §3:r2        = run 2 in paragraph 3\n"
        "  §3:r2:5-10   = characters 5-10 in run 2 of paragraph 3\n"
        "  §3-7         = paragraphs 3 through 7\n\n"
        "Paragraph coordinates are lex_read visible § numbers: body-level "
        "paragraphs only. Table-cell text must be edited with table ops.\n\n"
        "## Table-level ops (require 'table_index')\n"
        "- replace_table_cell: find cell by old_text, replace with new_text\n"
        "- replace_table_cells: batch {old, new} across table cells\n"
        "- set_table_cells: batch set cells by stable row/column coordinates\n"
        "- insert_table_rows: copy template_row, fill cell text from rows_data\n\n"
        "## Block-level ops\n"
        "- insert_paragraphs: insert paras with optional page breaks after after_para\n"
        "- create_table: insert a new table after a lex_read § paragraph\n"
        "- remove_blue_text: remove blue internal-note text runs from the document\n\n"
        "## Header/footer ops\n"
        "- replace_header_footer: replace text in Word header/footer parts. "
        "Use lex_read(mode='headers_footers') first and pass kind/ref_type/part_path "
        "when needed. Header/footer edits are direct XML replacements; Track Changes "
        "is not applied there.\n\n"
        "Use lex_read first to see § numbers and table structure, "
        "then target your edits precisely."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "op": {
                "type": "string",
                "enum": [
                    "replace", "replace_paragraph", "insert", "delete", "set_format",
                    "replace_table_cell", "replace_table_cells",
                    "set_table_cells",
                    "insert_table_rows", "insert_paragraphs",
                    "create_table", "replace_header_footer",
                    "remove_blue_text", "replace_all", "find_replace",
                    "delete_table", "merge_cells", "split_cell",
                    "delete_table_row", "insert_table_column",
                    "delete_table_column", "list_tables",
                    "add_image", "remove_image", "list_images",
                ],
                "description": (
                    "Operation type. Paragraph-level: replace, insert, delete, set_format, replace_all. "
                    "replace_paragraph replaces a whole visible § paragraph using Track Changes. "
                    "Table-level: replace_table_cell (single cell), replace_table_cells (batch), "
                    "set_table_cells (batch by row/col coordinates), "
                    "insert_table_rows (copy template row with cell text), "
                    "create_table (insert a new table with headers and data rows). "
                    "Block-level: insert_paragraphs (insert multiple paras after anchor). "
                    "Batch: replace_all (cross-paragraph find-and-replace with TC). "
                    "Cleanup: remove_blue_text (delete blue internal-note runs). "
                    "Header/footer: replace_header_footer."
                ),
            },
            "target": {
                "type": "string",
                "description": "Target specifier: §N, §N:X-Y, §N:rM, §N:rM:X-Y, or §N-M.",
            },
            "targets": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "List of paragraph numbers (1-indexed §N) for replace_all. "
                    "These should come from prior lex_scan + lex_read review; "
                    "do not use this as blind whole-document keyword replacement."
                ),
            },
            "bulk_confirmed": {
                "type": "boolean",
                "description": (
                    "Required true for high-risk replace_all operations (many "
                    "targets or short/generic old_text). Means the agent has "
                    "reviewed the affected ranges and the user/task explicitly "
                    "authorizes mechanical bulk replacement."
                ),
            },
            "bypass_legal_drafting_gate": {
                "type": "boolean",
                "description": (
                    "Emergency override for the legal drafting gate. Use only after "
                    "the user explicitly approves bypassing missing style memo or "
                    "revision plan prerequisites. Must include bypass_reason."
                ),
            },
            "bypass_reason": {
                "type": "string",
                "description": (
                    "Required when bypass_legal_drafting_gate=true. Summarize the "
                    "user approval and why editing may proceed before prerequisites."
                ),
            },
            "new_text": {
                "type": "string",
                "description": "New text for replace/replace_paragraph/insert. May contain format markup like [b]bold[/b].",
            },
            "format": {
                "type": "object",
                "description": (
                    'Format properties: {"bold": true, "font": "宋体", "size": "12pt", '
                    '"indent": "2.66667ch", "style": "Normal"}. For set_format and '
                    "insert_paragraphs default formatting."
                ),
            },
            "tc": {
                "type": "boolean",
                "description": "Track Changes mode. Default: true.",
            },
            "author": {
                "type": "string",
                "description": "Author name for Track Changes annotations. Default: 'ai-agent'.",
            },
            "font_size": {
                "type": "string",
                "description": "Font size for replacement text, e.g. '11pt'. Default: '11pt'.",
            },
            # -- Table / block parameters --
            "table_index": {
                "type": "integer",
                "description": "0-indexed table number. For replace_table_cell, replace_table_cells, insert_table_rows.",
            },
            "old_text": {
                "type": "string",
                "description": (
                    "Text to find. For paragraph replace this performs an in-paragraph "
                    "partial replacement; also used by replace_table_cell and "
                    "replace_header_footer."
                ),
            },
            "kind": {
                "type": "string",
                "enum": ["all", "header", "footer"],
                "description": "Header/footer part kind for replace_header_footer. Default: all.",
            },
            "ref_type": {
                "type": "string",
                "enum": ["default", "first", "even", "unknown"],
                "description": "Optional Word section reference type filter for replace_header_footer.",
            },
            "part_path": {
                "type": "string",
                "description": "Optional exact OPC part path from lex_read, e.g. word/header1.xml or word/footer1.xml.",
            },
            "replacements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "old": {"type": "string"},
                        "new": {"type": "string"},
                        "bold": {"type": "boolean"},
                    },
                    "required": ["old", "new"],
                },
                "description": "List of {old, new, bold?} for batch cell replacement. For replace_table_cells.",
            },
            "cells": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "row": {"type": "integer"},
                        "col": {"type": "integer"},
                        "text": {"type": "string"},
                        "old_text": {"type": "string"},
                        "bold": {"type": "boolean"},
                    },
                    "required": ["row", "col", "text"],
                },
                "description": (
                    "List of {row, col, text, old_text?, bold?} for stable table "
                    "cell edits by zero-indexed row/column. For set_table_cells."
                ),
            },
            "template_row": {
                "type": "integer",
                "description": "0-indexed row to copy as template. For insert_table_rows.",
            },
            "rows_data": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "description": "List of rows, each a list of cell text strings. For insert_table_rows.",
            },
            "after_para": {
                "type": "integer",
                "description": (
                    "Anchor paragraph. For create_table, pass lex_read's visible "
                    "§N paragraph number (1-indexed; 0 is accepted as legacy §1). "
                    "For insert_paragraphs, existing behavior is 0-indexed."
                ),
            },
            "paragraphs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "bold": {"type": "boolean"},
                        "page_break_before": {"type": "boolean"},
                        "style": {"type": "string"},
                        "style_id": {"type": "string"},
                        "format": {"type": "object"},
                    },
                    "required": ["text"],
                },
                "description": (
                    "List of {text, bold?, page_break_before?, style?, format?}. "
                    "For insert_paragraphs. Empty text paragraphs are skipped by default."
                ),
            },
            "inherit_format": {
                "type": "boolean",
                "description": (
                    "For insert_paragraphs, inherit paragraph/run formatting from after_para "
                    "without inheriting numbering. Default: true."
                ),
            },
            "skip_empty": {
                "type": "boolean",
                "description": "For insert_paragraphs, skip blank text items. Default: true.",
            },
            "headers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Column header texts. For create_table.",
            },
            "rows": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "description": "Table data rows, each a list of cell text strings. For create_table.",
            },
        },
        "required": ["path", "op"],
    },
}


def _handle_edit(args: dict, **kwargs) -> str:
    from lxml import etree
    from lexitool.edit_ops import (
        _read_docx, _write_docx,
        _first_run_rpr, _make_text_run_from_rpr,
        replace_table_cell_text, replace_table_cell_text_all,
        set_table_cells_by_position, insert_table_rows, insert_paragraph_block,
        remove_blue_text,
        trim_paragraph,
    )

    path = _resolve_path(args["path"])
    op = args["op"]
    tc = args.get("tc", True)
    author = args.get("author", "ai-agent")
    font_size_str = args.get("font_size", "11pt")
    try:
        font_size = float(font_size_str.replace("pt", ""))
    except (ValueError, AttributeError):
        font_size = 11.0

    # ── Table / block operations (no target needed) ──────────────────────────
    if op == "replace_header_footer":
        try:
            from lexitool.header_footer_ops import replace_header_footer_text

            old_text = args.get("old_text", "")
            old_text = _strip_format_markers(old_text)
            new_text = args.get("new_text", "")
            if not old_text:
                return tool_error("'old_text' is required for replace_header_footer")
            res = replace_header_footer_text(
                path,
                old_text,
                new_text,
                kind=args.get("kind", "all"),
                ref_type=args.get("ref_type"),
                part_path=args.get("part_path"),
                output=path,
            )
            res["op"] = op
            res["tc_mode"] = "direct-xml"
            return tool_result(res)
        except Exception as e:
            return tool_error(str(e))

    if op in ("replace_table_cell", "replace_table_cells", "set_table_cells",
              "insert_table_rows", "insert_paragraphs",
              "create_table", "remove_blue_text", "trim_end",
              "delete_table", "merge_cells", "split_cell",
              "delete_table_row", "insert_table_column",
              "delete_table_column", "list_tables",
              "add_image", "remove_image", "list_images"):
        try:
            if op == "remove_blue_text":
                res = remove_blue_text(path, output=path)

            elif op == "replace_table_cell":
                table_index = args.get("table_index", 0)
                old_text = args.get("old_text", "")
                old_text = _strip_format_markers(old_text)
                new_text = args.get("new_text", "")
                if not old_text:
                    return tool_error("'old_text' is required for replace_table_cell")
                res = replace_table_cell_text(
                    path, table_index, old_text, new_text,
                    tc=tc, author=author,
                    font_size=font_size,
                    output=path,
                )

            elif op == "replace_table_cells":
                table_index = args.get("table_index", 0)
                replacements = args.get("replacements", [])
                if not replacements:
                    return tool_error("'replacements' is required for replace_table_cells")
                res = replace_table_cell_text_all(
                    path, table_index, replacements,
                    tc=tc, author=author,
                    font_size=font_size,
                    output=path,
                )

            elif op == "set_table_cells":
                table_index = args.get("table_index", 0)
                cells = args.get("cells", [])
                if not cells:
                    return tool_error("'cells' is required for set_table_cells")
                res = set_table_cells_by_position(
                    path, table_index, cells,
                    tc=tc, author=author,
                    font_size=font_size,
                    output=path,
                )

            elif op == "insert_table_rows":
                table_index = args.get("table_index", 0)
                template_row = args.get("template_row", 0)
                rows_data = args.get("rows_data", [])
                if not rows_data:
                    return tool_error("'rows_data' is required for insert_table_rows")
                res = insert_table_rows(
                    path, table_index, template_row, rows_data,
                    output=path,
                )

            elif op == "insert_paragraphs":
                after_para = args.get("after_para", 0)
                paragraphs = args.get("paragraphs", [])
                if not paragraphs:
                    return tool_error("'paragraphs' is required for insert_paragraphs")
                res = _insert_paragraph_block_compat(
                    insert_paragraph_block,
                    path, after_para, paragraphs,
                    tc=tc, author=author,
                    sz=int(font_size * 2),
                    default_format=args.get("format") or {},
                    inherit_format=args.get("inherit_format", True),
                    skip_empty=args.get("skip_empty", True),
                    output=path,
                )

            elif op == "create_table":
                from lexitool.edit_ops import create_table
                after_para = args.get("after_para", 0)
                headers = args.get("headers", [])
                rows = args.get("rows", [])
                if not headers and not rows:
                    return tool_error("'headers' or 'rows' is required for create_table")
                res = create_table(
                    path, after_para, headers, rows,
                    font_size=font_size,
                    output=path,
                )

            elif op == "delete_table":
                from lexitool.openxml_table import delete_table
                table_index = args.get("table_index", 0)
                res = delete_table(path, table_index, output=path)

            elif op == "merge_cells":
                from lexitool.openxml_table import merge_cells
                table_index = args.get("table_index", 0)
                start_row = args.get("start_row", 0)
                start_col = args.get("start_col", 0)
                end_row = args.get("end_row", 0)
                end_col = args.get("end_col", 0)
                res = merge_cells(path, table_index,
                                  start_row, start_col, end_row, end_col,
                                  output=path)

            elif op == "split_cell":
                from lexitool.openxml_table import split_cell
                table_index = args.get("table_index", 0)
                row = args.get("row", 0)
                col = args.get("col", 0)
                res = split_cell(path, table_index, row, col, output=path)

            elif op == "delete_table_row":
                from lexitool.openxml_table import delete_table_row
                table_index = args.get("table_index", 0)
                row_index = args.get("row_index", 0)
                res = delete_table_row(path, table_index, row_index, output=path)

            elif op == "insert_table_column":
                from lexitool.openxml_table import insert_table_column
                table_index = args.get("table_index", 0)
                position = args.get("position", -1)
                res = insert_table_column(path, table_index, position, output=path)

            elif op == "delete_table_column":
                from lexitool.openxml_table import delete_table_column
                table_index = args.get("table_index", 0)
                col_index = args.get("col_index", 0)
                res = delete_table_column(path, table_index, col_index, output=path)

            elif op == "list_tables":
                from lexitool.openxml_table import list_tables
                res = list_tables(path)

            elif op == "add_image":
                from lexitool.openxml_image import add_image
                image_path = args.get("image_path", "")
                if not image_path:
                    return tool_error("'image_path' is required for add_image")
                res = add_image(path, image_path,
                                para=args.get("para", 1),
                                offset=args.get("offset", -1),
                                width=args.get("width"),
                                height=args.get("height"),
                                dpi=args.get("dpi", 96),
                                output=path)

            elif op == "remove_image":
                from lexitool.openxml_image import remove_image
                res = remove_image(path,
                                   relationship_id=args.get("rid"),
                                   image_name=args.get("image_name"),
                                   output=path)

            elif op == "list_images":
                from lexitool.openxml_image import list_images
                res = list_images(path)

            elif op == "trim_end":
                para = args.get("para", 0)
                chars = args.get("chars", 1)
                from_end = args.get("from_end", True)
                res = trim_paragraph(
                    path, para,
                    chars=chars,
                    from_end=from_end,
                    output=path,
                )

            return tool_result({
                "ok": res.ok,
                "op": op,
                "message": res.message,
                "path": res.path,
                "tc_mode": res.tc_mode,
                "tc_id": res.tc_id,
            })

        except Exception as e:
            return tool_error(str(e))

    # ── Batch replace_all ────────────────────────────────────────────────────
    if op == "replace_all":
        targets = args.get("targets", [])
        old_text = args.get("old_text", "")
        old_text = _strip_format_markers(old_text)
        new_text = args.get("new_text", "")
        if not targets:
            return tool_error("'targets' is required for replace_all (list of paragraph numbers)")
        if not old_text:
            return tool_error("'old_text' is required for replace_all")
        high_risk_bulk = len(targets) > 8 or len(str(old_text).strip()) < 4
        if high_risk_bulk and not args.get("bulk_confirmed", False):
            return tool_error(
                "LEGAL_BULK_REPLACE_REVIEW_REQUIRED: replace_all across many "
                "paragraphs or with a short/generic old_text is high-risk for "
                "legal documents. First use lex_read(mode='review') and targeted "
                "lex_read(paras=[...]) to confirm every affected paragraph, then "
                "retry with bulk_confirmed=true only if this is an explicitly "
                "authorized mechanical replacement."
            )
        try:
            from lexitool.edit_ops import replace_text_all
            from lexitool.scan import scan_text
            # Resolve body_paras indices to all_paras indices for _find_para.
            doc_xml_r, _ = _read_docx(path)
            root_r = etree.fromstring(doc_xml_r)
            body_r = root_r.find(f"{W}body")
            body_paras_r = [el for el in body_r if el.tag == f"{W}p"]
            all_paras_r = [el for el in root_r.iter() if el.tag == f"{W}p"]
            para_indices = []
            for t in targets:
                body_idx = int(t) - 1
                if body_idx < 0 or body_idx >= len(body_paras_r):
                    continue
                body_el = body_paras_r[body_idx]
                found = False
                for j, el in enumerate(all_paras_r):
                    if el is body_el:
                        para_indices.append(j)
                        found = True
                        break
                if not found:
                    para_indices.append(body_idx)  # fallback
            result = replace_text_all(
                path, para_indices, old_text, new_text,
                tc=tc, author=author, font_size=font_size,
                output=path,
            )
            remaining = scan_text(
                path,
                old_text,
                regex=False,
                case_sensitive=True,
                flexible_whitespace=True,
                view="final",
                include_tables=True,
                context_chars=80,
                max_results=50,
            )
            result["post_edit_scan"] = {
                "query": old_text,
                "view": "final",
                "remaining_matches": remaining.get("total_matches", 0),
                "remaining_paragraph_targets": remaining.get("paragraph_targets", []),
                "remaining_table_targets": remaining.get("table_targets", []),
                "sample_results": remaining.get("results", [])[:10],
                "warning": (
                    "Residual matches remain in final view. Do not report completion "
                    "until each residual is intentionally retained or edited."
                    if remaining.get("total_matches", 0) else ""
                ),
            }
            return tool_result(result)
        except Exception as e:
            return tool_error(str(e))

    # ── Whole-document find_replace ──────────────────────────────────────────
    if op == "find_replace":
        from lexitool.edit_ops import find_and_replace_all
        find = args.get("find", args.get("old_text", ""))
        find = _strip_format_markers(find)
        if not find:
            return tool_error("'find' (or 'old_text') is required for find_replace")
        replace_text = args.get("replace", args.get("new_text", ""))
        result = find_and_replace_all(
            path, find, replace_text,
            match_case=args.get("match_case", True),
            whole_word=args.get("whole_word", False),
            regex=args.get("regex", False),
            tc=tc, author=author,
            include_headers_footers=args.get("include_headers_footers", False),
            include_tables=args.get("include_tables", True),
        )
        return tool_result(result)

    # ── Paragraph-level operations ───────────────────────────────────────────
    from lexitool.markup import parse_target
    from lexitool.tc_utils import tc_replace_first_in_para, tc_ins_text, tc_del_paragraph

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    target_str = args.get("target", "")
    if not target_str:
        return tool_error("'target' is required for paragraph-level operations (replace, replace_paragraph, insert, delete, set_format)")
    new_text = args.get("new_text", "")
    fmt = args.get("format")

    target = parse_target(target_str)
    doc_xml, other = _read_docx(path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    para_idx = target.para_start - 1
    para_end_idx = (target.para_end - 1) if target.para_end else para_idx

    # Build two paragraph lists:
    #   body_paras — body-direct w:p only (matches lex_read numbering)
    #   all_paras  — every w:p in document order including table cells
    #                 (matches edit_ops._find_para semantics)
    body_paras = [el for el in body if el.tag == f"{W}p"]
    all_paras = [el for el in root.iter() if el.tag == f"{W}p"]

    if para_idx < 0 or para_idx >= len(body_paras):
        return tool_error(f"Paragraph {target.para_start} out of range (1-{len(body_paras)})")
    if para_end_idx >= len(body_paras):
        return tool_error(f"Paragraph {target.para_end} out of range (1-{len(body_paras)})")

    # Build the list of target paragraphs for range-based delete
    target_indices = list(range(para_idx, para_end_idx + 1))
    target_paras = [body_paras[i] for i in target_indices]

    tc_id = _next_tc_id_from_body(body)

    # For single-paragraph ops, use the first target paragraph
    para_el = target_paras[0] if target_paras else None

    # Resolve _all_idx for nearby search fallbacks (replace op only)
    _all_idx = None
    for i, el in enumerate(all_paras):
        if el is para_el:
            _all_idx = i
            break

    try:
        if op == "delete":
            # Process range in reverse to avoid XML mutation interference
            for p_el in reversed(target_paras):
                if tc:
                    tc_del_paragraph(p_el, tc_id, author)
                    tc_id += 10  # spacing between paragraph TC IDs
                else:
                    for t_el in p_el.iter(f"{W}t"):
                        t_el.text = None

        elif op == "replace_paragraph":
            new_text_plain, format_segments = _parse_format_markers(new_text)
            has_fmt = _has_format_markers(new_text)
            if target.para_end and target.para_end > target.para_start:
                return tool_error("replace_paragraph only supports a single § paragraph target")
            if target.char_start is not None or target.char_end is not None:
                return tool_error("replace_paragraph requires an entire paragraph target like §12")
            if not str(new_text_plain).strip():
                return tool_error("'new_text' is required for replace_paragraph")

            if tc or has_fmt:
                base_rPr = tc_del_paragraph(
                    para_el,
                    tc_id,
                    author,
                    delete_para_mark=False,
                )
                ins = tc_ins_text(
                    para_el,
                    new_text_plain,
                    tc_id + 100,
                    author,
                    position="end",
                    base_rPr=base_rPr,
                )
                if has_fmt:
                    inserted_id = ins.get(f"{W}id")
                    _apply_format_segments_to_ins(para_el, inserted_id, format_segments)
            else:
                pPr = para_el.find(f"{W}pPr")
                base_rPr = _first_run_rpr(para_el)
                for child in [c for c in list(para_el) if c is not pPr]:
                    para_el.remove(child)
                run = _make_text_run_from_rpr(new_text_plain, base_rPr)
                if pPr is not None:
                    pPr.addnext(run)
                else:
                    para_el.append(run)

        elif op == "replace":
            requested_old_text = args.get("old_text")
            if not isinstance(requested_old_text, str) or not requested_old_text.strip():
                return tool_error("'old_text' is required for replace operation")
            old_text = _strip_format_markers(requested_old_text)

            # Parse format markers from new_text so that [b], [i], [u]
            # are converted to actual OOXML formatting instead of being
            # treated as literal text.
            new_text_plain, format_segments = _parse_format_markers(new_text)
            has_fmt = _has_format_markers(new_text)

            # When format markers are present, force TC mode so we can
            # create multiple runs with different formatting.
            if tc or has_fmt:
                tc_result = tc_replace_first_in_para(para_el, old_text, new_text_plain, tc_id, author)
                used_nearby_fallback = False
                if not tc_result.get("ok"):
                    # Multi-stage fallback mirroring _resolve_replace_match:
                    # exact → strip → tab-strip → whitespace-normalized (now
                    # also normalizes full-width chars via _normalize_fullwidth).
                    para_text = _get_para_text(para_el)
                    match_text = _resolve_replace_match(para_text, old_text)
                    if match_text is not None and match_text != old_text:
                        tc_result = tc_replace_first_in_para(
                            para_el, match_text, new_text_plain, tc_id, author
                        )
                # Fallback: search ALL paragraphs including table cells.
                if not tc_result.get("ok") and _all_idx is not None:
                    tc_result = _search_and_tc_replace_nearby(
                        all_paras, _all_idx, old_text, new_text_plain, tc_id, author
                    )
                    used_nearby_fallback = tc_result.get("ok", False)
                if not tc_result.get("ok"):
                    return tool_error(
                        f"Text '{old_text}' not found in paragraph {target.para_start}"
                        + (" or nearby table cells" if _all_idx is not None else "")
                    )
                if has_fmt and not used_nearby_fallback:
                    _apply_format_segments_to_ins(
                        para_el, tc_result["inserted_id"], format_segments
                    )
            else:
                result = _direct_replace(para_el, old_text, new_text_plain)
                # Fallback: try whitespace-normalized matching
                if not result["ok"]:
                    para_text = _get_para_text(para_el)
                    located = _locate_normalized_text(para_text, old_text)
                    if located is not None:
                        start, end = located
                        actual_old = para_text[start:end]
                        if actual_old and actual_old != old_text:
                            result = _direct_replace(para_el, actual_old, new_text_plain)
                # Further fallback: search ALL paragraphs including table cells
                if not result["ok"] and _all_idx is not None:
                    result = _search_and_replace_nearby(
                        all_paras, _all_idx, old_text, new_text_plain
                    )
                if not result["ok"]:
                    return tool_error(f"Text '{old_text}' not found in paragraph {target.para_start}" +
                                      (" or nearby table cells" if _all_idx is not None else ""))

        elif op == "insert":
            new_text_plain, format_segments = _parse_format_markers(new_text)
            has_fmt = _has_format_markers(new_text)

            if tc or has_fmt:
                ins = tc_ins_text(para_el, new_text_plain, tc_id, author, position=target.char_start or "end")
                if has_fmt:
                    inserted_id = ins.get(f"{W}id")
                    _apply_format_segments_to_ins(para_el, inserted_id, format_segments)
            else:
                _direct_insert(para_el, new_text_plain, target.char_start or -1)

        elif op == "set_format" and fmt:
            for p_el in target_paras:
                _apply_format_to_range(p_el, target.char_start, target.char_end, fmt)

    except Exception as e:
        return tool_error(str(e))

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    _write_docx(path, doc_xml_out, other)

    # If new_text contains [ref:name] or [page-ref:name] markup, convert to real fields
    _field_markers = ("[ref:", "[page-ref:", "[note-ref:", "[style-ref:")
    if new_text and any(m in new_text for m in _field_markers):
        try:
            from lexitool.fields import resolve_field_markup
            field_result = resolve_field_markup(path)
            if field_result.get("converted"):
                from lexitool.fields import update_fields
                update_fields(path)
        except Exception:
            pass  # field conversion is best-effort

    # Post-edit verification: read back the changed paragraphs so the agent
    # can confirm the edit was applied correctly without an extra round-trip.
    verified_output = ""
    try:
        from lexitool.markup import lex_read
        # For range ops, read the full affected range; for single-para, read with context
        if target.para_end and target.para_end > target.para_start:
            read_start = target.para_start
            read_end = target.para_end
        else:
            read_start = max(1, target.para_start - 1)
            read_end = target.para_start + 1
        verified = lex_read(path, paras=list(range(read_start, read_end + 1)),
                            mode="full", show_tc=True, show_format=True)
        verified_output = verified.get("text", "")
    except Exception:
        verified_output = ""  # best-effort

    result = {"ok": True, "op": op, "target": target_str, "para": target.para_start}
    if target.para_end and target.para_end > target.para_start:
        result["para_end"] = target.para_end
        result["paragraphs_affected"] = len(target_indices)
    if verified_output:
        result["verified_output"] = verified_output
    return tool_result(result)


def _next_tc_id_from_body(body) -> int:
    """Scan body for max w:id across ins/del/comment elements."""
    max_id = 0
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    for tag in (f"{W}ins", f"{W}del", f"{W}commentRangeStart", f"{W}commentRangeEnd"):
        for el in body.iter(tag):
            try:
                max_id = max(max_id, int(el.get(f"{W}id", 0)))
            except (ValueError, TypeError):
                pass
    return max_id + 1


def _get_para_text(para_el) -> str:
    """Get paragraph plain text matching _para_full_text semantics.

    Only collects w:t (not w:delText) so that fallback text position
    calculations remain consistent with tc_replace_first_in_para's
    internal _para_full_text.  Filters w:tab inside w:tabs (paragraph
    formatting) and normalizes curly quotes to straight quotes.
    """
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    parts = []
    for el in para_el.iter():
        if el.tag == f"{W}t":
            parts.append(el.text or "")
        elif el.tag == f"{W}tab":
            parent = el.getparent()
            if parent is not None and parent.tag == f"{W}tabs":
                continue
            parts.append("\t")
    text = "".join(parts).replace('“', '"').replace('”', '"').replace('‘', "'").replace('’', "'")
    return _normalize_fullwidth(text)


def _direct_replace(para_el, old_text: str, new_text: str) -> dict:
    """Replace old_text with new_text in a paragraph element.

    Uses iter() to find ALL w:t descendants (including those nested inside
    w:ins, w:del, w:smartTag, and other wrappers common in table cells).
    Supports cross-run matching: if old_text spans multiple w:t elements,
    only the overlapped elements are modified — other runs are preserved.
    This is critical for numbered paragraphs where the auto-number text
    lives in a separate run from the content text.
    """
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    t_elements = [el for el in para_el.iter() if el.tag == f"{W}t"]
    if not t_elements:
        return {"ok": False, "reason": "no text runs in paragraph"}

    full_text = "".join(t.text or "" for t in t_elements)
    match_text = _resolve_replace_match(full_text, old_text)
    if match_text is None:
        return {"ok": False, "reason": "text not found"}

    pos = full_text.find(match_text)
    end_pos = pos + len(match_text)

    # Build character offset ranges for each w:t, then modify only those
    # that overlap the match.  This preserves run structure (auto-number
    # text, TC markup, mid-paragraph format changes, etc.).
    new_text_inserted = False
    offset = 0
    for t in t_elements:
        tlen = len(t.text or "")
        t_start = offset
        t_end = offset + tlen
        offset = t_end

        if t_start < end_pos and t_end > pos:
            local_start = max(0, pos - t_start)
            local_end = min(tlen, end_pos - t_start)
            before = (t.text or "")[:local_start]
            after = (t.text or "")[local_end:]

            if not new_text_inserted:
                t.text = before + new_text + after
                new_text_inserted = True
            else:
                t.text = after if after else None

    return {"ok": True, "matched_text": match_text}


def _parse_format_markers(text: str):
    """Parse [b]/[i]/[u] format markers from text.

    Returns (plain_text, segments) where segments is a list of
    {"text": str, "bold": bool, "italic": bool, "underline": bool}.
    The plain_text strips all markers for text matching.
    """
    segments = []
    bold = False
    italic = False
    underline = False

    pattern = re.compile(r"\[/(?:b|i|u)\]|\[(?:b|i|u)\]")
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            segments.append({
                "text": text[pos:m.start()],
                "bold": bold, "italic": italic, "underline": underline,
            })
        tag = m.group()
        if tag == "[b]":
            bold = True
        elif tag == "[/b]":
            bold = False
        elif tag == "[i]":
            italic = True
        elif tag == "[/i]":
            italic = False
        elif tag == "[u]":
            underline = True
        elif tag == "[/u]":
            underline = False
        pos = m.end()

    if pos < len(text):
        segments.append({
            "text": text[pos:],
            "bold": bold, "italic": italic, "underline": underline,
        })

    plain_text = "".join(s["text"] for s in segments)
    return plain_text, segments


def _apply_format_segments_to_ins(para_el, inserted_id, segments):
    """Replace the single r inside a w:ins with multiple formatted runs."""
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    from lxml import etree

    for ins in para_el.iter(f"{W}ins"):
        if ins.get(f"{W}id") == str(inserted_id):
            for r in list(ins.findall(f"{W}r")):
                ins.remove(r)

            for seg in segments:
                if not seg["text"]:
                    continue
                r = etree.Element(f"{W}r")
                if seg["bold"] or seg["italic"] or seg["underline"]:
                    rPr = etree.SubElement(r, f"{W}rPr")
                    if seg["bold"]:
                        etree.SubElement(rPr, f"{W}b")
                        etree.SubElement(rPr, f"{W}bCs")
                    if seg["italic"]:
                        etree.SubElement(rPr, f"{W}i")
                        etree.SubElement(rPr, f"{W}iCs")
                    if seg["underline"]:
                        u = etree.SubElement(rPr, f"{W}u")
                        u.set(f"{W}val", "single")
                t = etree.SubElement(r, f"{W}t")
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                t.text = seg["text"]
                ins.append(r)
            return True
    return False


def _has_format_markers(text: str) -> bool:
    """Check if text contains [b]/[i]/[u] format markers."""
    return bool(re.search(r"\[/?(?:b|i|u)\]", text))


# Fullwidth ASCII range U+FF01–U+FF5E → U+0021–U+007E, plus U+3000 → space.
_FULLWIDTH_TABLE = {i: chr(i - 0xFF01 + 0x0021) for i in range(0xFF01, 0xFF5F)}
_FULLWIDTH_TABLE[0x3000] = " "


def _normalize_fullwidth(text: str) -> str:
    """Normalize full-width ASCII / punctuation / digits to half-width."""
    return text.translate(_FULLWIDTH_TABLE)


def _normalize_match_text(text: str) -> str:
    """Normalize only for fallback matching; never changes document output."""
    return _normalize_fullwidth(re.sub(r"\s+", "", text or ""))


# Regex to strip lexitool format markers from old_text so that text
# copied from lex_scan output (which includes [u], [b], [ins], etc.)
# can be used as search text in lex_edit replace operations.
_FORMAT_TAG_RE = re.compile(
    r'\[/?(?:b|i|u|s|ins|del|tab|num|spacing|indent|align|highlight|font|bookmark|ref|page-ref|note-ref|style-ref)(?::[^\]]*)?\]'
)


def _strip_format_markers(text: str) -> str:
    """Strip lexitool format markers from text for plain-text matching."""
    return _FORMAT_TAG_RE.sub('', text)


def _locate_normalized_text(haystack: str, needle: str) -> tuple[int, int] | None:
    """Locate needle in haystack while ignoring whitespace + fullwidth differences."""
    normalized_haystack = []
    index_map = []
    for idx, char in enumerate(haystack or ""):
        if char.isspace():
            continue
        normalized_haystack.append(_normalize_fullwidth(char))
        index_map.append(idx)

    normalized_needle = _normalize_match_text(needle)
    if not normalized_needle or not normalized_haystack:
        return None

    pos = "".join(normalized_haystack).find(normalized_needle)
    if pos < 0:
        return None
    start = index_map[pos]
    end = index_map[pos + len(normalized_needle) - 1] + 1
    return start, end


def _resolve_replace_match(full_text: str, old_text: str) -> str | None:
    if old_text in full_text:
        return old_text

    candidates = []
    stripped = (old_text or "").strip()
    if stripped and stripped != old_text:
        candidates.append(stripped)
    no_tabs = (old_text or "").replace("\t", "")
    if no_tabs and no_tabs != old_text:
        candidates.append(no_tabs)

    for candidate in candidates:
        if candidate in full_text:
            return candidate

    located = _locate_normalized_text(full_text, old_text)
    if located is None:
        return None
    start, end = located
    return full_text[start:end]


def _search_and_replace_nearby(
    all_paras: list, body_idx: int, old_text: str, new_text: str
) -> dict:
    """Fallback: search paragraphs near body_idx (including table cell paragraphs)
    for old_text and replace it.  Handles the common case where lex_read shows
    a table adjacent to paragraph N but the table's cell paragraphs aren't
    individually numbered.
    """
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    # Search window: from body_idx forward up to 200 paragraphs, then backward
    n = len(all_paras)
    # Find the body-level paragraph in the all_paras list
    search_start = 0
    body_para_count = 0
    for i, el in enumerate(all_paras):
        parent = el.getparent()
        # A body-level paragraph has w:body as ancestor (not w:tc)
        is_body = True
        anc = parent
        while anc is not None:
            if anc.tag == f"{W}tc":
                is_body = False
                break
            anc = anc.getparent()
        if is_body:
            if body_para_count == body_idx:
                search_start = i
                break
            body_para_count += 1

    # Search forward from the body paragraph (cover table cells that follow it)
    for i in range(search_start, min(search_start + 200, n)):
        result = _direct_replace(all_paras[i], old_text, new_text)
        if result["ok"]:
            return result

    # Search backward as a last resort
    for i in range(search_start - 1, max(search_start - 50, -1), -1):
        result = _direct_replace(all_paras[i], old_text, new_text)
        if result["ok"]:
            return result

    return {"ok": False, "reason": "text not found in nearby paragraphs"}


def _tc_replace_with_fallback(para_el, old_text: str, new_text: str, tc_id: int, author: str) -> dict:
    """Try TC replace with full multi-stage match resolution fallback."""
    from lexitool.tc_utils import tc_replace_first_in_para

    result = tc_replace_first_in_para(para_el, old_text, new_text, tc_id, author)
    if not result.get("ok"):
        para_text = _get_para_text(para_el)
        match_text = _resolve_replace_match(para_text, old_text)
        if match_text is not None and match_text != old_text:
            result = tc_replace_first_in_para(para_el, match_text, new_text, tc_id, author)
    return result


def _search_and_tc_replace_nearby(
    all_paras: list, body_idx: int, old_text: str, new_text: str,
    tc_id: int, author: str,
) -> dict:
    """Same as _search_and_replace_nearby but for TC mode replacement."""
    from lexitool.tc_utils import tc_replace_first_in_para

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    n = len(all_paras)
    search_start = 0
    body_para_count = 0
    for i, el in enumerate(all_paras):
        anc = el.getparent()
        is_body = True
        while anc is not None:
            if anc.tag == f"{W}tc":
                is_body = False
                break
            anc = anc.getparent()
        if is_body:
            if body_para_count == body_idx:
                search_start = i
                break
            body_para_count += 1

    # Search forward
    for i in range(search_start, min(search_start + 200, n)):
        result = _tc_replace_with_fallback(all_paras[i], old_text, new_text, tc_id, author)
        if result.get("ok"):
            return result

    # Search backward
    for i in range(search_start - 1, max(search_start - 50, -1), -1):
        result = _tc_replace_with_fallback(all_paras[i], old_text, new_text, tc_id, author)
        if result.get("ok"):
            return result

    return {"ok": False, "reason": "text not found in nearby paragraphs"}


def _direct_insert(para_el, text: str, offset: int) -> None:
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    from lxml import etree
    r = etree.SubElement(para_el, f"{W}r")
    t = etree.SubElement(r, f"{W}t")
    t.text = text
    if text and (text[0] == " " or text[-1] == " "):
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    if offset >= 0:
        _char_pos = 0
        _target_t = None
        for _t_el in para_el.iter(f"{W}t"):
            _tlen = len(_t_el.text or "")
            if _char_pos + _tlen > offset:
                _target_t = _t_el
                break
            _char_pos += _tlen
        if _target_t is not None:
            _ins_pos = offset - _char_pos
            _old = _target_t.text or ""
            _target_t.text = _old[:_ins_pos] + text + _old[_ins_pos:]
            # Remove the appended run since we inserted inline
            para_el.remove(r)
            if text and (text[0] == " " or text[-1] == " "):
                _target_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _apply_format_to_range(para_el, start: int, end: int, fmt: dict) -> None:
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    from lxml import etree
    from lexitool.tc_utils import make_rPr_from_dict
    from lexitool.edit_ops import _parse_indent_twips, _parse_size_half_points, _set_pstyle, _ensure_child

    # ── Paragraph-level properties ──
    pPr = para_el.find(f"{W}pPr")
    if pPr is None:
        pPr = etree.Element(f"{W}pPr")
        para_el.insert(0, pPr)

    if fmt.get("style") or fmt.get("style_id"):
        _set_pstyle(pPr, fmt.get("style") or fmt.get("style_id"))

    if fmt.get("align"):
        jc = _ensure_child(pPr, f"{W}jc")
        jc.set(f"{W}val", str(fmt["align"]))

    if fmt.get("spacing"):
        spacing = _ensure_child(pPr, f"{W}spacing")
        spacing.set(f"{W}line", str(int(float(fmt["spacing"]) * 240)))
        spacing.set(f"{W}lineRule", "auto")

    if fmt.get("indent") is not None:
        ind = _ensure_child(pPr, f"{W}ind")
        ind.set(f"{W}firstLine", _parse_indent_twips(fmt["indent"]))

    # ── Run-level properties ──
    rpr_dict = {}
    if fmt.get("bold"):
        rpr_dict["b"] = True
    if fmt.get("italic"):
        rpr_dict["i"] = True
    if fmt.get("font"):
        rpr_dict["eastAsia"] = fmt["font"]
    if fmt.get("size"):
        val = fmt["size"].replace("pt", "").strip()
        try:
            rpr_dict["sz"] = str(int(float(val) * 2))
        except (ValueError, TypeError):
            pass
    if fmt.get("color"):
        rpr_dict["color"] = str(fmt["color"]).lstrip("#")
    if fmt.get("highlight"):
        rpr_dict["highlight"] = str(fmt["highlight"])

    if rpr_dict:
        new_rPr = make_rPr_from_dict(rpr_dict)
        for r_el in para_el.findall(f".//{W}r"):
            existing = r_el.find(f"{W}rPr")
            if existing is not None:
                r_el.remove(existing)
            r_el.insert(0, new_rPr)


# ── 3b. lex_tc (Track Changes accept/reject/list) ─────────────────────────────

LEX_TC_SCHEMA = {
    "name": "lex_tc",
    "description": (
        "Manage Track Changes in a .docx file: list, accept, or reject tracked "
        "insertions and deletions. Supports filtering by paragraph range, author, "
        "and change type (ins/del).\n\n"
        "Operations:\n"
        "- 'list': List all TC entries with paragraph number, type, author, and text\n"
        "- 'accept': Accept (finalize) TC changes — deletions are removed, insertions become normal text\n"
        "- 'reject': Reject (revert) TC changes — deletions are restored, insertions are removed\n\n"
        "Use 'reject' with type='del' to restore deleted text (e.g., restoring a removed clause).\n"
        "Use 'accept' with type='ins' to finalize inserted text.\n"
        "Use paragraph range to target specific sections without affecting the whole document."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "op": {
                "type": "string",
                "enum": ["list", "accept", "reject"],
                "description": "Operation: 'list' (read-only scan), 'accept' (finalize changes), 'reject' (revert changes).",
            },
            "author": {
                "type": "string",
                "description": "Filter by author name. Omit to match all authors.",
            },
            "type_filter": {
                "type": "string",
                "enum": ["ins", "del"],
                "description": "Filter by change type: 'ins' for insertions only, 'del' for deletions only. Omit for both.",
            },
            "text_pattern": {
                "type": "string",
                "description": "Filter by text content: only match TC entries whose text contains this string. Enables selective accept/reject by content rather than just type.",
            },
            "para_range": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
                ],
                "description": "Paragraph range to operate on. Accepts '50,100' (string) or [50, 100] (array of two ints). Omit for entire document.",
            },
            "dry_run": {
                "type": "boolean",
                "description": "If true, preview what would change without saving. Default: false.",
            },
            "include_tables": {
                "type": "boolean",
                "description": "If true, also include TC entries inside tables when using para_range. Essential when restoring sections that span both paragraphs and tables (e.g., a clause with a data table). Default: false.",
            },
        },
        "required": ["path", "op"],
    },
}


def _handle_tc(args: dict, **kwargs) -> str:
    from lexitool import tc_ops
    from docx import Document

    path = _resolve_path(args["path"])
    op = args["op"]
    author = args.get("author")
    type_filter = args.get("type_filter")
    text_pattern = args.get("text_pattern")
    para_range_str = args.get("para_range")
    dry_run = args.get("dry_run", False)
    include_tables = args.get("include_tables", False)

    # Parse paragraph range — accept multiple model-generated formats:
    #   24, "24", "24-24", [24, 24], ["24", "24"], "24,26", [24, 26]
    para_range = _normalize_para_range(para_range_str)

    doc = Document(path)

    if op == "list":
        items = tc_ops.list_tc(doc, author_filter=author,
                               para_range=para_range, type_filter=type_filter,
                               include_tables=include_tables,
                               text_pattern=text_pattern)
        return tool_result({"ok": True, "op": "list", "tc_items": items,
                            "count": len(items)})

    # accept / reject
    if op == "accept":
        stats = tc_ops.accept_all(doc, author_filter=author,
                                  para_range=para_range, type_filter=type_filter,
                                  include_tables=include_tables,
                                  text_pattern=text_pattern)
    else:
        stats = tc_ops.reject_all(doc, author_filter=author,
                                  para_range=para_range, type_filter=type_filter,
                                  include_tables=include_tables,
                                  text_pattern=text_pattern)

    if dry_run:
        return tool_result({"ok": True, "op": op, "dry_run": True, "would_change": stats})

    doc.save(path)
    return tool_result({"ok": True, "op": op, "stats": stats})


# ── 4. lex_format ─────────────────────────────────────────────────────────────

LEX_FORMAT_SCHEMA = {
    "name": "lex_format",
    "description": (
        "Apply formatting to text ranges in a .docx file. Supports format brush "
        "(copy format from one paragraph), direct property application, and style management.\n\n"
        "Format properties: bold, italic, underline, strikethrough, font (name), "
        "size (e.g. '12pt'), color (e.g. '#FF0000'), highlight, spacing, indent, align.\n\n"
        "Style ops: read_styles (list all), create_style, modify_style, delete_style, apply_style."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": ["format", "read_styles", "create_style", "modify_style", "delete_style", "apply_style"],
                "description": "Operation. 'format' = apply format brush/properties (default). Others = style management.",
                "default": "format",
            },
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "target": {
                "type": "string",
                "description": "Range to format: §N, §N:X-Y, §N-M. Same syntax as lex_edit.",
            },
            "source_para": {
                "type": "integer",
                "description": "Copy format from this paragraph (format brush pattern).",
            },
            "properties": {
                "type": "object",
                "description": 'Format to apply: {"font": "宋体", "size": "11.5pt", "bold": false, "align": "justify"}.',
            },
            "style_id": {
                "type": "string",
                "description": "Style ID for create/modify/delete/apply_style operations.",
            },
            "style_name": {
                "type": "string",
                "description": "Display name for the style.",
            },
            "style_type": {
                "type": "string",
                "enum": ["paragraph", "character", "table", "numbering"],
                "description": "Style type. Default: paragraph.",
            },
            "based_on": {
                "type": "string",
                "description": "Base this style on another style ID.",
            },
            "next_style": {
                "type": "string",
                "description": "Next paragraph style (paragraph styles only).",
            },
            "style_props": {
                "type": "object",
                "description": "Run and paragraph properties for the style: {bold, italic, font_size, font_ascii, font_eastAsia, alignment, spacing_before, spacing_after, outline_level, ...}.",
            },
            "paragraphs": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Paragraph indices (1-indexed) to apply the style to.",
            },
        },
        "required": ["path"],
    },
}


def _handle_format(args: dict, **kwargs) -> str:
    from lexitool.markup import parse_target
    from lexitool.edit_ops import _read_docx, _write_docx

    path = _resolve_path(args["path"])

    # ── Style management ops ──
    style_op = args.get("op", "format")

    if style_op == "read_styles":
        from lexitool.openxml_style import read_styles
        styles = read_styles(path)
        result = {}
        for sid, sd in styles.items():
            result[sid] = {
                "name": sd.name,
                "type": sd.type,
                "based_on": sd.based_on,
                "next_style": sd.next_style,
                "linked_style": sd.linked_style,
                "is_default": sd.is_default,
                "is_custom": sd.is_custom,
                "ui_priority": sd.ui_priority,
                "hidden": sd.hidden,
                "run_props": {
                    "bold": sd.run_props.bold,
                    "italic": sd.run_props.italic,
                    "font_size": sd.run_props.font_size,
                    "font_ascii": sd.run_props.font_ascii,
                    "font_eastAsia": sd.run_props.font_eastAsia,
                    "color": sd.run_props.color,
                },
                "para_props": {
                    "alignment": sd.para_props.alignment,
                    "spacing_before": sd.para_props.spacing_before,
                    "spacing_after": sd.para_props.spacing_after,
                    "indent_left": sd.para_props.indent_left,
                    "outline_level": sd.para_props.outline_level,
                },
            }
        return tool_result({"ok": True, "styles": result})

    elif style_op == "create_style":
        from lexitool.openxml_style import create_style, StyleRunProps, StyleParaProps
        style_id = (args.get("style_id") or "")
        style_name = (args.get("style_name") or "")
        if not style_id:
            return tool_error("style_id is required for create_style")
        if not style_name:
            return tool_error("style_name is required for create_style")

        style_props = args.get("style_props", {}) or {}
        rp = StyleRunProps(
            bold=style_props.get("bold"),
            italic=style_props.get("italic"),
            font_size=None if "font_size" not in style_props else float(style_props["font_size"]),
            font_ascii=style_props.get("font_ascii"),
            font_hAnsi=style_props.get("font_hAnsi"),
            font_eastAsia=style_props.get("font_eastAsia"),
            font_cs=style_props.get("font_cs"),
            color=style_props.get("color"),
            highlight=style_props.get("highlight"),
            underline=style_props.get("underline"),
        )
        pp = StyleParaProps(
            alignment=style_props.get("alignment"),
            spacing_before=style_props.get("spacing_before"),
            spacing_after=style_props.get("spacing_after"),
            line_spacing=style_props.get("line_spacing"),
            indent_left=style_props.get("indent_left"),
            indent_right=style_props.get("indent_right"),
            indent_first_line=style_props.get("indent_first_line"),
            outline_level=style_props.get("outline_level"),
        )
        return tool_result(create_style(
            path, style_id, style_name,
            type=args.get("style_type", "paragraph"),
            based_on=args.get("based_on"),
            next_style=args.get("next_style"),
            linked_style=args.get("linked_style"),
            run_props=rp,
            para_props=pp,
        ))

    elif style_op == "modify_style":
        from lexitool.openxml_style import modify_style, StyleRunProps, StyleParaProps
        style_id = (args.get("style_id") or "")
        if not style_id:
            return tool_error("style_id is required for modify_style")

        style_props = args.get("style_props", {}) or {}
        rp = None
        pp = None
        if style_props:
            rp = StyleRunProps(
                bold=style_props.get("bold"),
                italic=style_props.get("italic"),
                font_size=None if "font_size" not in style_props else float(style_props["font_size"]),
                font_ascii=style_props.get("font_ascii"),
                font_hAnsi=style_props.get("font_hAnsi"),
                font_eastAsia=style_props.get("font_eastAsia"),
                font_cs=style_props.get("font_cs"),
                color=style_props.get("color"),
                highlight=style_props.get("highlight"),
                underline=style_props.get("underline"),
            )
            pp = StyleParaProps(
                alignment=style_props.get("alignment"),
                spacing_before=style_props.get("spacing_before"),
                spacing_after=style_props.get("spacing_after"),
                line_spacing=style_props.get("line_spacing"),
                indent_left=style_props.get("indent_left"),
                indent_right=style_props.get("indent_right"),
                indent_first_line=style_props.get("indent_first_line"),
                outline_level=style_props.get("outline_level"),
            )
        return tool_result(modify_style(
            path, style_id,
            name=args.get("style_name"),
            based_on=args.get("based_on"),
            next_style=args.get("next_style"),
            linked_style=args.get("linked_style"),
            run_props=rp,
            para_props=pp,
        ))

    elif style_op == "delete_style":
        from lexitool.openxml_style import delete_style
        style_id = (args.get("style_id") or "")
        if not style_id:
            return tool_error("style_id is required for delete_style")
        return tool_result(delete_style(path, style_id))

    elif style_op == "apply_style":
        from lexitool.openxml_style import apply_style
        style_id = (args.get("style_id") or "")
        if not style_id:
            return tool_error("style_id is required for apply_style")
        paragraphs = args.get("paragraphs", [])
        if not paragraphs and args.get("target"):
            target = parse_target(args["target"])
            if target.para_end:
                paragraphs = list(range(target.para_start - 1, target.para_end))
            else:
                paragraphs = [target.para_start - 1]
        if not paragraphs:
            return tool_error("paragraphs or target is required for apply_style")
        return tool_result(apply_style(path, paragraphs, style_id))

    # ── Format brush / direct properties (default) ──
    target = parse_target(args["target"])
    props = args.get("properties", {})
    source_para = args.get("source_para")

    doc_xml, other = _read_docx(path)
    from lxml import etree
    from copy import deepcopy
    root = etree.fromstring(doc_xml)
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = root.find(f"{W}body")
    paras = [el for el in body if el.tag == f"{W}p"]

    if target.para_end:
        target_indices = list(range(target.para_start - 1, target.para_end))
    else:
        target_indices = [target.para_start - 1]

    for idx in target_indices:
        if idx < 0 or idx >= len(paras):
            continue
        target_para = paras[idx]

        if source_para:
            src_idx = source_para - 1
            if 0 <= src_idx < len(paras):
                _apply_format_brush(paras, target_para, paras[src_idx], W)
        else:
            _apply_format_props(target_para, props, W, target)

    try:
        doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = etree.tostring(root, encoding="UTF-8")

    _write_docx(path, doc_xml_out, other)
    return tool_result({"ok": True, "target": args["target"],
                        "source_para": source_para, "properties": props})


def _apply_format_brush(paras, target_para, src_para, W):
    """Copy paragraph-level and run-level formatting from src_para to target_para.

    Copies:
      - Paragraph properties: indent, spacing, jc (alignment), outlineLvl
      - Run properties: font, size, bold, italic, underline, color
    Skips numPr (numbering) and pStyle (paragraph style) to avoid side effects.
    """
    from copy import deepcopy

    from lxml import etree

    src_pPr = src_para.find(f"{W}pPr")
    target_pPr = target_para.find(f"{W}pPr")
    if target_pPr is None:
        target_pPr = etree.Element(f"{W}pPr")
        target_para.insert(0, target_pPr)

    if src_pPr is not None:
        # Copy individual pPr children (selective, not wholesale)
        for child_tag in [f"{W}ind", f"{W}spacing", f"{W}jc", f"{W}outlineLvl"]:
            src_child = src_pPr.find(child_tag)
            if src_child is not None:
                existing = target_pPr.find(child_tag)
                if existing is not None:
                    target_pPr.replace(existing, deepcopy(src_child))
                else:
                    target_pPr.append(deepcopy(src_child))

    # Copy run-level formatting from first run of source to all runs of target
    src_runs = src_para.findall(f".//{W}r")
    target_runs = target_para.findall(f".//{W}r")
    if src_runs and target_runs:
        src_rPr = src_runs[0].find(f"{W}rPr")
        if src_rPr is not None:
            for t_run in target_runs:
                t_rPr = t_run.find(f"{W}rPr")
                if t_rPr is None:
                    t_rPr = etree.Element(f"{W}rPr")
                    t_run.insert(0, t_rPr)
                # Copy font, size, bold, italic, underline, color
                for child_tag in [f"{W}rFonts", f"{W}sz", f"{W}szCs",
                                  f"{W}b", f"{W}i", f"{W}u",
                                  f"{W}color", f"{W}highlight"]:
                    src_child = src_rPr.find(child_tag)
                    if src_child is not None:
                        existing = t_rPr.find(child_tag)
                        if existing is not None:
                            t_rPr.replace(existing, deepcopy(src_child))
                        else:
                            t_rPr.append(deepcopy(src_child))


def _apply_format_props(para_el, props, W, target):
    """Apply explicit format properties to a paragraph and/or its runs.

    Paragraph-level: spacing, align, indent, outlineLvl
    Run-level (applied to target character range or all runs): bold, italic,
      underline, strikethrough, font (name), size, color, highlight
    """
    from copy import deepcopy
    from lxml import etree
    from lexitool.edit_ops import _parse_indent_twips, _parse_size_half_points

    # ── Paragraph-level properties ──
    pPr = para_el.find(f"{W}pPr")
    if pPr is None:
        pPr = etree.Element(f"{W}pPr")
        para_el.insert(0, pPr)

    if props.get("spacing"):
        sp = pPr.find(f"{W}spacing")
        if sp is None:
            sp = etree.SubElement(pPr, f"{W}spacing")
        sp.set(f"{W}line", str(int(float(props["spacing"]) * 240)))
        sp.set(f"{W}lineRule", "auto")

    if props.get("align"):
        jc = pPr.find(f"{W}jc")
        if jc is None:
            jc = etree.SubElement(pPr, f"{W}jc")
        jc.set(f"{W}val", str(props["align"]))

    if props.get("indent"):
        ind = pPr.find(f"{W}ind")
        if ind is None:
            ind = etree.SubElement(pPr, f"{W}ind")
        ind.set(f"{W}firstLine", _parse_indent_twips(props["indent"]))

    if props.get("style") or props.get("style_id"):
        pStyle = pPr.find(f"{W}pStyle")
        if pStyle is None:
            pStyle = etree.SubElement(pPr, f"{W}pStyle")
        pStyle.set(f"{W}val", str(props.get("style") or props.get("style_id")))

    if props.get("outlineLvl") is not None:
        ol = pPr.find(f"{W}outlineLvl")
        if ol is None:
            ol = etree.SubElement(pPr, f"{W}outlineLvl")
        ol.set(f"{W}val", str(props["outlineLvl"]))

    # ── Run-level properties ──
    run_props = {k: v for k, v in props.items()
                 if k in ("bold", "italic", "underline", "strikethrough",
                          "font", "size", "color", "highlight")}
    if not run_props:
        return

    # Also write run defaults to paragraph mark formatting. This matters for
    # empty paragraphs and for lex_read verification of paragraph-level defaults.
    pPr_rPr = pPr.find(f"{W}rPr")
    if pPr_rPr is None:
        pPr_rPr = etree.SubElement(pPr, f"{W}rPr")
    _apply_run_format_props(pPr_rPr, run_props, W, _parse_size_half_points)

    # Determine target runs
    all_runs = para_el.findall(f".//{W}r")
    if target.char_start is not None and all_runs:
        # Apply to runs within character range (approximate: apply to all runs
        # when range is given — exact char-level targeting needs text splitting
        # which is handled by lex_edit for text changes)
        target_runs = all_runs
    else:
        target_runs = all_runs

    for run_el in target_runs:
        rPr = run_el.find(f"{W}rPr")
        if rPr is None:
            rPr = etree.Element(f"{W}rPr")
            run_el.insert(0, rPr)
        _apply_run_format_props(rPr, run_props, W, _parse_size_half_points)


def _apply_run_format_props(rPr, run_props, W, parse_size_half_points):
    from lxml import etree

    if "bold" in run_props:
        b = rPr.find(f"{W}b")
        if run_props["bold"]:
            if b is None:
                etree.SubElement(rPr, f"{W}b")
        else:
            if b is not None:
                rPr.remove(b)

    if "italic" in run_props:
        i = rPr.find(f"{W}i")
        if run_props["italic"]:
            if i is None:
                etree.SubElement(rPr, f"{W}i")
        else:
            if i is not None:
                rPr.remove(i)

    if "underline" in run_props:
        u = rPr.find(f"{W}u")
        if run_props["underline"]:
            if u is None:
                u = etree.SubElement(rPr, f"{W}u")
            u.set(f"{W}val", "single")
        else:
            if u is not None:
                rPr.remove(u)

    if "strikethrough" in run_props:
        s = rPr.find(f"{W}strike")
        if run_props["strikethrough"]:
            if s is None:
                etree.SubElement(rPr, f"{W}strike")
        else:
            if s is not None:
                rPr.remove(s)

    if "font" in run_props:
        rf = rPr.find(f"{W}rFonts")
        if rf is None:
            rf = etree.SubElement(rPr, f"{W}rFonts")
        font_name = str(run_props["font"])
        rf.set(f"{W}ascii", font_name)
        rf.set(f"{W}hAnsi", font_name)
        rf.set(f"{W}eastAsia", font_name)

    if "size" in run_props:
        sz_half_pt = parse_size_half_points(run_props["size"])
        for sz_tag in [f"{W}sz", f"{W}szCs"]:
            sz_el = rPr.find(sz_tag)
            if sz_el is None:
                sz_el = etree.SubElement(rPr, sz_tag)
            sz_el.set(f"{W}val", sz_half_pt)

    if "color" in run_props:
        c = rPr.find(f"{W}color")
        if c is None:
            c = etree.SubElement(rPr, f"{W}color")
        c.set(f"{W}val", str(run_props["color"]).lstrip("#"))

    if "highlight" in run_props:
        hl = rPr.find(f"{W}highlight")
        if hl is None:
            hl = etree.SubElement(rPr, f"{W}highlight")
        hl.set(f"{W}val", str(run_props["highlight"]))


# ── 5. lex_list ───────────────────────────────────────────────────────────────

LEX_LIST_SCHEMA = {
    "name": "lex_list",
    "description": (
        "Create and manage bullet and numbered lists in a .docx file.\n\n"
        "Bullet styles:\n"
        "  bullet        — ● ○ ■ □ ◇ ◆ ▪ ▸ (9 levels)\n"
        "  bullet_dash   — — – · • ‣ ⁃ (dash/hyphen)\n"
        "  bullet_arrow  — ➤ ► → › » (arrow)\n"
        "  bullet_tick   — ✓ ✔ ☑ ☐ (checkmark)\n\n"
        "Numbered styles:\n"
        "  decimal         — 1. / a) / i. / (1) / (a)\n"
        "  decimal_bracket — 1) / a) / i) / (1)\n"
        "  roman_upper     — I. / A. / 1. / a)\n"
        "  roman_lower     — i. / a. / 1. / a)\n"
        "  letter_upper    — A. / 1. / a. / (1)\n"
        "  letter_lower    — a. / 1. / (a) / (1)\n\n"
        "Chinese styles:\n"
        "  chinese         — 一、/(一)/1./(1)/a. (公文格式, 9 levels)\n"
        "  chinese_article — 第一条/1./(1)/a) (合同条款)\n"
        "  chinese_section — 第一章/第一节/一、 (章节)\n\n"
        "Legal styles:\n"
        "  legal          — 1/1.1/1.1.1/1.1.1.1 (7 levels)\n"
        "  legal_chinese  — 一、/1.1/(1)/a) (Chinese legal hybrid)\n"
        "  legal_article  — Article One/§1.1/(a)/(i)\n\n"
        "Special styles:\n"
        "  circled_decimal — ①②③④\n"
        "  parenthesized   — (1)/(a)/(i)\n"
        "  fullwidth       — １２３４\n\n"
        "Operations:\n"
        "  create      — create new list from paragraphs\n"
        "  list_styles — return all available style names and descriptions\n"
        "  promote     — increase indent level\n"
        "  demote      — decrease indent level\n"
        "  restart     — restart numbering at a given value\n"
        "  remove      — remove numbering from paragraphs"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "op": {
                "type": "string",
                "enum": ["create", "list_styles", "promote", "demote", "restart", "remove"],
                "description": "Operation to perform on the list.",
            },
            "paras": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Paragraph numbers (1-indexed) to operate on.",
            },
            "style": {
                "type": "string",
                "enum": [
                    "bullet", "bullet_dash", "bullet_arrow", "bullet_tick",
                    "decimal", "decimal_bracket",
                    "roman_upper", "roman_lower",
                    "letter_upper", "letter_lower",
                    "chinese", "chinese_article", "chinese_section",
                    "legal", "legal_chinese", "legal_article",
                    "circled_decimal", "parenthesized", "fullwidth",
                ],
                "description": "List style. Required for 'create' operation.",
            },
            "level": {
                "type": "integer",
                "description": "List level (0-based). Default: 0.",
            },
            "start": {
                "type": "integer",
                "description": "Starting number for 'create' (default 1), or restart value for 'restart' operation.",
            },
            "num_id": {
                "type": "integer",
                "description": "Existing numbering instance ID to continue from. When provided with 'create', skips creating new numbering and applies the existing numId to the target paragraphs. Use this to continue an existing auto-numbered sequence instead of starting a new one.",
            },
        },
        "required": ["path", "op"],
    },
}


def _handle_list(args: dict, **kwargs) -> str:
    from lexitool import lists

    path = _resolve_path(args["path"])
    op = args["op"]
    paras = [p - 1 for p in args.get("paras", [])]  # Convert to 0-indexed

    if op == "create":
        if not args.get("style") and not args.get("num_id"):
            return tool_error("'style' is required for create operation (or provide 'num_id' to continue existing numbering)")
        result = lists.create_list(path, paras,
                                    style=args.get("style", "decimal"),
                                    start=args.get("start", 1),
                                    num_id=args.get("num_id"))
    elif op == "list_styles":
        result = lists.list_styles()
    elif op == "promote" and paras:
        result = lists.promote_list_level(path, paras[0])
    elif op == "demote" and paras:
        result = lists.demote_list_level(path, paras[0])
    elif op == "restart" and paras:
        result = lists.restart_numbering(path, paras[0], start_value=args.get("start", 1))
    elif op == "remove" and paras:
        result = lists.remove_numbering(path, paras[0])
    else:
        return tool_error(f"Unknown op '{op}' or missing paragraphs")

    return tool_result(result)


# ── 6. lex_ref ────────────────────────────────────────────────────────────────

LEX_REF_SCHEMA = {
    "name": "lex_ref",
    "description": (
        "Manage bookmarks and cross-references in a .docx file.\n\n"
        "Operations:\n"
        "  add_bookmark    — Define a named bookmark around text\n"
        "  remove_bookmark — Remove a bookmark\n"
        "  add_ref         — Insert a REF field (shows bookmark text)\n"
        "  add_page_ref    — Insert a PAGEREF field (shows page number)\n"
        "  add_noteref     — Insert a NOTEREF field (footnote/endnote reference)\n"
        "  add_styleref    — Insert a STYLEREF field (shows text with a given style)\n"
        "  list            — List all bookmarks in the document\n"
        "  list_fields     — List all field codes (REF, PAGEREF, TOC, etc.) in the document\n"
        "  resolve_fields  — Convert [ref:name]/[page-ref:name] markup in runs to real field codes\n"
        "  scan_xref       — Dry-run scan: find static '第X条' patterns and what they'd link to\n"
        "  auto_xref       — Full conversion: add bookmarks to headings, wrap xref text in hyperlinks\n"
        "  xref_audit      — Comprehensive audit: scan ALL references (第X条/Section/Clause/Article)\n"
        "                     against actual headings, find dead refs and unreferenced clauses\n"
        "  audit_documents  — Run xref_audit on multiple docs + cross_doc_scan. Full project audit\n"
        "  cross_doc_scan   — Multi-document scan: validate 《DocName》第X条 cross-document refs\n"
        "  convert_static_refs — Convert hardcoded 第X条 text to real Word REF fields\n"
        "  term_format_audit — Extract defined terms with run-level formatting (bold/italic/underline/caps/font)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "op": {
                "type": "string",
                "enum": ["add_bookmark", "remove_bookmark", "add_ref", "add_page_ref",
                         "add_noteref", "add_styleref", "list", "list_fields",
                         "resolve_fields", "scan_xref", "auto_xref", "cross_doc_scan",
                         "xref_audit", "audit_documents", "convert_static_refs",
                         "term_format_audit",
                         "add_hyperlink", "remove_hyperlink", "list_hyperlinks",
                         "add_footnote", "add_endnote", "remove_footnote",
                         "remove_endnote", "list_footnotes", "list_endnotes"],
                "description": "Operation to perform.",
            },
            "name": {
                "type": "string",
                "description": "Bookmark name (for add/remove/ref) or style name (for add_styleref).",
            },
            "target_para": {
                "type": "integer",
                "description": "Paragraph number (1-indexed) for bookmark anchor or field insert point.",
            },
            "offset": {
                "type": "integer",
                "description": "Character offset within paragraph for field insertion (default: 0).",
            },
            "docs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of document paths for cross_doc_scan / audit_documents. All docs are scanned for cross-references to each other.",
            },
            "clauses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific clause numbers to convert (for convert_static_refs). If omitted, converts all.",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Preview mode for convert_static_refs — shows what would change without writing.",
            },
        },
        "required": ["path", "op"],
    },
}


def _handle_ref(args: dict, **kwargs) -> str:
    from lexitool import bookmarks, fields

    path = _resolve_path(args["path"])
    op = args["op"]

    if op == "list":
        result = bookmarks.list_bookmarks(path)
        return tool_result(result)

    if op == "list_fields":
        result = fields.list_fields(path)
        return tool_result(result)

    if op == "resolve_fields":
        result = fields.resolve_field_markup(path)
        if result.get("ok"):
            fields.update_fields(path)
        return tool_result(result)

    if op in ("scan_xref", "auto_xref", "cross_doc_scan", "xref_audit",
               "audit_documents", "convert_static_refs"):
        from lexitool import xref
        if op == "scan_xref":
            result = xref.scan_xrefs(path)
        elif op == "cross_doc_scan":
            docs = args.get("docs", [])
            if not docs:
                return tool_error("'docs' is required for cross_doc_scan (list of doc paths)")
            docs = [_resolve_path(d) for d in docs]
            result = xref.cross_doc_scan(docs)
        elif op == "xref_audit":
            result = xref.xref_audit(path)
        elif op == "audit_documents":
            docs = args.get("docs", [])
            if not docs:
                return tool_error("'docs' is required for audit_documents (list of doc paths)")
            docs = [_resolve_path(d) for d in docs]
            result = xref.audit_documents(docs)
        elif op == "convert_static_refs":
            clauses = args.get("clauses")
            dry_run = args.get("dry_run", True)
            result = xref.convert_static_refs(path, clauses=clauses, dry_run=dry_run)
        else:
            result = xref.auto_xref(path)
        return tool_result(result)

    if op == "term_format_audit":
        from lexitool.defined_terms import term_format_audit
        from docx import Document
        doc = Document(path)
        result = term_format_audit(doc)
        return tool_result(result)

    name = args.get("name")
    if not name:
        return tool_error("'name' is required for this operation")

    target_para = args.get("target_para", 1)
    offset = args.get("offset", 0)

    if op == "add_hyperlink":
        from lexitool.openxml_hyperlink import add_hyperlink
        result = add_hyperlink(path, target_para, name,
                               url=args.get("url"),
                               anchor=args.get("anchor"),
                               tooltip=args.get("tooltip"))
        return tool_result(result)

    elif op == "remove_hyperlink":
        from lexitool.openxml_hyperlink import remove_hyperlink
        result = remove_hyperlink(path, target_para, name)
        return tool_result(result)

    elif op == "list_hyperlinks":
        from lexitool.openxml_hyperlink import list_hyperlinks
        result = list_hyperlinks(path)
        return tool_result(result)

    elif op == "add_footnote":
        from lexitool.openxml_footnotes import add_footnote
        result = add_footnote(path, target_para,
                              text=args.get("text", ""),
                              offset=args.get("offset", -1))
        return tool_result(result)

    elif op == "add_endnote":
        from lexitool.openxml_footnotes import add_endnote
        result = add_endnote(path, target_para,
                              text=args.get("text", ""),
                              offset=args.get("offset", -1))
        return tool_result(result)

    elif op == "remove_footnote":
        from lexitool.openxml_footnotes import remove_footnote
        note_id = args.get("note_id")
        if note_id is None:
            return tool_error("'note_id' is required for remove_footnote")
        result = remove_footnote(path, int(note_id))
        return tool_result(result)

    elif op == "remove_endnote":
        from lexitool.openxml_footnotes import remove_endnote
        note_id = args.get("note_id")
        if note_id is None:
            return tool_error("'note_id' is required for remove_endnote")
        result = remove_endnote(path, int(note_id))
        return tool_result(result)

    elif op == "list_footnotes":
        from lexitool.openxml_footnotes import list_footnotes
        result = list_footnotes(path)
        return tool_result(result)

    elif op == "list_endnotes":
        from lexitool.openxml_footnotes import list_endnotes
        result = list_endnotes(path)
        return tool_result(result)

    elif op == "add_bookmark":
        result = bookmarks.add_bookmark(path, target_para - 1, name)
    elif op == "remove_bookmark":
        result = bookmarks.remove_bookmark(path, name)
    elif op == "add_ref":
        result = fields.insert_field(path, target_para - 1, offset, "REF", name, f"[{name}]")
    elif op == "add_page_ref":
        result = fields.insert_field(path, target_para - 1, offset, "PAGEREF", name, f"[p.{name}]")
    elif op == "add_noteref":
        result = fields.insert_field(path, target_para - 1, offset, "NOTEREF", name, f"[fn.{name}]")
    elif op == "add_styleref":
        # STYLEREF accepts quoted style names like "Heading 1"
        instr = f'"{name}"' if " " in name else name
        result = fields.insert_field(path, target_para - 1, offset, "STYLEREF", instr, f"[{name}]")
    else:
        return tool_error(f"Unknown op: {op}")

    return tool_result(result)


# ── 7. lex_section ────────────────────────────────────────────────────────────

LEX_SECTION_SCHEMA = {
    "name": "lex_section",
    "description": (
        "Manage page layout, breaks, margins, columns, and orientation.\n\n"
        "Breaks: page, column, section_next (next page), section_continuous (same page).\n"
        "Page sizes: A4, A3, A5, Letter, Legal, Tabloid, B5, Executive.\n"
        "Margins: {'top': '2.54cm', 'bottom': '2.54cm', 'left': '3.18cm', 'right': '3.18cm'}.\n"
        "Orientation: portrait or landscape.\n\n"
        "Section ops: add_section_break inserts a proper OOXML section break after\n"
        "a paragraph, preserving layout from the previous section. remove_section_break\n"
        "merges two sections. page_setup sets page size and margins in one call.\n"
        "read_sections returns a list of all sections with their properties."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "op": {
                "type": "string",
                "enum": [
                    "add_break", "set_margins", "set_orientation",
                    "add_section_break", "remove_section_break",
                    "page_setup", "read_sections",
                ],
                "description": "Operation to perform.",
            },
            "at_para": {
                "type": "integer",
                "description": "Paragraph number (1-indexed) for break placement.",
            },
            "type": {
                "type": "string",
                "enum": ["page", "column", "section_next", "section_continuous", "nextPage", "continuous", "evenPage", "oddPage"],
                "description": "Break type. Required for add_break / add_section_break.",
            },
            "margins": {
                "type": "object",
                "description": 'Margin values: {"top": "2.54cm", "bottom": "2.54cm", "left": "3.18cm", "right": "3.18cm"}.',
            },
            "orientation": {
                "type": "string",
                "enum": ["portrait", "landscape"],
            },
            "page_size": {
                "type": "string",
                "description": "Named page size: A4, A3, A5, Letter, Legal, Tabloid, B5, Executive.",
            },
            "section_index": {
                "type": "integer",
                "description": "0-indexed section number. -1 = last section. Used by remove_section_break and page_setup.",
            },
        },
        "required": ["path", "op"],
    },
}


def _handle_section(args: dict, **kwargs) -> str:
    from lexitool.edit_ops import _read_docx, _write_docx
    from lxml import etree

    path = _resolve_path(args["path"])
    op = args["op"]
    W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    W = f"{{{W_NS}}}"

    # ── New ops using openxml_section ──
    if op == "read_sections":
        from lexitool.openxml_section import read_sections

        sections = read_sections(path)
        result = []
        for s in sections:
            ps = s.page_setup
            result.append({
                "index": s.index,
                "start_para": s.start_para,
                "end_para": s.end_para,
                "page_width": ps.page_width,
                "page_height": ps.page_height,
                "orientation": ps.orientation,
                "margin_top": ps.margin_top,
                "margin_bottom": ps.margin_bottom,
                "margin_left": ps.margin_left,
                "margin_right": ps.margin_right,
                "margin_gutter": ps.margin_gutter,
                "margin_header": ps.margin_header,
                "margin_footer": ps.margin_footer,
                "header_refs": s.header_refs,
                "footer_refs": s.footer_refs,
            })
        return tool_result({"ok": True, "sections": result})

    elif op == "add_section_break":
        from lexitool.openxml_section import add_section_break, PageSetup, _parse_dim

        after_para = args.get("at_para", 1)
        break_type_map = {
            "section_next": "nextPage", "section_continuous": "continuous",
            "nextPage": "nextPage", "continuous": "continuous",
            "evenPage": "evenPage", "oddPage": "oddPage",
        }
        break_type = break_type_map.get(args.get("type", "nextPage"), "nextPage")

        ps = None
        if args.get("page_size") or args.get("orientation") or args.get("margins"):
            kwargs_ps = {}
            page_size = args.get("page_size")
            if page_size:
                orient = args.get("orientation", "portrait")
                kwargs_ps["page_width"], kwargs_ps["page_height"] = PageSetup.from_preset(page_size, orient).page_width, PageSetup.from_preset(page_size, orient).page_height
                kwargs_ps["orientation"] = orient
            orientation = args.get("orientation")
            if orientation and not page_size:
                kwargs_ps["orientation"] = orientation
            margins = args.get("margins")
            if margins:
                for key, attr in [("top", "margin_top"), ("bottom", "margin_bottom"),
                                   ("left", "margin_left"), ("right", "margin_right")]:
                    if key in margins:
                        kwargs_ps[attr] = _parse_dim(str(margins[key]))
            ps = PageSetup(**kwargs_ps) if kwargs_ps else None

        return tool_result(add_section_break(path, after_para, break_type, page_setup=ps))

    elif op == "remove_section_break":
        from lexitool.openxml_section import remove_section_break

        section_index = args.get("section_index", 0)
        return tool_result(remove_section_break(path, section_index))

    elif op == "page_setup":
        from lexitool.openxml_section import apply_page_setup, PageSetup, _parse_dim

        section_index = args.get("section_index", -1)
        ps = PageSetup()

        page_size = args.get("page_size")
        if page_size:
            orient = args.get("orientation", "portrait")
            preset = PageSetup.from_preset(page_size, orient)
            ps = ps.merge_into(preset)

        orientation = args.get("orientation")
        if orientation and not page_size:
            ps.orientation = orientation

        margins = args.get("margins")
        if margins:
            for key, attr in [("top", "margin_top"), ("bottom", "margin_bottom"),
                               ("left", "margin_left"), ("right", "margin_right"),
                               ("gutter", "margin_gutter"), ("header", "margin_header"),
                               ("footer", "margin_footer")]:
                if key in margins:
                    setattr(ps, attr, _parse_dim(str(margins[key])))

        return tool_result(apply_page_setup(path, ps, section_index=section_index))

    # ── Legacy ops (backward compatible) ──
    doc_xml, other = _read_docx(path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    if op == "add_break":
        break_type = args.get("type", "page")
        at_para = args.get("at_para", 1) - 1
        paras = [el for el in body if el.tag == f"{W}p"]

        if at_para < 0 or at_para >= len(paras):
            return tool_error(f"Paragraph {at_para + 1} out of range")

        para_el = paras[at_para]

        if break_type in ("page", "column"):
            r = etree.SubElement(para_el, f"{W}r")
            br = etree.SubElement(r, f"{W}br")
            br.set(f"{W}type", break_type)
        elif break_type in ("section_next", "section_continuous"):
            pPr = para_el.find(f"{W}pPr")
            if pPr is None:
                pPr = etree.Element(f"{W}pPr")
                para_el.insert(0, pPr)
            sectPr = etree.SubElement(pPr, f"{W}sectPr")
            sect_type = "nextPage" if break_type == "section_next" else "continuous"
            tp = etree.SubElement(sectPr, f"{W}type")
            tp.set(f"{W}val", sect_type)

    elif op == "set_margins" and args.get("margins"):
        margins = args["margins"]
        from lexitool.openxml_section import _parse_dim

        sectPr = body.find(f"{W}sectPr")
        if sectPr is None:
            paras = [el for el in body if el.tag == f"{W}p"]
            if paras:
                pPr = paras[-1].find(f"{W}pPr")
                if pPr is None:
                    pPr = etree.Element(f"{W}pPr")
                    paras[-1].insert(0, pPr)
                sectPr = etree.SubElement(pPr, f"{W}sectPr")

        if sectPr is not None:
            pgMar = sectPr.find(f"{W}pgMar")
            if pgMar is None:
                pgMar = etree.Element(f"{W}pgMar")
                sectPr.insert(0, pgMar)
            for key in ("top", "bottom", "left", "right"):
                if key in margins:
                    val = margins[key]
                    if isinstance(val, str):
                        val = _parse_dim(val)
                    else:
                        val = int(val)
                    pgMar.set(f"{W}{key}", str(val))

    elif op == "set_orientation" and args.get("orientation"):
        orientation = args["orientation"]
        from lexitool.openxml_section import PAGE_SIZES

        sectPr = body.find(f"{W}sectPr")
        if sectPr is None:
            paras = [el for el in body if el.tag == f"{W}p"]
            if paras:
                pPr = paras[-1].find(f"{W}pPr")
                if pPr is None:
                    pPr = etree.Element(f"{W}pPr")
                    paras[-1].insert(0, pPr)
                sectPr = etree.SubElement(pPr, f"{W}sectPr")

        if sectPr is not None:
            pgSz = sectPr.find(f"{W}pgSz")
            if pgSz is None:
                pgSz = etree.SubElement(sectPr, f"{W}pgSz")
            # Preserve existing page size and swap dimensions
            existing_w = pgSz.get(f"{W}w")
            existing_h = pgSz.get(f"{W}h")
            if orientation == "landscape":
                pgSz.set(f"{W}orient", "landscape")
                if existing_w and existing_h:
                    pgSz.set(f"{W}w", existing_h)
                    pgSz.set(f"{W}h", existing_w)
                else:
                    pgSz.set(f"{W}w", "16838")
                    pgSz.set(f"{W}h", "11906")
            else:
                pgSz.set(f"{W}orient", "portrait")
                if existing_w and existing_h:
                    pgSz.set(f"{W}w", existing_h)
                    pgSz.set(f"{W}h", existing_w)
                else:
                    pgSz.set(f"{W}w", "11906")
                    pgSz.set(f"{W}h", "16838")

    try:
        doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = etree.tostring(root, encoding="UTF-8")

    _write_docx(path, doc_xml_out, other)
    return tool_result({"ok": True, "op": op})


# ── 8. lex_doc ────────────────────────────────────────────────────────────────

LEX_DOC_SCHEMA = {
    "name": "lex_doc",
    "description": (
        "Document-level operations: create new .docx, clean metadata/TC, "
        "update table of contents, update fields, and merge documents."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": ["create", "clean", "update_toc", "update_fields",
                         "add_watermark", "remove_watermark", "list_watermarks",
                         "merge", "split",
                         "set_property", "get_properties", "remove_property"],
                "description": "Operation to perform.",
            },
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "template": {
                "type": "string",
                "description": "Template .docx path for create operation.",
            },
            "output": {
                "type": "string",
                "description": "Output path (defaults to overwriting path).",
            },
            "metadata": {
                "type": "object",
                "description": 'Document metadata: {"title": "...", "author": "...", "case_no": "..."}.',
            },
            "watermark_text": {
                "type": "string",
                "description": "Watermark text (e.g. DRAFT, CONFIDENTIAL).",
            },
            "watermark_layout": {
                "type": "string",
                "enum": ["diagonal", "horizontal"],
                "description": "Watermark layout. Default: diagonal.",
            },
        },
        "required": ["op"],
    },
}


def _handle_doc(args: dict, **kwargs) -> str:
    op = args["op"]

    if op == "create":
        from lexitool.doc_create import create_document
        path = _resolve_path(args.get("output", args.get("path", "/tmp/out.docx")))
        meta = args.get("metadata", {})
        result = create_document(
            output=path,
            title=meta.get("title", "Untitled"),
            font_size=meta.get("font_size", 11.0),
        )
        return tool_result(result)

    elif op == "clean":
        from docx import Document
        from lexitool.cleanup import cleanup_all
        path = _resolve_path(args["path"])
        doc = Document(str(path))
        result = cleanup_all(doc, as_tc_del=True)
        doc.save(str(path))
        return tool_result({"ok": True, "path": path, "result": result})

    elif op == "update_toc":
        from lexitool.toc_ops import toc_generate
        path = _resolve_path(args["path"])
        result = toc_generate(docx_path=path)
        return tool_result(result)

    elif op == "update_fields":
        from lexitool.fields import update_fields
        path = _resolve_path(args["path"])
        result = update_fields(doc_path=path)
        return tool_result(result)

    elif op == "add_watermark":
        from lexitool.openxml_watermark import add_watermark
        path = _resolve_path(args["path"])
        return tool_result(add_watermark(
            path,
            text=args.get("watermark_text", "DRAFT"),
            layout=args.get("watermark_layout", "diagonal"),
        ))

    elif op == "remove_watermark":
        from lexitool.openxml_watermark import remove_watermark
        path = _resolve_path(args["path"])
        return tool_result(remove_watermark(path))

    elif op == "list_watermarks":
        from lexitool.openxml_watermark import list_watermarks
        path = _resolve_path(args["path"])
        return tool_result(list_watermarks(path))

    elif op == "merge":
        from lexitool.openxml_merge import merge_documents
        path = _resolve_path(args["path"])
        insert_path = args.get("insert_path", "")
        if not insert_path:
            return tool_error("'insert_path' is required for merge")
        return tool_result(merge_documents(
            path, insert_path,
            after_para=args.get("after_para", 0),
        ))

    elif op == "split":
        from lexitool.openxml_merge import split_document
        path = _resolve_path(args["path"])
        para_start = args.get("para_start", 1)
        para_end = args.get("para_end", 1)
        output = args.get("output", "")
        if not output:
            return tool_error("'output' is required for split")
        return tool_result(split_document(
            path, para_start, para_end, output=output,
        ))

    elif op == "set_property":
        from lexitool.openxml_custom_props import set_custom_property
        path = _resolve_path(args["path"])
        prop_name = args.get("prop_name", "")
        prop_value = args.get("prop_value", "")
        if not prop_name:
            return tool_error("'prop_name' is required for set_property")
        return tool_result(set_custom_property(
            path, prop_name, prop_value,
        ))

    elif op == "get_properties":
        from lexitool.openxml_custom_props import get_custom_properties
        path = _resolve_path(args["path"])
        return tool_result(get_custom_properties(path))

    elif op == "remove_property":
        from lexitool.openxml_custom_props import remove_custom_property
        path = _resolve_path(args["path"])
        prop_name = args.get("prop_name", "")
        if not prop_name:
            return tool_error("'prop_name' is required for remove_property")
        return tool_result(remove_custom_property(path, prop_name))

    return tool_error(f"Unknown op: {op}")


# ── lex_clause ─────────────────────────────────────────────────────────────────

LEX_CLAUSE_SCHEMA = {
    "name": "lex_clause",
    "description": (
        "Split a Word document into semantic sections or clauses, extract "
        "paragraph ranges into standalone snippet .docx files, insert snippets "
        "into a target document with numbering adjustment, and compare two "
        "sections for compatibility.\n\n"
        "Ops:\n"
        "  split  — Detect clause boundaries and return metadata\n"
        "  extract — Extract a paragraph range into a standalone .docx snippet\n"
        "  insert — Insert source document content into target, optionally "
        "stripping numbering\n"
        "  compare — Compare two clause ranges for compatibility\n"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to .docx file (target for insert, source for split/extract/compare).",
            },
            "op": {
                "type": "string",
                "enum": ["split", "extract", "insert", "compare"],
                "description": "Operation to perform.",
            },
            "method": {
                "type": "string",
                "enum": ["auto", "heading", "numbering", "flat"],
                "description": "Clause detection method (for split). Default: auto.",
            },
            "para_start": {
                "type": "integer",
                "description": "First paragraph to extract/compare (1-indexed).",
            },
            "para_end": {
                "type": "integer",
                "description": "Last paragraph to extract/compare (1-indexed, inclusive).",
            },
            "source_path": {
                "type": "string",
                "description": "Path to source .docx (for insert: the snippet to insert from).",
            },
            "output_path": {
                "type": "string",
                "description": "Output path for extracted snippet .docx (for extract).",
            },
            "insert_after_para": {
                "type": "integer",
                "description": "Insert after this paragraph number (1-indexed, for insert).",
            },
            "adjust_numbering": {
                "type": "boolean",
                "description": "Strip numbering from inserted paragraphs (default: true).",
            },
            "clause_a": {
                "type": "object",
                "description": "First clause spec for compare: {para_start, para_end, title}.",
            },
            "clause_b": {
                "type": "object",
                "description": "Second clause spec for compare: {para_start, para_end, title}.",
            },
        },
        "required": ["path", "op"],
    },
}


def _handle_clause(args: dict, **kwargs) -> str:
    op = args["op"]
    path = _resolve_path(args["path"])

    if op == "split":
        from lexitool.clause_ops import list_clauses
        method = args.get("method", "auto")
        clauses = list_clauses(path)
        # Override method in split if specified
        if method != "auto":
            from lexitool.clause_ops import split_clauses
            clauses_raw = split_clauses(path, method=method)
            clauses = [
                {
                    "id": c.id, "title": c.title,
                    "para_start": c.para_start, "para_end": c.para_end,
                    "level": c.level, "type": c.clause_type,
                    "key_terms": c.key_terms[:10], "detection": c.detection,
                }
                for c in clauses_raw
            ]
        return tool_result({"ok": True, "clauses": clauses, "count": len(clauses)})

    elif op == "extract":
        from lexitool.clause_ops import extract_clause
        para_start = args.get("para_start")
        para_end = args.get("para_end")
        if para_start is None or para_end is None:
            return tool_error("extract requires para_start and para_end")
        output_path = args.get("output_path", f"/tmp/extracted_clause_{para_start}_{para_end}.docx")
        output_path = _resolve_path(output_path)
        result = extract_clause(path, para_start, para_end, output_path)
        return tool_result({"ok": True, "output_path": result, "para_start": para_start, "para_end": para_end})

    elif op == "insert":
        from lexitool.clause_ops import insert_clause
        source_path = args.get("source_path")
        if not source_path:
            return tool_error("insert requires source_path")
        source_path = _resolve_path(source_path)
        insert_after = args.get("insert_after_para")
        if insert_after is None:
            return tool_error("insert requires insert_after_para")
        adjust = args.get("adjust_numbering", True)
        result = insert_clause(path, source_path, insert_after, adjust)
        return tool_result({"ok": True, "path": result, "inserted_after": insert_after})

    elif op == "compare":
        from lexitool.clause_ops import compare_clauses, CompareResult, Clause
        clause_a = args.get("clause_a", {})
        clause_b = args.get("clause_b", {})
        if not clause_a or not clause_b:
            return tool_error("compare requires clause_a and clause_b with para_start/para_end/title")
        # We only have one path for compare — the target document
        # For cross-document compare, use path and source_path
        source_path = args.get("source_path")
        doc_b = _resolve_path(source_path) if source_path else path
        result = compare_clauses(path, clause_a, doc_b, clause_b)
        return tool_result({
            "compatible": result.compatible,
            "issues": result.issues,
            "info": result.info,
        })

    return tool_error(f"Unknown op: {op}")


# ── lex_corpus ─────────────────────────────────────────────────────────────────

LEX_CORPUS_SCHEMA = {
    "name": "lex_corpus",
    "description": (
        "Index and search across multiple .docx files in a directory. "
        "Builds a searchable corpus of document sections with type "
        "classification and key term extraction.\n\n"
        "Ops:\n"
        "  index  — Recursively scan directory for .docx files, split each into "
        "sections, build inverted index\n"
        "  search — Search indexed corpus by query text, section type, or "
        "specific key terms\n"
        "  status — Show corpus metadata (document count, section count, last "
        "indexed timestamp)\n"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": ["index", "search", "status"],
                "description": "Corpus operation to perform.",
            },
            "dir_path": {
                "type": "string",
                "description": "Root directory containing .docx files.",
            },
            "query": {
                "type": "string",
                "description": "Free-text search query (for search op).",
            },
            "clause_type": {
                "type": "string",
                "description": "Filter by clause type: definitions, representations, covenants, conditions, events_of_default, governing_law, indemnity, miscellaneous, parties, background, operative, term_termination.",
            },
            "terms": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter by specific defined terms (exact match).",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum search results (default: 20).",
            },
            "pattern": {
                "type": "string",
                "description": "File glob pattern for index (default: *.docx).",
            },
        },
        "required": ["op", "dir_path"],
    },
}


def _handle_corpus(args: dict, **kwargs) -> str:
    op = args["op"]
    dir_path = _resolve_path(args["dir_path"])

    if op == "index":
        from lexitool.corpus import index_dir
        pattern = args.get("pattern", "*.docx")
        result = index_dir(dir_path, pattern=pattern)
        return tool_result(result)

    elif op == "search":
        from lexitool.corpus import search_corpus
        result = search_corpus(
            dir_path,
            query=args.get("query"),
            clause_type=args.get("clause_type"),
            terms=args.get("terms"),
            limit=args.get("limit", 20),
        )
        return tool_result(result)

    elif op == "status":
        from lexitool.corpus import corpus_status
        result = corpus_status(dir_path)
        return tool_result(result)

    return tool_error(f"Unknown op: {op}")


# ── lex_ocr schema & handler ─────────────────────────────────────────────────

LEX_OCR_SCHEMA = {
    "name": "lex_ocr",
    "description": (
        "Read a PDF file and return its full text as markdown. This is the "
        "PRIMARY tool for ALL PDF reading — never use `exec` with parse_pdf "
        "or any other PDF library. Uses MinerU OCR engine.\n\n"
        "Supports both the free Agent API (no key needed, lower quality) and "
        "the Precision API (set MINERU_API_KEY env var for best quality with "
        "VLM-based recognition). Use this for legal documents, scanned "
        "contracts, and any PDF — whether text-based or scanned, extractable "
        "or image-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute or relative path to the PDF file.",
            },
            "language": {
                "type": "string",
                "description": "Document language code ('ch' for Chinese, 'en' for English, etc.). Default: 'ch'.",
            },
            "page_range": {
                "type": "string",
                "description": "Page range to process, e.g. '1-5' or '1,3,5-7'. Omit for all pages.",
            },
            "model_version": {
                "type": "string",
                "description": "Precision API model version: 'vlm' (best quality, default), 'pipeline', or 'MinerU-HTML'. Only used when MINERU_API_KEY is set.",
            },
        },
        "required": ["file_path"],
    },
}


def _handle_ocr(args: dict, **kwargs) -> str:
    from lexitool.ocr import parse_pdf

    file_path = _resolve_path(args["file_path"])
    language = args.get("language", "ch")
    page_range = args.get("page_range")
    model_version = args.get("model_version", "vlm")

    result = parse_pdf(
        file_path,
        language=language,
        page_range=page_range,
        model_version=model_version,
        prefer_precise=True,
    )

    if result.get("ok"):
        return tool_result(result)
    return tool_error(result.get("error", "OCR failed"))


# ── lex_project_init schema & handler ────────────────────────────────────────

LEX_PROJECT_INIT_SCHEMA = {
    "name": "lex_project_init",
    "description": (
        "Initialise a new legal project by scanning a directory for .docx and "
        ".pdf files, extracting all content (PDF via OCR), detecting key "
        "entities (parties, dates, amounts, law citations), and building a "
        "comprehensive project context. Creates the full project scaffold with "
        "enriched project-context.md, AGENTS.md, STANDARDS.md, and role files.\n\n"
        "Use this at the start of any legal project to pre-load all case "
        "documents into the project knowledge base. After init, use hermes chat "
        "from the project directory to activate HPSwarm multi-agent mode."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "dir_path": {
                "type": "string",
                "description": "Path to the directory containing source documents (.docx, .pdf).",
            },
            "project_name": {
                "type": "string",
                "description": "Short project identifier, used as the project directory name (e.g. 'case-2024-001').",
            },
            "client_name": {
                "type": "string",
                "description": "Client or organisation name.",
            },
            "goal": {
                "type": "string",
                "description": "What the project aims to accomplish (e.g. 'Draft defence statement for copyright infringement case').",
            },
            "language": {
                "type": "string",
                "description": "Document language for OCR. Default: 'ch'.",
            },
            "recursive": {
                "type": "boolean",
                "description": "Whether to scan subdirectories recursively. Default: false.",
            },
            "management_dir": {
                "type": "string",
                "description": "Where to create bootstrap files. Default: /workspace/<project_name>/. Separate from dir_path which is where documents live.",
            },
            "in_place": {
                "type": "boolean",
                "description": "If true, bootstrap files go into dir_path itself (management_dir == dir_path). Use when the working directory IS the project root.",
            },
        },
        "required": ["dir_path", "project_name", "client_name", "goal"],
    },
}


def _handle_project_init(args: dict, **kwargs) -> str:
    from lexitool.project_init import scan_and_init_project

    dir_path = _resolve_path(args["dir_path"])
    project_name = args["project_name"]
    client_name = args["client_name"]
    goal = args["goal"]
    language = args.get("language", "ch")
    recursive = args.get("recursive", False)
    management_dir = args.get("management_dir")
    in_place = args.get("in_place", False)

    result = scan_and_init_project(
        dir_path=dir_path,
        project_name=project_name,
        client_name=client_name,
        goal=goal,
        language=language,
        recursive=recursive,
        management_dir=management_dir,
        in_place=in_place,
    )

    if result.get("ok"):
        return tool_result(result)
    return tool_error(result.get("error", "Project init failed"))


# ── 13. lex_diff ───────────────────────────────────────────────────────────────

LEX_DIFF_SCHEMA = {
    "name": "lex_diff",
    "description": (
        "Compare two .docx files. Use mode='summary' first when the agent needs "
        "to understand legal revision intent; use mode='redline' to produce a "
        "tracked-changes Word/PDF redline via quicompare.\n\n"
        "summary returns paragraph-level before/after changes, legal category "
        "tags, revised tracked-change stats, and recommended lex_read follow-ups. "
        "redline produces a Word .docx with all insertions, deletions, and moves tracked.\n"
        "Optionally also generates a PDF redline.\n\n"
        "Typical use: compare contract v1 vs v2, or compare final vs draft."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "original": {
                "type": "string",
                "description": "Path to the original (older) .docx file.",
            },
            "revised": {
                "type": "string",
                "description": "Path to the revised (newer) .docx file.",
            },
            "mode": {
                "type": "string",
                "enum": ["summary", "redline"],
                "description": "summary=readable JSON review summary; redline=generate redline document. Default: summary.",
            },
            "output_dir": {
                "type": "string",
                "description": "Directory for output files (default: same folder as revised).",
            },
            "author": {
                "type": "string",
                "description": "Author name shown in tracked changes (default: from config).",
            },
            "pdf": {
                "type": "boolean",
                "description": "Also generate a PDF redline (default: false).",
            },
            "granularity": {
                "type": "string",
                "enum": ["char", "word"],
                "description": "Track changes at character or word level (default: char).",
            },
            "limit": {
                "type": "integer",
                "description": "Max changed blocks to return for mode='summary'. Default: 80.",
            },
        },
        "required": ["original", "revised"],
    },
}


def _handle_diff(args: dict, **kwargs) -> str:
    from lexitool import diff as _diff

    original = _resolve_path(args["original"])
    revised = _resolve_path(args["revised"])
    mode = str(args.get("mode") or "summary").lower()

    if mode == "summary":
        result = _diff.summary(
            original=original,
            revised=revised,
            limit=int(args.get("limit", 80)),
        )
        return tool_result(result) if result.get("ok") else tool_error(result.get("error", "diff summary failed"))

    if not _diff.is_available():
        return tool_error(
            "quicompare is not available in this environment. "
            "Install it: cd /root/.hermes/tools/lex-workspace/tools/quicompare && bash install.sh",
            success=False,
        )

    result = _diff.redline(
        original=original,
        revised=revised,
        output_dir=_resolve_path(args.get("output_dir")) if args.get("output_dir") else None,
        author=args.get("author"),
        pdf=args.get("pdf", False),
        granularity=args.get("granularity", "char"),
    )
    return tool_result(result)


# ── 13b. lex_xref_audit ────────────────────────────────────────────────────────

LEX_XREF_AUDIT_SCHEMA = {
    "name": "lex_xref_audit",
    "description": (
        "Audit all cross-references in a .docx file against actual clause headings. "
        "Finds every clause reference (Chinese 第X条 and English Section/Clause/Article) "
        "and checks whether the referenced clause exists. Reports dead links (references "
        "to non-existing clauses) and unreferenced clauses (clauses that exist but are "
        "never referenced).\n\n"
        "Use this BEFORE delivery to catch broken cross-references that would confuse "
        "readers or create legal ambiguity. This is a READ-ONLY audit — it does not "
        "modify the document."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_path": {
                "type": "string",
                "description": "Path to the .docx file to audit.",
            },
        },
        "required": ["doc_path"],
    },
}


def _handle_xref_audit(args: dict, **kwargs) -> str:
    from lexitool.xref import xref_audit

    path = _resolve_path(args["doc_path"])
    result = xref_audit(path)
    return tool_result(result)


# ── 14. lex_deliver ────────────────────────────────────────────────────────────

LEX_DELIVER_SCHEMA = {
    "name": "lex_deliver",
    "description": (
        "Package project deliverables into a timestamped delivery folder.\n"
        "Collects all .docx files, runs cross-reference verification, and\n"
        "generates a delivery manifest.\n\n"
        "Typical use: at project completion, run lex_deliver to produce a\n"
        "delivery bundle ready for client handoff."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Path to the project root (containing .hermes-project/).",
            },
            "output_dir": {
                "type": "string",
                "description": "Where to create the delivery folder (default: project_dir/delivery/).",
            },
            "author": {
                "type": "string",
                "description": "Author name for tracked changes in comparison redlines.",
            },
            "require_gates": {
                "type": "boolean",
                "description": "If true, runs lex_gate_check(strict=true) before packaging. Delivery is blocked if any gates fail. Default: true (gates REQUIRED for delivery).",
            },
            "git_tag": {
                "type": "boolean",
                "description": "If true, creates a git deliver tag after successful packaging. Requires git repo in project_dir. Default: true.",
            },
        },
        "required": ["project_dir"],
    },
}


def _handle_deliver(args: dict, **kwargs) -> str:
    from lexitool import deliver as _deliver

    project_dir = _resolve_path(args["project_dir"])
    result = _deliver.package(
        project_dir=project_dir,
        output_dir=_resolve_path(args.get("output_dir")) if args.get("output_dir") else None,
        author=args.get("author"),
        require_gates=args.get("require_gates", True),
        git_tag=args.get("git_tag", True),
    )
    return tool_result(result)


LEX_GATE_CHECK_SCHEMA = {
    "name": "lex_gate_check",
    "description": (
        "Run quality gate checks against a legal project before delivery.\n"
        "Validates all 7 HPSwarm quality gates: structure, content review,\n"
        "format review, TS consistency, cross-references, translation, and\n"
        "final readiness. Reads reviewer reports from .hermes-project/reviews/\n"
        "and runs automated checks.\n\n"
        "Use this before calling lex_deliver to ensure all gates pass.\n"
        "In strict mode, missing reports are treated as failures."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Path to the project root (containing .hermes-project/).",
            },
            "gates": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1, "maximum": 7},
                "description": "Specific gates to check (1-7). Default: all.",
            },
            "strict": {
                "type": "boolean",
                "description": "Treat missing/pending reports as failures. Default: false.",
            },
        },
        "required": ["project_dir"],
    },
}


def _handle_gate_check(args: dict, **kwargs) -> str:
    from lexitool import gate_check as _gc

    project_dir = _resolve_path(args["project_dir"])
    result = _gc.gate_check(
        project_dir=project_dir,
        gates=args.get("gates"),
        strict=args.get("strict", False),
    )
    return tool_result(result)


# ── 15b. lex_git ──────────────────────────────────────────────────────────────

LEX_GIT_SCHEMA = {
    "name": "lex_git",
    "description": (
        "Version-control a legal project directory with a constrained git "
        "interface. Treat this as the native legal matter version-control tool, "
        "not a coding-only utility. Use it before/after material document edits: "
        "initialize a project repo, inspect status/logs, snapshot drafting "
        "milestones, tag deliveries, and create/list/remove worktrees for "
        "parallel reviewers or alternative drafting approaches. Use worktrees "
        "when exploring competing legal positions so the main working draft is "
        "not overwritten. This tool does NOT expose arbitrary git args."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Path to the legal project root directory.",
            },
            "action": {
                "type": "string",
                "enum": [
                    "ensure_repo", "status", "snapshot", "log",
                    "deliver_tag", "revision_branch",
                    "create_worktree", "list_worktrees",
                    "remove_worktree", "prune_worktrees",
                ],
                "description": (
                    "Constrained git operation. Typical legal sequence: "
                    "ensure_repo -> status -> snapshot before major revision -> "
                    "create_worktree/revision_branch for alternatives -> snapshot "
                    "after verified edits -> deliver_tag for client delivery."
                ),
            },
            "message": {
                "type": "string",
                "description": "Commit/tag message for snapshot or deliver_tag.",
            },
            "author": {
                "type": "string",
                "description": "Author name for snapshot commits. Default: hermes-agent.",
            },
            "tag": {
                "type": "string",
                "description": "Optional annotated tag to apply to a snapshot commit.",
            },
            "version": {
                "type": "string",
                "description": "Delivery version identifier for deliver_tag, e.g. v1 or v2-final.",
            },
            "name": {
                "type": "string",
                "description": "Branch or worktree name for revision/worktree actions.",
            },
            "initial_branch": {
                "type": "string",
                "description": "Initial branch for ensure_repo. Default: master.",
            },
            "base_branch": {
                "type": "string",
                "description": "Base branch/commit for create_worktree.",
            },
            "create": {
                "type": "boolean",
                "description": "For revision_branch: create branch if true. Default: true.",
            },
            "switch": {
                "type": "boolean",
                "description": "For revision_branch: switch to branch if true. Default: true.",
            },
            "force": {
                "type": "boolean",
                "description": "For remove_worktree: force removal. Default: false.",
            },
            "allow_empty": {
                "type": "boolean",
                "description": "For snapshot: allow empty commit. Default: false.",
            },
            "push": {
                "type": "boolean",
                "description": "For deliver_tag: push tag to origin. Default: false.",
            },
            "n": {
                "type": "integer",
                "description": "Number of commits for log. Default: 10.",
            },
        },
        "required": ["project_dir", "action"],
    },
}


def _git_result_payload(res) -> dict:
    return {
        "ok": bool(getattr(res, "ok", False)),
        "message": getattr(res, "message", ""),
        "data": getattr(res, "data", {}) or {},
        "commit_hash": getattr(res, "commit_hash", ""),
        "tag": getattr(res, "tag", ""),
        "branch": getattr(res, "branch", ""),
    }


def _handle_git(args: dict, **kwargs) -> str:
    from lexitool import git_ops

    project_dir = _resolve_path(args["project_dir"])
    action = args.get("action")

    if action == "ensure_repo":
        res = git_ops.ensure_repo(
            project_dir,
            initial_branch=args.get("initial_branch") or "master",
        )
    elif action == "status":
        res = git_ops.status(project_dir)
    elif action == "snapshot":
        message = args.get("message")
        if not message:
            return tool_error("'message' is required for lex_git snapshot")
        res = git_ops.snapshot(
            project_dir,
            message,
            author=args.get("author") or "hermes-agent",
            tag=args.get("tag") or None,
            allow_empty=bool(args.get("allow_empty", False)),
        )
    elif action == "log":
        res = git_ops.log(project_dir, n=int(args.get("n", 10)))
    elif action == "deliver_tag":
        version = args.get("version")
        if not version:
            return tool_error("'version' is required for lex_git deliver_tag")
        res = git_ops.deliver_tag(
            project_dir,
            version,
            message=args.get("message") or None,
            push=bool(args.get("push", False)),
        )
    elif action == "revision_branch":
        name = args.get("name")
        if not name:
            return tool_error("'name' is required for lex_git revision_branch")
        res = git_ops.revision_branch(
            project_dir,
            name,
            create=bool(args.get("create", True)),
            switch=bool(args.get("switch", True)),
        )
    elif action == "create_worktree":
        name = args.get("name")
        if not name:
            return tool_error("'name' is required for lex_git create_worktree")
        res = git_ops.create_worktree(
            project_dir,
            name,
            base_branch=args.get("base_branch") or None,
        )
    elif action == "list_worktrees":
        res = git_ops.list_worktrees(project_dir)
    elif action == "remove_worktree":
        name = args.get("name")
        if not name:
            return tool_error("'name' is required for lex_git remove_worktree")
        res = git_ops.remove_worktree(
            project_dir,
            name,
            force=bool(args.get("force", False)),
        )
    elif action == "prune_worktrees":
        res = git_ops.prune_worktrees(project_dir)
    else:
        return tool_error(f"Unknown lex_git action: {action}")

    payload = _git_result_payload(res)
    payload["action"] = action
    payload["project_dir"] = project_dir
    return tool_result(payload)


# ── 16. update_project_state ──────────────────────────────────────────────────

UPDATE_PROJECT_STATE_SCHEMA = {
    "name": "update_project_state",
    "description": (
        "Update the evolving project state. Call this at key milestones: "
        "phase changes, new findings, document creation/completion, "
        "gate check results, and major decisions. All updates are journaled "
        "to project-context.md and synced to memory files that survive "
        "context compression."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Path to the project root (containing .hermes-project/).",
            },
            "phase": {
                "type": "string",
                "description": "New project phase: init, drafting, review, execution, cp, closing, registration.",
                "enum": ["init", "drafting", "review", "execution", "cp", "closing", "registration"],
            },
            "phase_note": {
                "type": "string",
                "description": "Human-readable note about the phase transition.",
            },
            "finding": {
                "type": "string",
                "description": "A key finding to record (dated, survives compression).",
            },
            "decision": {
                "type": "string",
                "description": "A major decision with rationale to record.",
            },
            "document": {
                "type": "object",
                "description": "Document tracking: {'path': '...', 'status': 'draft|review|final|executed', 'D_number': 'D01', 'notes': '...'}.",
                "properties": {
                    "path": {"type": "string"},
                    "status": {"type": "string", "enum": ["draft", "review", "final", "executed"]},
                    "D_number": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
            "gate_result": {
                "type": "object",
                "description": "The full result dict returned by lex_gate_check — auto-updates gate status.",
            },
        },
        "required": ["project_dir"],
    },
}


def _handle_update_project_state(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import update_project_state

    project_dir = _resolve_path(args["project_dir"])
    result = update_project_state(
        project_dir=project_dir,
        phase=args.get("phase"),
        phase_note=args.get("phase_note", ""),
        finding=args.get("finding"),
        decision=args.get("decision"),
        document=args.get("document"),
        gate_result=args.get("gate_result"),
    )
    return tool_result(result)


# ── 17. get_project_state ─────────────────────────────────────────────────────

GET_PROJECT_STATE_SCHEMA = {
    "name": "get_project_state",
    "description": (
        "Read the current project state: phase, key findings, active documents, "
        "last gate check result, major decisions. Use this after context "
        "compression to rehydrate project awareness."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Path to the project root (containing .hermes-project/).",
            },
        },
        "required": ["project_dir"],
    },
}


def _handle_get_project_state(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import get_project_state

    project_dir = _resolve_path(args["project_dir"])
    result = get_project_state(project_dir)
    return tool_result(result)


# ── 17b. project_facts ───────────────────────────────────────────────────────

PROJECT_FACTS_SCHEMA = {
    "name": "project_facts",
    "description": (
        "Maintain the living matter facts database for a legal project. "
        "Use this whenever project facts are discovered, corrected, confirmed, "
        "or superseded. Facts persist in .hermes-project/project-facts.json "
        "and sync to memories/project_facts.md so future turns and compressed "
        "sessions stay aware of the latest project facts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Path to the project root (containing .hermes-project/).",
            },
            "action": {
                "type": "string",
                "enum": ["upsert", "list", "get", "delete", "search", "history"],
                "description": "Fact operation.",
            },
            "fact_id": {
                "type": "string",
                "description": "Fact ID for get/delete/history or targeted update.",
            },
            "category": {
                "type": "string",
                "description": "Fact category, e.g. parties, economics, security, cp, dates, documents, issues.",
            },
            "key": {
                "type": "string",
                "description": "Stable fact key within category, e.g. borrower, loan_amount, maturity.",
            },
            "value": {
                "description": "Fact value. May be string, number, boolean, object, or array.",
            },
            "source": {
                "type": "string",
                "description": "Evidence/source for the fact: filename, paragraph, user instruction, TS clause, etc.",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Confidence level. Default: medium.",
            },
            "status": {
                "type": "string",
                "enum": ["confirmed", "assumed", "needs_confirmation", "superseded"],
                "description": "Fact status. Use needs_confirmation for unresolved points.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for filtering.",
            },
            "query": {
                "type": "string",
                "description": "Search text for search/list filtering.",
            },
            "limit": {
                "type": "integer",
                "description": "Max facts to return for list/search. Default: 50.",
            },
        },
        "required": ["project_dir", "action"],
    },
}


def _handle_project_facts(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import project_facts

    project_dir = _resolve_path(args["project_dir"])
    result = project_facts(
        project_dir,
        args["action"],
        fact_id=args.get("fact_id"),
        category=args.get("category"),
        key=args.get("key"),
        value=args.get("value"),
        source=args.get("source"),
        confidence=args.get("confidence", "medium"),
        status=args.get("status"),
        tags=args.get("tags"),
        query=args.get("query"),
        limit=int(args.get("limit", 50)),
    )
    return tool_result(result)


# ── 17c. Legal harness primitives ────────────────────────────────────────────

LEX_CONVENTION_PROFILE_SCHEMA = {
    "name": "lex_convention_profile",
    "description": (
        "Create/read the native convention profile for a legal DOCX. Use before "
        "substantive edits to capture defined-term formatting, cross-reference "
        "style, drafting voice, legal structure, and review hotspots. This is "
        "the harness-level replacement for prompt-only 'convention analysis'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_path": {"type": "string", "description": "Path to the DOCX document."},
            "project_dir": {"type": "string", "description": "Optional project root containing .hermes-project/."},
            "action": {
                "type": "string",
                "enum": ["create", "get", "list"],
                "description": "create=generate/update profile; get=latest for document; list=all project profiles. Default: create.",
            },
        },
        "required": ["document_path"],
    },
}


def _handle_lex_convention_profile(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import lex_convention_profile

    result = lex_convention_profile(
        _resolve_path(args["document_path"]),
        project_dir=_resolve_path(args["project_dir"]) if args.get("project_dir") else None,
        action=args.get("action", "create"),
    )
    return tool_result(result) if result.get("ok") else tool_error(result.get("error", "convention profile failed"))


LEGAL_REVIEW_PLAN_SCHEMA = {
    "name": "legal_review_plan",
    "description": (
        "Create and maintain a deterministic legal review plan for a DOCX. "
        "The plan turns lex_read(mode='review') hotspots, legal outline, and "
        "review dimensions into explicit steps for lawyer-style paragraph-by-"
        "paragraph review, rather than broad keyword replacement."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_path": {"type": "string", "description": "Path to the DOCX document."},
            "project_dir": {"type": "string", "description": "Optional project root containing .hermes-project/."},
            "action": {
                "type": "string",
                "enum": ["create", "list", "get", "update_status"],
                "description": "Plan operation. Default: create.",
            },
            "plan_id": {"type": "string", "description": "Plan ID for get/update_status."},
            "scope": {"type": "string", "description": "Review scope, or new status when action=update_status."},
            "review_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Review dimensions: content, format, xref, facts, ts, delivery, translation.",
            },
            "instructions": {"type": "string", "description": "User/task-specific review instructions."},
        },
        "required": ["document_path"],
    },
}


def _handle_legal_review_plan(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import legal_review_plan

    result = legal_review_plan(
        _resolve_path(args["document_path"]),
        project_dir=_resolve_path(args["project_dir"]) if args.get("project_dir") else None,
        action=args.get("action", "create"),
        plan_id=args.get("plan_id"),
        scope=args.get("scope", "full_document"),
        review_types=args.get("review_types"),
        instructions=args.get("instructions", ""),
    )
    return tool_result(result) if result.get("ok") else tool_error(result.get("error", "legal review plan failed"))


EDIT_VERIFICATION_RECORD_SCHEMA = {
    "name": "edit_verification_record",
    "description": (
        "Record/list/get verification records for material legal DOCX edits. "
        "Use after lex_edit readback to persist before/after, checks performed, "
        "status, and remaining issues in .hermes-project so future sessions can "
        "audit what was changed and verified."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {"type": "string", "description": "Project root containing .hermes-project/."},
            "document_path": {"type": "string", "description": "Path to the edited DOCX document."},
            "action": {
                "type": "string",
                "enum": ["add", "list", "get"],
                "description": "Record operation. Default: add.",
            },
            "record_id": {"type": "string", "description": "Record ID for get."},
            "target": {"type": "string", "description": "Edited target, e.g. §12 or table 1 R2C3."},
            "edit_summary": {"type": "string", "description": "Short description of the edit and legal rationale."},
            "before_text": {"type": "string", "description": "Relevant before/read context."},
            "after_text": {"type": "string", "description": "Readback after edit."},
            "checks": {"type": "array", "items": {"type": "string"}, "description": "Checks performed."},
            "status": {"type": "string", "enum": ["passed", "failed", "partial", "not_applicable"], "description": "Verification status."},
            "issues": {"type": "array", "items": {"type": "string"}, "description": "Remaining issues, if any."},
        },
        "required": ["project_dir", "document_path"],
    },
}


def _handle_edit_verification_record(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import edit_verification_record

    result = edit_verification_record(
        _resolve_path(args["project_dir"]),
        _resolve_path(args["document_path"]),
        action=args.get("action", "add"),
        record_id=args.get("record_id"),
        target=args.get("target", ""),
        edit_summary=args.get("edit_summary", ""),
        before_text=args.get("before_text", ""),
        after_text=args.get("after_text", ""),
        checks=args.get("checks"),
        status=args.get("status", "passed"),
        issues=args.get("issues"),
    )
    return tool_result(result) if result.get("ok") else tool_error(result.get("error", "edit verification record failed"))


LEGAL_HARNESS_MIGRATE_SCHEMA = {
    "name": "legal_harness_migrate",
    "description": (
        "Migrate registered legal projects to the current native harness layout. "
        "Reads the Hermes project DB by default, creates missing .hermes-project "
        "state/facts/convention/review/verification files, syncs memories, and "
        "marks project rows with the current harness version."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "db_path": {"type": "string", "description": "Optional explicit state.db path. Defaults to current Hermes profile DB."},
            "project_dirs": {"type": "array", "items": {"type": "string"}, "description": "Optional explicit project dirs instead of DB projects."},
        },
    },
}


def _handle_legal_harness_migrate(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import legal_harness_migrate

    result = legal_harness_migrate(
        db_path=_resolve_path(args["db_path"]) if args.get("db_path") else None,
        project_dirs=[_resolve_path(p) for p in args.get("project_dirs", [])] if args.get("project_dirs") else None,
    )
    return tool_result(result) if result.get("ok") else tool_error(result.get("error", "legal harness migration failed"), **result)


LEGAL_HARNESS_WORKFLOW_SCHEMA = {
    "name": "legal_harness_workflow",
    "description": (
        "List or read declarative legal harness workflow YAML templates. "
        "These templates define graph nodes, dependencies, worker profiles, "
        "required structured handoff fields, and scorecard checks for reusable "
        "legal workflows such as contract_revision and full_review."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {"type": "string", "description": "Project root containing .hermes-project/."},
            "action": {"type": "string", "enum": ["list", "get"], "description": "Workflow operation. Default: list."},
            "workflow_id": {"type": "string", "description": "Workflow ID for get, e.g. contract_revision."},
        },
        "required": ["project_dir"],
    },
}


def _handle_legal_harness_workflow(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import legal_harness_workflow

    result = legal_harness_workflow(
        _resolve_path(args["project_dir"]),
        action=args.get("action", "list"),
        workflow_id=args.get("workflow_id"),
    )
    return tool_result(result) if result.get("ok") else tool_error(result.get("error", "legal harness workflow failed"), **result)


LEGAL_HANDOFF_RECORD_SCHEMA = {
    "name": "legal_handoff_record",
    "description": (
        "Persist/list/get a structured handoff envelope for a legal harness run. "
        "Use this when a workflow node completes so later scorecards can verify "
        "machine-readable evidence instead of trusting free-form agent prose."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {"type": "string", "description": "Project root containing .hermes-project/."},
            "workflow_id": {"type": "string", "description": "Workflow template ID. Default: contract_revision."},
            "node_id": {"type": "string", "description": "Workflow node ID, e.g. revise or review."},
            "handoff": {"type": "object", "description": "Structured handoff envelope with status, evidence, guards, modified files, findings, etc."},
            "run_id": {"type": "string", "description": "Existing run ID. Omit on add to create a new run."},
            "action": {"type": "string", "enum": ["add", "list", "get"], "description": "Record operation. Default: add."},
        },
        "required": ["project_dir"],
    },
}


def _handle_legal_handoff_record(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import legal_handoff_record

    result = legal_handoff_record(
        _resolve_path(args["project_dir"]),
        workflow_id=args.get("workflow_id", "contract_revision"),
        node_id=args.get("node_id", ""),
        handoff=args.get("handoff") if isinstance(args.get("handoff"), dict) else None,
        run_id=args.get("run_id"),
        action=args.get("action", "add"),
    )
    return tool_result(result) if result.get("ok") else tool_error(result.get("error", "legal handoff record failed"), **result)


LEGAL_SCORECARD_SCHEMA = {
    "name": "legal_scorecard",
    "description": (
        "Evaluate persisted legal harness evidence before completion or delivery. "
        "Checks convention profiles, review plans, edit verification records, "
        "structured handoff envelopes, and git snapshot availability."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {"type": "string", "description": "Project root containing .hermes-project/."},
            "document_path": {"type": "string", "description": "Optional DOCX path to scope evidence checks."},
            "workflow_id": {"type": "string", "description": "Workflow template ID. Default: contract_revision."},
            "run_id": {"type": "string", "description": "Optional harness run ID for handoff envelope validation."},
            "strict": {"type": "boolean", "description": "If true, missing run handoffs fail when run_id is provided. Default: true."},
        },
        "required": ["project_dir"],
    },
}


def _handle_legal_scorecard(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import legal_scorecard

    result = legal_scorecard(
        _resolve_path(args["project_dir"]),
        document_path=_resolve_path(args["document_path"]) if args.get("document_path") else None,
        workflow_id=args.get("workflow_id", "contract_revision"),
        run_id=args.get("run_id"),
        strict=bool(args.get("strict", True)),
    )
    return tool_result(result) if result.get("ok") else tool_error(result.get("failures", "legal scorecard failed"), **result)


# ── 18. refine_goal ─────────────────────────────────────────────────────────

REFINE_GOAL_SCHEMA = {
    "name": "refine_goal",
    "description": (
        "Refine a raw user goal into a complete, clear legal project goal. "
        "Call this BEFORE lex_project_init whenever the user provides a goal. "
        "The tool returns a structured framework — use it to produce the "
        "refined goal, then pass the refined goal to lex_project_init.\n\n"
        "A well-formed legal project goal includes: (1) document type and "
        "jurisdiction, (2) key parties and their roles, (3) commercial context "
        "and deal structure, (4) specific deliverables and scope boundaries, "
        "(5) quality standards and review criteria, (6) cross-references to "
        "related documents if any."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "raw_goal": {
                "type": "string",
                "description": "The raw goal text as provided by the user, in any language.",
            },
            "context": {
                "type": "string",
                "description": "Optional: additional context from the conversation or project directory that helps refine the goal.",
            },
        },
        "required": ["raw_goal"],
    },
}


def _handle_refine_goal(args: dict, **kwargs) -> str:
    raw_goal = args["raw_goal"]
    context = args.get("context", "")

    context_header = "### Additional Context\n" + context if context else ""

    framework = f"""## Goal Refinement Framework

### Original Goal
{raw_goal}

{context_header}

### Refinement Checklist

Use this checklist to refine the goal into a complete legal project brief:

1. **Document Type & Jurisdiction**: What specific legal document(s) are being produced? Under which governing law?
2. **Parties & Roles**: Who are the parties? Lender/Borrower/Guarantor/Trustee etc. — clarify each role.
3. **Commercial Context**: What transaction type? Key commercial terms (amount, term, security structure, conditions precedent)?
4. **Scope & Deliverables**: Which documents exactly? What is explicitly OUT of scope?
5. **Quality Standards**: Any specific formatting, language, or regulatory requirements?
6. **Cross-References**: What source documents or related agreements exist? (Term Sheet, Loan Agreement, etc.)

### Output Format

Return ONE refined goal sentence (Chinese or English, matching the user's language) that captures all the above. Follow with a short structured breakdown in this format:

**Refined Goal:** [One comprehensive sentence]

**Scope:** [What's included]
**Parties:** [Key parties and roles]
**Key References:** [Source documents]
**Quality Gates:** [Key review criteria]

Now refine the original goal above and pass the result to lex_project_init."""

    return framework


# ── 19. project_add_task ────────────────────────────────────────────────────

PROJECT_ADD_TASK_SCHEMA = {
    "name": "project_add_task",
    "description": (
        "Capture a raw task for the current project and return a refinement "
        "framework. The task is stored immediately (status=pending, title=raw_input). "
        "The returned framework helps you refine the task into a structured, "
        "actionable item — then call project_update_task to save the refined version.\n\n"
        "Use this whenever the user asks to record, capture, or note down a task "
        "for later execution. Tasks persist in .hermes-project/project-tasks.json "
        "and survive context compression via memories/tasks.md."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Path to the project root (containing .hermes-project/).",
            },
            "raw_task": {
                "type": "string",
                "description": "The raw task description as entered by the user, in any language.",
            },
            "priority": {
                "type": "string",
                "description": "Initial priority hint: high, medium, or low. Default: medium.",
                "enum": ["high", "medium", "low"],
            },
        },
        "required": ["project_dir", "raw_task"],
    },
}


def _handle_project_add_task(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import add_project_task

    project_dir = _resolve_path(args["project_dir"])
    result = add_project_task(
        project_dir=project_dir,
        raw_task=args["raw_task"],
        priority=args.get("priority", "medium"),
    )
    return tool_result(result)


# ── 20. project_list_tasks ───────────────────────────────────────────────────

PROJECT_LIST_TASKS_SCHEMA = {
    "name": "project_list_tasks",
    "description": (
        "List tasks on the project task board. Optionally filter by status "
        "and/or priority. Use this to review the backlog, find pending work, "
        "or check what's been completed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Path to the project root (containing .hermes-project/).",
            },
            "status": {
                "type": "string",
                "description": "Filter by task status.",
                "enum": ["pending", "in_progress", "completed", "cancelled"],
            },
            "priority": {
                "type": "string",
                "description": "Filter by task priority.",
                "enum": ["high", "medium", "low"],
            },
        },
        "required": ["project_dir"],
    },
}


def _handle_project_list_tasks(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import list_project_tasks

    project_dir = _resolve_path(args["project_dir"])
    result = list_project_tasks(
        project_dir=project_dir,
        status=args.get("status"),
        priority=args.get("priority"),
    )
    return tool_result(result)


# ── 21. project_update_task ──────────────────────────────────────────────────

PROJECT_UPDATE_TASK_SCHEMA = {
    "name": "project_update_task",
    "description": (
        "Update a task on the project task board. Only the fields you provide "
        "are changed — omitted fields are left unchanged. Use this to:\n"
        "- Refine a task after the refinement framework (update title, description, tags)\n"
        "- Change task status (pending → in_progress → completed)\n"
        "- Reprioritize tasks\n\n"
        "After every update, memories/tasks.md is synced for compression survival."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Path to the project root (containing .hermes-project/).",
            },
            "task_id": {
                "type": "string",
                "description": "The task ID (t_xxxxxxxx) to update.",
            },
            "status": {
                "type": "string",
                "description": "New task status.",
                "enum": ["pending", "in_progress", "completed", "cancelled"],
            },
            "priority": {
                "type": "string",
                "description": "New task priority.",
                "enum": ["high", "medium", "low"],
            },
            "title": {
                "type": "string",
                "description": "Refined one-line task title.",
            },
            "description": {
                "type": "string",
                "description": "Expanded task description: scope, deliverables, quality criteria.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Category tags: drafting, review, translation, urgent, etc.",
            },
        },
        "required": ["project_dir", "task_id"],
    },
}


def _handle_project_update_task(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import update_project_task

    project_dir = _resolve_path(args["project_dir"])
    result = update_project_task(
        project_dir=project_dir,
        task_id=args["task_id"],
        status=args.get("status"),
        priority=args.get("priority"),
        title=args.get("title"),
        description=args.get("description"),
        tags=args.get("tags"),
    )
    return tool_result(result)


# ── 22. project_delete_task ──────────────────────────────────────────────────

PROJECT_DELETE_TASK_SCHEMA = {
    "name": "project_delete_task",
    "description": (
        "Delete a task from the project task board. Use this to remove "
        "cancelled, duplicate, or obsolete tasks."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Path to the project root (containing .hermes-project/).",
            },
            "task_id": {
                "type": "string",
                "description": "The task ID (t_xxxxxxxx) to delete.",
            },
        },
        "required": ["project_dir", "task_id"],
    },
}


def _handle_project_delete_task(args: dict, **kwargs) -> str:
    from hermes_cli.project_commands import delete_project_task

    project_dir = _resolve_path(args["project_dir"])
    result = delete_project_task(
        project_dir=project_dir,
        task_id=args["task_id"],
    )
    return tool_result(result)


# ── lex_comment ─────────────────────────────────────────────────────────────

LEX_COMMENT_SCHEMA = {
    "name": "lex_comment",
    "description": (
        "Manage Word comments (批注) in a .docx file. "
        "Supports listing, adding, replying, and deleting comments.\n\n"
        "Ops:\n"
        "- list: List all comments, optionally filtered by paragraph or author\n"
        "- add: Add a comment to a paragraph, optionally anchored to a character range\n"
        "- reply: Reply to an existing comment by ID\n"
        "- delete: Remove a comment by its ID (also accepts 'remove' as alias)\n\n"
        "Use add with range_start/range_end to anchor the comment to specific text "
        "within a paragraph. Use reply to continue a discussion thread on an existing comment."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "op": {
                "type": "string",
                "enum": ["list", "add", "reply", "delete", "remove"],
                "description": "Operation: list, add, reply, or delete/remove comments.",
            },
            "para": {
                "type": "integer",
                "description": "Paragraph index (0-based) for add operation or filtering list.",
            },
            "text": {
                "type": "string",
                "description": "Comment text (required for add and reply).",
            },
            "author": {
                "type": "string",
                "description": "Comment author name (default: 'agent'), or filter for list.",
            },
            "comment_id": {
                "type": "integer",
                "description": "Comment ID to reply to or delete (required for reply and delete).",
            },
            "range_start": {
                "type": "integer",
                "description": "Character offset where comment begins within the paragraph (for add op).",
            },
            "range_end": {
                "type": "integer",
                "description": "Character offset where comment ends within the paragraph (for add op).",
            },
        },
        "required": ["path", "op"],
    },
}


def _handle_comment(args: dict, **kwargs) -> str:
    from lexitool.comment_ops import add_comment, list_comments, remove_comment, reply_comment
    path = _resolve_path(args["path"])
    op = args["op"]
    author = args.get("author", "agent")

    if op == "list":
        para = args.get("para")
        res = list_comments(path, para=para, author=args.get("author"))
        return tool_result({
            "ok": res.ok, "op": "list",
            "comments": res.data, "count": len(res.data),
            "message": res.message,
        })

    elif op == "add":
        para = args.get("para", 0)
        text = args.get("text", "")
        if not text:
            return tool_error("'text' is required for add operation")
        range_start = args.get("range_start")
        range_end = args.get("range_end")
        res = add_comment(path, para, text, author=author,
                         range_start=range_start, range_end=range_end)
        return tool_result({
            "ok": res.ok, "op": "add",
            "comment": res.data[0] if res.data else None,
            "message": res.message,
        })

    elif op == "reply":
        comment_id = args.get("comment_id")
        if comment_id is None:
            return tool_error("'comment_id' is required for reply operation")
        text = args.get("text", "")
        if not text:
            return tool_error("'text' is required for reply operation")
        res = reply_comment(path, int(comment_id), text, author=author)
        return tool_result({
            "ok": res.ok, "op": "reply",
            "comment_id": comment_id, "message": res.message,
        })

    elif op in ("remove", "delete"):
        comment_id = args.get("comment_id")
        if comment_id is None:
            return tool_error("'comment_id' is required for delete operation")
        res = remove_comment(path, int(comment_id))
        return tool_result({
            "ok": res.ok, "op": op,
            "comment_id": comment_id, "message": res.message,
        })

    return tool_error(f"Unknown op: {op}")


# ── Registration ──────────────────────────────────────────────────────────────

LEX_REVIEW_WORKFLOW_SCHEMA = {
    "name": "lex_review_workflow",
    "description": "End-to-end legal review workflow: read, discover clauses, audit terms, lint, recommend. Read-only.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
        },
        "required": ["path"],
    },
}

def _handle_review_workflow(args: dict, **kwargs) -> str:
    from lexitool.review_detailed import review_workflow
    path = _resolve_path(args["path"])
    try:
        result = review_workflow(path)
        return tool_result(result)
    except Exception as exc:
        return tool_error(str(exc))


LEX_VERIFY_EDITS_SCHEMA = {
    "name": "lex_verify_edits",
    "description": "Verify that edits were applied correctly. Re-reads affected paragraphs and checks expectations.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the .docx file.",
            },
            "expected": {
                "type": "string",
                "description": "JSON array of expected results, each with: para (int), should_contain (list[str]), should_not_contain (list[str]).",
            },
            "output": {
                "type": "string",
                "description": "Optional output path for verification report.",
            },
        },
        "required": ["path", "expected"],
    },
}

def _handle_verify_edits(args: dict, **kwargs) -> str:
    import json
    from lexitool.edit_ops import verify_edits
    path = _resolve_path(args["path"])
    try:
        expected = json.loads(args["expected"])
    except (json.JSONDecodeError, TypeError) as exc:
        return tool_error(f"Invalid 'expected' JSON: {exc}")
    try:
        result = verify_edits(path, expected)
        return tool_result(result)
    except Exception as exc:
        return tool_error(str(exc))


# ── Registration ──────────────────────────────────────────────────────────────

LEX_BULK_SCAN_SCHEMA = {
    "name": "lex_bulk_scan",
    "description": (
        "Scan multiple .docx files for text/regex matches. Returns compact "
        "per-file counts and match locations (paragraph IDs, context snippets) "
        "instead of full document text. Use this for legal term audits across "
        "document sets — never loop lex_scan manually for the same purpose. "
        "Supports glob patterns like \"**/*.docx\"."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "root": {
                "type": "string",
                "description": "Directory containing .docx files to scan."
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern for .docx files (e.g. \"*.docx\" or \"**/*.docx\")."
            },
            "terms": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Text or regex patterns to search for."
            },
            "regex": {
                "type": "boolean",
                "description": "Treat terms as regex patterns.",
                "default": False
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Case-sensitive matching.",
                "default": True
            },
            "include_headers_footers": {
                "type": "boolean",
                "description": "Include header/footer text in scan.",
                "default": True
            },
            "max_matches_per_file": {
                "type": "integer",
                "description": "Max matches to return per file before truncating.",
                "default": 50
            },
            "context_chars": {
                "type": "integer",
                "description": "Characters of surrounding context per match.",
                "default": 120
            },
        },
        "required": ["root", "terms"],
        "additionalProperties": False,
    },
}


def _handle_bulk_scan(args: dict, **kwargs) -> str:
    """Scan multiple .docx files for terms and return compact counts."""
    import glob as glob_mod
    import os

    root_dir = _resolve_path(args["root"])
    glob_pattern = args.get("glob", "*.docx")
    terms: list = args.get("terms", [])
    regex = args.get("regex", False)
    case_sensitive = args.get("case_sensitive", True)
    include_hf = args.get("include_headers_footers", True)
    max_per_file = int(args.get("max_matches_per_file", 50))
    ctx_chars = int(args.get("context_chars", 120))

    if not terms:
        return tool_error("terms is required")
    if not os.path.isdir(root_dir):
        return tool_error(f"root directory not found: {root_dir}")

    pattern = os.path.join(root_dir, glob_pattern)
    paths = sorted(glob_mod.glob(pattern, recursive="**" in glob_pattern))
    docx_paths = [p for p in paths if p.lower().endswith(".docx")]

    if not docx_paths:
        return tool_error(f"no .docx files matched: {pattern} (found {len(paths)} files)")
    if len(docx_paths) > 100:
        return tool_error(f"too many files: {len(docx_paths)}. Limit to 100.")

    results: list = []
    total_by_term: dict = {}
    files_scanned = 0

    for fp in docx_paths:
        file_matches: list = []
        file_counts: dict = {}
        try:
            for term in terms:
                res = _lex_scan_core(fp, term, regex=regex,
                                     case_sensitive=case_sensitive,
                                     include_hf=include_hf,
                                     max_results=max_per_file,
                                     context_chars=ctx_chars)
                if res.get("ok"):
                    matches = res.get("results", [])
                    file_counts[term] = len(matches)
                    total_by_term[term] = total_by_term.get(term, 0) + len(matches)
                    for m in matches[:max_per_file]:
                        file_matches.append({
                            "term": term,
                            "paragraph": m.get("paragraph"),
                            "context": m.get("context", ""),
                        })
            results.append({
                "path": os.path.relpath(fp, root_dir),
                "counts": file_counts,
                "matches": file_matches[:max_per_file],
                "truncated": sum(file_counts.values()) > max_per_file,
            })
            files_scanned += 1
        except Exception as e:
            results.append({
                "path": os.path.relpath(fp, root_dir),
                "error": str(e),
                "counts": {},
                "matches": [],
            })

    return tool_result({
        "ok": True,
        "root": root_dir,
        "files_scanned": files_scanned,
        "total_files": len(docx_paths),
        "terms": terms,
        "total_counts": total_by_term,
        "files": results,
    })


_TOOLS = [
    # Read
    ("lex_read",     "lexitool", LEX_READ_SCHEMA,     _handle_read),
    ("lex_scan",     "lexitool", LEX_SCAN_SCHEMA,     _handle_scan),
    ("lex_bulk_scan","lexitool", LEX_BULK_SCAN_SCHEMA, _handle_bulk_scan),
    ("lex_revision_guard", "lexitool", LEX_REVISION_GUARD_SCHEMA, _handle_revision_guard),
    ("lex_stats",    "lexitool", LEX_STATS_SCHEMA,    _handle_stats),
    ("lex_table_list", "lexitool", LEX_TABLE_LIST_SCHEMA, _handle_table_list),
    # Write
    ("lex_edit",     "lexitool", LEX_EDIT_SCHEMA,     _handle_edit),
    ("lex_tc",       "lexitool", LEX_TC_SCHEMA,       _handle_tc),
    ("lex_comment",  "lexitool", LEX_COMMENT_SCHEMA,  _handle_comment),
    ("lex_format",   "lexitool", LEX_FORMAT_SCHEMA,   _handle_format),
    # Structure
    ("lex_list",     "lexitool", LEX_LIST_SCHEMA,     _handle_list),
    ("lex_ref",      "lexitool", LEX_REF_SCHEMA,      _handle_ref),
    # Layout
    ("lex_section",  "lexitool", LEX_SECTION_SCHEMA,  _handle_section),
    # Document
    ("lex_doc",      "lexitool", LEX_DOC_SCHEMA,      _handle_doc),
    # Clause & Corpus
    ("lex_clause",        "lexitool", LEX_CLAUSE_SCHEMA,        _handle_clause),
    ("lex_corpus",        "lexitool", LEX_CORPUS_SCHEMA,        _handle_corpus),
    # OCR & Project
    ("lex_ocr",           "lexitool", LEX_OCR_SCHEMA,           _handle_ocr),
    ("lex_project_init",  "lexitool", LEX_PROJECT_INIT_SCHEMA,  _handle_project_init),
    # Diff & Deliver
    ("lex_diff",          "lexitool", LEX_DIFF_SCHEMA,          _handle_diff),
    ("lex_xref_audit",    "lexitool", LEX_XREF_AUDIT_SCHEMA,    _handle_xref_audit),
    ("lex_deliver",       "lexitool", LEX_DELIVER_SCHEMA,       _handle_deliver),
    # Gate Check
    ("lex_gate_check",         "lexitool", LEX_GATE_CHECK_SCHEMA,         _handle_gate_check),
    ("lex_git",                "lexitool", LEX_GIT_SCHEMA,                _handle_git),
    # Project State Evolution
    ("update_project_state",   "lexitool", UPDATE_PROJECT_STATE_SCHEMA,   _handle_update_project_state),
    ("get_project_state",      "lexitool", GET_PROJECT_STATE_SCHEMA,      _handle_get_project_state),
    ("project_facts",          "lexitool", PROJECT_FACTS_SCHEMA,          _handle_project_facts),
    ("lex_convention_profile", "lexitool", LEX_CONVENTION_PROFILE_SCHEMA, _handle_lex_convention_profile),
    ("legal_review_plan",      "lexitool", LEGAL_REVIEW_PLAN_SCHEMA,      _handle_legal_review_plan),
    ("edit_verification_record", "lexitool", EDIT_VERIFICATION_RECORD_SCHEMA, _handle_edit_verification_record),
    ("lex_review_workflow",    "lexitool", LEX_REVIEW_WORKFLOW_SCHEMA,    _handle_review_workflow),
    ("lex_verify_edits",       "lexitool", LEX_VERIFY_EDITS_SCHEMA,       _handle_verify_edits),
    ("legal_harness_migrate",  "lexitool", LEGAL_HARNESS_MIGRATE_SCHEMA,  _handle_legal_harness_migrate),
    ("legal_harness_workflow", "lexitool", LEGAL_HARNESS_WORKFLOW_SCHEMA, _handle_legal_harness_workflow),
    ("legal_handoff_record",   "lexitool", LEGAL_HANDOFF_RECORD_SCHEMA,   _handle_legal_handoff_record),
    ("legal_scorecard",        "lexitool", LEGAL_SCORECARD_SCHEMA,        _handle_legal_scorecard),
    # Goal
    ("refine_goal",            "lexitool", REFINE_GOAL_SCHEMA,            _handle_refine_goal),
    # Task Board
    ("project_add_task",       "lexitool", PROJECT_ADD_TASK_SCHEMA,       _handle_project_add_task),
    ("project_list_tasks",     "lexitool", PROJECT_LIST_TASKS_SCHEMA,     _handle_project_list_tasks),
    ("project_update_task",    "lexitool", PROJECT_UPDATE_TASK_SCHEMA,    _handle_project_update_task),
    ("project_delete_task",    "lexitool", PROJECT_DELETE_TASK_SCHEMA,    _handle_project_delete_task),
]

for _name, _toolset, _schema, _handler in _TOOLS:
    registry.register(
        name=_name,
        toolset=_toolset,
        schema=_schema,
        handler=_handler,
        check_fn=_check_lexitool,
        description=_schema.get("description", ""),
        emoji="",
    )


def reload_lexitool_tools() -> dict:
    """Hot-reload all lexitool tools by re-importing this module.

    Purges vendored ``lexitool.*`` modules from ``sys.modules`` before
    re-import so that handler-level lazy imports resolve to fresh code.
    """
    import sys
    import importlib
    from . import lexitool_tool

    before = set(registry.get_tool_names_for_toolset("lexitool"))

    # Purge vendored lexitool modules so the re-import picks up fresh code.
    purged = 0
    for key in sorted(sys.modules.keys()):
        if key == "lexitool" or key.startswith("lexitool."):
            del sys.modules[key]
            purged += 1

    with registry.batch_tools_changed():
        for name in list(before):
            registry.deregister(name)

        importlib.reload(lexitool_tool)

    invalidate_check_fn_cache()

    after = set(registry.get_tool_names_for_toolset("lexitool"))
    return {
        "deregistered": len(before),
        "reregistered": len(after),
        "tools": sorted(after),
        "vendored_modules_purged": purged,
    }
