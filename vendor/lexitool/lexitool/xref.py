"""
xref.py — Cross-reference hyperlinking for legal documents.

Converts static "第X条(title)" patterns into clickable w:hyperlink elements
pointing to heading bookmarks. Preserves all run-level formatting.

Operations:
  - scan_xrefs:    Dry-run scan showing what xrefs exist and what they'd link to
  - auto_xref:     Full conversion — add bookmarks, flatten broken field codes,
                    wrap xref text in hyperlinks
  - cross_doc_scan: Multi-document scan — detect cross-document references
                    and validate they point to existing docs and clauses
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


# ── Read / Write helpers ──────────────────────────────────────────────────────

def _read_docx(path: str) -> tuple[bytes, dict[str, bytes], list[str]]:
    """Read document.xml and all other ZIP entries. Returns (doc_xml, other, order)."""
    with zipfile.ZipFile(path, "r") as zf:
        order = zf.namelist()
        doc_xml = zf.read("word/document.xml")
        other = {n: zf.read(n) for n in order if n != "word/document.xml"}
    return doc_xml, other, order


def _write_docx(path: str, doc_xml: bytes, other: dict[str, bytes],
                zip_order: list[str]) -> None:
    """Write back preserving original ZIP entry order. Only document.xml is replaced."""
    fd, tmp = tempfile.mkstemp(prefix="lexitool_xref.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in zip_order:
            if name == "word/document.xml":
                zf.writestr(name, doc_xml)
            elif name in other:
                zf.writestr(name, other[name])
    shutil.move(tmp, path)


# ── Clause-index building ─────────────────────────────────────────────────────

def _build_clause_index(paras: list) -> dict[str, int]:
    """Build clause-number → paragraph-index map from AOHead styles.

    Falls back to _build_broad_clause_index when no AOHead styles are found,
    ensuring coverage for documents that use standard Heading styles or
    manually-formatted headings (inferred).
    """
    art = sec1 = sec2 = sec3 = 0
    clause_to_pidx = {}

    for pi, p in enumerate(paras):
        pPr = p.find(f"{W}pPr")
        if pPr is None:
            continue
        ps = pPr.find(f"{W}pStyle")
        if ps is None:
            continue
        style = ps.get(f"{W}val", "")
        if "AOHead" not in style:
            continue

        if style == "AOHead1":
            art += 1
            sec1 = sec2 = sec3 = 0
        elif style == "AOHead2":
            sec1 += 1
            sec2 = sec3 = 0
        elif style == "AOHead3":
            sec2 += 1
            sec3 = 0
        elif style == "AOHead4":
            sec3 += 1

        cn = str(art)
        if sec1:
            cn += f".{sec1}"
        if sec2:
            cn += f".{sec2}"
        if sec3:
            cn += f".{sec3}"
        clause_to_pidx[cn] = pi

    if not clause_to_pidx:
        clause_to_pidx = _build_broad_clause_index(paras)

    return clause_to_pidx


# English section heading patterns — detected at paragraph start
_SECTION_HEAD_RE = re.compile(
    r'^\s*(?:Section|SECTION|Clause|CLAUSE|Article|ARTICLE)\s+(\d+(?:\.\d+)*)\b',
)
# Mixed: "第1条 (Section 1)" or "Article 1 第1条"
_MIXED_HEAD_RE = re.compile(
    r'^\s*(?:Section|Article|Clause)\s+(\d+(?:\.\d+)*)\s+第\1条',
)


def _is_inferred_heading(p) -> bool:
    """Check if a paragraph looks like a heading based on formatting heuristics.

    Mirrors markup.py's _infer_heading logic: bold first run, no first-line
    indent, short text. Works on raw lxml paragraph elements.
    """
    pPr = p.find(f"{W}pPr")

    # Never override explicit heading style or outline level
    if pPr is not None:
        pStyle = pPr.find(f"{W}pStyle")
        if pStyle is not None:
            style_id = (pStyle.get(f"{W}val", "") or "").lower()
            if "heading" in style_id or "标题" in style_id or style_id.startswith("toc"):
                return False
        if pPr.find(f"{W}outlineLvl") is not None:
            return False
        numPr = pPr.find(f"{W}numPr")
        if numPr is not None and numPr.find(f"{W}numId") is not None:
            return False

    # Find first text-bearing run and check for bold
    first_text_run = None
    for child in p:
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
        return False

    rPr = first_text_run.find(f"{W}rPr")
    if rPr is None:
        return False
    if rPr.find(f"{W}b") is None:
        return False  # Must be bold

    # Check for no first-line indent
    if pPr is not None:
        ind = pPr.find(f"{W}ind")
        if ind is not None:
            first_line = ind.get(f"{W}firstLine")
            if first_line is not None:
                try:
                    if int(first_line) > 0:
                        return False
                except (ValueError, TypeError):
                    pass

    # Length check: short text is a heading signal
    text_len = len("".join(
        (t.text or "") for t in p.iter(f"{W}t")
    ).strip())
    if text_len > 80:
        return False

    return True


# ── Field-code flattening ─────────────────────────────────────────────────────

def _remove_field_runs(p) -> int:
    """Remove w:r elements that contain fldChar or instrText from a paragraph.

    Only runs with actual field-code elements are removed. All other runs
    (including empty runs, rPr-only formatting carriers, and runs with
    symbols/objects) are preserved to avoid breaking formatting context.

    Returns count of removed runs.
    """
    removed = 0
    for r in list(p):
        if r.tag != f"{W}r":
            continue
        has_field = any(
            etree.QName(c).localname in ("fldChar", "instrText")
            for c in r
        )
        if has_field:
            p.remove(r)
            removed += 1
    return removed


# ── Bookmark helpers ──────────────────────────────────────────────────────────

def _add_bookmarks_to_headings(paras: list, clause_to_pidx: dict) -> int:
    """Add w:bookmarkStart/w:bookmarkEnd to each heading paragraph.
    Returns count of bookmarks added."""
    count = 0
    for cn, pi in clause_to_pidx.items():
        p = paras[pi]
        bm_name = f"_TocClause{cn.replace('.', '_')}"

        bm_start = etree.Element(f"{W}bookmarkStart")
        bm_start.set(f"{W}id", str(pi))
        bm_start.set(f"{W}name", bm_name)

        bm_end = etree.Element(f"{W}bookmarkEnd")
        bm_end.set(f"{W}id", str(pi))

        # Insert after pPr
        pPr = p.find(f"{W}pPr")
        if pPr is not None:
            p.insert(list(p).index(pPr) + 1, bm_start)
        else:
            p.insert(0, bm_start)
        p.append(bm_end)
        count += 1
    return count


# ── Run helpers ───────────────────────────────────────────────────────────────

def _get_direct_runs(parent_elem) -> list[tuple]:
    """Return (run_element, text) for direct-child w:r elements only."""
    result = []
    for child in parent_elem:
        if child.tag == f"{W}r":
            t = child.find(f"{W}t")
            txt = t.text if (t is not None and t.text) else ""
            result.append((child, txt))
    return result


def _split_run(run_elem, char_pos: int):
    """Split a w:r at char_pos (into the w:t text).
    Preserves all run properties via deepcopy.
    Returns (left_run, right_run). right_run is None if no split needed.
    """
    t_elem = run_elem.find(f"{W}t")
    if t_elem is None or t_elem.text is None:
        return run_elem, None

    text = t_elem.text
    if char_pos <= 0 or char_pos >= len(text):
        return run_elem, None

    right_run = deepcopy(run_elem)
    t_elem.text = text[:char_pos]
    right_run.find(f"{W}t").text = text[char_pos:]
    t_elem.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    right_run.find(f"{W}t").set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return run_elem, right_run


# ── Cross-reference detection pattern ─────────────────────────────────────────

_XREF_PATTERN = re.compile(r'第(\d+(?:\.\d+)*)条(?:\(([^)]*)\))?')
_STATIC_ARTICLE_REF_RE = re.compile(r'第(?P<clause>\d+(?:\.\d+)*)条')
_NUMBERED_HEADING_RE = re.compile(
    r'^\s*(?:第)?(?P<clause>\d+(?:\.\d+)*)(?:条)?(?:[\.．、\s]+|$)(?P<title>.*)$'
)
_FIELD_CODE_TAGS = {"fldChar", "instrText"}


# ── Scan (dry run) ───────────────────────────────────────────────────────────

def scan_xrefs(doc_path: str) -> dict:
    """Scan document for cross-reference patterns without modifying anything.

    Returns:
        {"ok": True, "xrefs": [...], "clause_count": N, "xref_count": M,
         "headings_indexed": K, "field_codes_present": F}
    """
    doc_xml, _other, _order = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    paras = [c for c in body if c.tag == f"{W}p"]

    clause_to_pidx = _build_clause_index(paras)

    # Check for field codes
    fld_count = sum(1 for p in paras for e in p.iter()
                    if etree.QName(e).localname in ("fldChar", "instrText"))

    # Find all xrefs
    xrefs = []
    for pi, p in enumerate(paras):
        full_text = "".join(txt for _, txt in _get_direct_runs(p))
        for m in _XREF_PATTERN.finditer(full_text):
            cn = m.group(1)
            title = m.group(2)
            if cn in clause_to_pidx:
                xrefs.append({
                    "para": pi,
                    "clause": cn,
                    "title": title,
                    "match": m.group(0),
                    "target_para": clause_to_pidx[cn],
                })

    return {
        "ok": True,
        "xrefs": xrefs,
        "xref_count": len(xrefs),
        "clause_count": len(clause_to_pidx),
        "headings_indexed": len(clause_to_pidx),
        "field_codes_present": fld_count,
    }


def _localname(el) -> str:
    return etree.QName(el).localname


def _paragraph_text(p) -> str:
    return "".join((t.text or "") for t in p.findall(f".//{W}t"))


def _paragraph_style(p) -> str:
    pPr = p.find(f"{W}pPr")
    if pPr is None:
        return ""
    ps = pPr.find(f"{W}pStyle")
    if ps is None:
        return ""
    return ps.get(f"{W}val", "")


def _is_heading_paragraph(p) -> bool:
    style = _paragraph_style(p)
    if style in _HEADING_STYLES or "AOHead" in style:
        return True
    text = _paragraph_text(p).strip()
    return bool(_NUMBERED_HEADING_RE.match(text)) and not text.startswith("第")


def _is_toc_paragraph(p) -> bool:
    style = _paragraph_style(p).lower()
    return style.startswith("toc") or style in {"msonormaltoc", "contents"}


def _bookmark_names_in_para(p) -> List[str]:
    names: List[str] = []
    for bm in p.iter(f"{W}bookmarkStart"):
        name = bm.get(f"{W}name")
        if name:
            names.append(name)
    return names


def _choose_ref_bookmark(names: Sequence[str]) -> Optional[str]:
    for prefix in ("_Ref", "_Toc"):
        for name in names:
            if name.startswith(prefix):
                return name
    return names[0] if names else None


def _build_heading_bookmark_index(paras: list) -> Dict[str, dict]:
    """Return exact clause-number -> heading bookmark metadata."""
    index: Dict[str, dict] = {}
    for pi, p in enumerate(paras):
        text = _paragraph_text(p).strip()
        if not text:
            continue
        match = _NUMBERED_HEADING_RE.match(text)
        if not match:
            continue
        clause = match.group("clause")
        names = _bookmark_names_in_para(p)
        bookmark = _choose_ref_bookmark(names)
        if not bookmark:
            continue
        index.setdefault(
            clause,
            {
                "bookmark": bookmark,
                "heading_para": pi + 1,
                "heading_text": text,
                "title": (match.group("title") or "").strip(),
            },
        )
    return index


def _make_fld_char(fld_char_type: str) -> etree._Element:
    r = etree.Element(f"{W}r")
    fld_char = etree.SubElement(r, f"{W}fldChar")
    fld_char.set(f"{W}fldCharType", fld_char_type)
    return r


def _make_instr_text(instruction: str) -> etree._Element:
    r = etree.Element(f"{W}r")
    instr = etree.SubElement(r, f"{W}instrText")
    instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instr.text = instruction
    return r


def _make_field_display(text: str) -> etree._Element:
    r = etree.Element(f"{W}r")
    t = etree.SubElement(r, f"{W}t")
    t.text = text
    return r


def _field_elements(bookmark: str, display_text: str) -> List[etree._Element]:
    return [
        _make_fld_char("begin"),
        _make_instr_text(f" REF {bookmark} "),
        _make_fld_char("separate"),
        _make_field_display(display_text),
        _make_fld_char("end"),
    ]


def _rewrite_run_text_with_fields(p, run_el, replacements: List[tuple[str, str, int, int]]) -> List[dict]:
    """Replace text spans inside one run with REF field elements."""
    t_el = run_el.find(f"{W}t")
    if t_el is None or t_el.text is None:
        return []
    text = t_el.text
    run_idx = list(p).index(run_el)
    p.remove(run_el)

    changed: List[dict] = []
    insert_pos = run_idx
    pos = 0
    for clause, bookmark, start, end in replacements:
        if start > pos:
            new_run = deepcopy(run_el)
            new_t = new_run.find(f"{W}t")
            new_t.text = text[pos:start]
            p.insert(insert_pos, new_run)
            insert_pos += 1
        for el in _field_elements(bookmark, clause):
            p.insert(insert_pos, el)
            insert_pos += 1
        changed.append({"clause": clause, "bookmark": bookmark})
        pos = end
    if pos < len(text):
        new_run = deepcopy(run_el)
        new_t = new_run.find(f"{W}t")
        new_t.text = text[pos:]
        p.insert(insert_pos, new_run)
    return changed


def _plain_text_runs_outside_fields(p) -> List[tuple]:
    runs = []
    in_field = False
    for child in p:
        if child.tag != f"{W}r":
            continue
        fld_char = child.find(f"{W}fldChar")
        if fld_char is not None:
            fld_type = fld_char.get(f"{W}fldCharType", "")
            if fld_type == "begin":
                in_field = True
            elif fld_type == "end":
                in_field = False
            continue
        if in_field or child.find(f"{W}instrText") is not None:
            continue
        t_el = child.find(f"{W}t")
        if t_el is not None and t_el.text:
            runs.append((child, t_el.text))
    return runs


def convert_static_refs(
    doc_path: str,
    clauses: Optional[Sequence[str]] = None,
    dry_run: bool = True,
    skip_toc: bool = True,
) -> dict:
    """Convert hardcoded ``第X条`` references to real REF fields.

    The conversion is deliberately exact: ``X`` must exist as a numbered
    heading with an existing bookmark. Existing Word fields and hyperlinks are
    skipped, so a ``[ref]`` marker in lex_read output is not treated as broken.
    """
    doc_xml, other, order = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "reason": "no document body"}
    paras = [c for c in body if c.tag == f"{W}p"]
    heading_index = _build_heading_bookmark_index(paras)
    allowed = {str(c) for c in clauses or [] if str(c).strip()}

    converted: List[dict] = []
    unresolved: List[dict] = []
    skipped: List[dict] = []

    for pi, p in enumerate(paras, start=1):
        if skip_toc and _is_toc_paragraph(p):
            continue
        if _is_heading_paragraph(p):
            continue

        for run_el, text in list(_plain_text_runs_outside_fields(p)):
            replacements = []
            for match in _STATIC_ARTICLE_REF_RE.finditer(text):
                clause = match.group("clause")
                if allowed and clause not in allowed:
                    continue
                target = heading_index.get(clause)
                detail = {
                    "para": pi,
                    "clause": clause,
                    "match": match.group(0),
                    "context": text[max(0, match.start() - 30):match.end() + 50],
                }
                if not target:
                    unresolved.append({**detail, "reason": "target heading bookmark not found"})
                    continue
                replacements.append((clause, target["bookmark"], match.start("clause"), match.end("clause")))
                converted.append({
                    **detail,
                    "bookmark": target["bookmark"],
                    "target_para": target["heading_para"],
                    "target_heading": target["heading_text"],
                })
            if replacements and not dry_run:
                _rewrite_run_text_with_fields(p, run_el, replacements)

    if not dry_run and converted:
        doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
        _write_docx(doc_path, doc_xml_out, other, order)
        try:
            from .fields import update_fields
            update_fields(doc_path)
        except Exception:
            pass

    return {
        "ok": not unresolved,
        "converted": len(converted),
        "dry_run": dry_run,
        "heading_bookmarks": len(heading_index),
        "details": converted,
        "unresolved": unresolved,
        "skipped": skipped,
    }


# ── Hyperlink wrapping helper ──────────────────────────────────────────────────

def _wrap_text_in_hyperlink(p, start_pos: int, end_pos: int, cn: str) -> bool:
    """Find runs spanning [start_pos, end_pos), split at boundaries,
    wrap them in a w:hyperlink pointing to _TocClause{cn}.
    Returns True on success."""
    runs = _get_direct_runs(p)
    full_text = "".join(txt for _, txt in runs)

    if end_pos > len(full_text):
        return False

    # Map character positions to run indices
    char_pos = 0
    first_ri = last_ri = -1
    for ri, (_r, rt) in enumerate(runs):
        r_start = char_pos
        r_end = char_pos + len(rt)
        if first_ri < 0 and r_start <= start_pos < r_end:
            first_ri = ri
        if r_start < end_pos <= r_end:
            last_ri = ri
            break
        char_pos += len(rt)

    if first_ri < 0 or last_ri < 0:
        return False

    # Split first run if xref starts mid-run
    char_before = sum(len(runs[i][1]) for i in range(first_ri))
    split_at = start_pos - char_before
    if split_at > 0:
        left_r, right_r = _split_run(runs[first_ri][0], split_at)
        if right_r is not None:
            p.insert(list(p).index(left_r) + 1, right_r)

    # Re-scan after split
    runs = _get_direct_runs(p)
    char_pos = 0
    first_ri = last_ri = -1
    for ri, (_r, rt) in enumerate(runs):
        r_start = char_pos
        r_end = char_pos + len(rt)
        if first_ri < 0 and r_start <= start_pos < r_end:
            first_ri = ri
        if r_start < end_pos <= r_end:
            last_ri = ri
            break
        char_pos += len(rt)

    # Split last run if xref ends mid-run
    char_before = sum(len(runs[i][1]) for i in range(last_ri))
    split_at = end_pos - char_before
    if 0 < split_at < len(runs[last_ri][1]):
        left_r, right_r = _split_run(runs[last_ri][0], split_at)
        if right_r is not None:
            p.insert(list(p).index(left_r) + 1, right_r)

    # Final run indices
    runs = _get_direct_runs(p)
    char_pos = 0
    first_ri = last_ri = -1
    for ri, (_r, rt) in enumerate(runs):
        r_start = char_pos
        r_end = char_pos + len(rt)
        if first_ri < 0 and r_start <= start_pos < r_end:
            first_ri = ri
        if r_start < end_pos <= r_end:
            last_ri = ri
            break
        char_pos += len(rt)

    # Verify match text is intact
    actual = "".join(txt for _, txt in runs)[start_pos:end_pos]
    if not actual.startswith("第") or cn not in actual:
        return False

    # Collect runs to wrap
    runs_to_wrap = [runs[i][0] for i in range(first_ri, last_ri + 1)]

    if not all(r.getparent() is p for r in runs_to_wrap):
        return False

    # Create hyperlink with w:anchor (internal bookmark link)
    hl = etree.Element(f"{W}hyperlink")
    hl.set(f"{W}anchor", f"_TocClause{cn.replace('.', '_')}")
    hl.set(f"{W}history", "1")

    first_pos = list(p).index(runs_to_wrap[0])
    p.insert(first_pos, hl)
    for r in runs_to_wrap:
        p.remove(r)
        hl.append(r)

    return True


# ── Auto XRef (full conversion) ───────────────────────────────────────────────

def auto_xref(doc_path: str) -> dict:
    """Convert all static cross-references to clickable hyperlinks.

    Uses python-docx for reading and saving (reliable ZIP handling).
    lxml is used only for in-memory XML manipulation.

    Returns:
        {"ok": True, "hyperlinks_created": N, "bookmarks_added": M,
         "field_runs_removed": F, "xrefs_converted": X}
    """
    from docx import Document

    doc = Document(doc_path)
    root = doc.element
    body = root.find(f"{W}body")
    paras = [c for c in body if c.tag == f"{W}p"]

    # Step 1: Build clause index
    clause_to_pidx = _build_clause_index(paras)

    # Step 2: Add bookmarks to heading paragraphs
    bookmarks_added = _add_bookmarks_to_headings(paras, clause_to_pidx)

    # Step 3: Pre-scan — find paragraphs with xref matches
    para_matches = {}  # para_index → [(start, end, clause_num), ...]
    for pi, p in enumerate(paras):
        full_text = "".join(txt for _, txt in _get_direct_runs(p))
        matches = []
        for m in _XREF_PATTERN.finditer(full_text):
            cn = m.group(1)
            if cn in clause_to_pidx:
                matches.append((m.start(), m.end(), cn))
        if matches:
            para_matches[pi] = matches

    # Step 4: Per-paragraph processing
    field_runs_removed = 0
    hyperlinks_created = 0
    xrefs_converted = 0

    for pi, _matches in para_matches.items():
        p = paras[pi]

        # 4a: Remove field-code runs from this paragraph only
        field_runs_removed += _remove_field_runs(p)

        # 4b: Re-scan for xrefs (positions shifted after run removal)
        runs = _get_direct_runs(p)
        full_text = "".join(txt for _, txt in runs)

        matches = []
        for m in _XREF_PATTERN.finditer(full_text):
            cn = m.group(1)
            if cn in clause_to_pidx:
                matches.append((m.start(), m.end(), cn))

        # 4c: Process right-to-left so earlier positions stay valid
        for start_pos, end_pos, cn in reversed(matches):
            if _wrap_text_in_hyperlink(p, start_pos, end_pos, cn):
                hyperlinks_created += 1
                xrefs_converted += 1

    # Save using python-docx (handles ZIP structure correctly)
    doc.save(doc_path)

    return {
        "ok": True,
        "hyperlinks_created": hyperlinks_created,
        "bookmarks_added": bookmarks_added,
        "field_runs_removed": field_runs_removed,
        "xrefs_converted": xrefs_converted,
        "clauses_indexed": len(clause_to_pidx),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-Document Reference Scanner
# ═══════════════════════════════════════════════════════════════════════════════

# Chinese numeral → digit mapping for clause references
_CN_NUM_MAP = {
    '一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
    '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
}

def _cn_to_digit(cn_num: str) -> str:
    """Convert a simple Chinese numeral to digit string. Handles 一-十, 十二, 二十一, etc."""
    # Already a digit
    if cn_num.isdigit():
        return cn_num
    # Try direct mapping
    if cn_num in _CN_NUM_MAP:
        return _CN_NUM_MAP[cn_num]
    # Compound: 十一, 十二, 二十, 二十一, etc.
    if '十' in cn_num:
        result = 0
        parts = cn_num.split('十')
        if parts[0] == '':
            result = 10
        else:
            result = int(_CN_NUM_MAP.get(parts[0], 0)) * 10
        if len(parts) > 1 and parts[1]:
            result += int(_CN_NUM_MAP.get(parts[1], 0))
        return str(result)
    return cn_num


def _cn_clause_to_digit(match_text: str) -> str:
    """Convert '第三条' or '第三.二条' to '3' or '3.2'."""
    inner = match_text[1:-1]  # strip 第 and 条
    parts = []
    for segment in inner.split('.'):
        # segment is like "三" or "3" or "3.1"
        sub = []
        i = 0
        while i < len(segment):
            ch = segment[i]
            if ch.isdigit() or ch == '.':
                sub.append(ch)
                i += 1
            elif ch in _CN_NUM_MAP:
                # Collect consecutive Chinese numerals
                cn_part = ch
                i += 1
                while i < len(segment) and segment[i] in _CN_NUM_MAP:
                    cn_part += segment[i]
                    i += 1
                sub.append(_cn_to_digit(cn_part))
            else:
                i += 1
        parts.append(''.join(sub))
    return '.'.join(parts) if parts else inner


# Cross-doc reference patterns — match both Chinese and English forms
# Matches: 《担保合同》第5.3条, 《担保合同》第五条, 《担保合同》第5.3条(保证责任)
_CROSS_DOC_XREF_RE = re.compile(
    r'《([^》]+)》\s*第\s*([\d一二三四五六七八九十.]+(?:\.[\d一二三四五六七八九十]+)*)\s*条'
)
_CROSS_DOC_XREF_EN_RE = re.compile(
    r'(?:the\s+)?[""]([^""]+)[""]\s+(?:Agreement|Contract|Schedule|Appendix|Annex)\s+'
    r'(?:Clause|Section|Article|Para(?:graph)?)\s*(\d+(?:\.\d+)*)',
    re.IGNORECASE,
)
# Loose English form: "the Loan Agreement, Clause 3.2" or "Guarantee Contract Article 5"
_CROSS_DOC_XREF_EN_LOOSE_RE = re.compile(
    r'(?:the\s+)?([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,4})\s*'
    r'(?:Clause|Section|Article|Para(?:graph)?)\s*(\d+(?:\.\d+)*)',
)
# Schedule/attachment refs: 附件1, 附件一, Schedule 1, Appendix A, Annex I
_SCHEDULE_REF_RE = re.compile(
    r'(附件[一二三四五六七八九十\d]+|Schedule\s+\d+|[Aa]ppendix\s+[A-Z\d]+|[Aa]nnex\s+[IVX\d]+)'
)

# Stop-words for English loose matching — common legal terms that aren't doc names
_ENGLISH_LEGAL_STOP_WORDS: Set[str] = {
    "this", "the", "each", "any", "such", "other", "relevant", "applicable",
    "said", "aforesaid", "herein", "hereto", "hereunder", "hereof",
    "thereto", "thereof", "therein", "thereunder", "whereby", "whereof",
}


def _doc_name_from_path(path: str) -> str:
    """Extract a display name from a document path (filename without extension)."""
    return Path(path).stem


def _build_doc_name_map(paths: List[str]) -> Dict[str, str]:
    """Build a lookup: doc display name → full path.

    Supports matching by:
    - Full stem (e.g. '担保合同' matches '担保合同.docx')
    - Stem with parent dir prefix for disambiguation
    Returns a dict where keys are lowercase for case-insensitive matching.
    """
    name_map: Dict[str, str] = {}
    for p in paths:
        stem = _doc_name_from_path(p)
        name_map[stem.lower()] = p
        # Also index common aliases — strip suffixes like （终稿）/（修订版）
        clean = re.sub(r'[（(][^)）]*[)）]$', '', stem).strip()
        if clean.lower() != stem.lower():
            name_map[clean.lower()] = p
    return name_map


def _extract_all_runs_text(paras: list) -> List[str]:
    """Extract full text of each paragraph from lxml paragraph elements."""
    texts = []
    for p in paras:
        runs = p.findall(f".//{W}t")
        para_text = "".join((r.text or "") for r in runs)
        texts.append(para_text)
    return texts


def cross_doc_scan(docs: List[str]) -> dict:
    """Scan a set of documents for cross-document references and validate them.

    For each document, detects references to OTHER documents in the set —
    e.g. 《担保合同》第5.3条 referencing a clause in a separate guarantee doc.
    Validates that the target document exists and the referenced clause is
    present in that document's heading structure.

    Args:
        docs: List of absolute paths to .docx files. All docs are scanned
              for cross-references to each other.

    Returns:
        {
            "ok": True,
            "docs_scanned": N,
            "total_refs": T,
            "broken_refs": [{"source": path, "target_doc": name, "clause": cn,
                              "match": "...", "reason": "..."}],
            "valid_refs": [...],
            "schedule_refs": [...],   # attachments detected (not validated)
            "summary": {"total": T, "broken": B, "valid": V}
        }
    """
    if not docs or len(docs) < 2:
        return {
            "ok": True,
            "docs_scanned": len(docs) if docs else 0,
            "total_refs": 0,
            "broken_refs": [],
            "valid_refs": [],
            "schedule_refs": [],
            "summary": {"total": 0, "broken": 0, "valid": 0, "schedules": 0},
            "note": "Need at least 2 documents for cross-doc scan",
        }

    # Build name → path map and clause index for each document
    name_map = _build_doc_name_map(docs)
    doc_clause_indices: Dict[str, Dict[str, int]] = {}  # path → {clause_num: para_idx}
    doc_para_texts: Dict[str, List[str]] = {}  # path → [para_text, ...]

    for p in docs:
        try:
            doc_xml, _other, _order = _read_docx(p)
            root = etree.fromstring(doc_xml)
            body = root.find(f"{W}body")
            paras = [c for c in body if c.tag == f"{W}p"]
            doc_clause_indices[p] = _build_clause_index(paras)
            doc_para_texts[p] = _extract_all_runs_text(paras)
        except Exception:
            doc_clause_indices[p] = {}
            doc_para_texts[p] = []

    broken_refs = []
    valid_refs = []
    schedule_refs = []

    for src_path in docs:
        src_name = _doc_name_from_path(src_path)
        para_texts = doc_para_texts.get(src_path, [])

        for pi, para_text in enumerate(para_texts):
            # ── Chinese cross-doc refs: 《DOC_NAME》第X条 ──
            for m in _CROSS_DOC_XREF_RE.finditer(para_text):
                target_name = m.group(1).strip()
                clause_raw = m.group(2)
                clause_num = _cn_clause_to_digit(f"第{clause_raw}条")
                full_match = m.group(0)

                # Resolve target doc
                target_path = name_map.get(target_name.lower())
                if target_path:
                    target_stem = _doc_name_from_path(target_path)
                    if target_stem.lower() == src_name.lower():
                        continue  # Same-doc ref → handled by scan_xrefs, skip here
                    clause_idx = doc_clause_indices.get(target_path, {})
                    if clause_num in clause_idx:
                        valid_refs.append({
                            "source": src_path,
                            "source_para": pi + 1,
                            "target_doc": target_name,
                            "target_path": target_path,
                            "clause": clause_num,
                            "match": full_match,
                        })
                    else:
                        available = list(clause_idx.keys())[:20]
                        broken_refs.append({
                            "source": src_path,
                            "source_para": pi + 1,
                            "target_doc": target_name,
                            "target_path": target_path,
                            "clause": clause_num,
                            "match": full_match,
                            "reason": f"Document '{target_name}' found but clause {clause_num} not in headings. "
                                      f"Available clauses: {available}",
                        })
                else:
                    # Target doc not in the document set
                    available_docs = sorted(set(_doc_name_from_path(d) for d in docs))
                    broken_refs.append({
                        "source": src_path,
                        "source_para": pi + 1,
                        "target_doc": target_name,
                        "target_path": None,
                        "clause": clause_num,
                        "match": full_match,
                        "reason": f"Target document '{target_name}' not found in project. "
                                  f"Available documents: {available_docs}",
                    })

            # ── Schedule/attachment refs ──
            for m in _SCHEDULE_REF_RE.finditer(para_text):
                schedule_refs.append({
                    "source": src_path,
                    "source_para": pi + 1,
                    "match": m.group(0),
                })

    total = len(valid_refs) + len(broken_refs)
    return {
        "ok": len(broken_refs) == 0,
        "docs_scanned": len(docs),
        "total_refs": total,
        "broken_refs": broken_refs,
        "valid_refs": valid_refs,
        "schedule_refs": schedule_refs,
        "summary": {
            "total": total,
            "broken": len(broken_refs),
            "valid": len(valid_refs),
            "schedules": len(schedule_refs),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-Reference Audit (single document, comprehensive)
# ═══════════════════════════════════════════════════════════════════════════════

# Patterns covering both Chinese and English clause references
_XREF_AUDIT_PATTERNS = [
    # Chinese: 第X条, 第X.Y条, 第X.Y.Z条 — numeric
    (re.compile(r'第(\d+(?:\.\d+)*)条'), "cn"),
    # Chinese: 第一条, 第二条, 第十二条 — Chinese numerals
    (re.compile(r'第([一二三四五六七八九十百]+(?:\.\d+)*)条'), "cn_numeral"),
    # English: Section X / Section X.Y
    (re.compile(r'[Ss]ection\s+(\d+(?:\.\d+)*)'), "en"),
    # English: Clause X / Clause X.Y
    (re.compile(r'[Cc]lause\s+(\d+(?:\.\d+)*)'), "en"),
    # English: Article X / Article X.Y
    (re.compile(r'[Aa]rticle\s+(\d+(?:\.\d+)*)'), "en"),
]

# Build clause index from all heading styles (not just AOHead)
_HEADING_STYLES = frozenset({
    "Heading1", "Heading2", "Heading3", "Heading4",
    "Heading5", "Heading6", "Heading7", "Heading8", "Heading9",
    "AOHead1", "AOHead2", "AOHead3", "AOHead4",
    "AOAltHead3", "AOAltHead4",  # schedule/appendix heading variants
    "1", "2", "3", "4", "5", "6", "7", "8", "9",  # standard Word heading style IDs
})

# ── Extended audit patterns ─────────────────────────────────────────────────────

# Empty clause refs: "第条" without a number, often from botched edits
_EMPTY_CLAUSE_REF_RE = re.compile(r'第\s*条')

# Schedule/attachment refs missing a number: "附件(抵押物清单)" instead of "附件1(抵押物清单)"
_MISSING_SCHEDULE_NUM_RE = re.compile(
    r'附件\s*[（(]\s*(?:抵押|合同|协议|清单|资产|标的|保证|担保|承诺)'
)

# Forward refs that need target validation: "定义见第X条", "详见第X条", "定义见下文"
_FORWARD_REF_RE = re.compile(
    r'(?:定义|内容|具体|详见|参见|见|参照|参考)'
    r'(?:见|详见|参照|参见)?'
    r'(?:第(\d+(?:\.\d+)*)条|下文)'
)
# Simpler: "见第X条" and "定义见第X条"
_SEE_CLAUSE_RE = re.compile(r'(?:见|参见|详见|参照)\s*第(\d+(?:\.\d+)*)条')
_SEE_BELOW_RE = re.compile(r'(?:定义|内容|具体|详见|参见|见|描述)见下文')

# JT / law firm notes in square brackets: "[竞天注：...]" "[JT注：...]" "[金杜注：...]"
_JT_NOTE_RE = re.compile(r'\[(?:竞天|JT|金杜|jd|jt)\s*[注Note][^\]]*\]', re.IGNORECASE)

# Draft placeholder brackets: "[]" used as fill-in-the-blank in initial drafts
# Catches isolated [] and bracketed options like [有限责任公司]/[股份有限公司]
_DRAFT_PLACEHOLDER_RE = re.compile(r'\[\]|\[[^\]]*\][/／]\[[^\]]*\]')

# Clause ref with placeholder brackets: "第[]条" — missing clause number
_EMPTY_CLAUSE_BRACKET_RE = re.compile(r'第\s*\[\s*\]\s*条')


def _build_broad_clause_index(paras: list) -> dict[str, int]:
    """Build clause-number → paragraph-index map from all heading styles.

    Falls back to scanning paragraph text for '第X条' patterns when no
    heading styles are found (covers manually-numbered documents).
    """
    clause_to_pidx: dict[str, int] = {}

    # Pass 1: heading styles (AOHead or standard Heading)
    counters: dict[int, int] = {}
    for pi, p in enumerate(paras):
        pPr = p.find(f"{W}pPr")
        if pPr is None:
            continue
        ps = pPr.find(f"{W}pStyle")
        if ps is None:
            continue
        style = ps.get(f"{W}val", "")
        if style not in _HEADING_STYLES:
            continue

        # Determine level (1-9)
        level = 1
        for c in reversed(style):
            if c.isdigit():
                level = int(c)
                break

        # Increment counter at this level; reset deeper levels
        counters[level] = counters.get(level, 0) + 1
        for l in range(level + 1, 10):
            counters[l] = 0

        # Build clause number like "3" or "3.1" or "3.1.2"
        parts = [str(counters[l]) for l in sorted(counters) if counters[l] > 0 and l <= level]
        cn = ".".join(parts) if parts else str(counters[level])
        clause_to_pidx[cn] = pi

    # Pass 2: if no heading styles found, detect clauses from paragraph text
    if not clause_to_pidx:
        # Match both Arabic-digit and Chinese-numeral clause headings:
        #   第一条, 第十二条, 第3.1条, 第5.2.3条
        _MANUAL_CLAUSE_RE = re.compile(
            r'^第([\d一二三四五六七八九十百]+(?:\.\d+)*)条'
        )
        for pi, p in enumerate(paras):
            full_text = "".join(txt for _, txt in _get_direct_runs(p))
            m = _MANUAL_CLAUSE_RE.match(full_text.strip())
            if m:
                raw_cn = m.group(1)
                cn = _cn_to_digit(raw_cn)  # Convert 一 → 1, 十二 → 12
                if cn not in clause_to_pidx:
                    clause_to_pidx[cn] = pi

    # Pass 3: supplement with English section headings and inferred
    # (bold + short) headings. Always runs to catch headings that Pass 1/2
    # missed — mixed CN/EN docs, Section/Article patterns, pure inferred.
    _MANUAL_CLAUSE_RE = re.compile(
        r'^第([\d一二三四五六七八九十百]+(?:\.\d+)*)条'
    )
    for pi, p in enumerate(paras):
        full_text = "".join(txt for _, txt in _get_direct_runs(p))
        if not full_text.strip():
            continue
        # Try English section heading patterns
        m = _SECTION_HEAD_RE.match(full_text.strip())
        if m:
            cn = m.group(1)
            if cn not in clause_to_pidx:
                clause_to_pidx[cn] = pi
            continue
        # Try Chinese clause headings (numeric + Chinese numeral)
        m2 = _MANUAL_CLAUSE_RE.match(full_text.strip())
        if m2:
            cn = _cn_to_digit(m2.group(1))
            if cn not in clause_to_pidx:
                clause_to_pidx[cn] = pi
            continue
        # Try inferred headings (bold, no indent, short text)
        if _is_inferred_heading(p):
            # Extract leading number if present (both Arabic and Chinese)
            num_match = re.match(
                r'^[\s]*([\d一二三四五六七八九十百]+(?:\.\d+)*)[\.\s、)]',
                full_text.strip()
            )
            if num_match:
                cn = _cn_to_digit(num_match.group(1))
                if cn not in clause_to_pidx:
                    clause_to_pidx[cn] = pi

    return clause_to_pidx


def xref_audit(doc_path: str) -> dict:
    """Audit all cross-references in a document against actual clause headings.

    Finds every reference to a clause (both Chinese 第X条 and English
    Section/Clause/Article patterns) and checks whether the referenced
    clause actually exists in the document's heading structure.

    Also detects:
    - Empty clause refs (第条 without a number) from botched edits
    - Schedule refs missing numbers (附件(描述) instead of 附件1(描述))
    - Forward refs (见第X条) — validates the target exists
    - "See below" refs (定义见下文) — flagged for manual review
    - Bookmark integrity — mismatched bookmarkStart/End tags
    - Law firm notes ([竞天注：...], [JT注：...]) — pre-send cleanup items

    Returns:
        {
            "ok": True,
            "clauses_indexed": N,
            "total_references": T,
            "valid_refs": [...],
            "dead_refs": [...],
            "unreferenced_clauses": [cn, ...],
            "empty_clause_refs": [...],
            "missing_schedule_nums": [...],
            "broken_forward_refs": [...],
            "see_below_refs": [...],
            "bookmark_issues": [...],
            "jt_notes": [...],
            "summary": "..."
        }
    """
    doc_xml, _other, _order = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    paras = [c for c in body if c.tag == f"{W}p"]

    clause_to_pidx = _build_broad_clause_index(paras)
    referenced_clauses: set[str] = set()

    valid_refs: list[dict] = []
    dead_refs: list[dict] = []
    empty_clause_refs: list[dict] = []
    missing_schedule_nums: list[dict] = []
    broken_forward_refs: list[dict] = []
    see_below_refs: list[dict] = []
    jt_notes: list[dict] = []
    draft_placeholders: list[dict] = []

    def _ctx(full_text, m):
        ctx_start = max(0, m.start() - 30)
        ctx_end = min(len(full_text), m.end() + 50)
        context = full_text[ctx_start:ctx_end].replace("\t", " ")
        if ctx_start > 0:
            context = "…" + context
        if ctx_end < len(full_text):
            context += "…"
        return context

    for pi, p in enumerate(paras):
        full_text = "".join(txt for _, txt in _get_direct_runs(p))
        if not full_text.strip():
            # Still check for bookmark issues on empty paras
            continue

        # ── Clause reference audit (original patterns) ──
        for pattern, ref_type in _XREF_AUDIT_PATTERNS:
            for m in pattern.finditer(full_text):
                raw_cn = m.group(1)
                if ref_type == "cn_numeral":
                    cn = _cn_to_digit(raw_cn)
                else:
                    cn = raw_cn

                referenced_clauses.add(cn)

                entry = {
                    "para": pi + 1,
                    "ref_text": m.group(0),
                    "clause_num": cn,
                    "context": _ctx(full_text, m),
                }

                if cn in clause_to_pidx:
                    entry["status"] = "valid"
                    entry["target_para"] = clause_to_pidx[cn] + 1
                    valid_refs.append(entry)
                else:
                    entry["status"] = "dead"
                    entry["target_para"] = None
                    dead_refs.append(entry)

        # ── Empty clause refs: "第条" without a number ──
        for m in _EMPTY_CLAUSE_REF_RE.finditer(full_text):
            empty_clause_refs.append({
                "para": pi + 1,
                "ref_text": m.group(0),
                "context": _ctx(full_text, m),
                "reason": "Clause reference missing number — likely from botched edit",
            })

        # ── Missing schedule numbers: "附件(描述)" without "附件N(描述)" ──
        for m in _MISSING_SCHEDULE_NUM_RE.finditer(full_text):
            missing_schedule_nums.append({
                "para": pi + 1,
                "ref_text": m.group(0),
                "context": _ctx(full_text, m),
                "reason": "Schedule reference missing number — should be e.g. 附件1(抵押物清单)",
            })

        # ── Forward refs: "见第X条" — validate the target clause exists ──
        for m in _SEE_CLAUSE_RE.finditer(full_text):
            target_cn = m.group(1)
            if target_cn not in clause_to_pidx:
                broken_forward_refs.append({
                    "para": pi + 1,
                    "ref_text": m.group(0),
                    "target_clause": target_cn,
                    "context": _ctx(full_text, m),
                    "reason": f"Forward reference to clause {target_cn} which does not exist",
                })

        # ── "See below" refs: "定义见下文" — flag for manual review ──
        for m in _SEE_BELOW_RE.finditer(full_text):
            see_below_refs.append({
                "para": pi + 1,
                "ref_text": m.group(0),
                "context": _ctx(full_text, m),
                "reason": "See-below reference — verify target definition exists later in document",
            })

        # ── JT / law firm notes: "[竞天注：...]" ──
        for m in _JT_NOTE_RE.finditer(full_text):
            jt_notes.append({
                "para": pi + 1,
                "ref_text": m.group(0),
                "context": _ctx(full_text, m),
                "reason": "Law firm internal note — should be removed before sending to counterparty",
            })

        # ── Empty clause bracket refs: "第[]条" — missing number ──
        for m in _EMPTY_CLAUSE_BRACKET_RE.finditer(full_text):
            empty_clause_refs.append({
                "para": pi + 1,
                "ref_text": m.group(0),
                "context": _ctx(full_text, m),
                "reason": "Clause reference with placeholder brackets — number must be filled",
            })

        # ── Draft placeholder brackets: "[]" or "[X]/[Y]" alternatives ──
        for m in _DRAFT_PLACEHOLDER_RE.finditer(full_text):
            draft_placeholders.append({
                "para": pi + 1,
                "ref_text": m.group(0),
                "context": _ctx(full_text, m),
                "reason": "Draft placeholder — must be filled or resolved before finalizing",
            })

    # ── Bookmark integrity check ──
    bookmark_issues: list[dict] = []
    bm_start_ids: set[str] = set()
    bm_end_ids: set[str] = set()
    bm_name_by_id: dict[str, str] = {}

    for pi, p in enumerate(paras):
        for bm in p.iter(f"{W}bookmarkStart"):
            bm_id = bm.get(f"{W}id", "")
            bm_name = bm.get(f"{W}name", "")
            if bm_id:
                bm_start_ids.add(bm_id)
                bm_name_by_id[bm_id] = bm_name
        for bm in p.iter(f"{W}bookmarkEnd"):
            bm_id = bm.get(f"{W}id", "")
            if bm_id:
                bm_end_ids.add(bm_id)

    unmatched_starts = bm_start_ids - bm_end_ids
    unmatched_ends = bm_end_ids - bm_start_ids

    for bm_id in sorted(unmatched_starts):
        bookmark_issues.append({
            "type": "unmatched_start",
            "bookmark_id": bm_id,
            "bookmark_name": bm_name_by_id.get(bm_id, ""),
            "reason": "bookmarkStart without matching bookmarkEnd — may corrupt document",
        })
    for bm_id in sorted(unmatched_ends):
        bookmark_issues.append({
            "type": "unmatched_end",
            "bookmark_id": bm_id,
            "reason": "bookmarkEnd without matching bookmarkStart — may corrupt document",
        })

    unreferenced = sorted(
        [cn for cn in clause_to_pidx if cn not in referenced_clauses],
        key=lambda x: tuple(int(p) for p in x.split(".")),
    )

    total = len(valid_refs) + len(dead_refs)
    issues = (
        len(dead_refs) + len(empty_clause_refs) + len(missing_schedule_nums)
        + len(broken_forward_refs) + len(bookmark_issues) + len(jt_notes)
        + len(draft_placeholders)
    )
    all_ok = issues == 0

    summary_parts = [f"{total} references: {len(valid_refs)} valid, {len(dead_refs)} dead"]
    if empty_clause_refs:
        summary_parts.append(f"{len(empty_clause_refs)} empty clause refs")
    if missing_schedule_nums:
        summary_parts.append(f"{len(missing_schedule_nums)} schedule refs missing number")
    if broken_forward_refs:
        summary_parts.append(f"{len(broken_forward_refs)} broken forward refs")
    if see_below_refs:
        summary_parts.append(f"{len(see_below_refs)} see-below refs to review")
    if bookmark_issues:
        summary_parts.append(f"{len(bookmark_issues)} bookmark issues")
    if jt_notes:
        summary_parts.append(f"{len(jt_notes)} JT/internal notes to remove")
    if draft_placeholders:
        summary_parts.append(f"{len(draft_placeholders)} draft placeholders to resolve")
    summary_parts.append(f"{len(unreferenced)} clauses unreferenced")

    return {
        "ok": all_ok,
        "clauses_indexed": len(clause_to_pidx),
        "total_references": total,
        "valid_refs": valid_refs,
        "dead_refs": dead_refs,
        "unreferenced_clauses": unreferenced,
        "empty_clause_refs": empty_clause_refs,
        "missing_schedule_nums": missing_schedule_nums,
        "broken_forward_refs": broken_forward_refs,
        "see_below_refs": see_below_refs,
        "bookmark_issues": bookmark_issues,
        "jt_notes": jt_notes,
        "draft_placeholders": draft_placeholders,
        "summary": ", ".join(summary_parts),
    }


def audit_documents(docs: List[str]) -> dict:
    """Run internal and cross-document reference audits for deliver/gates."""
    doc_results = []
    internal_total = 0
    internal_dead = 0

    for path in docs:
        try:
            result = xref_audit(path)
        except Exception as exc:
            result = {
                "ok": False,
                "error": str(exc),
                "total_references": 0,
                "dead_refs": [],
                "valid_refs": [],
            }
        internal_total += int(result.get("total_references") or 0)
        internal_dead += len(result.get("dead_refs") or [])
        doc_results.append({"path": path, **result})

    cross_result = cross_doc_scan(docs) if len(docs) >= 2 else None
    cross_summary = cross_result.get("summary", {}) if isinstance(cross_result, dict) else {}
    cross_total = int(cross_summary.get("total") or 0)
    cross_broken = int(cross_summary.get("broken") or 0)
    total = internal_total + cross_total
    broken = internal_dead + cross_broken

    return {
        "ok": broken == 0 and all(bool(r.get("ok", True)) for r in doc_results),
        "docs_scanned": len(docs),
        "documents": doc_results,
        "cross_doc_scan": cross_result,
        "summary": {
            "total": total,
            "broken": broken,
            "valid": max(total - broken, 0),
            "internal_total": internal_total,
            "internal_dead": internal_dead,
            "cross_total": cross_total,
            "cross_broken": cross_broken,
            "schedules": int(cross_summary.get("schedules") or 0),
        },
    }
