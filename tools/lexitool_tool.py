"""lexitool — Atomic Word Document Manipulation for AI Agents.

Consolidates ~30 lex_docx tools into 8 focused tools:
  lex_read    — Read document content with inline format markup
  lex_stats   — Document statistics and diagnostics
  lex_edit    — Atomic text edits with optional TC tracking
  lex_format  — Apply formatting to ranges
  lex_list    — Bullet and numbered list management
  lex_ref     — Bookmarks and cross-references
  lex_section — Page/section/column layout
  lex_doc     — Document-level operations (create, clean, TOC, merge)
"""
from __future__ import annotations

import json
import logging
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
                "enum": ["full", "structure", "stats"],
                "description": "full=all content with markup, structure=headings only, stats=counts only. Default: full.",
            },
            "show_tc": {
                "type": "boolean",
                "description": "Include [ins]/[del] markup for Track Changes. Default: true.",
            },
            "show_format": {
                "type": "boolean",
                "description": "Include format tags. Set false for plain text. Default: true.",
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


# ── 3. lex_edit ───────────────────────────────────────────────────────────────

LEX_EDIT_SCHEMA = {
    "name": "lex_edit",
    "description": (
        "Atomically edit text in a .docx file. Supports replace, insert, delete, "
        "and set_format operations with optional Track Changes.\n\n"
        "Target syntax:\n"
        "  §3           = entire paragraph 3\n"
        "  §3:5-10      = characters 5-10 in paragraph 3\n"
        "  §3:r2        = run 2 in paragraph 3\n"
        "  §3:r2:5-10   = characters 5-10 in run 2 of paragraph 3\n"
        "  §3-7         = paragraphs 3 through 7\n\n"
        "Use lex_read first to see § numbers and format markup, "
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
                "enum": ["replace", "insert", "delete", "set_format"],
                "description": "Operation type.",
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
        },
        "required": ["path", "op", "target"],
    },
}


def _handle_edit(args: dict, **kwargs) -> str:
    from lxml import etree
    from lexitool.markup import parse_target
    from lexitool.edit_ops import _read_docx, _write_docx
    from lexitool.tc_utils import tc_replace_first_in_para, tc_ins_text, tc_del_paragraph

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    path = _resolve_path(args["path"])
    op = args["op"]
    target_str = args["target"]
    new_text = args.get("new_text", "")
    fmt = args.get("format")
    tc = args.get("tc", True)
    author = kwargs.get("author", "ai-agent")

    target = parse_target(target_str)
    doc_xml, other = _read_docx(path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    para_idx = target.para_start - 1
    paras = [el for el in body if el.tag == f"{W}p"]

    if para_idx < 0 or para_idx >= len(paras):
        return tool_error(f"Paragraph {target.para_start} out of range (1-{len(paras)})")

    para_el = paras[para_idx]
    tc_id = _next_tc_id_from_body(body)

    try:
        if op == "delete":
            tc_del_paragraph(para_el, tc_id, author)

        elif op == "replace" and target.char_start is not None:
            old_text = _get_para_text(para_el)[target.char_start:target.char_end]
            if tc:
                tc_replace_first_in_para(para_el, old_text, new_text, tc_id, author)
            else:
                _direct_replace(para_el, old_text, new_text)

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
    return tool_result({"ok": True, "op": op, "target": target_str, "para": target.para_start})


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
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    from lxml import etree
    for r_el in para_el.findall(f"{W}r"):
        for t_el in r_el.findall(f"{W}t"):
            if old_text in (t_el.text or ""):
                t_el.text = (t_el.text or "").replace(old_text, new_text, 1)
                return {"ok": True}
    return {"ok": False, "reason": "text not found"}


def _direct_insert(para_el, text: str, offset: int) -> None:
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    from lxml import etree
    r = etree.SubElement(para_el, f"{W}r")
    t = etree.SubElement(r, f"{W}t")
    t.text = text
    if text and (text[0] == " " or text[-1] == " "):
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


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
        "  list            — List all bookmarks in the document"
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
                "enum": ["add_bookmark", "remove_bookmark", "add_ref", "add_page_ref", "list"],
                "description": "Operation to perform.",
            },
            "name": {
                "type": "string",
                "description": "Bookmark name (required for add/remove/ref operations).",
            },
            "target_para": {
                "type": "integer",
                "description": "Paragraph number (1-indexed) for bookmark anchor or field insert point.",
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

    name = args.get("name")
    if not name and op != "list":
        return tool_error("'name' is required for this operation")

    target_para = args.get("target_para", 1)

    if op == "add_bookmark":
        result = bookmarks.add_bookmark(path, target_para - 1, name)
    elif op == "remove_bookmark":
        result = bookmarks.remove_bookmark(path, name)
    elif op == "add_ref":
        result = fields.insert_field(path, target_para - 1, 0, "REF", name, f"[{name}]")
    elif op == "add_page_ref":
        result = fields.insert_field(path, target_para - 1, 0, "PAGEREF", name, f"[p.{name}]")
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
        from lexitool.doc_create import create as doc_create
        path = _resolve_path(args.get("output", args.get("path", "/tmp/out.docx")))
        template = args.get("template")
        if template:
            template = _resolve_path(template)
        meta = args.get("metadata", {})
        result = doc_create(
            path,
            title=meta.get("title", "Untitled"),
            font_size=meta.get("font_size", 11.5),
        )
        return tool_result({"ok": True, "path": path, "title": meta.get("title", "Untitled")})

    elif op == "clean":
        from lexitool.cleanup import clean_docx
        path = _resolve_path(args["path"])
        clean_docx(path, args.get("output"))
        return tool_result({"ok": True, "path": path})

    elif op == "update_toc":
        from lexitool.toc_ops import update_toc
        path = _resolve_path(args["path"])
        update_toc(path)
        return tool_result({"ok": True, "path": path})

    elif op == "update_fields":
        from lexitool.fields import update_fields
        path = _resolve_path(args["path"])
        result = update_fields(path)
        return tool_result(result)

    return tool_error(f"Unknown op: {op}")


# ── Registration ──────────────────────────────────────────────────────────────

_TOOLS = [
    # Read
    ("lex_read",     "lexitool", LEX_READ_SCHEMA,     _handle_read),
    ("lex_stats",    "lexitool", LEX_STATS_SCHEMA,    _handle_stats),
    # Write
    ("lex_edit",     "lexitool", LEX_EDIT_SCHEMA,     _handle_edit),
    ("lex_format",   "lexitool", LEX_FORMAT_SCHEMA,   _handle_format),
    # Structure
    ("lex_list",     "lexitool", LEX_LIST_SCHEMA,     _handle_list),
    ("lex_ref",      "lexitool", LEX_REF_SCHEMA,      _handle_ref),
    # Layout
    ("lex_section",  "lexitool", LEX_SECTION_SCHEMA,  _handle_section),
    # Document
    ("lex_doc",      "lexitool", LEX_DOC_SCHEMA,      _handle_doc),
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
