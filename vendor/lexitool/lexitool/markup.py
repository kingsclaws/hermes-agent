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


def export_paragraphs(
    doc_path: str,
    para_indices: list[int] | None = None,
    show_tc: bool = True,
    show_format: bool = True,
) -> str:
    """Read a .docx file and return annotated text with inline format markup.

    Args:
        doc_path: Path to .docx file.
        para_indices: 1-indexed paragraph numbers to export (None = all).
        show_tc: Include [ins]/[del] markup around tracked changes.
        show_format: Include format tags ([b], [font:...], etc.).

    Returns:
        Annotated text with §-prefixed paragraph markers.
    """
    with zipfile.ZipFile(doc_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return ""

    para_set = set(para_indices) if para_indices else None
    lines: list[str] = []
    para_count = 0
    # Track bookmark starts/ends across paragraphs
    open_bookmarks: list[str] = []

    for child in body:
        if child.tag == f"{W}p":
            para_count += 1
            if para_set is not None and para_count not in para_set:
                continue

            line = _export_paragraph(
                child, para_count, show_tc, show_format, open_bookmarks
            )
            lines.append(line)

        elif child.tag == f"{W}tbl" and para_set is None:
            # Export tables as structured text when exporting all paragraphs
            table_text = _export_table(child, show_tc, show_format)
            if table_text:
                lines.append(table_text)

    return "\n".join(lines)


def _export_paragraph(
    para_el, para_num: int, show_tc: bool, show_format: bool,
    open_bookmarks: list[str],
) -> str:
    """Export a single paragraph to annotated text."""
    parts = [f"§{para_num} "]

    pPr = para_el.find(f"{W}pPr")

    # Paragraph-level markers
    if show_format:
        markers = _extract_para_markers(pPr)
        for m in markers:
            parts.append(m)

    # Collect runs with their format context
    _export_runs(para_el, parts, show_tc, show_format, open_bookmarks)

    # Detect breaks at paragraph level
    if show_format:
        _export_breaks(para_el, parts)

    return "".join(parts)


def _export_runs(
    para_el, parts: list[str], show_tc: bool, show_format: bool,
    open_bookmarks: list[str],
) -> None:
    """Process all runs, bookmarks, and TC wrappers in a paragraph."""
    # Build a flat list of "segments" with their TC context
    segments = _collect_segments(para_el)

    current_fmt: dict[str, Any] = {}
    current_tc: str | None = None

    for seg in segments:
        # Handle bookmark boundaries
        for bm_name in seg.get("bm_starts", []):
            parts.append(f"[bookmark:{bm_name}]")
        for bm_name in seg.get("bm_ends", []):
            parts.append(f"[/bookmark:{bm_name}]")

        # Handle TC state changes — need to track which tag is open
        if show_tc:
            seg_tc = seg.get("tc")
            if seg_tc != current_tc:
                if current_tc:
                    parts.append(f"[/{current_tc}]")
                if seg_tc:
                    parts.append(f"[{seg_tc}]")
                current_tc = seg_tc

        if not seg.get("text"):
            continue

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


def _collect_segments(para_el) -> list[dict]:
    """Walk paragraph children and collect text segments with TC/format context."""
    segments: list[dict] = []
    bm_id_to_name: dict[str, str] = {}

    for child in para_el:
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

    return segments


def _segment_from_run(r_el, is_del: bool = False) -> dict:
    """Extract text and format from a w:r element."""
    rPr = r_el.find(f"{W}rPr")
    fmt = _extract_run_format(rPr) if rPr is not None else {}

    text_tag = f"{W}delText" if is_del else f"{W}t"
    text_parts = []
    for child in r_el:
        if child.tag == text_tag:
            text_parts.append(child.text or "")
        elif child.tag == f"{W}tab":
            text_parts.append("\t")
        elif child.tag == f"{W}br":
            text_parts.append("\n")

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


def _export_table(tbl_el, show_tc: bool, show_format: bool) -> str:
    """Export a table as a structured text block."""
    rows = tbl_el.findall(f"{W}tr")
    if not rows:
        return ""

    lines = []
    for row in rows:
        cells = row.findall(f"{W}tc")
        cell_texts = []
        for cell in cells:
            cell_parts = []
            for p in cell.findall(f"{W}p"):
                cell_parts.append(_get_para_plain_text(p))
            cell_texts.append("".join(cell_parts))
        lines.append(" | ".join(cell_texts))

    result = "[table]\n" + "\n".join(f"  {line}" for line in lines)
    return result


def _get_para_plain_text(para_el) -> str:
    """Get plain text from a paragraph element."""
    parts = []
    for child in para_el.iter():
        if child.tag in (f"{W}t", f"{W}delText"):
            parts.append(child.text or "")
        elif child.tag == f"{W}tab":
            parts.append("\t")
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

def lex_read(
    path: str,
    paras: list[int] | None = None,
    mode: str = "full",
    show_tc: bool = True,
    show_format: bool = True,
) -> str:
    """Read document content with inline format markup.

    Args:
        path: Path to .docx file.
        paras: Specific paragraphs (1-indexed), None = all.
        mode: "full" (all content), "structure" (headings only), "stats" (counts).
        show_tc: Include [ins]/[del] markup around tracked changes.
        show_format: Include format tags around styled text.

    Returns:
        Annotated text with §-prefixed paragraph markers.
    """
    if mode == "structure":
        return _export_structure(path)
    if mode == "stats":
        return _export_stats(path)
    return export_paragraphs(path, paras, show_tc, show_format)


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
    """Export document statistics."""
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

        elif child.tag == f"{W}sectPr":
            sections += 1

    return (
        f"Paragraphs: {para_count}\n"
        f"Characters: {word_count}\n"
        f"Sections: {sections}\n"
        f"Fonts: {', '.join(sorted(fonts)) if fonts else '(none)'}\n"
        f"TC Insertions: {tc_ins}\n"
        f"TC Deletions: {tc_del}"
    )
