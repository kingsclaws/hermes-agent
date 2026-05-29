"""
xref.py — Cross-reference hyperlinking for legal documents.

Converts static "第X条(title)" patterns into clickable w:hyperlink elements
pointing to heading bookmarks. Preserves all run-level formatting.

Operations:
  - scan_xrefs: Dry-run scan showing what xrefs exist and what they'd link to
  - auto_xref:   Full conversion — add bookmarks, flatten broken field codes,
                  wrap xref text in hyperlinks
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from copy import deepcopy

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
    """Build clause-number → paragraph-index map from AOHead styles."""
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

    return clause_to_pidx


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
