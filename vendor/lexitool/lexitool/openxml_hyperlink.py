"""Hyperlink add/remove/list for WordprocessingML documents.

Hyperlinks in OOXML use the w:hyperlink element wrapping one or more w:r runs.

External hyperlinks (URLs, mailto:) require a relationship entry in
word/_rels/document.xml.rels.  Internal hyperlinks (bookmark references)
use the w:anchor attribute.

Borrows the hyperlink model from dolanmiu/docx.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any

from lxml import etree

from .openxml_opc import ensure_relationship, read_relationships, rels_member_for_part

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

W = f"{{{W_NS}}}"


@dataclass
class HyperlinkInfo:
    para_index: int
    text: str
    url: str | None = None  # external URL or mailto:
    anchor: str | None = None  # bookmark name for internal links
    tooltip: str | None = None
    external: bool = True


def _find_para_by_index(body: etree._Element, para_idx: int) -> etree._Element | None:
    count = 0
    for el in body.iter():
        if el.tag == f"{W}p":
            if count == para_idx:
                return el
            count += 1
    return None


def add_hyperlink(
    docx_path: str,
    para: int,
    text: str,
    *,
    url: str | None = None,
    anchor: str | None = None,
    tooltip: str | None = None,
    output: str | None = None,
) -> dict:
    """Wrap text in a hyperlink within a paragraph.

    Specify *url* for an external hyperlink (https://..., mailto:...) or
    *anchor* for an internal bookmark reference.  One of *url* or *anchor*
    must be provided.

    *para* is 1-indexed.  If the text already exists as a w:r inside a
    w:hyperlink, it is skipped.
    """
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    para_el = _find_para_by_index(body, para - 1)
    if para_el is None:
        return {"ok": False, "error": f"paragraph {para} not found"}

    if not url and not anchor:
        return {"ok": False, "error": "url or anchor is required"}

    external = bool(url and not anchor)

    # Build hyperlink element
    hl = etree.Element(f"{W}hyperlink")
    if tooltip:
        hl.set(f"{W}tooltip", tooltip)

    if external:
        # Ensure relationship exists
        rels_path = rels_member_for_part("word/document.xml")
        rels_bytes = other.get(rels_path)
        if rels_bytes is None:
            rels_root = etree.Element(
                f"{{{PKG_REL_NS}}}Relationships",
                nsmap={None: PKG_REL_NS},
            )
        else:
            rels_root = etree.fromstring(rels_bytes)

        rid = ensure_relationship(rels_root,
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
            url, target_mode="External")
        hl.set(f"{{{REL_NS}}}id", rid)
        other[rels_path] = etree.tostring(rels_root, xml_declaration=True,
                                          encoding="UTF-8", standalone="yes")
    else:
        hl.set(f"{W}anchor", anchor or "")

    # Find the run containing the target text and wrap it
    found = False
    for child in list(para_el):
        if child.tag == f"{W}r":
            t_el = child.find(f"{W}t")
            if t_el is not None and t_el.text is not None and text in (t_el.text or ""):
                child_copy = etree.Element(f"{W}r")
                for sub in child:
                    child_copy.append(sub)
                hl.append(child_copy)
                para_el.replace(child, hl)
                found = True
                break
        elif child.tag == f"{W}hyperlink":
            # Skip existing hyperlinks
            for r in child.iter(f"{W}r"):
                t_el = r.find(f"{W}t")
                if t_el is not None and t_el.text is not None and text in (t_el.text or ""):
                    return {"ok": False, "error": f"text '{text}' is already inside a hyperlink"}

    if not found:
        return {"ok": False, "error": f"text '{text}' not found in paragraph {para}"}

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_hyperlink.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct = other.get("[Content_Types].xml")
        if ct is not None:
            zf.writestr("[Content_Types].xml", ct)
        zf.writestr("word/document.xml", doc_xml_out)
        for name, data in other.items():
            if name == "[Content_Types].xml":
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "para": para, "text": text,
            "url": url, "anchor": anchor, "external": external, "path": out_path}


def remove_hyperlink(
    docx_path: str,
    para: int,
    text: str,
    *,
    output: str | None = None,
) -> dict:
    """Unwrap a hyperlink element, preserving its child runs."""
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    para_el = _find_para_by_index(body, para - 1)
    if para_el is None:
        return {"ok": False, "error": f"paragraph {para} not found"}

    removed = 0
    for hl in list(para_el.findall(f"{W}hyperlink")):
        for r in hl.iter(f"{W}r"):
            t_el = r.find(f"{W}t")
            if t_el is not None and t_el.text is not None and text in (t_el.text or ""):
                # Unwrap: replace hyperlink with its children
                idx = list(para_el).index(hl)
                for child in reversed(list(hl)):
                    para_el.insert(idx, child)
                para_el.remove(hl)
                removed += 1
                break

    if removed == 0:
        return {"ok": False, "error": f"no hyperlink containing '{text}' found in paragraph {para}"}

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_rm_link.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct = other.get("[Content_Types].xml")
        if ct is not None:
            zf.writestr("[Content_Types].xml", ct)
        zf.writestr("word/document.xml", doc_xml_out)
        for name, data in other.items():
            if name == "[Content_Types].xml":
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "removed": removed, "path": out_path}


def list_hyperlinks(docx_path: str) -> dict:
    """List all hyperlinks in the document."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": True, "hyperlinks": []}

    hyperlinks: list[dict[str, Any]] = []
    para_idx = 0
    for el in body.iter():
        if el.tag == f"{W}p":
            para_idx += 1
        elif el.tag == f"{W}hyperlink":
            rid = el.get(f"{{{REL_NS}}}id")
            anchor = el.get(f"{W}anchor")
            tooltip = el.get(f"{W}tooltip")
            text_parts: list[str] = []
            for r in el.iter(f"{W}r"):
                t_el = r.find(f"{W}t")
                if t_el is not None and t_el.text:
                    text_parts.append(t_el.text)
            hyperlinks.append({
                "para": para_idx,
                "text": "".join(text_parts),
                "anchor": anchor or None,
                "relationship_id": rid or None,
                "tooltip": tooltip or None,
                "external": bool(rid and not anchor),
            })

    return {"ok": True, "hyperlinks": hyperlinks}
