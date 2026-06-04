"""
fields.py — Field code injection for Word cross-references, page numbering,
and document structure fields.

Supports: REF, PAGEREF, PAGE, NUMPAGES, TOC, SEQ, HYPERLINK, DOCPROPERTY,
          NOTEREF, STYLEREF, AUTONUM, AUTONUMLGL.

Constructs the five-element field structure:
  w:r > w:fldChar (begin)
  w:r > w:instrText
  w:r > w:fldChar (separate)
  w:r > w:t              (display text / placeholder)
  w:r > w:fldChar (end)
"""
from __future__ import annotations

import os
import re
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
    "NOTEREF": "NOTEREF",
    "STYLEREF": "STYLEREF",
    "AUTONUM": "AUTONUM",
    "AUTONUMLGL": "AUTONUMLGL",
}

# Fields whose instruction is a bookmark name (used for validation / scanning)
_BOOKMARK_FIELDS = {"REF", "PAGEREF", "NOTEREF"}

# Fields whose instruction is a style name
_STYLE_FIELDS = {"STYLEREF"}


def _read_docx(path: str) -> tuple[bytes, dict[str, bytes]]:
    with zipfile.ZipFile(path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {n: zf.read(n) for n in zf.namelist() if n != "word/document.xml"}
    return doc_xml, other


def _write_docx(path: str, doc_xml: bytes, other: dict[str, bytes]) -> None:
    fd, tmp = tempfile.mkstemp(prefix="lexitool_fld.", suffix=".docx")
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


def list_fields(doc_path: str) -> list[dict]:
    """Scan the document and return all field codes with their metadata.

    Returns a list of dicts with keys:
      - para: 1-indexed paragraph number
      - field_type: REF, PAGEREF, NOTEREF, STYLEREF, TOC, PAGE, etc.
      - instruction: the full field instruction string
      - display_text: current display text (before field update)
    """
    try:
        with zipfile.ZipFile(doc_path, "r") as zf:
            doc_xml = zf.read("word/document.xml")
    except Exception as e:
        return [{"error": str(e)}]

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return []

    fields: list[dict] = []
    para_idx = 0

    for child in body:
        if child.tag != f"{W}p":
            continue
        para_idx += 1

        # State machine: find fldChar begin → collect instrText → collect display → fldChar end
        in_field = False
        instr_text = ""
        display_text = ""
        runs = list(child.findall(f"{W}r"))

        for r_el in runs:
            fld_char = r_el.find(f"{W}fldChar")
            if fld_char is not None:
                fld_type = fld_char.get(f"{W}fldCharType", "")
                if fld_type == "begin":
                    in_field = True
                    instr_text = ""
                    display_text = ""
                elif fld_type == "separate":
                    # Done collecting instruction, now collecting display
                    pass
                elif fld_type == "end" and in_field:
                    in_field = False
                    ft = _parse_field_type(instr_text)
                    bm = _parse_bookmark_name(instr_text)
                    fields.append({
                        "para": para_idx,
                        "field_type": ft,
                        "bookmark": bm,
                        "instruction": instr_text.strip(),
                        "display_text": display_text.strip(),
                    })
                continue

            if in_field:
                instr_el = r_el.find(f"{W}instrText")
                if instr_el is not None:
                    instr_text += instr_el.text or ""
                    continue

                # After separator (or before it, for simple fields)
                t_el = r_el.find(f"{W}t")
                if t_el is not None:
                    display_text += t_el.text or ""

    return fields


def _parse_field_type(instr: str) -> str:
    """Extract field type from an instruction string like ' REF MyBookmark \\h '."""
    parts = instr.strip().split()
    if parts:
        return parts[0].upper()
    return "UNKNOWN"


def _parse_bookmark_name(instr: str) -> str:
    """Extract bookmark/style name from a field instruction.

    For REF/PAGEREF/NOTEREF: the first token after the field type is the bookmark name.
    For STYLEREF: the first token after the field type is the style name.
    Handles quoted names like STYLEREF "Heading 1".
    """
    parts = instr.strip().split()
    if len(parts) < 2:
        return ""
    name = parts[1]
    # Handle quoted names
    if name.startswith('"') and len(parts) >= 2:
        name = parts[1].strip('"')
        # Check if there's more of the quoted string
        for p in parts[2:]:
            name += " " + p
            if p.endswith('"'):
                name = name.rstrip('"')
                break
    return name


# Field markup patterns used in annotated text: [ref:name], [page-ref:name], etc.
_FIELD_MARKUP_RE = re.compile(
    r"\[(?P<marker>ref|page-ref|note-ref|style-ref):(?P<name>[^\]]+)\]"
)


def resolve_field_markup(doc_path: str) -> dict:
    """Convert [ref:name] / [page-ref:name] markup in run text to real field codes.

    Scans all paragraphs in the document. When a run contains a field markup
    tag like '[ref:MyBookmark]', it splits the run into text/field/text parts
    and inserts the corresponding Word field code (fldChar begin/instrText/
    separate/t/end).

    Handles multiple field markers in the same paragraph correctly.

    After calling this, run update_fields() to tell Word to refresh.

    Returns:
        {"ok": True, "converted": N, "details": [...]}
    """
    doc_xml, other = _read_docx(doc_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "reason": "no document body"}

    marker_to_field = {
        "ref": "REF",
        "page-ref": "PAGEREF",
        "note-ref": "NOTEREF",
        "style-ref": "STYLEREF",
    }

    converted = []
    para_idx = 0

    for child in body:
        if child.tag != f"{W}p":
            continue
        para_idx += 1

        # Build a replacement plan: collect (run_el, old_text, replacements)
        # where replacements is [(start, end, field_type, name)]
        # Process from end to start so character offsets stay valid.
        plan: list[tuple] = []

        for r_el in child.findall(f"{W}r"):
            t_el = r_el.find(f"{W}t")
            if t_el is None or not t_el.text:
                continue

            replacements = []
            for m in _FIELD_MARKUP_RE.finditer(t_el.text):
                marker = m.group("marker")
                name = m.group("name")
                field_type = marker_to_field.get(marker)
                if field_type:
                    replacements.append((m.start(), m.end(), field_type, name))

            if replacements:
                plan.append((r_el, t_el, replacements))

        # Process each run with its replacements
        for r_el, t_el, replacements in plan:
            run_idx = list(child).index(r_el)

            # Split text at replacement boundaries, building pieces
            # Each piece is either text or a field block
            text = t_el.text
            last_end = 0
            pieces = []

            for start, end, field_type, name in replacements:
                # Text before this marker
                if start > last_end:
                    pieces.append(("text", text[last_end:start]))
                # The field
                pieces.append(("field", field_type, name))
                last_end = end

            # Trailing text after last marker
            if last_end < len(text):
                pieces.append(("text", text[last_end:]))

            # Remove the original run
            child.remove(r_el)

            # Insert replacement elements at the original position
            insert_pos = run_idx
            for piece in pieces:
                if piece[0] == "text":
                    if piece[1].strip():
                        new_r = etree.Element(f"{W}r")
                        new_t = etree.SubElement(new_r, f"{W}t")
                        new_t.text = piece[1]
                        child.insert(insert_pos, new_r)
                        insert_pos += 1
                    # Empty text: skip, don't insert anything
                else:
                    # Insert field code block (5 elements)
                    _, field_type, name = piece
                    display = f"[{name}]"
                    instruction = f" {field_type} {name} "

                    field_begin = _make_fld_char("begin")
                    field_instr = _make_instr_text(instruction)
                    field_sep = _make_fld_char("separate")
                    field_display = _make_field_display(display)
                    field_end = _make_fld_char("end")

                    for el in [field_begin, field_instr, field_sep, field_display, field_end]:
                        child.insert(insert_pos, el)
                        insert_pos += 1

                    converted.append({
                        "para": para_idx,
                        "field_type": field_type,
                        "name": name,
                        "display": display,
                    })

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    _write_docx(doc_path, doc_xml_out, other)

    return {"ok": True, "converted": len(converted), "details": converted}
