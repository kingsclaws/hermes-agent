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
from pathlib import Path

from tools.registry import invalidate_check_fn_cache, registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# lexitool lives at /root/.hermes/tools/lexitool (symlink to vendor package).
# Make sure its PARENT is on sys.path so ``import lexitool`` resolves.
_LEXITOOL_PATH = Path("/root/.hermes/tools/lexitool")
if str(_LEXITOOL_PATH.parent) not in sys.path:
    sys.path.insert(0, str(_LEXITOOL_PATH.parent))


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


# ── 1. lex_read ──────────────────────────────────────────────────────────────

LEX_READ_SCHEMA = {
    "name": "lex_read",
    "description": (
        "Read a .docx file and return its content as annotated text with inline "
        "format markup. This is the PRIMARY tool for understanding document content. "
        "Always call this FIRST before editing.\n\n"
        "Format tags you will see in output:\n"
        "  [b]bold text[/b]  [i]italic[/i]  [u]underline[/u]  [s]strikethrough[/s]\n"
        "  [font:宋体,12pt]text[/font]  [color:#FF0000]text[/color]\n"
        "  [highlight:yellow]text[/highlight]\n"
        "  [ins]tracked added text[/ins]  [del]tracked deleted text[/del]\n"
        "  [bullet:0] list item  [num:0] numbered item\n"
        "  [bookmark:name]text[/bookmark]  [page-break]  [section-break:next]\n"
        "  [spacing:1.5]  [indent:2ch]  [align:center]\n\n"
        "Paragraphs are prefixed with §N (1-indexed). Use these § numbers "
        "as targets for lex_edit."
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
                "enum": ["full", "structure", "stats", "headers_footers"],
                "description": "full=all content with markup, structure=headings only, stats=counts only, headers_footers=only Word section header/footer text. Default: full.",
            },
            "show_tc": {
                "type": "boolean",
                "description": "Include [ins]/[del] markup for Track Changes. Default: true.",
            },
            "show_format": {
                "type": "boolean",
                "description": "Include format tags. Set false for plain text. Default: true.",
            },
            "include_headers_footers": {
                "type": "boolean",
                "description": "Append Word section header/footer text to full reads. Default: true.",
            },
        },
        "required": ["path"],
    },
}


def _handle_read(args: dict, **kwargs) -> str:
    from lexitool.markup import lex_read
    path = _resolve_path(args["path"])
    result = lex_read(
        path=path,
        paras=args.get("paras"),
        mode=args.get("mode", "full"),
        show_tc=args.get("show_tc", True),
        show_format=args.get("show_format", True),
        include_headers_footers=args.get("include_headers_footers", True),
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


# ── 3. lex_edit ───────────────────────────────────────────────────────────────

LEX_EDIT_SCHEMA = {
    "name": "lex_edit",
    "description": (
        "Atomically edit text in a .docx file. Supports paragraph-level, "
        "table-level, and block-level operations with optional Track Changes.\n\n"
        "## Paragraph-level ops (require 'target')\n"
        "Target syntax:\n"
        "  §3           = entire paragraph 3\n"
        "  §3:5-10      = characters 5-10 in paragraph 3\n"
        "  §3:r2        = run 2 in paragraph 3\n"
        "  §3:r2:5-10   = characters 5-10 in run 2 of paragraph 3\n"
        "  §3-7         = paragraphs 3 through 7\n\n"
        "## Table-level ops (require 'table_index')\n"
        "- replace_table_cell: find cell by old_text, replace with new_text\n"
        "- replace_table_cells: batch {old, new} across table cells\n"
        "- set_table_cells: batch set cells by stable row/column coordinates\n"
        "- insert_table_rows: copy template_row, fill cell text from rows_data\n\n"
        "## Block-level ops\n"
        "- insert_paragraphs: insert paras with optional page breaks after after_para\n\n"
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
                    "replace", "insert", "delete", "set_format",
                    "replace_table_cell", "replace_table_cells",
                    "set_table_cells",
                    "insert_table_rows", "insert_paragraphs",
                    "create_table", "replace_header_footer",
                ],
                "description": (
                    "Operation type. Paragraph-level: replace, insert, delete, set_format. "
                    "Table-level: replace_table_cell (single cell), replace_table_cells (batch), "
                    "set_table_cells (batch by row/col coordinates), "
                    "insert_table_rows (copy template row with cell text), "
                    "create_table (insert a new table with headers and data rows). "
                    "Block-level: insert_paragraphs (insert multiple paras after anchor). "
                    "Header/footer: replace_header_footer."
                ),
            },
            "target": {
                "type": "string",
                "description": "Target specifier: §N, §N:X-Y, §N:rM, §N:rM:X-Y, or §N-M.",
            },
            "new_text": {
                "type": "string",
                "description": "New text for replace/insert. May contain format markup like [b]bold[/b].",
            },
            "format": {
                "type": "object",
                "description": 'Format properties: {"bold": true, "font": "宋体", "size": "12pt"}. For set_format op.',
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
                "description": "0-indexed paragraph number to insert after. For insert_paragraphs and create_table.",
            },
            "paragraphs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "bold": {"type": "boolean"},
                        "page_break_before": {"type": "boolean"},
                    },
                    "required": ["text"],
                },
                "description": "List of {text, bold?, page_break_before?}. For insert_paragraphs.",
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
        replace_table_cell_text, replace_table_cell_text_all,
        set_table_cells_by_position, insert_table_rows, insert_paragraph_block,
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
              "create_table"):
        try:
            if op == "replace_table_cell":
                table_index = args.get("table_index", 0)
                old_text = args.get("old_text", "")
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
                res = insert_paragraph_block(
                    path, after_para, paragraphs,
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

    # ── Paragraph-level operations ───────────────────────────────────────────
    from lexitool.markup import parse_target
    from lexitool.tc_utils import tc_replace_first_in_para, tc_ins_text, tc_del_paragraph

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    target_str = args.get("target", "")
    if not target_str:
        return tool_error("'target' is required for paragraph-level operations (replace, insert, delete, set_format)")
    new_text = args.get("new_text", "")
    fmt = args.get("format")

    target = parse_target(target_str)
    doc_xml, other = _read_docx(path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    para_idx = target.para_start - 1

    # Build two paragraph lists:
    #   body_paras — body-direct w:p only (matches lex_read numbering)
    #   all_paras  — every w:p in document order including table cells
    #                 (matches edit_ops._find_para semantics)
    body_paras = [el for el in body if el.tag == f"{W}p"]
    all_paras = [el for el in root.iter() if el.tag == f"{W}p"]

    if para_idx < 0 or para_idx >= len(body_paras):
        return tool_error(f"Paragraph {target.para_start} out of range (1-{len(body_paras)})")

    para_el = body_paras[para_idx]
    tc_id = _next_tc_id_from_body(body)

    # Resolve the paragraph in the all_paras list for operations that need
    # the canonical element from root.iter() (tc_replace_first_in_para
    # requires the element's parent chain to match the root).
    _all_idx = None
    for i, el in enumerate(all_paras):
        if el is para_el:
            _all_idx = i
            break

    try:
        if op == "delete":
            if tc:
                tc_del_paragraph(para_el, tc_id, author)
            else:
                for t_el in para_el.iter(f"{W}t"):
                    t_el.text = None

        elif op == "replace":
            requested_old_text = args.get("old_text")
            if isinstance(requested_old_text, str) and requested_old_text:
                old_text = requested_old_text
            elif target.char_start is not None:
                old_text = _get_para_text(para_el)[target.char_start:target.char_end]
            else:
                old_text = _get_para_text(para_el)
            if tc:
                tc_result = tc_replace_first_in_para(para_el, old_text, new_text, tc_id, author)
                if not tc_result.get("ok"):
                    para_text = _get_para_text(para_el)
                    located = _locate_normalized_text(para_text, old_text)
                    if located is not None:
                        start, end = located
                        actual_old = para_text[start:end]
                        if actual_old and actual_old != old_text:
                            tc_result = tc_replace_first_in_para(
                                para_el, actual_old, new_text, tc_id, author
                            )
                # Fallback: if text not found in the body-level paragraph,
                # search ALL paragraphs including table cells.
                if not tc_result.get("ok") and _all_idx is not None:
                    tc_result = _search_and_tc_replace_nearby(
                        all_paras, _all_idx, old_text, new_text, tc_id, author
                    )
                if not tc_result.get("ok"):
                    return tool_error(
                        f"Text '{old_text}' not found in paragraph {target.para_start}"
                        + (" or nearby table cells" if _all_idx is not None else "")
                    )
            else:
                result = _direct_replace(para_el, old_text, new_text)
                # Fallback: if text not found in the body-level paragraph,
                # search ALL paragraphs including table cells.  This handles
                # the case where the LLM identified a paragraph number from
                # lex_read (which only counts body-level w:p) but the actual
                # text lives inside a table adjacent to that paragraph.
                if not result["ok"] and _all_idx is not None:
                    result = _search_and_replace_nearby(
                        all_paras, _all_idx, old_text, new_text
                    )
                if not result["ok"]:
                    return tool_error(f"Text '{old_text}' not found in paragraph {target.para_start}" +
                                      (" or nearby table cells" if _all_idx is not None else ""))

        elif op == "insert":
            if tc:
                tc_ins_text(para_el, new_text, tc_id, author, position=target.char_start or "end")
            else:
                _direct_insert(para_el, new_text, target.char_start or -1)

        elif op == "set_format" and fmt:
            _apply_format_to_range(para_el, target.char_start, target.char_end, fmt)

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
        # Read the edited paragraph plus one on each side for context
        read_start = max(1, target.para_start - 1)
        read_end = target.para_start + 1
        verified = lex_read(path, paras=list(range(read_start, read_end + 1)),
                            mode="full", show_tc=True, show_format=True)
        verified_output = verified.get("text", "")
    except Exception:
        verified_output = ""  # best-effort

    result = {"ok": True, "op": op, "target": target_str, "para": target.para_start}
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
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    parts = []
    for el in para_el.iter():
        if el.tag in (f"{W}t", f"{W}delText"):
            parts.append(el.text or "")
        elif el.tag == f"{W}tab":
            parts.append("\t")
    return "".join(parts)


def _direct_replace(para_el, old_text: str, new_text: str) -> dict:
    """Replace old_text with new_text in a paragraph element.

    Uses iter() to find ALL w:t descendants (including those nested inside
    w:ins, w:del, w:smartTag, and other wrappers common in table cells).
    Supports cross-run matching: if old_text spans multiple w:t elements,
    the combined text is flattened into the first w:t and the rest are
    cleared — same strategy as edit_ops.replace_text().
    """
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    t_elements = [el for el in para_el.iter() if el.tag == f"{W}t"]
    if not t_elements:
        return {"ok": False, "reason": "no text runs in paragraph"}

    full_text = "".join(t.text or "" for t in t_elements)
    match_text = _resolve_replace_match(full_text, old_text)
    if match_text is None:
        return {"ok": False, "reason": "text not found"}

    new_full = full_text.replace(match_text, new_text, 1)
    t_elements[0].text = new_full
    for t in t_elements[1:]:
        t.text = ""
    return {"ok": True, "matched_text": match_text}


def _normalize_match_text(text: str) -> str:
    """Normalize only for fallback matching; never changes document output."""
    return re.sub(r"\s+", "", text or "")


def _locate_normalized_text(haystack: str, needle: str) -> tuple[int, int] | None:
    """Locate needle in haystack while ignoring whitespace differences."""
    normalized_haystack = []
    index_map = []
    for idx, char in enumerate(haystack or ""):
        if char.isspace():
            continue
        normalized_haystack.append(char)
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
        result = tc_replace_first_in_para(all_paras[i], old_text, new_text, tc_id, author)
        if result.get("ok"):
            return result

    # Search backward
    for i in range(search_start - 1, max(search_start - 50, -1), -1):
        result = tc_replace_first_in_para(all_paras[i], old_text, new_text, tc_id, author)
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

    if rpr_dict:
        new_rPr = make_rPr_from_dict(rpr_dict)
        for r_el in para_el.findall(f"{W}r"):
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
    para_range_str = args.get("para_range")
    dry_run = args.get("dry_run", False)
    include_tables = args.get("include_tables", False)

    # Parse paragraph range — accept both string ("1119,1124") and array ([1119, 1124])
    para_range = None
    if para_range_str is not None:
        if isinstance(para_range_str, list):
            if len(para_range_str) == 2:
                para_range = (int(para_range_str[0]), int(para_range_str[1]))
            else:
                return tool_error(f"Invalid para_range list (need exactly 2 elements): {para_range_str}")
        elif isinstance(para_range_str, str) and para_range_str.strip():
            parts = para_range_str.split(",")
            if len(parts) == 2:
                try:
                    para_range = (int(parts[0].strip()), int(parts[1].strip()))
                except ValueError:
                    return tool_error(f"Invalid para_range: {para_range_str}")

    doc = Document(path)

    if op == "list":
        items = tc_ops.list_tc(doc, author_filter=author,
                               para_range=para_range, type_filter=type_filter,
                               include_tables=include_tables)
        return tool_result({"ok": True, "op": "list", "tc_items": items,
                            "count": len(items)})

    # accept / reject
    if op == "accept":
        stats = tc_ops.accept_all(doc, author_filter=author,
                                  para_range=para_range, type_filter=type_filter,
                                  include_tables=include_tables)
    else:
        stats = tc_ops.reject_all(doc, author_filter=author,
                                  para_range=para_range, type_filter=type_filter,
                                  include_tables=include_tables)

    if dry_run:
        return tool_result({"ok": True, "op": op, "dry_run": True, "would_change": stats})

    doc.save(path)
    return tool_result({"ok": True, "op": op, "stats": stats})


# ── 4. lex_format ─────────────────────────────────────────────────────────────

LEX_FORMAT_SCHEMA = {
    "name": "lex_format",
    "description": (
        "Apply formatting to text ranges in a .docx file. Supports format brush "
        "(copy format from one paragraph) and direct property application.\n\n"
        "Format properties: bold, italic, underline, strikethrough, font (name), "
        "size (e.g. '12pt'), color (e.g. '#FF0000'), highlight, spacing, indent, align."
    ),
    "parameters": {
        "type": "object",
        "properties": {
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
        },
        "required": ["path", "target"],
    },
}


def _handle_format(args: dict, **kwargs) -> str:
    from lexitool.markup import parse_target
    from lexitool.edit_ops import _read_docx, _write_docx

    path = _resolve_path(args["path"])
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
            # ── Format brush: copy from source paragraph ──
            src_idx = source_para - 1
            if 0 <= src_idx < len(paras):
                _apply_format_brush(paras, target_para, paras[src_idx], W)
        else:
            # ── Apply explicit properties ──
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
    src_runs = src_para.findall(f"{W}r")
    target_runs = target_para.findall(f"{W}r")
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
        ind.set(f"{W}firstLine", str(int(float(props["indent"]) * 240)))

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

    # Determine target runs
    all_runs = para_el.findall(f"{W}r")
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
            font_name = run_props["font"]
            rf.set(f"{W}ascii", font_name)
            rf.set(f"{W}hAnsi", font_name)
            rf.set(f"{W}eastAsia", font_name)

        if "size" in run_props:
            size_str = run_props["size"].replace("pt", "").strip()
            sz_half_pt = str(int(float(size_str) * 2))
            for sz_tag in [f"{W}sz", f"{W}szCs"]:
                sz_el = rPr.find(sz_tag)
                if sz_el is None:
                    sz_el = etree.SubElement(rPr, sz_tag)
                sz_el.set(f"{W}val", sz_half_pt)

        if "color" in run_props:
            c = rPr.find(f"{W}color")
            if c is None:
                c = etree.SubElement(rPr, f"{W}color")
            c.set(f"{W}val", run_props["color"].lstrip("#"))

        if "highlight" in run_props:
            hl = rPr.find(f"{W}highlight")
            if hl is None:
                hl = etree.SubElement(rPr, f"{W}highlight")
            hl.set(f"{W}val", run_props["highlight"])


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
                "description": "Restart numbering at this value. For 'restart' operation.",
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
        if not args.get("style"):
            return tool_error("'style' is required for create operation")
        result = lists.create_list(path, paras, style=args["style"])
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
        "  auto_xref       — Full conversion: add bookmarks to headings, wrap xref text in hyperlinks"
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
                         "resolve_fields", "scan_xref", "auto_xref", "cross_doc_scan"],
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
                "description": "List of document paths for cross_doc_scan. All docs are scanned for cross-references to each other.",
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

    if op in ("scan_xref", "auto_xref", "cross_doc_scan"):
        from lexitool import xref
        if op == "scan_xref":
            result = xref.scan_xrefs(path)
        elif op == "cross_doc_scan":
            docs = args.get("docs", [])
            if not docs:
                return tool_error("'docs' is required for cross_doc_scan (list of doc paths)")
            docs = [_resolve_path(d) for d in docs]
            result = xref.cross_doc_scan(docs)
        else:
            result = xref.auto_xref(path)
        return tool_result(result)

    name = args.get("name")
    if not name:
        return tool_error("'name' is required for this operation")

    target_para = args.get("target_para", 1)
    offset = args.get("offset", 0)

    if op == "add_bookmark":
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
        "Breaks: page, column, section_next (new page), section_continuous (same page).\n"
        "Margins: {'top': '2.54cm', 'bottom': '2.54cm', 'left': '3.18cm', 'right': '3.18cm'}.\n"
        "Orientation: portrait or landscape."
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
                "enum": ["add_break", "set_margins", "set_orientation"],
                "description": "Operation to perform.",
            },
            "at_para": {
                "type": "integer",
                "description": "Paragraph number (1-indexed) for break placement.",
            },
            "type": {
                "type": "string",
                "enum": ["page", "column", "section_next", "section_continuous"],
                "description": "Break type. Required for add_break.",
            },
            "margins": {
                "type": "object",
                "description": 'Margin values: {"top": "2.54cm", "bottom": "2.54cm", "left": "3.18cm", "right": "3.18cm"}.',
            },
            "orientation": {
                "type": "string",
                "enum": ["portrait", "landscape"],
            },
        },
        "required": ["path", "op"],
    },
}


def _handle_section(args: dict, **kwargs) -> str:
    from lexitool.edit_ops import _read_docx, _write_docx

    path = _resolve_path(args["path"])
    op = args["op"]

    doc_xml, other = _read_docx(path)
    from lxml import etree
    root = etree.fromstring(doc_xml)
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
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
        # Find or create the last sectPr in the document
        sectPr = body.find(f"{W}sectPr")
        if sectPr is None:
            # Add sectPr to the last paragraph
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
                    val = int(float(margins[key].replace("cm", "").replace("in", "").strip()) * 567)  # cm to twips approx
                    pgMar.set(f"{W}{key}", str(val))

    elif op == "set_orientation" and args.get("orientation"):
        orientation = args["orientation"]
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
            if orientation == "landscape":
                pgSz.set(f"{W}orient", "landscape")
                pgSz.set(f"{W}w", "16838")
                pgSz.set(f"{W}h", "11906")
            else:
                pgSz.set(f"{W}orient", "portrait")
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
                "enum": ["create", "clean", "update_toc", "update_fields"],
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
        "Compare two .docx files and produce a tracked-changes redline document.\n"
        "Uses quicompare (Aspose Words backend) for professional legal redlining.\n\n"
        "Produces a Word .docx with all insertions, deletions, and moves tracked.\n"
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
        },
        "required": ["original", "revised"],
    },
}


def _handle_diff(args: dict, **kwargs) -> str:
    from lexitool import diff as _diff

    original = _resolve_path(args["original"])
    revised = _resolve_path(args["revised"])

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


# ── Registration ──────────────────────────────────────────────────────────────

_TOOLS = [
    # Read
    ("lex_read",     "lexitool", LEX_READ_SCHEMA,     _handle_read),
    ("lex_stats",    "lexitool", LEX_STATS_SCHEMA,    _handle_stats),
    ("lex_table_list", "lexitool", LEX_TABLE_LIST_SCHEMA, _handle_table_list),
    # Write
    ("lex_edit",     "lexitool", LEX_EDIT_SCHEMA,     _handle_edit),
    ("lex_tc",       "lexitool", LEX_TC_SCHEMA,       _handle_tc),
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
    # Project State Evolution
    ("update_project_state",   "lexitool", UPDATE_PROJECT_STATE_SCHEMA,   _handle_update_project_state),
    ("get_project_state",      "lexitool", GET_PROJECT_STATE_SCHEMA,      _handle_get_project_state),
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
    """Hot-reload all lexitool tools by re-importing this module."""
    before = set(registry.get_tool_names_for_toolset("lexitool"))

    for name in list(before):
        registry.deregister(name)

    import importlib
    import tools.lexitool_tool
    importlib.reload(tools.lexitool_tool)

    invalidate_check_fn_cache()

    after = set(registry.get_tool_names_for_toolset("lexitool"))
    return {
        "deregistered": len(before),
        "reregistered": len(after),
        "tools": sorted(after),
    }
