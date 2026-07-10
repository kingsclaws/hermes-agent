"""
markup.py — Bidirectional bridge between OOXML and annotated plain text.

Export: OOXML (document.xml) → annotated text with inline format tags
  §1 [b]bold text[/b] [font:宋体,12pt]Chinese text[/font][page-break]

Import: annotated text + target syntax → targeted OOXML edits
  §3:5-10 → paragraph 3, characters 5-10

This is the core innovation of lexitool — making formatting visible to AI agents.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

# ── Export: OOXML → annotated text ───────────────────────────────────────────

# Mapping from run-property tag to markup tag
_RPR_TAG_MAP = [
    ("w:b", "b"),       ("w:bCs", None),    # bCs is companion to b
    ("w:i", "i"),       ("w:iCs", None),    # iCs is companion to i
    ("w:u", "u"),
    ("w:strike", "s"),  ("w:dstrike", "s"),
    ("w:vertAlign", "vertAlign"),  # superscript/subscript
    ("w:highlight", "highlight"),
]

# Paragraph-level property tags
_PPR_TAG_MAP = {
    "w:jc": "align",
    "w:spacing": "spacing",
    "w:ind": "indent",
}


def _parse_font_info(rPr) -> str | None:
    """Extract font info from w:rPr/w:rFonts and w:sz/w:szCs."""
    if rPr is None:
        return None
    rFonts = rPr.find(f"{W}rFonts")
    sz = rPr.find(f"{W}sz")
    szCs = rPr.find(f"{W}szCs")
    color = rPr.find(f"{W}color")

    parts = []
    if rFonts is not None:
        eastAsia = rFonts.get(f"{W}eastAsia", "")
        ascii_font = rFonts.get(f"{W}ascii", "")
        hAnsi = rFonts.get(f"{W}hAnsi", "")
        if eastAsia:
            parts.append(eastAsia)
        elif ascii_font:
            parts.append(ascii_font)
        elif hAnsi:
            parts.append(hAnsi)

    # sz is in half-points; convert to pt
    sz_val = None
    if szCs is not None:
        sz_val = szCs.get(f"{W}val")
    elif sz is not None:
        sz_val = sz.get(f"{W}val")
    if sz_val:
        try:
            pts = float(sz_val) / 2
            pts_str = f"{pts:g}pt"
            parts.append(pts_str)
        except (ValueError, TypeError):
            pass

    if color is not None:
        color_val = color.get(f"{W}val", "")
        if color_val and color_val != "auto":
            parts.append(f"#{color_val}")

    return ",".join(parts) if parts else None


def _extract_run_format(rPr) -> dict[str, Any]:
    """Extract character formatting from a w:rPr element."""
    fmt: dict[str, Any] = {}
    if rPr is None:
        return fmt

    if rPr.find(f"{W}b") is not None or rPr.find(f"{W}bCs") is not None:
        fmt["b"] = True
    if rPr.find(f"{W}i") is not None or rPr.find(f"{W}iCs") is not None:
        fmt["i"] = True
    if rPr.find(f"{W}u") is not None:
        u_el = rPr.find(f"{W}u")
        val = u_el.get(f"{W}val", "single")
        fmt["u"] = val if val != "none" else False
    if rPr.find(f"{W}strike") is not None or rPr.find(f"{W}dstrike") is not None:
        fmt["s"] = True
    if rPr.find(f"{W}highlight") is not None:
        hl = rPr.find(f"{W}highlight")
        fmt["highlight"] = hl.get(f"{W}val", "yellow")

    font_info = _parse_font_info(rPr)
    if font_info:
        fmt["font"] = font_info

    return fmt


def _fmt_tags_open(fmt: dict[str, Any]) -> str:
    """Generate opening format tags from format dict."""
    tags = []
    if fmt.get("b"):
        tags.append("[b]")
    if fmt.get("i"):
        tags.append("[i]")
    if fmt.get("u"):
        tags.append("[u]")
    if fmt.get("s"):
        tags.append("[s]")
    if fmt.get("highlight"):
        tags.append(f"[highlight:{fmt['highlight']}]")
    if fmt.get("font"):
        tags.append(f"[font:{fmt['font']}]")
    return "".join(tags)


def _fmt_tags_close(fmt: dict[str, Any]) -> str:
    """Generate closing format tags in reverse order."""
    tags = []
    if fmt.get("font"):
        tags.append("[/font]")
    if fmt.get("highlight"):
        tags.append("[/highlight]")
    if fmt.get("s"):
        tags.append("[/s]")
    if fmt.get("u"):
        tags.append("[/u]")
    if fmt.get("i"):
        tags.append("[/i]")
    if fmt.get("b"):
        tags.append("[/b]")
    return "".join(tags)


def _extract_para_markers(pPr) -> list[str]:
    """Extract paragraph-level markers from w:pPr."""
    markers = []
    if pPr is None:
        return markers

    # Numbering (bullet/num)
    numPr = pPr.find(f"{W}numPr")
    if numPr is not None:
        ilvl = numPr.find(f"{W}ilvl")
        numId = numPr.find(f"{W}numId")
        ilvl_val = ilvl.get(f"{W}val", "0") if ilvl is not None else "0"
        if numId is not None:
            markers.append(f"[num:{ilvl_val}]")

    # Spacing
    spacing = pPr.find(f"{W}spacing")
    if spacing is not None:
        line = spacing.get(f"{W}line")
        lineRule = spacing.get(f"{W}lineRule")
        if line:
            try:
                line_val = int(line) / 240
                if lineRule == "exact":
                    markers.append(f"[spacing:exact,{line_val:g}pt]")
                elif line_val != 1.0:
                    markers.append(f"[spacing:{line_val:g}]")
            except (ValueError, TypeError):
                pass

    # Indent
    ind = pPr.find(f"{W}ind")
    if ind is not None:
        firstLine = ind.get(f"{W}firstLine")
        if firstLine:
            try:
                chars = int(firstLine) / 240
                if chars > 0:
                    markers.append(f"[indent:{chars:g}ch]")
            except (ValueError, TypeError):
                pass

    # Alignment
    jc = pPr.find(f"{W}jc")
    if jc is not None:
        val = jc.get(f"{W}val", "")
        if val and val != "left":
            markers.append(f"[align:{val}]")

    return markers


def _infer_heading(para_el, pPr) -> str | None:
    """Detect paragraphs that look like headings but lack Word heading styles.

    Many legal templates format headings manually (bold + larger font +
    centered/no indent) instead of using Heading 1/2 styles. This heuristic
    marks such paragraphs as [heading:inferred] so AI agents can recognize
    the document structure.

    Only fires when the paragraph has NO explicit outline level or heading
    style. Short paragraphs (< 60 chars) with bold + no first-line indent
    are strong heading candidates.
    """
    if pPr is None:
        pPr = para_el.find(f"{W}pPr")
    # Allow None pPr — default formatting from Normal style; we'll check
    # run-level bold/size heuristics instead of requiring pPr.

    # Never override explicit outline level or heading style
    if pPr is not None:
        pStyle = pPr.find(f"{W}pStyle")
        if pStyle is not None:
            style_id = (pStyle.get(f"{W}val", "") or "").lower()
            if "heading" in style_id or "标题" in style_id or style_id.startswith("toc"):
                return None
        if pPr.find(f"{W}outlineLvl") is not None:
            return None

        # Check for numbering — numbered paragraphs are content, not headings
        numPr = pPr.find(f"{W}numPr")
        if numPr is not None and numPr.find(f"{W}numId") is not None:
            return None

    # Find the first text-bearing run
    first_text_run = None
    for child in para_el:
        if child.tag == f"{W}r":
            if child.find(f"{W}t") is not None:
                first_text_run = child
                break
        elif child.tag in (f"{W}ins", f"{W}del"):
            for r in child.findall(f"{W}r"):
                if r.find(f"{W}t") is not None:
                    first_text_run = r
                    break
            if first_text_run is not None:
                break

    if first_text_run is None:
        return None

    rPr = first_text_run.find(f"{W}rPr")
    if rPr is None:
        return None

    b = rPr.find(f"{W}b")
    if b is None:
        return None  # Must be bold

    # Check for no paragraph indentation
    if pPr is not None:
        ind = pPr.find(f"{W}ind")
        if ind is not None:
            first_line = ind.get(f"{W}firstLine")
            if first_line is not None:
                try:
                    if int(first_line) > 0:
                        return None  # Has first-line indent → body text
                except (ValueError, TypeError):
                    pass

        # Check center alignment (strong heading signal)
        jc = pPr.find(f"{W}jc")
        is_centered = jc is not None and jc.get(f"{W}val", "") == "center"
    else:
        is_centered = False

    # Aggregate paragraph text length
    text_len = len("".join(
        (t.text or "") for t in para_el.iter(f"{W}t")
    ).strip())

    if text_len == 0 or text_len > 80:
        return None  # Too short or too long for a heading

    # Check if font size is >= body text size
    sz = rPr.find(f"{W}sz")
    szCs = rPr.find(f"{W}szCs")
    sz_val = None
    if sz is not None:
        try:
            sz_val = int(sz.get(f"{W}val", "0")) / 2  # half-points → pt
        except (ValueError, TypeError):
            pass
    if sz_val is None and szCs is not None:
        try:
            sz_val = int(szCs.get(f"{W}val", "0")) / 2
        except (ValueError, TypeError):
            pass

    if sz_val is not None and sz_val < 11:
        return None  # Too small for a heading font

    if is_centered and text_len <= 40:
        return "[heading:inferred,l1]"

    # Short bold text without indent — likely a heading
    if text_len <= 50:
        return "[heading:inferred,l2]"

    return "[heading:inferred,l3]"


def export_paragraphs(
    doc_path: str,
    para_indices: list[int] | None = None,
    show_tc: bool | str = True,
    show_format: bool = True,
    include_comments: bool = False,
    comment_map: dict[int, list[str]] | None = None,
) -> str:
    """Read a .docx file and return annotated text with inline format markup.

    Args:
        doc_path: Path to .docx file.
        para_indices: 1-indexed paragraph numbers to export (None = all).
        show_tc: Track Changes mode.
            True or "all" — show [ins]/[del] markup (default).
            "final" — accept all revisions (show insertions, hide deletions).
            "original" — reject all revisions (show deletions, hide insertions).
            False — no Track Changes markup at all.
        show_format: Include format tags ([b], [font:...], etc.).
        include_comments: Append inline [comment:author text] markers.
        comment_map: Pre-built dict mapping paragraph index to list of comment texts.
            If None and include_comments is True, comments are parsed from the docx.

    Returns:
        Annotated text with §-prefixed paragraph markers.
    """
    # Normalize show_tc parameter
    tc_mode = "all"
    if show_tc is False:
        tc_mode = "none"
    elif show_tc is True:
        tc_mode = "all"
    elif isinstance(show_tc, str):
        tc_mode = show_tc.lower()
        if tc_mode not in ("all", "final", "original", "none"):
            tc_mode = "all"
    with zipfile.ZipFile(doc_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return ""

    # Coerce to int (LLMs sometimes pass strings despite integer schema)
    para_indices = [int(p) for p in para_indices] if para_indices else None
    para_set = set(para_indices) if para_indices else None
    para_min = min(para_indices) if para_indices else 0
    para_max = max(para_indices) if para_indices else 0
    lines: list[str] = []
    para_count = 0
    # Track bookmark starts/ends across paragraphs
    open_bookmarks: list[str] = []
    # Body text stats for heading inference
    first_para_runs: list[dict] = []
    body_font_sz_hints: dict[str, float] = {}
    # Collect paragraph positions (element index → paragraph number)
    para_positions: dict[int, int] = {}  # lxml element position → 1-based para num

    for child in body:
        # Unwrap sdtContent containers that may wrap tables or paragraphs
        actual = child
        if child.tag in (f"{W}sdt", f"{W}customXml"):
            sdt_content = child.find(f"{W}sdtContent")
            if sdt_content is not None and len(sdt_content) == 1:
                actual = sdt_content[0]

        if actual.tag == f"{W}p":
            para_count += 1
            if para_set is not None and para_count not in para_set:
                continue

            # Track para position for comment anchoring
            para_positions[para_count] = para_count

            line = _export_paragraph(
                actual, para_count, tc_mode, show_format, open_bookmarks,
            )
            if include_comments and comment_map and para_count in comment_map:
                for c in comment_map[para_count]:
                    line += f" [comment:{c}]"
            lines.append(line)

        elif actual.tag == f"{W}tbl":
            # Include table when reading all paragraphs, or when it falls
            # within (or immediately before) the requested paragraph range.
            if para_set is None or (para_min - 1 <= para_count <= para_max):
                table_text = _export_table(actual, tc_mode, show_format)
                if table_text:
                    lines.append(table_text)

    return "\n".join(lines)


def _count_tc_segments(para_el) -> int:
    """Count the number of w:ins and w:del elements in a paragraph."""
    count = 0
    for child in para_el.iter():
        if child.tag in (f"{W}ins", f"{W}del"):
            count += 1
    return count


def _export_paragraph(
    para_el, para_num: int, tc_mode: str, show_format: bool,
    open_bookmarks: list[str],
) -> str:
    """Export a single paragraph to annotated text."""
    parts = [f"§{para_num} "]

    pPr = para_el.find(f"{W}pPr")

    # TC density warning: if a paragraph has many TC alternations,
    # the [ins]/[del] markup is hard to parse for both humans and AI.
    if tc_mode == "all":
        tc_count = _count_tc_segments(para_el)
        if tc_count > 5:
            parts.append(f"[tc-density:high,{tc_count}-segments] ")

    # Paragraph-level markers
    if show_format:
        markers = _extract_para_markers(pPr)
        # Heading inference: detect manual headings where Word style is
        # not used but visual formatting clearly indicates a heading.
        if not any(m.startswith("[outline:") or m.startswith("[heading:")
                   for m in markers):
            heading_marker = _infer_heading(para_el, pPr)
            if heading_marker:
                markers.insert(0, heading_marker)
        for m in markers:
            parts.append(m)

    # Collect runs with their format context
    _export_runs(para_el, parts, tc_mode, show_format, open_bookmarks)

    # Detect breaks at paragraph level
    if show_format:
        _export_breaks(para_el, parts)

    return "".join(parts)


def _export_runs(
    para_el, parts: list[str], tc_mode: str, show_format: bool,
    open_bookmarks: list[str],
) -> None:
    """Process all runs, bookmarks, and TC wrappers in a paragraph."""
    # Build a flat list of "segments" with their TC context
    segments = _collect_segments(para_el, tc_mode)

    current_fmt: dict[str, Any] = {}
    current_tc: str | None = None

    for seg in segments:
        # Handle bookmark boundaries
        for bm_name in seg.get("bm_starts", []):
            parts.append(f"[bookmark:{bm_name}]")
        for bm_name in seg.get("bm_ends", []):
            parts.append(f"[/bookmark:{bm_name}]")

        # Handle field reference markers (REF, PAGEREF, NOTEREF, STYLEREF)
        # When a segment has a field marker, emit ONLY the marker (not the display text)
        # since the marker communicates what the field points to.
        has_field_marker = False
        if seg.get("ref_name"):
            parts.append(f"[ref:{seg['ref_name']}]")
            has_field_marker = True
        elif seg.get("pageref_name"):
            parts.append(f"[page-ref:{seg['pageref_name']}]")
            has_field_marker = True
        elif seg.get("noteref_name"):
            parts.append(f"[note-ref:{seg['noteref_name']}]")
            has_field_marker = True
        elif seg.get("styleref_name"):
            parts.append(f"[style-ref:{seg['styleref_name']}]")
            has_field_marker = True

        if has_field_marker:
            continue

        if not seg.get("text"):
            continue

        # Handle TC state changes — need to track which tag is open
        if tc_mode == "all":
            seg_tc = seg.get("tc")
            if seg_tc != current_tc:
                if current_tc:
                    parts.append(f"[/{current_tc}]")
                if seg_tc:
                    parts.append(f"[{seg_tc}]")
                current_tc = seg_tc

        if show_format:
            fmt = seg["format"]
            # Close tags that changed
            close_tags = []
            for key in list(current_fmt.keys()):
                if key not in fmt or fmt[key] != current_fmt[key]:
                    close_tags.append(key)
            # Close in reverse order
            for key in reversed(close_tags):
                tag_map = {"b": "[/b]", "i": "[/i]", "u": "[/u]", "s": "[/s]",
                           "highlight": "[/highlight]", "font": "[/font]"}
                parts.append(tag_map.get(key, ""))
                del current_fmt[key]

            # Open new/changed tags
            for key, val in fmt.items():
                if key not in current_fmt or current_fmt[key] != val:
                    if key == "b":
                        parts.append("[b]")
                    elif key == "i":
                        parts.append("[i]")
                    elif key == "u":
                        parts.append("[u]")
                    elif key == "s":
                        parts.append("[s]")
                    elif key == "highlight":
                        parts.append(f"[highlight:{val}]")
                    elif key == "font":
                        parts.append(f"[font:{val}]")
                    current_fmt[key] = val

        parts.append(seg["text"])

    # Close any remaining format tags
    if show_format:
        for key in reversed(list(current_fmt.keys())):
            tag_map = {"b": "[/b]", "i": "[/i]", "u": "[/u]", "s": "[/s]",
                       "highlight": "[/highlight]", "font": "[/font]"}
            parts.append(tag_map.get(key, ""))

    # Close any remaining TC tag
    if current_tc:
        parts.append(f"[/{current_tc}]")


def _collect_segments(para_el, tc_mode: str = "all") -> list[dict]:
    """Walk paragraph children and collect text segments with TC/format context.

    Also detects Word field codes (REF, PAGEREF, NOTEREF, STYLEREF) and
    emits [ref:name], [page-ref:name], etc. markers.

    tc_mode controls how Track Changes content is collected:
      "all"      — show all TC content with [ins]/[del] markers (default)
      "final"    — accept all revisions: drop deletions, show insertions as plain
      "original" — reject all revisions: drop insertions, show deletions as plain
      "none"     — strip all TC markers (plain text only)
    """
    segments: list[dict] = []
    bm_id_to_name: dict[str, str] = {}

    # Field state machine
    in_field = False
    field_instr = ""
    field_display = ""

    for child in para_el:
        # ── Field code detection ──
        if child.tag == f"{W}r":
            fld_char = child.find(f"{W}fldChar")
            if fld_char is not None:
                fld_type = fld_char.get(f"{W}fldCharType", "")
                if fld_type == "begin":
                    in_field = True
                    field_instr = ""
                    field_display = ""
                    continue
                elif fld_type == "separate":
                    # End of instruction, start of display text
                    continue
                elif fld_type == "end" and in_field:
                    in_field = False
                    ft = _field_type_from_instr(field_instr)
                    name = _field_name_from_instr(field_instr)
                    if ft == "REF" and name:
                        segments.append({
                            "text": field_display or f"[{name}]",
                            "format": {}, "tc": None,
                            "ref_name": name,
                        })
                    elif ft == "PAGEREF" and name:
                        segments.append({
                            "text": field_display or f"[p.{name}]",
                            "format": {}, "tc": None,
                            "pageref_name": name,
                        })
                    elif ft == "NOTEREF" and name:
                        segments.append({
                            "text": field_display or f"[fn.{name}]",
                            "format": {}, "tc": None,
                            "noteref_name": name,
                        })
                    elif ft == "STYLEREF" and name:
                        segments.append({
                            "text": field_display or f"[style:{name}]",
                            "format": {}, "tc": None,
                            "styleref_name": name,
                        })
                    else:
                        # Other field (PAGE, NUMPAGES, TOC, etc.) — keep display text
                        if field_display:
                            segments.append({
                                "text": field_display,
                                "format": {}, "tc": None,
                            })
                    continue

            if in_field:
                instr_el = child.find(f"{W}instrText")
                if instr_el is not None:
                    field_instr += instr_el.text or ""
                    continue
                # After separate, collect display text
                t_el = child.find(f"{W}t")
                if t_el is not None:
                    field_display += t_el.text or ""
                    continue

            # Regular run
            seg = _segment_from_run(child)
            segments.append(seg)

        elif child.tag == f"{W}ins":
            for r in child.findall(f"{W}r"):
                seg = _segment_from_run(r)
                seg["tc"] = "ins"
                segments.append(seg)

        elif child.tag == f"{W}del":
            for r in child.findall(f"{W}r"):
                seg = _segment_from_run(r, is_del=True)
                seg["tc"] = "del"
                segments.append(seg)

        elif child.tag == f"{W}bookmarkStart":
            name = child.get(f"{W}name", "")
            bm_id = child.get(f"{W}id", "")
            if bm_id and name:
                bm_id_to_name[bm_id] = name
            if name:
                if segments:
                    segments[-1].setdefault("bm_starts", []).append(name)
                else:
                    segments.append({"text": "", "format": {}, "tc": None,
                                     "bm_starts": [name], "bm_ends": []})

        elif child.tag == f"{W}bookmarkEnd":
            # bookmarkEnd only has w:id, look up name from w:bookmarkStart
            bm_id = child.get(f"{W}id", "")
            name = bm_id_to_name.get(bm_id, bm_id)
            if bm_id:
                if segments:
                    segments[-1].setdefault("bm_ends", []).append(name)
                else:
                    segments.append({"text": "", "format": {}, "tc": None,
                                     "bm_starts": [], "bm_ends": [name]})

        elif child.tag == f"{W}br":
            br_type = child.get(f"{W}type", "")
            if br_type == "page":
                segments.append({"text": "[page-break]", "format": {}, "tc": None})
            elif br_type == "column":
                segments.append({"text": "[column-break]", "format": {}, "tc": None})
            else:
                segments.append({"text": "[line-break]", "format": {}, "tc": None})

        # Recurse into wrapper elements that can contain w:r, w:ins, w:del
        elif child.tag in _TC_WRAPPER_TAGS:
            _collect_segments_from_wrapper(child, segments)

    # Apply TC mode filtering
    if tc_mode == "final":
        segments = [s for s in segments if s.get("tc") != "del"]
        for s in segments:
            if s.get("tc") == "ins":
                s["tc"] = None
    elif tc_mode == "original":
        segments = [s for s in segments if s.get("tc") != "ins"]
        for s in segments:
            if s.get("tc") == "del":
                s["tc"] = None
    elif tc_mode == "none":
        for s in segments:
            s["tc"] = None

    return segments


_TC_WRAPPER_TAGS = {
    f"{W}smartTag", f"{W}moveFrom", f"{W}moveTo",
    f"{W}customXmlMoveFromRangeStart", f"{W}customXmlMoveToRangeStart",
    f"{W}sdt",
}


def _collect_segments_from_wrapper(wrapper_el, segments: list[dict]) -> None:
    """Recurse into wrapper elements that may contain w:r, w:del, or w:ins."""
    children = wrapper_el
    # w:sdt wraps content in w:sdtContent
    if wrapper_el.tag == f"{W}sdt":
        sdt_content = wrapper_el.find(f"{W}sdtContent")
        if sdt_content is not None:
            children = sdt_content

    for child in children:
        if child.tag == f"{W}r":
            seg = _segment_from_run(child)
            segments.append(seg)
        elif child.tag == f"{W}ins":
            for r in child.findall(f"{W}r"):
                seg = _segment_from_run(r)
                seg["tc"] = "ins"
                segments.append(seg)
        elif child.tag == f"{W}del":
            for r in child.findall(f"{W}r"):
                seg = _segment_from_run(r, is_del=True)
                seg["tc"] = "del"
                segments.append(seg)


def _field_type_from_instr(instr: str) -> str:
    """Extract field type (e.g. REF, PAGEREF) from instruction text."""
    parts = instr.strip().split()
    return parts[0].upper() if parts else ""


def _field_name_from_instr(instr: str) -> str:
    """Extract bookmark/style name from field instruction."""
    parts = instr.strip().split()
    if len(parts) < 2:
        return ""
    # Skip switches like \h, \p, \* MERGEFORMAT
    for p in parts[1:]:
        if p.startswith("\\"):
            continue
        return p.strip('"')
    return ""


def _segment_from_run(r_el, is_del: bool = False) -> dict:
    """Extract text and format from a w:r element."""
    rPr = r_el.find(f"{W}rPr")
    fmt = _extract_run_format(rPr) if rPr is not None else {}

    text_tag = f"{W}delText" if is_del else f"{W}t"
    text_parts = []
    # Use iter() not direct children — w:delText may be wrapped inside
    # additional elements in some OOXML variants.
    for child in r_el.iter():
        if child.tag == text_tag:
            text_parts.append(child.text or "")
        elif child.tag == f"{W}tab":
            text_parts.append("\t")
        elif child.tag == f"{W}br":
            text_parts.append("\n")
        # Fallback: some OOXML writers put deleted text in w:t instead of w:delText
        elif is_del and child.tag == f"{W}t" and text_tag != f"{W}t":
            text_parts.append(child.text or "")

    return {"text": "".join(text_parts), "format": fmt, "tc": None}


def _export_breaks(para_el, parts: list[str]) -> None:
    """Detect page/column/section breaks in paragraph properties."""
    pPr = para_el.find(f"{W}pPr")
    if pPr is None:
        return

    # w:sectPr can appear inside the last paragraph
    sectPr = pPr.find(f"{W}sectPr")
    if sectPr is not None:
        sect_type = sectPr.find(f"{W}type")
        if sect_type is not None:
            val = sect_type.get(f"{W}val", "")
            if val == "nextPage":
                parts.append("[section-break:next]")
            elif val == "continuous":
                parts.append("[section-break:continuous]")


def _export_table(tbl_el, tc_mode: str, show_format: bool) -> str:
    """Export a table as a structured text block.

    Handles merged cells (gridSpan / vMerge), sdtContent wrappers around
    rows and cells, and TC markup inside table cell runs.
    """
    # Collect all table rows (use iter() to find rows inside sdtContent wrappers)
    rows: list = []
    for el in tbl_el.iter(f"{W}tr"):
        # Only include rows that are descendants of THIS table, not nested tables
        parent_tbl = el.getparent()
        if parent_tbl is not None:
            # Walk up to find the nearest w:tbl ancestor
            ancestor = parent_tbl
            while ancestor is not None and ancestor.tag != f"{W}tbl":
                ancestor = ancestor.getparent()
            if ancestor is not tbl_el:
                continue  # This row belongs to a nested table
        rows.append(el)

    if not rows:
        return ""

    # First pass: determine max columns per row (accounting for gridSpan)
    max_cols = 0
    row_grids: list[list[int]] = []  # gridSpan per cell per row

    for row in rows:
        cells = _get_row_cells(row)
        cell_spans = []
        for cell in cells:
            span = _get_grid_span(cell)
            cell_spans.append(span)
        row_grids.append(cell_spans)
        total_spans = sum(cell_spans)
        if total_spans > max_cols:
            max_cols = total_spans

    # Second pass: emit rows, handling vertical merges with carry-forward
    prev_row_cells: list[str] = []
    lines = []

    for row_idx, row in enumerate(rows):
        cells = _get_row_cells(row)
        cell_texts: list[str] = []
        col_idx = 0

        for cell_idx, cell in enumerate(cells):
            grid_span = row_grids[row_idx][cell_idx] if cell_idx < len(row_grids[row_idx]) else 1
            vmerge = _get_vmerge(cell)

            # Extract cell text (handle sdtContent wrappers around paragraphs)
            cell_parts = []
            for p in cell.iter(f"{W}p"):
                cell_parts.append(_get_para_plain_text(p, tc_mode))
            text = "".join(cell_parts).strip()

            if vmerge == "continue":
                # Vertically merged cell — carry forward from previous row
                if col_idx < len(prev_row_cells):
                    text = prev_row_cells[col_idx]
                else:
                    text = "(merged)"

            # Pad for gridSpan > 1: repeat the cell text (or leave empty for merged)
            for _ in range(grid_span):
                cell_texts.append(text)
                col_idx += 1

        # Pad row to max_cols
        while len(cell_texts) < max_cols:
            cell_texts.append("")

        # Store for vertical merge carry-forward
        prev_row_cells = list(cell_texts)
        lines.append(" | ".join(cell_texts))

    result = "[table]\n" + "\n".join(f"  {line}" for line in lines)
    return result


def _get_row_cells(row) -> list:
    """Get w:tc elements from a row, unwrapping sdtContent containers."""
    cells = []
    for child in row:
        if child.tag == f"{W}tc":
            cells.append(child)
        elif child.tag == f"{W}sdt":
            sdt_content = child.find(f"{W}sdtContent")
            if sdt_content is not None:
                for inner in sdt_content:
                    if inner.tag == f"{W}tc":
                        cells.append(inner)
    return cells


def _get_grid_span(cell) -> int:
    """Return the gridSpan value for a cell (default 1)."""
    tcPr = cell.find(f"{W}tcPr")
    if tcPr is None:
        return 1
    grid_span = tcPr.find(f"{W}gridSpan")
    if grid_span is None:
        return 1
    try:
        return int(grid_span.get(f"{W}val", "1"))
    except (ValueError, TypeError):
        return 1


def _get_vmerge(cell) -> str | None:
    """Return the vMerge value for a cell: 'restart', 'continue', or None."""
    tcPr = cell.find(f"{W}tcPr")
    if tcPr is None:
        return None
    vmerge = tcPr.find(f"{W}vMerge")
    if vmerge is None:
        return None
    val = vmerge.get(f"{W}val", "continue")
    return val or "continue"


def _get_para_plain_text(para_el, tc_mode: str = "none") -> str:
    """Get plain text from a paragraph element, optionally including TC markup.

    tc_mode:
      "all"      — show [del] markers for w:delText
      "final"    — accept deletions: skip w:delText
      "original" — reject deletions: show w:delText as plain text (no markers)
      "none"     — no TC markers (default for this function)
    """
    parts = []
    for child in para_el.iter():
        if child.tag == f"{W}t":
            parts.append(child.text or "")
        elif child.tag == f"{W}delText":
            if tc_mode == "all":
                if child.text:
                    parts.append(f"[del]{child.text}[/del]")
            elif tc_mode == "original":
                parts.append(child.text or "")
            elif tc_mode == "final":
                pass  # Skip deleted text in final mode
            else:
                parts.append(child.text or "")
        elif child.tag == f"{W}tab":
            parts.append("\t")
        elif child.tag == f"{W}br":
            parts.append("\n")
    return "".join(parts)


# ── Import: target parsing ────────────────────────────────────────────────────

@dataclass
class TargetRef:
    """Parsed edit target reference."""
    para_start: int          # 1-indexed paragraph number
    para_end: int | None = None   # for paragraph ranges (§3-7)
    char_start: int | None = None  # character offset within paragraph
    char_end: int | None = None
    run_index: int | None = None   # run index within paragraph


_TARGET_RE = re.compile(
    r"^§(\d+)"                       # §3
    r"(?:-(\d+))?"                    # §3-7 (paragraph range)
    r"(?::r(\d+))?"                   # §3:r2 (run)
    r"(?::(\d+)-(\d+))?$"            # §3:5-10 or §3:r2:5-10
)


def parse_target(target: str) -> TargetRef:
    """Parse a target string like '§3', '§3:5-10', '§3:r2', '§3:r2:5-10'.

    Raises ValueError if the target syntax is invalid.
    """
    m = _TARGET_RE.match(target.strip())
    if not m:
        raise ValueError(
            f"Invalid target syntax: {target!r}. "
            f"Expected §N, §N-M, §N:X-Y, §N:rM, or §N:rM:X-Y"
        )

    para_start = int(m.group(1))
    para_end = int(m.group(2)) if m.group(2) else None
    run_index = int(m.group(3)) if m.group(3) else None

    if m.group(4) is not None:
        char_start = int(m.group(4))
        char_end = int(m.group(5))
    else:
        char_start = None
        char_end = None

    return TargetRef(
        para_start=para_start,
        para_end=para_end,
        char_start=char_start,
        char_end=char_end,
        run_index=run_index,
    )


# ── Import: markup parsing ────────────────────────────────────────────────────

@dataclass
class MarkupToken:
    """A parsed token from annotated text."""
    type: str  # "text", "tag_open", "tag_close", "void_tag"
    text: str = ""
    tag_name: str = ""
    tag_value: str = ""


# Regex for inline tags: [tag] or [tag:value] or [/tag]
_TAG_RE = re.compile(r"\[/?[a-z][a-z0-9_:,\-#]*\]")


def _tokenize_markup(text: str) -> list[MarkupToken]:
    """Tokenize annotated text into markup tokens."""
    tokens: list[MarkupToken] = []
    pos = 0

    for m in _TAG_RE.finditer(text):
        # Text before this tag
        if m.start() > pos:
            tokens.append(MarkupToken(type="text", text=text[pos:m.start()]))

        raw = m.group()
        if raw == "[page-break]":
            tokens.append(MarkupToken(type="void_tag", tag_name="page-break"))
        elif raw == "[line-break]":
            tokens.append(MarkupToken(type="void_tag", tag_name="line-break"))
        elif raw == "[section-break:next]":
            tokens.append(MarkupToken(type="void_tag", tag_name="section-break", tag_value="next"))
        elif raw == "[section-break:continuous]":
            tokens.append(MarkupToken(type="void_tag", tag_name="section-break", tag_value="continuous"))
        elif raw.startswith("[/"):
            tag_inner = raw[2:-1]
            if ":" in tag_inner:
                tag_inner = tag_inner.split(":")[0]
            tokens.append(MarkupToken(type="tag_close", tag_name=tag_inner))
        else:
            tag_inner = raw[1:-1]  # strip [...]
            if ":" in tag_inner:
                name, value = tag_inner.split(":", 1)
                tokens.append(MarkupToken(type="tag_open", tag_name=name, tag_value=value))
            else:
                tokens.append(MarkupToken(type="tag_open", tag_name=tag_inner))

        pos = m.end()

    # Remaining text
    if pos < len(text):
        tokens.append(MarkupToken(type="text", text=text[pos:]))

    return tokens


def parse_format_from_markup(text: str) -> list[dict]:
    """Parse annotated text and return segments with format context.

    Returns list of {"text": str, "format": dict, "tc": str|None}.
    """
    tokens = _tokenize_markup(text)
    segments: list[dict] = []
    current_fmt: dict[str, Any] = {}
    current_tc: str | None = None

    for token in tokens:
        if token.type == "text":
            segments.append({
                "text": token.text,
                "format": dict(current_fmt),
                "tc": current_tc,
            })
        elif token.type == "tag_open":
            if token.tag_name == "b":
                current_fmt["b"] = True
            elif token.tag_name == "i":
                current_fmt["i"] = True
            elif token.tag_name == "u":
                current_fmt["u"] = True
            elif token.tag_name == "s":
                current_fmt["s"] = True
            elif token.tag_name == "font":
                current_fmt["font"] = token.tag_value
            elif token.tag_name == "highlight":
                current_fmt["highlight"] = token.tag_value
            elif token.tag_name == "ins":
                current_tc = "ins"
            elif token.tag_name == "del":
                current_tc = "del"
            elif token.tag_name in ("bookmark", "ref", "page-ref"):
                current_fmt[token.tag_name] = token.tag_value
        elif token.type == "tag_close":
            if token.tag_name == "b":
                current_fmt.pop("b", None)
            elif token.tag_name == "i":
                current_fmt.pop("i", None)
            elif token.tag_name == "u":
                current_fmt.pop("u", None)
            elif token.tag_name == "s":
                current_fmt.pop("s", None)
            elif token.tag_name == "font":
                current_fmt.pop("font", None)
            elif token.tag_name == "highlight":
                current_fmt.pop("highlight", None)
            elif token.tag_name in ("ins", "del"):
                current_tc = None
            elif token.tag_name == "bookmark":
                current_fmt.pop("bookmark", None)

    return segments


# ── Character-level resolution ────────────────────────────────────────────────

def _get_para_runs(para_el) -> list[dict]:
    """Get all runs in a paragraph with their text offsets.

    Returns list of {"element": lxml element, "start": int, "end": int, "text": str}.
    """
    runs = []
    pos = 0
    for child in para_el:
        if child.tag == f"{W}r":
            text = ""
            for sub in child:
                if sub.tag in (f"{W}t", f"{W}delText"):
                    text += sub.text or ""
                elif sub.tag == f"{W}tab":
                    text += "\t"
                elif sub.tag == f"{W}br":
                    text += "\n"
            runs.append({
                "element": child,
                "start": pos,
                "end": pos + len(text),
                "text": text,
            })
            pos += len(text)
    return runs


def resolve_target_in_doc(doc_path: str, target: TargetRef) -> dict:
    """Resolve a TargetRef to actual XML elements and offsets.

    Returns dict with keys: para_el, runs, char_start, char_end, etc.
    """
    with zipfile.ZipFile(doc_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        raise ValueError("document has no body")

    # Find target paragraph(s)
    paras = [el for el in body if el.tag == f"{W}p"]

    para_idx = target.para_start - 1  # convert to 0-indexed
    if para_idx < 0 or para_idx >= len(paras):
        raise ValueError(f"Paragraph {target.para_start} out of range (1-{len(paras)})")

    para_el = paras[para_idx]
    runs = _get_para_runs(para_el)

    result = {"para_el": para_el, "para_index": target.para_start, "runs": runs}

    if target.char_start is not None:
        result["char_start"] = target.char_start
        result["char_end"] = target.char_end or target.char_start

    if target.run_index is not None:
        if target.run_index < 0 or target.run_index >= len(runs):
            raise ValueError(
                f"Run {target.run_index} out of range in paragraph {target.para_start} "
                f"(has {len(runs)} runs)"
            )
        result["target_run"] = runs[target.run_index]

    return result


def _get_para_text(para_el) -> str:
    """Get the full text of a paragraph as plain text."""
    parts = []
    for el in para_el.iter():
        if el.tag in (f"{W}t", f"{W}delText"):
            parts.append(el.text or "")
        elif el.tag == f"{W}tab":
            parts.append("\t")
    return "".join(parts)


# ── Public API ────────────────────────────────────────────────────────────────

def _load_comment_map(docx_path: str) -> dict[int, list[str]]:
    """Parse word/comments.xml and return a dict mapping para index → comment texts.

    Comment references in comments.xml use w:annotationRef which points to
    the paragraph element the comment was attached to. We resolve these to
    the 1-indexed paragraph number from word/document.xml.
    """
    try:
        with zipfile.ZipFile(docx_path, "r") as zf:
            if "word/comments.xml" not in zf.namelist():
                return {}
            comments_xml = zf.read("word/comments.xml")
            doc_xml = zf.read("word/document.xml")
    except Exception:
        return {}

    # Build an element-address map: lxml element → 1-based para index
    doc_root = etree.fromstring(doc_xml)
    doc_body = doc_root.find(f"{W}body")
    if doc_body is None:
        return {}

    para_index_map: dict[str, int] = {}  # paraId → 1-based para index
    para_count = 0
    for child in doc_body:
        if child.tag == f"{W}p":
            para_count += 1
            para14_id = child.get(f"{W}paraId") or child.get(
                "{http://schemas.microsoft.com/office/word/2010/wordml}paraId", ""
            )
            # Also index by element position as fallback
            para14_id_fallback = child.get(
                "{http://schemas.microsoft.com/office/word/2010/wordml}paraId", ""
            )
            pid = para14_id or para14_id_fallback
            if pid:
                para_index_map[pid] = para_count

    # Parse comments
    comments_root = etree.fromstring(comments_xml)
    # Build: author name map
    people_map: dict[str, str] = {}
    for cmt in comments_root:
        if cmt.tag == f"{W}comment":
            cmt_id = cmt.get(f"{W}id", "")
            author = cmt.get(f"{W}author", "comment")
            text_parts = []
            for r in cmt.iter(f"{W}r"):
                t = r.find(f"{W}t")
                if t is not None and t.text:
                    text_parts.append(t.text)
            full_text = "".join(text_parts).strip()
            if not full_text:
                continue

            # Resolve comment to paragraph via annotationRef or direct child position
            # OOXML comments extended: w15:paraId attached to paragraphs
            # Simple approach: comments are usually about the paragraph they're near
            # We store by comment index for later attachment
            if cmt_id:
                people_map[cmt_id] = f"{author}: {full_text}"

    # Map comments to paragraphs via comment reference ranges in document.xml
    comment_map: dict[int, list[str]] = {}
    para_num = 0
    for child in doc_body:
        if child.tag == f"{W}p":
            para_num += 1
            # Check for commentRangeStart elements
            for crs in child.iter(f"{W}commentRangeStart"):
                cmt_id = crs.get(f"{W}id", "")
                if cmt_id and cmt_id in people_map:
                    comment_map.setdefault(para_num, []).append(people_map[cmt_id])

    return comment_map


def lex_read(
    path: str,
    paras: list[int] | None = None,
    mode: str = "full",
    show_tc: bool | str = True,
    show_format: bool = True,
    include_headers_footers: bool = True,
    include_comments: bool = False,
) -> str:
    """Read document content with inline format markup.

    Args:
        path: Path to .docx file.
        paras: Specific paragraphs (1-indexed), None = all.
        mode: "full" (all content), "structure" (headings only), "stats" (counts),
            "headers_footers" (only section header/footer mapping).
        show_tc: Track Changes mode.
            True or "all" — show [ins]/[del] markup (default).
            "final" — accept all revisions (show insertions, hide deletions).
            "original" — reject all revisions (show deletions, hide insertions).
            False — no Track Changes markup.
        show_format: Include format tags around styled text.
        include_headers_footers: Append header/footer text to full reads.
        include_comments: Append inline [comment:author: text] markers at
            paragraph level. Reads word/comments.xml from the .docx.

    Returns:
        Annotated text with §-prefixed paragraph markers.
    """
    if mode == "structure":
        return _export_structure(path)
    if mode == "stats":
        return _export_stats(path)
    if mode in {"headers_footers", "headers-footers", "hf"}:
        return _export_headers_footers(path)

    # Load comments if requested
    comment_map = None
    if include_comments:
        comment_map = _load_comment_map(path)

    body = export_paragraphs(
        path, paras, show_tc, show_format,
        include_comments=include_comments,
        comment_map=comment_map,
    )
    if include_headers_footers:
        hf = _export_headers_footers(path)
        if hf.strip() and hf.strip() != "(no headers or footers found)":
            return body.rstrip() + "\n\n" + hf
    return body


def _export_headers_footers(path: str) -> str:
    """Export Word section header/footer text without assigning body paragraph ids."""
    try:
        from .header_footer_ops import audit_all
    except Exception:
        return "(headers/footers unavailable: header_footer_ops import failed)"

    try:
        audit = audit_all(path)
    except Exception as exc:
        return f"(headers/footers unavailable: {exc})"

    sections = audit.get("sections") or []
    parts = audit.get("parts") or {}
    lines: list[str] = []

    if sections:
        lines.append("[headers-footers]")
        seen: set[tuple[str, str, str]] = set()
        for sec in sections:
            idx = sec.get("section_idx", "?")
            for kind, key in (("header", "headers"), ("footer", "footers")):
                for item in sec.get(key) or []:
                    text = " ".join(str(item.get("text") or "").split())
                    if not text:
                        continue
                    ref_type = str(item.get("type") or "default")
                    part = str(item.get("part_path") or item.get("part") or "")
                    marker = (kind, ref_type, part)
                    if marker in seen:
                        continue
                    seen.add(marker)
                    textbox = " textbox" if item.get("has_textbox") else ""
                    part_label = f" part={part}" if part else ""
                    lines.append(f"§HF{idx} [{kind}:{ref_type}{part_label}{textbox}] {text}")

    if not lines and parts:
        lines.append("[headers-footers]")
        for part, info in sorted(parts.items()):
            text = " ".join(str(info.get("text") or "").split())
            if not text:
                continue
            textbox = " textbox" if info.get("has_textbox") else ""
            lines.append(f"§HF [{info.get('kind', 'part')}:unknown part={part}{textbox}] {text}")

    return "\n".join(lines) if lines else "(no headers or footers found)"


def _export_structure(path: str) -> str:
    """Export document structure (headings and their outline levels)."""
    with zipfile.ZipFile(path, "r") as zf:
        doc_xml = zf.read("word/document.xml")

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return ""

    lines = []
    para_count = 0

    for child in body:
        if child.tag != f"{W}p":
            continue
        para_count += 1

        pPr = child.find(f"{W}pPr")
        if pPr is None:
            continue

        # Check for heading style
        pStyle = pPr.find(f"{W}pStyle")
        style_id = pStyle.get(f"{W}val", "") if pStyle is not None else ""

        outline_lvl = pPr.find(f"{W}outlineLvl")
        level = None
        if outline_lvl is not None:
            try:
                level = int(outline_lvl.get(f"{W}val", "9")) + 1
            except (ValueError, TypeError):
                pass

        is_heading = style_id and (style_id.lower().startswith("heading") or style_id.lower().startswith("toc"))

        if is_heading or level is not None:
            level_str = f"H{level}" if level else (style_id.replace("Heading", "H").replace("heading", "H") if is_heading else "?")
            text = _get_para_plain_text(child)[:120]
            lines.append(f"§{para_count} [{level_str}] {text}")

    return "\n".join(lines) if lines else "(no headings found)"


def _export_stats(path: str) -> str:
    """Export document statistics including table locations."""
    with zipfile.ZipFile(path, "r") as zf:
        doc_xml = zf.read("word/document.xml")

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    para_count = 0
    word_count = 0
    fonts = set()
    tc_ins = 0
    tc_del = 0
    sections = 0
    tables: list[dict] = []

    for child in body:
        if child.tag == f"{W}p":
            para_count += 1
            text = _get_para_plain_text(child)
            word_count += len(text)

            for rPr in child.iter(f"{W}rPr"):
                rFonts = rPr.find(f"{W}rFonts")
                if rFonts is not None:
                    for attr in ("eastAsia", "ascii", "hAnsi"):
                        val = rFonts.get(f"{W}{attr}", "")
                        if val:
                            fonts.add(val)

            tc_ins += len(child.findall(f".//{W}ins"))
            tc_del += len(child.findall(f".//{W}del"))

        elif child.tag == f"{W}tbl":
            rows = child.findall(f"{W}tr")
            ncols = 0
            if rows:
                ncols = len(rows[0].findall(f"{W}tc"))
            header = ""
            if rows:
                cells = rows[0].findall(f"{W}tc")
                parts = []
                for c in cells:
                    for p_el in c.findall(f"{W}p"):
                        parts.append(_get_para_plain_text(p_el))
                header = " | ".join(parts)[:120]
            tables.append({
                "after_para": para_count,
                "rows": len(rows),
                "cols": ncols,
                "header": header,
            })

        elif child.tag == f"{W}sectPr":
            sections += 1

    lines = [
        f"Paragraphs: {para_count}",
        f"Characters: {word_count}",
        f"Sections: {sections}",
        f"Tables: {len(tables)}",
    ]
    for i, t in enumerate(tables):
        lines.append(
            f"  Table {i}: after §{t['after_para']} | "
            f"{t['rows']}r × {t['cols']}c | header: {t['header']}"
        )
    lines.append(f"Fonts: {', '.join(sorted(fonts)) if fonts else '(none)'}")
    lines.append(f"TC Insertions: {tc_ins}")
    lines.append(f"TC Deletions: {tc_del}")

    return "\n".join(lines)
