"""Footnote and endnote add/remove for WordprocessingML documents.

Footnotes live in word/footnotes.xml, endnotes in word/endnotes.xml.
Each has a root <w:footnotes> or <w:endnotes> containing <w:footnote> or
<w:endnote> elements with w:id attributes.

In the document body, <w:footnoteReference w:id="N"> or <w:endnoteReference>
points to the corresponding note content.

Part relationships are defined in word/_rels/document.xml.rels.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

W = f"{{{W_NS}}}"

FOOTNOTES_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
ENDNOTES_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes"


@dataclass
class FootnoteInfo:
    note_id: int
    para_index: int
    ref_text: str
    content_text: str


def _find_para_by_index(body: etree._Element, para_idx: int) -> etree._Element | None:
    """Find the Nth w:p in body (0-indexed)."""
    count = 0
    for el in body.iter():
        if el.tag == f"{W}p":
            if count == para_idx:
                return el
            count += 1
    return None


def _next_note_id(notes_root: etree._Element, tag: str) -> int:
    """Find the next available footnote/endnote id (max + 1, starting from 1)."""
    max_id = 0
    for note in notes_root.findall(tag):
        nid_str = note.get(f"{W}id", "0")
        try:
            nid = int(nid_str)
            if nid > max_id:
                max_id = nid
        except ValueError:
            pass
    return max_id + 1


def _make_empty_notes_root(tag: str) -> etree._Element:
    """Create a minimal <w:footnotes> or <w:endnotes> element."""
    return etree.Element(
        tag,
        nsmap={
            "w": W_NS,
            "r": REL_NS,
        },
    )


def _read_notes(docx_path: str, part_name: str) -> etree._Element | None:
    """Read footnotes.xml or endnotes.xml from the docx, or None."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        if part_name in zf.namelist():
            return etree.fromstring(zf.read(part_name))
    return None


def _ensure_notes_part(
    other: dict[str, bytes],
    doc_xml: str,
    part_name: str,
    root_tag: str,
    rel_type: str,
) -> etree._Element:
    """Get or create a notes part (footnotes or endnotes)."""
    if part_name in other:
        return etree.fromstring(other[part_name])

    # Create new notes part
    notes_root = _make_empty_notes_root(root_tag)
    other[part_name] = etree.tostring(notes_root, xml_declaration=True,
                                       encoding="UTF-8", standalone="yes")

    # Ensure relationship in document.xml.rels
    from .openxml_opc import ensure_relationship, rels_member_for_part

    rels_path = rels_member_for_part("word/document.xml")
    rels_bytes = other.get(rels_path)
    if rels_bytes is not None:
        rels_root = etree.fromstring(rels_bytes)
    else:
        rels_root = etree.Element(
            f"{{{PKG_REL_NS}}}Relationships",
            nsmap={None: PKG_REL_NS},
        )

    ensure_relationship(rels_root, rel_type,
                        part_name.replace("word/", "", 1),
                        target_mode="Internal")
    other[rels_path] = etree.tostring(rels_root, xml_declaration=True,
                                       encoding="UTF-8", standalone="yes")

    # Ensure [Content_Types].xml has the part type
    ct_key = "[Content_Types].xml"
    ct_bytes = other.get(ct_key)
    if ct_bytes is not None:
        ct_root = etree.fromstring(ct_bytes)
        ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
        part_ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
        # Check if override already exists
        found = False
        for ov in ct_root.findall(f"{{{ct_ns}}}Override"):
            if ov.get("PartName") == f"/{part_name}":
                found = True
                break
        if not found:
            override = etree.SubElement(ct_root, f"{{{ct_ns}}}Override")
            override.set("PartName", f"/{part_name}")
            override.set("ContentType", part_ct)
            other[ct_key] = etree.tostring(ct_root, xml_declaration=True,
                                            encoding="UTF-8", standalone="yes")

    return notes_root


def add_footnote(
    docx_path: str,
    para: int,
    *,
    text: str = "",
    offset: int = -1,
    output: str | None = None,
) -> dict:
    """Add a footnote reference in the document body and its content.

    Args:
        docx_path: Path to .docx file.
        para: 1-indexed paragraph number.
        text: Footnote content text.
        offset: Character offset within paragraph text (-1 = append at end).
        output: Output path (default: overwrite input).
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

    # Ensure footnotes part exists
    notes_root = _ensure_notes_part(other, doc_xml,
                                     "word/footnotes.xml",
                                     f"{W}footnotes",
                                     FOOTNOTES_REL_TYPE)

    note_id = _next_note_id(notes_root, f"{W}footnote")

    # Build footnote content
    fn_el = etree.SubElement(notes_root, f"{W}footnote")
    fn_el.set(f"{W}id", str(note_id))

    if text:
        p = etree.SubElement(fn_el, f"{W}p")
        pPr = etree.SubElement(p, f"{W}pPr")
        pStyle = etree.SubElement(pPr, f"{W}pStyle")
        pStyle.set(f"{W}val", "FootnoteText")
        r = etree.SubElement(p, f"{W}r")
        rPr = etree.SubElement(r, f"{W}rPr")
        rStyle = etree.SubElement(rPr, f"{W}rStyle")
        rStyle.set(f"{W}val", "FootnoteReference")
        fn_ref = etree.SubElement(r, f"{W}footnoteRef")
        t = etree.SubElement(r, f"{W}t")
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = " " + text

    other["word/footnotes.xml"] = etree.tostring(notes_root, xml_declaration=True,
                                                   encoding="UTF-8", standalone="yes")

    # Insert footnote reference in document body
    ref_run = etree.Element(f"{W}r")
    ref_rPr = etree.SubElement(ref_run, f"{W}rPr")
    ref_style = etree.SubElement(ref_rPr, f"{W}rStyle")
    ref_style.set(f"{W}val", "FootnoteReference")
    ref = etree.SubElement(ref_run, f"{W}footnoteReference")
    ref.set(f"{W}id", str(note_id))

    if offset < 0 or offset >= len(list(para_el.iter(f"{W}t"))):
        para_el.append(ref_run)
    else:
        # Insert at character offset
        char_count = 0
        inserted = False
        for r_el in para_el.findall(f"{W}r"):
            t_els = r_el.findall(f"{W}t")
            for t_el in t_els:
                tlen = len(t_el.text or "")
                if char_count + tlen >= offset:
                    # Split the text at the offset
                    split_at = offset - char_count
                    orig = t_el.text or ""
                    t_el.text = orig[:split_at]
                    # Build a new run with remaining text
                    remain_run = etree.Element(f"{W}r")
                    remain_t = etree.SubElement(remain_run, f"{W}t")
                    remain_t.text = orig[split_at:]
                    remain_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                    # Insert ref_run then remain_run after current r_el
                    idx = list(para_el).index(r_el)
                    para_el.insert(idx + 1, remain_run)
                    para_el.insert(idx + 1, ref_run)
                    inserted = True
                    break
                char_count += tlen
            if inserted:
                break
        if not inserted:
            para_el.append(ref_run)

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_footnote.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct_key = "[Content_Types].xml"
        ct = other.get(ct_key)
        if ct is not None:
            zf.writestr(ct_key, ct)
        zf.writestr("word/document.xml", doc_xml_out)
        for name, data in other.items():
            if name == ct_key:
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "note_id": note_id, "para": para,
            "text": text, "type": "footnote", "path": out_path}


def add_endnote(
    docx_path: str,
    para: int,
    *,
    text: str = "",
    offset: int = -1,
    output: str | None = None,
) -> dict:
    """Add an endnote reference in the document body and its content."""
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

    notes_root = _ensure_notes_part(other, doc_xml,
                                     "word/endnotes.xml",
                                     f"{W}endnotes",
                                     ENDNOTES_REL_TYPE)

    note_id = _next_note_id(notes_root, f"{W}endnote")

    en_el = etree.SubElement(notes_root, f"{W}endnote")
    en_el.set(f"{W}id", str(note_id))

    if text:
        p = etree.SubElement(en_el, f"{W}p")
        pPr = etree.SubElement(p, f"{W}pPr")
        pStyle = etree.SubElement(pPr, f"{W}pStyle")
        pStyle.set(f"{W}val", "EndnoteText")
        r = etree.SubElement(p, f"{W}r")
        rPr = etree.SubElement(r, f"{W}rPr")
        rStyle = etree.SubElement(rPr, f"{W}rStyle")
        rStyle.set(f"{W}val", "EndnoteReference")
        fn_ref = etree.SubElement(r, f"{W}endnoteRef")
        t = etree.SubElement(r, f"{W}t")
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = " " + text

    other["word/endnotes.xml"] = etree.tostring(notes_root, xml_declaration=True,
                                                  encoding="UTF-8", standalone="yes")

    ref_run = etree.Element(f"{W}r")
    ref_rPr = etree.SubElement(ref_run, f"{W}rPr")
    ref_style = etree.SubElement(ref_rPr, f"{W}rStyle")
    ref_style.set(f"{W}val", "EndnoteReference")
    ref = etree.SubElement(ref_run, f"{W}endnoteReference")
    ref.set(f"{W}id", str(note_id))

    if offset < 0 or offset >= len(list(para_el.iter(f"{W}t"))):
        para_el.append(ref_run)
    else:
        char_count = 0
        inserted = False
        for r_el in para_el.findall(f"{W}r"):
            t_els = r_el.findall(f"{W}t")
            for t_el in t_els:
                tlen = len(t_el.text or "")
                if char_count + tlen >= offset:
                    split_at = offset - char_count
                    orig = t_el.text or ""
                    t_el.text = orig[:split_at]
                    remain_run = etree.Element(f"{W}r")
                    remain_t = etree.SubElement(remain_run, f"{W}t")
                    remain_t.text = orig[split_at:]
                    remain_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                    idx = list(para_el).index(r_el)
                    para_el.insert(idx + 1, remain_run)
                    para_el.insert(idx + 1, ref_run)
                    inserted = True
                    break
                char_count += tlen
            if inserted:
                break
        if not inserted:
            para_el.append(ref_run)

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_endnote.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct_key = "[Content_Types].xml"
        ct = other.get(ct_key)
        if ct is not None:
            zf.writestr(ct_key, ct)
        zf.writestr("word/document.xml", doc_xml_out)
        for name, data in other.items():
            if name == ct_key:
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "note_id": note_id, "para": para,
            "text": text, "type": "endnote", "path": out_path}


def remove_footnote(
    docx_path: str,
    note_id: int,
    *,
    output: str | None = None,
) -> dict:
    """Remove a footnote by id — removes both the content and all references."""
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    # Remove from footnotes.xml
    fn_bytes = other.get("word/footnotes.xml")
    removed_content = False
    if fn_bytes is not None:
        fn_root = etree.fromstring(fn_bytes)
        for fn in fn_root.findall(f"{W}footnote"):
            if fn.get(f"{W}id") == str(note_id):
                fn_root.remove(fn)
                removed_content = True
                break
        other["word/footnotes.xml"] = etree.tostring(fn_root, xml_declaration=True,
                                                       encoding="UTF-8", standalone="yes")

    # Remove footnote references from document body
    refs_removed = 0
    for r_el in body.iter(f"{W}r"):
        for fn_ref in r_el.findall(f"{W}footnoteReference"):
            if fn_ref.get(f"{W}id") == str(note_id):
                # Remove the entire run containing the reference
                parent = r_el.getparent()
                if parent is not None:
                    parent.remove(r_el)
                refs_removed += 1

    if not removed_content and refs_removed == 0:
        return {"ok": False, "error": f"footnote {note_id} not found"}

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_rm_fn.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct_key = "[Content_Types].xml"
        ct = other.get(ct_key)
        if ct is not None:
            zf.writestr(ct_key, ct)
        zf.writestr("word/document.xml", doc_xml_out)
        for name, data in other.items():
            if name == ct_key:
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "note_id": note_id, "refs_removed": refs_removed,
            "content_removed": removed_content, "path": out_path}


def remove_endnote(
    docx_path: str,
    note_id: int,
    *,
    output: str | None = None,
) -> dict:
    """Remove an endnote by id — removes both the content and all references."""
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    en_bytes = other.get("word/endnotes.xml")
    removed_content = False
    if en_bytes is not None:
        en_root = etree.fromstring(en_bytes)
        for en in en_root.findall(f"{W}endnote"):
            if en.get(f"{W}id") == str(note_id):
                en_root.remove(en)
                removed_content = True
                break
        other["word/endnotes.xml"] = etree.tostring(en_root, xml_declaration=True,
                                                      encoding="UTF-8", standalone="yes")

    refs_removed = 0
    for r_el in body.iter(f"{W}r"):
        for en_ref in r_el.findall(f"{W}endnoteReference"):
            if en_ref.get(f"{W}id") == str(note_id):
                parent = r_el.getparent()
                if parent is not None:
                    parent.remove(r_el)
                refs_removed += 1

    if not removed_content and refs_removed == 0:
        return {"ok": False, "error": f"endnote {note_id} not found"}

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_rm_en.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct_key = "[Content_Types].xml"
        ct = other.get(ct_key)
        if ct is not None:
            zf.writestr(ct_key, ct)
        zf.writestr("word/document.xml", doc_xml_out)
        for name, data in other.items():
            if name == ct_key:
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "note_id": note_id, "refs_removed": refs_removed,
            "content_removed": removed_content, "path": out_path}


def list_footnotes(docx_path: str) -> dict:
    """List all footnotes with their content and reference locations."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        fn_bytes = zf.read("word/footnotes.xml") if "word/footnotes.xml" in zf.namelist() else None

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    footnotes: list[dict[str, Any]] = []

    # Map note_id -> content text from footnotes.xml
    content_map: dict[int, str] = {}
    if fn_bytes is not None:
        fn_root = etree.fromstring(fn_bytes)
        for fn in fn_root.findall(f"{W}footnote"):
            nid_str = fn.get(f"{W}id", "")
            try:
                nid = int(nid_str)
            except ValueError:
                continue
            if nid <= 0:
                continue  # skip separator/continuation
            texts: list[str] = []
            for t in fn.iter(f"{W}t"):
                if t.text:
                    texts.append(t.text)
            content_map[nid] = "".join(texts)

    # Find footnote references in document body
    para_idx = 0
    for el in body.iter():
        if el.tag == f"{W}p":
            para_idx += 1
        elif el.tag == f"{W}r":
            for fn_ref in el.findall(f"{W}footnoteReference"):
                nid_str = fn_ref.get(f"{W}id", "")
                try:
                    nid = int(nid_str)
                except ValueError:
                    continue
                if nid <= 0:
                    continue
                ref_text = ""
                t = el.find(f"{W}t")
                if t is not None and t.text:
                    ref_text = t.text
                footnotes.append({
                    "id": nid,
                    "para": para_idx,
                    "ref_text": ref_text,
                    "content": content_map.get(nid, ""),
                    "type": "footnote",
                })

    return {"ok": True, "footnotes": footnotes}


def list_endnotes(docx_path: str) -> dict:
    """List all endnotes with their content and reference locations."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        en_bytes = zf.read("word/endnotes.xml") if "word/endnotes.xml" in zf.namelist() else None

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    endnotes: list[dict[str, Any]] = []

    content_map: dict[int, str] = {}
    if en_bytes is not None:
        en_root = etree.fromstring(en_bytes)
        for en in en_root.findall(f"{W}endnote"):
            nid_str = en.get(f"{W}id", "")
            try:
                nid = int(nid_str)
            except ValueError:
                continue
            if nid <= 0:
                continue
            texts: list[str] = []
            for t in en.iter(f"{W}t"):
                if t.text:
                    texts.append(t.text)
            content_map[nid] = "".join(texts)

    para_idx = 0
    for el in body.iter():
        if el.tag == f"{W}p":
            para_idx += 1
        elif el.tag == f"{W}r":
            for en_ref in el.findall(f"{W}endnoteReference"):
                nid_str = en_ref.get(f"{W}id", "")
                try:
                    nid = int(nid_str)
                except ValueError:
                    continue
                if nid <= 0:
                    continue
                ref_text = ""
                t = el.find(f"{W}t")
                if t is not None and t.text:
                    ref_text = t.text
                endnotes.append({
                    "id": nid,
                    "para": para_idx,
                    "ref_text": ref_text,
                    "content": content_map.get(nid, ""),
                    "type": "endnote",
                })

    return {"ok": True, "endnotes": endnotes}
