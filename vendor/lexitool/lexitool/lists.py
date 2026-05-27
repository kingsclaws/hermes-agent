"""
lists.py — Full CRUD for Word numbering definitions and list instances.

Extends numbering_ops.py with the ability to create new abstract numbering
definitions from scratch and apply them.

OOXML structure:
  w:abstractNum (definition) → w:num (instance) → w:p/w:pPr/w:numPr (usage)

Style reference (20+ presets):

  Bullet styles:
    bullet           ● ○ ■ □ ◇               standard filled/hollow/square/diamond
    bullet_dash      — – · •                  dash/hyphen based
    bullet_arrow     ➤ ► → ›                  arrow based
    bullet_tick      ✓ ✔ ☑ ☐                  checkmark based

  Numbered styles:
    decimal          1. / a) / i. / (1)       standard hierarchical
    decimal_bracket  1) / a) / i) / (1)       bracket-delimited
    roman_upper      I. / A. / 1. / a)        uppercase Roman top level
    roman_lower      i. / a. / 1. / (1)       lowercase Roman top level
    letter_upper     A. / 1. / a. / (1)       uppercase letter top level
    letter_lower     a. / 1. / (a) / (1)      lowercase letter top level

  Chinese styles:
    chinese          一、/ (一) / 1. / (1)     standard Chinese document format
    chinese_article  第一条 / 1. / (1) / a)    contract article-level
    chinese_section  第一章 / 第一节 / 一、     chapter/section numbering

  Legal styles:
    legal            1.1 / 1.1.1 / 1.1.1.1    legal outline (multi-level decimal)
    legal_chinese    一、/ 1.1 / (1) / a)      Chinese legal hybrid
    legal_article    Article 1 / §1.1 / (a)    international legal

  Special styles:
    circled_decimal  ① ② ③                    single-level circled numbers
    parenthesized    (1) (2) (3)               simple parenthesized
    fullwidth         １ ２ ３                  fullwidth digits

Every style supports up to 9 levels (MAX_LEVEL = 8).  Only defined levels
are emitted — callers choose how many levels to create.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

MAX_LEVEL = 8

# ── Style registry ────────────────────────────────────────────────────────────
#
# Each entry maps level → (numFmt, lvlText).
# numFmt is the OOXML numbering format.
# lvlText uses %1, %2, %3, ... as placeholders for the resolved number at that
# level.  Everything else is literal text.
#
# OOXML numFmt values:
#   decimal           1, 2, 3 …
#   upperLetter       A, B, C …
#   lowerLetter       a, b, c …
#   upperRoman        I, II, III …
#   lowerRoman        i, ii, iii …
#   chineseCounting   一, 二, 三 …
#   chineseLegalSimplified  壹, 贰, 叁 … (legal-capable)
#   japaneseCounting  一, 二, 三 …
#   decimalZero       01, 02, 03 …
#   bullet            ● (bullet character from w:lvlText)
#   none              (no number — plain text prefix)
#   ordinal          1st, 2nd, 3rd (English)
#   cardinalText     One, Two, Three (English)

# (numFmt, lvlText) per level
_STYLES: dict[str, list[tuple[str, str]]] = {

    # ── Bullet styles ─────────────────────────────────────────────────────
    "bullet": [
        ("bullet", "●"),
        ("bullet", "○"),
        ("bullet", "■"),
        ("bullet", "□"),
        ("bullet", "◇"),
        ("bullet", "◆"),
        ("bullet", "▪"),
        ("bullet", "▸"),
        ("bullet", "•"),
    ],

    "bullet_dash": [
        ("bullet", "—"),
        ("bullet", "–"),
        ("bullet", "·"),
        ("bullet", "•"),
        ("bullet", "‣"),
        ("bullet", "⁃"),
    ],

    "bullet_arrow": [
        ("bullet", "➤"),
        ("bullet", "►"),
        ("bullet", "→"),
        ("bullet", "›"),
        ("bullet", "»"),
    ],

    "bullet_tick": [
        ("bullet", "✓"),
        ("bullet", "✔"),
        ("bullet", "☑"),
        ("bullet", "☐"),
    ],

    # ── Decimal / Numbered styles ──────────────────────────────────────────
    "decimal": [
        ("decimal",     "%1."),
        ("lowerLetter", "%2)"),
        ("lowerRoman",  "%3."),
        ("decimal",     "(%4)"),
        ("lowerLetter", "(%5)"),
        ("lowerRoman",  "(%6)"),
        ("decimal",     "%7."),
        ("lowerLetter", "%8."),
        ("decimal",     "(%9)"),
    ],

    "decimal_bracket": [
        ("decimal",     "%1)"),
        ("lowerLetter", "%2)"),
        ("lowerRoman",  "%3)"),
        ("decimal",     "(%4)"),
        ("lowerLetter", "(%5)"),
        ("lowerRoman",  "(%6)"),
    ],

    # ── Roman styles ───────────────────────────────────────────────────────
    "roman_upper": [
        ("upperRoman",  "%1."),
        ("upperLetter", "%2."),
        ("decimal",     "%3."),
        ("lowerLetter", "%4)"),
        ("decimal",     "(%5)"),
        ("lowerLetter", "(%6)"),
    ],

    "roman_lower": [
        ("lowerRoman",  "%1."),
        ("lowerLetter", "%2."),
        ("decimal",     "%3."),
        ("lowerLetter", "%4)"),
        ("decimal",     "(%5)"),
    ],

    # ── Letter styles ──────────────────────────────────────────────────────
    "letter_upper": [
        ("upperLetter", "%1."),
        ("decimal",     "%2."),
        ("lowerLetter", "%3."),
        ("decimal",     "(%4)"),
        ("lowerLetter", "(%5)"),
    ],

    "letter_lower": [
        ("lowerLetter", "%1."),
        ("decimal",     "%2."),
        ("lowerLetter", "(%3)"),
        ("decimal",     "(%4)"),
    ],

    # ── Chinese styles ─────────────────────────────────────────────────────
    "chinese": [
        ("chineseCounting", "%1、"),
        ("chineseCounting", "（%1）"),
        ("decimal",         "%1."),
        ("decimal",         "（%1）"),
        ("lowerLetter",     "%1."),
        ("lowerLetter",     "（%1）"),
        ("lowerRoman",      "%1."),
        ("chineseCounting", "%1）"),
        ("decimal",         "%1）"),
    ],

    "chinese_article": [
        ("chineseCounting", "第%1条"),        # 第一条
        ("decimal",         "%1."),           # 1.
        ("decimal",         "（%1）"),         # （1）
        ("lowerLetter",     "%1）"),           # a）
        ("lowerRoman",      "（%1）"),         # （i）
    ],

    "chinese_section": [
        ("chineseCounting", "第%1章"),        # 第一章
        ("chineseCounting", "第%1节"),        # 第一节
        ("chineseCounting", "%1、"),           # 一、
        ("decimal",         "%1."),           # 1.
        ("decimal",         "（%1）"),         # （1）
        ("lowerLetter",     "%1）"),           # a）
    ],

    # ── Legal styles ───────────────────────────────────────────────────────
    "legal": [
        ("decimal",     "%1"),
        ("decimal",     "%1.%2"),
        ("decimal",     "%1.%2.%3"),
        ("decimal",     "%1.%2.%3.%4"),
        ("decimal",     "%1.%2.%3.%4.%5"),
        ("lowerLetter", "(%6)"),
        ("lowerRoman",  "(%7)"),
    ],

    "legal_chinese": [
        ("chineseCounting", "%1、"),
        ("decimal",         "%1.%2"),
        ("decimal",         "（%1）"),
        ("lowerLetter",     "%1）"),
        ("lowerRoman",      "（%1）"),
    ],

    "legal_article": [
        ("cardinalText", "Article %1"),       # Article One
        ("decimal",      "§%1.%2"),           # §1.1
        ("lowerLetter",  "(%3)"),             # (a)
        ("lowerRoman",   "(%4)"),             # (i)
        ("decimal",      "(%5)"),             # (1)
    ],

    # ── Special styles ─────────────────────────────────────────────────────
    "circled_decimal": [
        ("decimal", "①"),
        ("decimal", "②"),
        ("decimal", "③"),
        ("decimal", "④"),
    ],

    "parenthesized": [
        ("decimal",     "（%1）"),
        ("lowerLetter", "（%2）"),
        ("lowerRoman",  "（%3）"),
    ],

    "fullwidth": [
        ("decimal", "１"),
        ("decimal", "２"),
        ("decimal", "３"),
        ("decimal", "４"),
    ],
}


def list_styles() -> dict[str, str]:
    """Return all available style names with their description and example output.

    Useful as a reference for the agent or CLI user.
    """
    descriptions = {
        # Bullets
        "bullet":          "● ○ ■ □ ◇ ◆ ▪ ▸ (filled/hollow/square/diamond bullets, 9 levels)",
        "bullet_dash":     "— – · • ‣ ⁃ (dash/hyphen bullets)",
        "bullet_arrow":    "➤ ► → › » (arrow bullets)",
        "bullet_tick":     "✓ ✔ ☑ ☐ (checkmark bullets)",
        # Decimal / numbered
        "decimal":         "1. / a) / i. / (1) / (a) / (i) (standard multi-level)",
        "decimal_bracket": "1) / a) / i) / (1) / (a) (bracket-delimited)",
        # Roman
        "roman_upper":     "I. / A. / 1. / a) / (1) (uppercase Roman top level)",
        "roman_lower":     "i. / a. / 1. / a) (lowercase Roman top level)",
        # Letter
        "letter_upper":    "A. / 1. / a. / (1) (uppercase letter top level)",
        "letter_lower":    "a. / 1. / (a) / (1) (lowercase letter top level)",
        # Chinese
        "chinese":         "一、/（一）/ 1. /（1）/ a. /（a）(standard Chinese 公文 format, 9 levels)",
        "chinese_article": "第一条 / 1. /（1）/ a）/（i）(contract article-level)",
        "chinese_section": "第一章 / 第一节 / 一、/ 1. /（1）(chapter/section)",
        # Legal
        "legal":           "1 / 1.1 / 1.1.1 / 1.1.1.1 (legal outline, 7 levels)",
        "legal_chinese":   "一、/ 1.1 /（1）/ a）(Chinese legal hybrid)",
        "legal_article":   "Article One / §1.1 / (a) / (i) (international legal)",
        # Special
        "circled_decimal": "①②③④ (circled numbers, single-level)",
        "parenthesized":   "（1）/（a）/（i）(parenthesized only)",
        "fullwidth":       "１２３４ (fullwidth digits)",
    }
    return descriptions


def get_style_preview(style: str, count: int = 4) -> str:
    """Return a human-readable preview of what a style looks like.

    Uses the lvlText templates with placeholder %N resolved with format-appropriate
    sample markers so the preview reads naturally (letter styles show A/B/C,
    Roman styles show I/II/III, etc.).
    """
    if style not in _STYLES:
        return f"(unknown style: {style})"

    levels = _STYLES[style]

    # Sample value maps for different numFmt types
    cn_map = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}
    upper_letter = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F", 7: "G", 8: "H", 9: "I"}
    lower_letter = {1: "a", 2: "b", 3: "c", 4: "d", 5: "e", 6: "f", 7: "g", 8: "h", 9: "i"}
    upper_roman = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX"}
    lower_roman = {1: "i", 2: "ii", 3: "iii", 4: "iv", 5: "v", 6: "vi", 7: "vii", 8: "viii", 9: "ix"}
    cardinal = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}

    def _resolve_num(n: int, num_fmt: str) -> str:
        if num_fmt == "chineseCounting":
            return cn_map.get(n, str(n))
        if num_fmt == "upperLetter":
            return upper_letter.get(n, str(n))
        if num_fmt == "lowerLetter":
            return lower_letter.get(n, str(n))
        if num_fmt == "upperRoman":
            return upper_roman.get(n, str(n))
        if num_fmt == "lowerRoman":
            return lower_roman.get(n, str(n))
        if num_fmt == "cardinalText":
            return cardinal.get(n, str(n))
        # decimal, bullet, none, and unknown
        return str(n)

    previews = []
    for i in range(min(count, len(levels))):
        num_fmt, lvl_text = levels[i]
        resolved = lvl_text
        for n in range(1, 10):
            placeholder = f"%{n}"
            if placeholder in resolved:
                resolved = resolved.replace(placeholder, _resolve_num(n, num_fmt))
        previews.append(resolved)
    return "  ".join(previews)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _read_docx(path: str) -> tuple[bytes, dict[str, bytes]]:
    with zipfile.ZipFile(path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {n: zf.read(n) for n in zf.namelist() if n != "word/document.xml"}
    return doc_xml, other


def _write_docx(path: str, doc_xml: bytes, other: dict[str, bytes]) -> None:
    fd, tmp = tempfile.mkstemp(prefix="lexitool_list.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", doc_xml)
        for name, data in other.items():
            zf.writestr(name, data)
    shutil.move(tmp, path)


def _check_and_insert_numbering_part(other: dict[str, bytes]) -> bytes:
    """Get or create the numbering.xml part."""
    if "word/numbering.xml" in other:
        return other["word/numbering.xml"]

    numbering_xml = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        b'<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'</w:numbering>'
    )
    other["word/numbering.xml"] = numbering_xml

    # Add relationship in word/_rels/document.xml.rels if needed
    rels_key = "word/_rels/document.xml.rels"
    if rels_key in other:
        rels_root = etree.fromstring(other[rels_key])
        has_num = any(
            rel.get("Type", "").endswith("/numbering") for rel in rels_root
        )
        if not has_num:
            max_id = 0
            for rel in rels_root:
                try:
                    max_id = max(max_id, int(rel.get("Id", "rId0").replace("rId", "")))
                except (ValueError, TypeError):
                    pass
            new_rel = etree.SubElement(rels_root, "Relationship")
            new_rel.set("Id", f"rId{max_id + 1}")
            new_rel.set("Type",
                        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering")
            new_rel.set("Target", "numbering.xml")
            other[rels_key] = etree.tostring(rels_root, xml_declaration=True,
                                             encoding="UTF-8", standalone="yes")

    return numbering_xml


def _next_abstract_num_id(numbering_el) -> int:
    max_id = 0
    for el in numbering_el.findall(f"{W}abstractNum"):
        try:
            max_id = max(max_id, int(el.get(f"{W}abstractNumId", 0)))
        except (ValueError, TypeError):
            pass
    return max_id + 1


def _next_num_id(numbering_el) -> int:
    max_id = 0
    for el in numbering_el.findall(f"{W}num"):
        try:
            max_id = max(max_id, int(el.get(f"{W}numId", 0)))
        except (ValueError, TypeError):
            pass
    return max_id + 1


def _build_lvl_element(
    lvl_num: int,
    num_fmt: str,
    lvl_text: str,
    start: int,
    parent,
) -> None:
    """Build a w:lvl element and append it to parent (w:abstractNum)."""
    lvl = etree.SubElement(parent, f"{W}lvl")
    lvl.set(f"{W}ilvl", str(lvl_num))

    start_el = etree.SubElement(lvl, f"{W}start")
    start_el.set(f"{W}val", str(start))

    num_fmt_el = etree.SubElement(lvl, f"{W}numFmt")
    num_fmt_el.set(f"{W}val", num_fmt)

    lvl_text_el = etree.SubElement(lvl, f"{W}lvlText")
    lvl_text_el.set(f"{W}val", lvl_text)

    lvl_jc = etree.SubElement(lvl, f"{W}lvlJc")
    lvl_jc.set(f"{W}val", "left")

    pPr = etree.SubElement(lvl, f"{W}pPr")
    ind = etree.SubElement(pPr, f"{W}ind")
    # Progressive indent: ~0.74 cm per level
    left_val = 420 + lvl_num * 420
    ind.set(f"{W}left", str(left_val))
    ind.set(f"{W}hanging", "420")


# ── Public API ────────────────────────────────────────────────────────────────

def create_abstract_num(
    doc_path: str,
    style: str = "decimal",
    levels: int | None = None,
    start: int = 1,
) -> dict:
    """Create a new abstract numbering definition.

    Args:
        doc_path: Path to .docx file.
        style: Any style from list_styles() — see module docstring.
        levels: Number of list levels to define (default varies by style).
        start: Starting number for all levels.

    Returns:
        {"ok": True, "abstractNumId": int, "style": style, "levels": int,
         "preview": str}
    """
    if style not in _STYLES:
        available = ", ".join(sorted(_STYLES.keys()))
        return {"ok": False, "reason": f"unknown style: {style!r}. Available: {available}"}

    style_levels = _STYLES[style]
    max_available = len(style_levels)
    if levels is None:
        levels = min(max_available, 4)
    else:
        levels = min(levels, max_available, MAX_LEVEL + 1)

    doc_xml, other = _read_docx(doc_path)
    numbering_xml = _check_and_insert_numbering_part(other)
    numbering_el = etree.fromstring(numbering_xml)

    abstract_num_id = _next_abstract_num_id(numbering_el)

    abs_num = etree.SubElement(numbering_el, f"{W}abstractNum")
    abs_num.set(f"{W}abstractNumId", str(abstract_num_id))

    multi_level_type = etree.SubElement(abs_num, f"{W}multiLevelType")
    multi_level_type.set(f"{W}val", "multilevel" if levels > 1 else "single")

    for lvl_num in range(levels):
        num_fmt, lvl_text = style_levels[lvl_num]
        _build_lvl_element(lvl_num, num_fmt, lvl_text, start, abs_num)

    other["word/numbering.xml"] = etree.tostring(numbering_el, xml_declaration=True,
                                                  encoding="UTF-8", standalone="yes")

    try:
        doc_xml_out = etree.tostring(etree.fromstring(doc_xml),
                                     xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = doc_xml

    _write_docx(doc_path, doc_xml_out, other)

    return {
        "ok": True,
        "abstractNumId": abstract_num_id,
        "style": style,
        "levels": levels,
        "preview": get_style_preview(style, levels),
    }


def add_num_instance(
    doc_path: str,
    abstract_num_id: int,
) -> dict:
    """Create a numbering instance linked to an abstract numbering definition.

    Returns {"ok": True, "numId": int, "abstractNumId": int}.
    """
    doc_xml, other = _read_docx(doc_path)
    numbering_xml = _check_and_insert_numbering_part(other)
    numbering_el = etree.fromstring(numbering_xml)

    found = any(
        abs_num.get(f"{W}abstractNumId") == str(abstract_num_id)
        for abs_num in numbering_el.findall(f"{W}abstractNum")
    )
    if not found:
        return {"ok": False, "reason": f"abstractNumId {abstract_num_id} not found"}

    num_id = _next_num_id(numbering_el)
    num_el = etree.SubElement(numbering_el, f"{W}num")
    num_el.set(f"{W}numId", str(num_id))

    abs_ref = etree.SubElement(num_el, f"{W}abstractNumId")
    abs_ref.set(f"{W}val", str(abstract_num_id))

    other["word/numbering.xml"] = etree.tostring(numbering_el, xml_declaration=True,
                                                  encoding="UTF-8", standalone="yes")

    try:
        doc_xml_out = etree.tostring(etree.fromstring(doc_xml),
                                     xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = doc_xml

    _write_docx(doc_path, doc_xml_out, other)
    return {"ok": True, "numId": num_id, "abstractNumId": abstract_num_id}


def apply_numbering(
    doc_path: str,
    para_idx: int,
    num_id: int,
    ilvl: int = 0,
) -> dict:
    """Apply a numbering instance to a paragraph.

    Returns {"ok": True, "para": para_idx, "numId": num_id, "ilvl": ilvl}.
    """
    doc_xml, other = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    paras = [el for el in body if el.tag == f"{W}p"]
    if para_idx < 0 or para_idx >= len(paras):
        return {"ok": False, "reason": f"para_idx {para_idx} out of range"}

    para_el = paras[para_idx]
    pPr = para_el.find(f"{W}pPr")
    if pPr is None:
        pPr = etree.Element(f"{W}pPr")
        para_el.insert(0, pPr)

    existing = pPr.find(f"{W}numPr")
    if existing is not None:
        pPr.remove(existing)

    numPr = etree.SubElement(pPr, f"{W}numPr")
    ilvl_el = etree.SubElement(numPr, f"{W}ilvl")
    ilvl_el.set(f"{W}val", str(ilvl))
    numId_el = etree.SubElement(numPr, f"{W}numId")
    numId_el.set(f"{W}val", str(num_id))

    try:
        doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = etree.tostring(root, encoding="UTF-8")

    _write_docx(doc_path, doc_xml_out, other)
    return {"ok": True, "para": para_idx, "numId": num_id, "ilvl": ilvl}


def remove_numbering(doc_path: str, para_idx: int) -> dict:
    """Remove numbering from a paragraph.

    Returns {"ok": True, "para": para_idx}.
    """
    doc_xml, other = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    paras = [el for el in body if el.tag == f"{W}p"]
    if para_idx < 0 or para_idx >= len(paras):
        return {"ok": False, "reason": f"para_idx {para_idx} out of range"}

    para_el = paras[para_idx]
    pPr = para_el.find(f"{W}pPr")
    if pPr is not None:
        numPr = pPr.find(f"{W}numPr")
        if numPr is not None:
            pPr.remove(numPr)

    try:
        doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = etree.tostring(root, encoding="UTF-8")

    _write_docx(doc_path, doc_xml_out, other)
    return {"ok": True, "para": para_idx}


def create_list(
    doc_path: str,
    para_indices: list[int],
    style: str = "decimal",
    start: int = 1,
    levels: int | None = None,
) -> dict:
    """Convenience: create a full list in one call.

    Args:
        doc_path: Path to .docx file.
        para_indices: 0-indexed paragraph numbers.
        style: Any style from list_styles().
        start: Starting number.
        levels: How many levels to define (default: 4).

    Returns:
        {"ok": True, "abstractNumId": int, "numId": int, "applied_to": int,
         "style": style, "preview": str}
    """
    result = create_abstract_num(doc_path, style, levels=levels, start=start)
    if not result["ok"]:
        return result

    result2 = add_num_instance(doc_path, result["abstractNumId"])
    if not result2["ok"]:
        return result2

    num_id = result2["numId"]
    applied = 0
    for para_idx in para_indices:
        r = apply_numbering(doc_path, para_idx, num_id, ilvl=0)
        if r["ok"]:
            applied += 1

    return {
        "ok": True,
        "abstractNumId": result["abstractNumId"],
        "numId": num_id,
        "style": style,
        "preview": result.get("preview", ""),
        "applied_to": applied,
        "total": len(para_indices),
    }


def apply_list_levels(
    doc_path: str,
    para_levels: list[tuple[int, int]],
    style: str = "decimal",
    start: int = 1,
) -> dict:
    """Create a list with paragraphs at specific levels.

    Args:
        doc_path: Path to .docx file.
        para_levels: List of (para_idx, level) tuples.  para_idx is 0-indexed,
                     level is the list level (0 = top level).
        style: Any style from list_styles().
        start: Starting number.

    Example:
        apply_list_levels(path, [(5,0), (6,1), (7,1), (8,0)], style="chinese")
        → §5: 一、  §6: （一）  §7: （二）  §8: 二、

    Returns:
        {"ok": True, "abstractNumId": int, "numId": int, "applied": int}
    """
    max_lvl = max(lvl for _, lvl in para_levels)
    result = create_abstract_num(doc_path, style, levels=max_lvl + 1, start=start)
    if not result["ok"]:
        return result

    result2 = add_num_instance(doc_path, result["abstractNumId"])
    if not result2["ok"]:
        return result2

    num_id = result2["numId"]
    applied = 0
    for para_idx, level in para_levels:
        r = apply_numbering(doc_path, para_idx, num_id, ilvl=level)
        if r["ok"]:
            applied += 1

    return {
        "ok": True,
        "abstractNumId": result["abstractNumId"],
        "numId": num_id,
        "style": style,
        "applied": applied,
        "total": len(para_levels),
    }


# ── Level manipulation ────────────────────────────────────────────────────────

def promote_list_level(doc_path: str, para_idx: int) -> dict:
    """Decrease the indent level of a numbered paragraph (promote outward).

    Level 1 → Level 0, Level 2 → Level 1, etc.
    """
    return _adjust_list_level(doc_path, para_idx, -1)


def demote_list_level(doc_path: str, para_idx: int) -> dict:
    """Increase the indent level of a numbered paragraph (demote inward).

    Level 0 → Level 1, Level 1 → Level 2, etc.
    """
    return _adjust_list_level(doc_path, para_idx, 1)


def set_list_level(doc_path: str, para_idx: int, level: int) -> dict:
    """Set a paragraph to an absolute list level."""
    doc_xml, other = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    paras = [el for el in body if el.tag == f"{W}p"]
    if para_idx < 0 or para_idx >= len(paras):
        return {"ok": False, "reason": f"para_idx {para_idx} out of range"}

    para_el = paras[para_idx]
    pPr = para_el.find(f"{W}pPr")
    if pPr is None:
        return {"ok": False, "reason": "paragraph has no pPr"}

    numPr = pPr.find(f"{W}numPr")
    if numPr is None:
        return {"ok": False, "reason": "paragraph has no numbering"}

    ilvl_el = numPr.find(f"{W}ilvl")
    if ilvl_el is None:
        return {"ok": False, "reason": "numbering has no ilvl"}

    try:
        old_level = int(ilvl_el.get(f"{W}val", "0"))
    except (ValueError, TypeError):
        old_level = 0

    new_level = max(0, min(level, MAX_LEVEL))
    ilvl_el.set(f"{W}val", str(new_level))

    try:
        doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = etree.tostring(root, encoding="UTF-8")

    _write_docx(doc_path, doc_xml_out, other)
    return {"ok": True, "para": para_idx, "old_level": old_level, "new_level": new_level}


def _adjust_list_level(doc_path: str, para_idx: int, delta: int) -> dict:
    doc_xml, other = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    paras = [el for el in body if el.tag == f"{W}p"]
    if para_idx < 0 or para_idx >= len(paras):
        return {"ok": False, "reason": f"para_idx {para_idx} out of range"}

    para_el = paras[para_idx]
    pPr = para_el.find(f"{W}pPr")
    if pPr is None:
        return {"ok": False, "reason": "paragraph has no pPr"}

    numPr = pPr.find(f"{W}numPr")
    if numPr is None:
        return {"ok": False, "reason": "paragraph has no numbering"}

    ilvl_el = numPr.find(f"{W}ilvl")
    if ilvl_el is None:
        return {"ok": False, "reason": "numbering has no ilvl"}

    try:
        current = int(ilvl_el.get(f"{W}val", "0"))
    except (ValueError, TypeError):
        current = 0

    new_level = max(0, min(current + delta, MAX_LEVEL))
    ilvl_el.set(f"{W}val", str(new_level))

    try:
        doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = etree.tostring(root, encoding="UTF-8")

    _write_docx(doc_path, doc_xml_out, other)
    return {"ok": True, "para": para_idx, "old_level": current, "new_level": new_level}


def restart_numbering(doc_path: str, para_idx: int, start_value: int = 1) -> dict:
    """Restart numbering at a specific paragraph with a new start value.

    Creates a new num instance with lvlOverride/startOverride.
    """
    doc_xml, other = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    paras = [el for el in body if el.tag == f"{W}p"]
    if para_idx < 0 or para_idx >= len(paras):
        return {"ok": False, "reason": f"para_idx {para_idx} out of range"}

    para_el = paras[para_idx]
    pPr = para_el.find(f"{W}pPr")
    if pPr is None:
        return {"ok": False, "reason": "paragraph has no pPr"}

    numPr = pPr.find(f"{W}numPr")
    if numPr is None:
        return {"ok": False, "reason": "paragraph has no numbering to restart"}

    numId_el = numPr.find(f"{W}numId")
    ilvl_el = numPr.find(f"{W}ilvl")
    if numId_el is None:
        return {"ok": False, "reason": "paragraph numbering has no numId"}

    abstract_num_id = int(numId_el.get(f"{W}val", "0"))
    ilvl = ilvl_el.get(f"{W}val", "0") if ilvl_el is not None else "0"

    numbering_xml = _check_and_insert_numbering_part(other)
    numbering_el = etree.fromstring(numbering_xml)

    new_num_id = _next_num_id(numbering_el)
    num_el = etree.SubElement(numbering_el, f"{W}num")
    num_el.set(f"{W}numId", str(new_num_id))

    abs_ref = etree.SubElement(num_el, f"{W}abstractNumId")
    abs_ref.set(f"{W}val", str(abstract_num_id))

    lvl_override = etree.SubElement(num_el, f"{W}lvlOverride")
    lvl_override.set(f"{W}ilvl", str(ilvl))
    start_override = etree.SubElement(lvl_override, f"{W}startOverride")
    start_override.set(f"{W}val", str(start_value))

    other["word/numbering.xml"] = etree.tostring(numbering_el, xml_declaration=True,
                                                  encoding="UTF-8", standalone="yes")
    numId_el.set(f"{W}val", str(new_num_id))

    try:
        doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = etree.tostring(root, encoding="UTF-8")

    _write_docx(doc_path, doc_xml_out, other)
    return {"ok": True, "para": para_idx, "new_numId": new_num_id,
            "abstractNumId": abstract_num_id, "start_at": start_value}
