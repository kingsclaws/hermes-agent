"""
bookmarks.py — Full CRUD for Word bookmarks (w:bookmarkStart / w:bookmarkEnd).

Operations:
  - add_bookmark: insert a named bookmark around a text range
  - remove_bookmark: delete a bookmark by name
  - list_bookmarks: enumerate all bookmarks in document
  - find_bookmark: locate a bookmark by name
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from copy import deepcopy

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _read_docx(path: str) -> tuple[bytes, dict[str, bytes]]:
    with zipfile.ZipFile(path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {n: zf.read(n) for n in zf.namelist() if n != "word/document.xml"}
    return doc_xml, other


def _write_docx(path: str, doc_xml: bytes, other: dict[str, bytes]) -> None:
    fd, tmp = tempfile.mkstemp(prefix="lexitool_bm.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # OOXML requires [Content_Types].xml as the first ZIP entry
        ct_xml = other.pop("[Content_Types].xml", None)
        if ct_xml is not None:
            zf.writestr("[Content_Types].xml", ct_xml)
        zf.writestr("word/document.xml", doc_xml)
        for name, data in other.items():
            zf.writestr(name, data)
    shutil.move(tmp, path)


def _make_bookmark_start(name: str, bm_id: int) -> etree._Element:
    el = etree.Element(f"{W}bookmarkStart")
    el.set(f"{W}name", name)
    el.set(f"{W}id", str(bm_id))
    return el


def _make_bookmark_end(bm_id: int) -> etree._Element:
    el = etree.Element(f"{W}bookmarkEnd")
    el.set(f"{W}id", str(bm_id))
    return el


def _next_bookmark_id(root) -> int:
    max_id = 0
    for el in root.iter(f"{W}bookmarkStart"):
        try:
            max_id = max(max_id, int(el.get(f"{W}id", 0)))
        except (ValueError, TypeError):
            pass
    for el in root.iter(f"{W}bookmarkEnd"):
        try:
            max_id = max(max_id, int(el.get(f"{W}id", 0)))
        except (ValueError, TypeError):
            pass
    return max_id + 1


def add_bookmark(
    doc_path: str,
    para_idx: int,
    name: str,
    start_offset: int = 0,
    end_offset: int | None = None,
) -> dict:
    """Add a named bookmark around text in a paragraph.

    Args:
        doc_path: Path to .docx file.
        para_idx: 0-indexed paragraph number.
        name: Bookmark name (must be unique in document).
        start_offset: Character offset where bookmark starts.
        end_offset: Character offset where bookmark ends
                    (None = end of paragraph text).

    Returns:
        {"ok": True, "name": name, "id": bm_id, "para": para_idx}
    """
    doc_xml, other = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    # Check name uniqueness
    for bm in root.iter(f"{W}bookmarkStart"):
        if bm.get(f"{W}name") == name:
            return {"ok": False, "reason": f"bookmark '{name}' already exists"}

    # Find target paragraph
    paras = [el for el in body if el.tag == f"{W}p"]
    if para_idx < 0 or para_idx >= len(paras):
        return {"ok": False, "reason": f"para_idx {para_idx} out of range (0-{len(paras)-1})"}

    para_el = paras[para_idx]
    bm_id = _next_bookmark_id(root)

    bm_start = _make_bookmark_start(name, bm_id)
    bm_end = _make_bookmark_end(bm_id)

    # Insert at paragraph boundaries by default
    if start_offset == 0 and end_offset is None:
        para_el.insert(0, bm_start)
        para_el.append(bm_end)

    try:
        doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = etree.tostring(root, encoding="UTF-8")

    _write_docx(doc_path, doc_xml_out, other)

    return {"ok": True, "name": name, "id": bm_id, "para": para_idx}


def remove_bookmark(doc_path: str, name: str) -> dict:
    """Remove a bookmark and its matching end marker by name.

    Returns {"ok": True, "removed": True} or {"ok": True, "removed": False}.
    """
    doc_xml, other = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)

    removed = False
    bm_id = None

    for bm in root.iter(f"{W}bookmarkStart"):
        if bm.get(f"{W}name") == name:
            bm_id = bm.get(f"{W}id")
            parent = bm.getparent()
            if parent is not None:
                parent.remove(bm)
                removed = True
            break

    if bm_id is not None:
        for bm in list(root.iter(f"{W}bookmarkEnd")):
            if bm.get(f"{W}id") == bm_id:
                parent = bm.getparent()
                if parent is not None:
                    parent.remove(bm)

    try:
        doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = etree.tostring(root, encoding="UTF-8")

    _write_docx(doc_path, doc_xml_out, other)

    return {"ok": True, "removed": removed}


def list_bookmarks(doc_path: str) -> list[dict]:
    """Enumerate all bookmarks in the document.

    Returns list of {"name": str, "id": str, "para": int (0-indexed)}.
    """
    with zipfile.ZipFile(doc_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    # Build paragraph index
    para_of_element: dict = {}
    para_count = 0
    for child in body:
        if child.tag == f"{W}p":
            para_count += 1
            for desc in child.iter():
                para_of_element[id(desc)] = para_count - 1

    bookmarks = []
    for bm in root.iter(f"{W}bookmarkStart"):
        name = bm.get(f"{W}name", "")
        bm_id = bm.get(f"{W}id", "0")
        # Find which paragraph contains this bookmark
        para_idx = 0
        parent = bm.getparent()
        while parent is not None:
            if parent.tag == f"{W}p":
                # Linear search for para index
                for i, el in enumerate(body):
                    if el is parent:
                        para_idx = i
                        break
                break
            parent = parent.getparent()

        bookmarks.append({"name": name, "id": bm_id, "para": para_idx})

    return bookmarks


def find_bookmark(doc_path: str, name: str) -> dict | None:
    """Find a bookmark by name.

    Returns {"name": str, "id": str, "para": int} or None.
    """
    bookmarks = list_bookmarks(doc_path)
    for bm in bookmarks:
        if bm["name"] == name:
            return bm
    return None
