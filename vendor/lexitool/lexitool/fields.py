"""
fields.py — Field code injection for Word cross-references and page numbering.

Supports: REF, PAGEREF, PAGE, NUMPAGES, TOC, SEQ, HYPERLINK, DOCPROPERTY.

Constructs the three-element field structure:
  w:r > w:fldChar (begin)
  w:r > w:instrText
  w:r > w:fldChar (separate)
  w:r > w:t              (display text / placeholder)
  w:r > w:fldChar (end)
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Mapping from field_type to INSTRUCTION text
FIELD_INSTRUCTIONS = {
    "REF": "REF",
    "PAGEREF": "PAGEREF",
    "PAGE": "PAGE",
    "NUMPAGES": "NUMPAGES",
    "TOC": "TOC",
    "SEQ": "SEQ",
    "HYPERLINK": "HYPERLINK",
    "DOCPROPERTY": "DOCPROPERTY",
}


def _read_docx(path: str) -> tuple[bytes, dict[str, bytes]]:
    with zipfile.ZipFile(path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {n: zf.read(n) for n in zf.namelist() if n != "word/document.xml"}
    return doc_xml, other


def _write_docx(path: str, doc_xml: bytes, other: dict[str, bytes]) -> None:
    fd, tmp = tempfile.mkstemp(prefix="lexitool_fld.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", doc_xml)
        for name, data in other.items():
            zf.writestr(name, data)
    shutil.move(tmp, path)


def _make_fld_char(fld_char_type: str) -> etree._Element:
    """Create a w:fldChar element.

    fld_char_type: "begin", "separate", or "end"
    """
    r = etree.Element(f"{W}r")
    fldChar = etree.SubElement(r, f"{W}fldChar")
    fldChar.set(f"{W}fldCharType", fld_char_type)
    return r


def _make_instr_text(instruction: str) -> etree._Element:
    """Create a w:instrText element inside w:r."""
    r = etree.Element(f"{W}r")
    instrText = etree.SubElement(r, f"{W}instrText")
    instrText.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instrText.text = instruction
    return r


def _make_field_display(text: str = "") -> etree._Element:
    """Create the display text run (between separator and end fldChar)."""
    r = etree.Element(f"{W}r")
    t = etree.SubElement(r, f"{W}t")
    t.text = text
    return r


def insert_field(
    doc_path: str,
    para_idx: int,
    offset: int,
    field_type: str,
    instruction: str,
    display_text: str = "",
) -> dict:
    """Insert a Word field code at a specific position in a paragraph.

    Args:
        doc_path: Path to .docx file.
        para_idx: 0-indexed paragraph number.
        offset: Character offset within the paragraph to insert at.
        field_type: One of "REF", "PAGEREF", "PAGE", "NUMPAGES", "TOC", "SEQ",
                    "HYPERLINK", "DOCPROPERTY".
        instruction: The field instruction (e.g. bookmark name for REF,
                     "\\o \"1-3\"" for TOC).
        display_text: Placeholder text shown before field update.

    Returns:
        {"ok": True, "para": para_idx, "offset": offset, "field": field_type}
    """
    doc_xml, other = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    if field_type.upper() in FIELD_INSTRUCTIONS:
        field_type = field_type.upper()
    else:
        return {"ok": False, "reason": f"unknown field type: {field_type}"}

    # Find target paragraph
    paras = [el for el in body if el.tag == f"{W}p"]
    if para_idx < 0 or para_idx >= len(paras):
        return {"ok": False, "reason": f"para_idx {para_idx} out of range"}

    para_el = paras[para_idx]

    # Build field instruction string
    full_instr = f" {field_type} {instruction} " if instruction else f" {field_type} "

    # Build the field elements
    field_begin = _make_fld_char("begin")
    field_instr = _make_instr_text(full_instr)
    field_sep = _make_fld_char("separate")
    field_display = _make_field_display(display_text or f"[{field_type}]")
    field_end = _make_fld_char("end")

    elements = [field_begin, field_instr, field_sep, field_display, field_end]

    # Insert at appropriate position within paragraph
    if offset == 0:
        for el in reversed(elements):
            para_el.insert(0, el)
    elif offset < 0:
        # Append at end
        for el in elements:
            para_el.append(el)
    else:
        # Insert after the run that contains this character offset
        insert_idx = _find_insert_index(para_el, offset)
        for el in reversed(elements):
            para_el.insert(insert_idx, el)

    try:
        doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    except Exception:
        doc_xml_out = etree.tostring(root, encoding="UTF-8")

    _write_docx(doc_path, doc_xml_out, other)

    return {"ok": True, "para": para_idx, "offset": offset, "field": field_type, "instruction": instruction}


def _find_insert_index(para_el, offset: int) -> int:
    """Find the insertion index within a paragraph for a given character offset."""
    pos = 0
    for i, child in enumerate(para_el):
        if child.tag == f"{W}r":
            text_len = 0
            for sub in child:
                if sub.tag in (f"{W}t", f"{W}delText"):
                    text_len += len(sub.text or "")
                elif sub.tag == f"{W}tab":
                    text_len += 1
            if pos + text_len >= offset:
                return i + 1
            pos += text_len
    return len(para_el)


def update_fields(doc_path: str) -> dict:
    """Signal that fields should be updated when document is opened.

    This sets the w:updateFields element in document settings, which tells
    Word to refresh all fields on open.  Note: actual recalculation is
    performed by Word (or LibreOffice), not by us.

    Returns {"ok": True, "message": "updateFields flag set in settings.xml"}
    """
    try:
        with zipfile.ZipFile(doc_path, "r") as zf:
            files = dict(zf.namelist())
            settings_xml = zf.read("word/settings.xml") if "word/settings.xml" in files else None

        other = {}
        with zipfile.ZipFile(doc_path, "r") as zf:
            for name in zf.namelist():
                if name != "word/settings.xml":
                    other[name] = zf.read(name)

        if settings_xml is not None:
            root = etree.fromstring(settings_xml)
        else:
            root = etree.Element(f"{W}settings")

        # Add w:updateFields if not present
        update = root.find(f"{W}updateFields")
        if update is None:
            update = etree.SubElement(root, f"{W}updateFields")
            update.set(f"{W}val", "true")

        settings_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

        fd, tmp = tempfile.mkstemp(prefix="lexitool_fld.", suffix=".docx")
        os.close(fd)
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("word/settings.xml", settings_out)
            for name, data in other.items():
                zf.writestr(name, data)
        shutil.move(tmp, doc_path)

        return {"ok": True, "message": "updateFields flag set in settings.xml"}

    except Exception as e:
        return {"ok": False, "reason": str(e)}
