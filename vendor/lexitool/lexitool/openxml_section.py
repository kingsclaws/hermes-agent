"""Page setup and section management for WordprocessingML documents.

Borrows the section property model from dolanmiu/docx and applies it
through zipfile + lxml (no python-docx dependency).

OOXML section semantics:
- The body's final child can be w:sectPr -- this defines the last (or only) section.
- A w:sectPr inside w:pPr of a body paragraph marks a section break. The paragraph
  ends the previous section; its w:sectPr defines the new section.
"""
from __future__ import annotations

import copy
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

# Twips per unit
TPCM = 567   # twips per cm
TPIN = 1440  # twips per inch

PAGE_SIZES = {
    "A4":        (11906, 16838),
    "A3":        (16838, 23814),
    "A5":        (8391,  11906),
    "Letter":    (12240, 15840),
    "Legal":     (12240, 20160),
    "Tabloid":   (15840, 24480),
    "B5":        (10058, 14580),
    "Executive": (10440, 15120),
}


def _parse_dim(val: str) -> int:
    """Parse a dimension string into twips.  Accepts cm, in, mm, pt, or plain twips."""
    val = (val or "").strip().lower()
    if not val:
        raise ValueError("empty dimension")
    if val.endswith("cm"):
        return int(round(float(val[:-2]) * TPCM))
    if val.endswith("in"):
        return int(round(float(val[:-2]) * TPIN))
    if val.endswith("mm"):
        return int(round(float(val[:-2]) * 56.7))
    if val.endswith("pt"):
        return int(round(float(val[:-2]) * 20))
    return int(val)


@dataclass
class PageSetup:
    """Page dimensions and margins for a section.

    All dimensions in twips (twentieths of a point).  Use helper constructors
    or _parse_dim for unit conversion.
    """

    page_width: int | None = None
    page_height: int | None = None
    orientation: str | None = None  # "portrait" or "landscape"
    margin_top: int | None = None
    margin_bottom: int | None = None
    margin_left: int | None = None
    margin_right: int | None = None
    margin_gutter: int | None = None
    margin_header: int | None = None
    margin_footer: int | None = None

    @classmethod
    def from_preset(cls, size: str, orientation: str = "portrait") -> PageSetup:
        """Create from a named page size.  ``size`` is one of A4/A3/A5/Letter/Legal/etc."""
        key = size.capitalize()
        w, h = PAGE_SIZES.get(key)
        if w is None:
            raise ValueError(f"unknown page size: {size}")
        if orientation == "landscape":
            w, h = h, w
        return cls(page_width=w, page_height=h, orientation=orientation)

    @classmethod
    def from_margins(
        cls,
        top: str = "2.54cm",
        bottom: str = "2.54cm",
        left: str = "3.18cm",
        right: str = "3.18cm",
        gutter: str = "0cm",
        header: str = "1.25cm",
        footer: str = "1.25cm",
    ) -> PageSetup:
        """Create margin-only setup using human-readable dimensions."""
        return cls(
            margin_top=_parse_dim(top),
            margin_bottom=_parse_dim(bottom),
            margin_left=_parse_dim(left),
            margin_right=_parse_dim(right),
            margin_gutter=_parse_dim(gutter),
            margin_header=_parse_dim(header),
            margin_footer=_parse_dim(footer),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.page_width is not None:
            d["w"] = str(self.page_width)
        if self.page_height is not None:
            d["h"] = str(self.page_height)
        if self.orientation:
            d["orient"] = self.orientation
        return d

    def margin_dict(self) -> dict[str, str]:
        d: dict[str, str] = {}
        for attr, key in [
            ("margin_top", "top"),
            ("margin_bottom", "bottom"),
            ("margin_left", "left"),
            ("margin_right", "right"),
            ("margin_gutter", "gutter"),
            ("margin_header", "header"),
            ("margin_footer", "footer"),
        ]:
            val = getattr(self, attr)
            if val is not None:
                d[key] = str(val)
        return d

    def merge_into(self, other: PageSetup) -> PageSetup:
        """Return a new PageSetup with non-None fields from *other* overriding *self*."""
        result = copy.copy(self)
        for field_name in [
            "page_width", "page_height", "orientation",
            "margin_top", "margin_bottom", "margin_left", "margin_right",
            "margin_gutter", "margin_header", "margin_footer",
        ]:
            val = getattr(other, field_name)
            if val is not None:
                setattr(result, field_name, val)
        return result


@dataclass
class SectionInfo:
    """Read-only descriptor for one document section."""

    index: int
    start_para: int  # first paragraph of this section (0-indexed)
    end_para: int | None  # last paragraph *before* the section break (None = end of doc)
    page_setup: PageSetup
    sect_pr_element: etree._Element | None  # the w:sectPr that governs this section
    header_refs: dict[str, str] = field(default_factory=dict)  # type -> rId
    footer_refs: dict[str, str] = field(default_factory=dict)  # type -> rId


def _find_paragraphs(body: etree._Element) -> list[etree._Element]:
    return [el for el in body if el.tag == f"{W}p"]


def _extract_page_setup_from_sect_pr(sectPr: etree._Element | None) -> PageSetup:
    if sectPr is None:
        return PageSetup()
    ps = PageSetup()
    pgSz = sectPr.find(f"{W}pgSz")
    if pgSz is not None:
        w = pgSz.get(f"{W}w")
        h = pgSz.get(f"{W}h")
        orient = pgSz.get(f"{W}orient")
        if w:
            ps.page_width = int(w)
        if h:
            ps.page_height = int(h)
        if orient:
            ps.orientation = orient
    pgMar = sectPr.find(f"{W}pgMar")
    if pgMar is not None:
        for attr, field_name in [
            ("top", "margin_top"), ("bottom", "margin_bottom"),
            ("left", "margin_left"), ("right", "margin_right"),
            ("gutter", "margin_gutter"), ("header", "margin_header"),
            ("footer", "margin_footer"),
        ]:
            val = pgMar.get(f"{W}{attr}")
            if val:
                setattr(ps, field_name, int(val))
    return ps


def _extract_header_footer_refs(sectPr: etree._Element) -> tuple[dict[str, str], dict[str, str]]:
    headers: dict[str, str] = {}
    footers: dict[str, str] = {}
    if sectPr is None:
        return headers, footers
    for el in sectPr:
        tag = el.tag
        if tag == f"{W}headerReference":
            tp = el.get(f"{W}type", "default")
            rid = el.get(f"{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
            if rid:
                headers[tp] = rid
        elif tag == f"{W}footerReference":
            tp = el.get(f"{W}type", "default")
            rid = el.get(f"{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
            if rid:
                footers[tp] = rid
    return headers, footers


def read_sections(docx_path: str) -> list[SectionInfo]:
    """Return a list of all sections in the document."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return []

    paragraphs = [el for el in body if el.tag == f"{W}p"]
    body_sectPr = body.find(f"{W}sectPr")
    sections: list[SectionInfo] = []
    section_start = 0

    for i, para in enumerate(paragraphs):
        pPr = para.find(f"{W}pPr")
        para_sectPr = pPr.find(f"{W}sectPr") if pPr is not None else None
        if para_sectPr is not None:
            ps = _extract_page_setup_from_sect_pr(para_sectPr)
            h, f = _extract_header_footer_refs(para_sectPr)
            sections.append(SectionInfo(
                index=len(sections),
                start_para=section_start,
                end_para=i,
                page_setup=ps,
                sect_pr_element=para_sectPr,
                header_refs=h,
                footer_refs=f,
            ))
            section_start = i + 1

    # Final section (governed by body-level sectPr)
    ps = _extract_page_setup_from_sect_pr(body_sectPr)
    h, f = _extract_header_footer_refs(body_sectPr) if body_sectPr is not None else ({}, {})
    sections.append(SectionInfo(
        index=len(sections),
        start_para=section_start,
        end_para=None,
        page_setup=ps,
        sect_pr_element=body_sectPr,
        header_refs=h,
        footer_refs=f,
    ))
    return sections


def _get_or_create_sect_pr_for_section(body: etree._Element, section_index: int) -> etree._Element:
    """Return the w:sectPr element governing the given section, creating it if needed."""
    paragraphs = [el for el in body if el.tag == f"{W}p"]
    section_boundaries: list[int] = []

    for i, para in enumerate(paragraphs):
        pPr = para.find(f"{W}pPr")
        if pPr is not None and pPr.find(f"{W}sectPr") is not None:
            section_boundaries.append(i)

    if section_index >= len(section_boundaries) + 1:
        raise ValueError(f"section_index {section_index} out of range")

    if section_index < len(section_boundaries):
        boundary_para_idx = section_boundaries[section_index]
        para = paragraphs[boundary_para_idx]
        pPr = para.find(f"{W}pPr")
        return pPr.find(f"{W}sectPr")  # type: ignore[return-value]

    # Final (body-level) sectPr
    sectPr = body.find(f"{W}sectPr")
    if sectPr is None:
        sectPr = etree.SubElement(body, f"{W}sectPr")
    return sectPr


def apply_page_setup(
    docx_path: str,
    page_setup: PageSetup,
    *,
    section_index: int = -1,
    output: str | None = None,
) -> dict:
    """Apply page dimensions and/or margins to a section."""
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        raise ValueError("document has no body")

    if section_index == -1:
        section_index = len([el for el in body if (
            el.tag == f"{W}p"
            and el.find(f"{W}pPr") is not None
            and el.find(f"{W}pPr").find(f"{W}sectPr") is not None  # type: ignore[union-attr]
        )])

    sectPr = _get_or_create_sect_pr_for_section(body, section_index)

    # Apply page size
    if page_setup.page_width is not None or page_setup.page_height is not None or page_setup.orientation is not None:
        pgSz = sectPr.find(f"{W}pgSz")
        if pgSz is None:
            pgSz = etree.Element(f"{W}pgSz")
            sectPr.insert(0, pgSz)
        if page_setup.page_width is not None:
            pgSz.set(f"{W}w", str(page_setup.page_width))
        if page_setup.page_height is not None:
            pgSz.set(f"{W}h", str(page_setup.page_height))
        if page_setup.orientation is not None:
            pgSz.set(f"{W}orient", page_setup.orientation)

    # Apply margins
    margins = page_setup.margin_dict()
    if margins:
        pgMar = sectPr.find(f"{W}pgMar")
        if pgMar is None:
            existing_pgSz = sectPr.find(f"{W}pgSz")
            pgMar = etree.Element(f"{W}pgMar")
            if existing_pgSz is not None:
                idx = list(sectPr).index(existing_pgSz)
                sectPr.insert(idx + 1, pgMar)
            else:
                sectPr.insert(0, pgMar)
        for key, val in margins.items():
            pgMar.set(f"{W}{key}", val)

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_section.", suffix=".docx")
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

    return {"ok": True, "section_index": section_index}


def add_section_break(
    docx_path: str,
    after_para: int,
    break_type: str = "nextPage",
    page_setup: PageSetup | None = None,
    *,
    output: str | None = None,
) -> dict:
    """Insert a section break after a paragraph.

    *after_para* is 1-indexed.  The preceding paragraph gets a w:sectPr in its
    w:pPr which governs the *new* section.  This mirrors how Word stores section
    breaks: the break lives in the paragraph properties of the last paragraph
    before the break.

    Valid *break_type* values:
    - ``"nextPage"`` — break to a new page
    - ``"continuous"`` — start new section on the same page
    - ``"evenPage"`` — start on next even-numbered page
    - ``"oddPage"`` — start on next odd-numbered page
    """
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        raise ValueError("document has no body")

    paragraphs = [el for el in body if el.tag == f"{W}p"]
    idx = after_para - 1
    if idx < 0 or idx >= len(paragraphs):
        return {"ok": False, "error": f"paragraph {after_para} out of range (1–{len(paragraphs)})"}

    para = paragraphs[idx]
    pPr = para.find(f"{W}pPr")
    if pPr is None:
        pPr = etree.Element(f"{W}pPr")
        para.insert(0, pPr)

    # Remove any existing sectPr from this paragraph (should be at end of pPr)
    existing = pPr.find(f"{W}sectPr")
    if existing is not None:
        pPr.remove(existing)

    sectPr = etree.SubElement(pPr, f"{W}sectPr")

    # Copy page setup from body-level sectPr for continuity
    body_sectPr = body.find(f"{W}sectPr")
    if body_sectPr is not None:
        for child_name in ("pgSz", "pgMar", "cols", "lnNumType", "paperSrc", "titlePg"):
            src = body_sectPr.find(f"{W}{child_name}")
            if src is not None:
                sectPr.append(copy.deepcopy(src))

    # Apply requested page setup overrides
    if page_setup is not None:
        if page_setup.page_width is not None or page_setup.page_height is not None or page_setup.orientation:
            pgSz = sectPr.find(f"{W}pgSz")
            if pgSz is None:
                pgSz = etree.SubElement(sectPr, f"{W}pgSz")
            if page_setup.page_width is not None:
                pgSz.set(f"{W}w", str(page_setup.page_width))
            if page_setup.page_height is not None:
                pgSz.set(f"{W}h", str(page_setup.page_height))
            if page_setup.orientation:
                pgSz.set(f"{W}orient", page_setup.orientation)
        margins = page_setup.margin_dict()
        if margins:
            pgMar = sectPr.find(f"{W}pgMar")
            if pgMar is None:
                pgMar = etree.SubElement(sectPr, f"{W}pgMar")
            for key, val in margins.items():
                pgMar.set(f"{W}{key}", val)

    # Set break type
    tp = etree.SubElement(sectPr, f"{W}type")
    tp.set(f"{W}val", break_type)

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_section_break.", suffix=".docx")
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

    return {"ok": True, "after_para": after_para, "break_type": break_type}


def remove_section_break(
    docx_path: str,
    section_index: int,
    *,
    output: str | None = None,
) -> dict:
    """Remove a section break, merging the section with the next one.

    *section_index* is 0-indexed and refers to the break at the end of that
    section.  The body-level (final) section cannot be removed — there is
    nothing to merge it with.
    """
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        raise ValueError("document has no body")

    paragraphs = [el for el in body if el.tag == f"{W}p"]
    boundaries: list[int] = []
    for i, para in enumerate(paragraphs):
        pPr = para.find(f"{W}pPr")
        if pPr is not None and pPr.find(f"{W}sectPr") is not None:
            boundaries.append(i)

    if section_index < 0 or section_index >= len(boundaries):
        return {"ok": False, "error": f"section_index {section_index} out of range (0–{len(boundaries) - 1})"}

    target_para = paragraphs[boundaries[section_index]]
    pPr = target_para.find(f"{W}pPr")
    sectPr = pPr.find(f"{W}sectPr")
    pPr.remove(sectPr)

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_section_merge.", suffix=".docx")
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

    return {"ok": True, "removed_section_index": section_index}


# ── Convenience helpers ──────────────────────────────────────────────────────


def set_page_size(
    docx_path: str,
    size: str = "A4",
    orientation: str = "portrait",
    *,
    section_index: int = -1,
    output: str | None = None,
) -> dict:
    """Convenience: set page size by name (A4, Letter, Legal, etc.)."""
    ps = PageSetup.from_preset(size, orientation)
    return apply_page_setup(docx_path, ps, section_index=section_index, output=output)


def set_page_margins(
    docx_path: str,
    top: str = "2.54cm",
    bottom: str = "2.54cm",
    left: str = "3.18cm",
    right: str = "3.18cm",
    *,
    section_index: int = -1,
    output: str | None = None,
) -> dict:
    """Convenience: set page margins with human-readable dimensions."""
    ps = PageSetup.from_margins(top=top, bottom=bottom, left=left, right=right)
    return apply_page_setup(docx_path, ps, section_index=section_index, output=output)
