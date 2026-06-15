"""Style creation and management for WordprocessingML documents.

Borrows the style model from dolanmiu/docx.  All operations work on the
word/styles.xml part directly via zipfile + lxml.

Style inheritance (w:basedOn), linked character styles (w:link), and
multi-language font settings (ascii, hAnsi, eastAsia, cs) are supported.
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

STYLE_TYPES = {"paragraph": "paragraph", "character": "character",
               "table": "table", "numbering": "numbering"}


@dataclass
class StyleRunProps:
    """Run-level formatting stored in w:rPr."""

    bold: bool | None = None
    italic: bool | None = None
    underline: str | None = None  # "single", "double", "none", etc.
    strike: bool | None = None
    small_caps: bool | None = None
    all_caps: bool | None = None
    font_size: float | None = None  # in half-points (e.g. 22 = 11pt)
    font_ascii: str | None = None
    font_hAnsi: str | None = None
    font_eastAsia: str | None = None
    font_cs: str | None = None
    color: str | None = None  # hex RRGGBB
    highlight: str | None = None  # "yellow", "cyan", etc.
    spacing: float | None = None  # line spacing in 240ths of a line

    def to_xml(self, parent: etree._Element) -> None:
        rPr = etree.SubElement(parent, f"{W}rPr")
        if self.bold is True:
            etree.SubElement(rPr, f"{W}b")
        if self.bold is False:
            etree.SubElement(rPr, f"{W}b").set(f"{W}val", "false")
        if self.italic is True:
            etree.SubElement(rPr, f"{W}i")
        if self.italic is False:
            etree.SubElement(rPr, f"{W}i").set(f"{W}val", "false")
        if self.underline:
            u = etree.SubElement(rPr, f"{W}u")
            u.set(f"{W}val", self.underline)
        if self.strike is True:
            etree.SubElement(rPr, f"{W}strike")
        if self.small_caps is True:
            etree.SubElement(rPr, f"{W}smallCaps")
        if self.all_caps is True:
            etree.SubElement(rPr, f"{W}caps")
        if self.font_size is not None:
            sz = str(int(self.font_size))
            etree.SubElement(rPr, f"{W}sz").set(f"{W}val", sz)
            etree.SubElement(rPr, f"{W}szCs").set(f"{W}val", sz)
        if any([self.font_ascii, self.font_hAnsi, self.font_eastAsia, self.font_cs]):
            rf = etree.SubElement(rPr, f"{W}rFonts")
            if self.font_ascii:
                rf.set(f"{W}ascii", self.font_ascii)
            if self.font_hAnsi:
                rf.set(f"{W}hAnsi", self.font_hAnsi)
            if self.font_eastAsia:
                rf.set(f"{W}eastAsia", self.font_eastAsia)
            if self.font_cs:
                rf.set(f"{W}cs", self.font_cs)
        if self.color:
            c = etree.SubElement(rPr, f"{W}color")
            c.set(f"{W}val", self.color)
        if self.highlight:
            hl = etree.SubElement(rPr, f"{W}highlight")
            hl.set(f"{W}val", self.highlight)
        if self.spacing is not None:
            sp = etree.SubElement(rPr, f"{W}spacing")
            sp.set(f"{W}line", str(int(self.spacing)))
            sp.set(f"{W}lineRule", "auto")


@dataclass
class StyleParaProps:
    """Paragraph-level formatting stored in w:pPr."""

    alignment: str | None = None  # left, center, right, both (justify)
    spacing_before: int | None = None  # twips
    spacing_after: int | None = None  # twips
    line_spacing: float | None = None  # 240ths of a line
    indent_left: int | None = None  # twips
    indent_right: int | None = None  # twips
    indent_first_line: int | None = None  # twips
    indent_hanging: int | None = None  # twips
    outline_level: int | None = None  # 0-8, 0=Heading1 level
    keep_lines: bool | None = None
    keep_next: bool | None = None
    page_break_before: bool | None = None
    widow_control: bool | None = None

    def to_xml(self, parent: etree._Element) -> None:
        pPr = etree.SubElement(parent, f"{W}pPr")
        if self.alignment:
            jc = etree.SubElement(pPr, f"{W}jc")
            jc.set(f"{W}val", self.alignment)
        if any(x is not None for x in [self.spacing_before, self.spacing_after, self.line_spacing]):
            sp = etree.SubElement(pPr, f"{W}spacing")
            if self.spacing_before is not None:
                sp.set(f"{W}before", str(self.spacing_before))
            if self.spacing_after is not None:
                sp.set(f"{W}after", str(self.spacing_after))
            if self.line_spacing is not None:
                sp.set(f"{W}line", str(int(self.line_spacing)))
                sp.set(f"{W}lineRule", "auto")
        if any(x is not None for x in [self.indent_left, self.indent_right,
                                         self.indent_first_line, self.indent_hanging]):
            ind = etree.SubElement(pPr, f"{W}ind")
            if self.indent_left is not None:
                ind.set(f"{W}left", str(self.indent_left))
            if self.indent_right is not None:
                ind.set(f"{W}right", str(self.indent_right))
            if self.indent_first_line is not None:
                ind.set(f"{W}firstLine", str(self.indent_first_line))
            if self.indent_hanging is not None:
                ind.set(f"{W}hanging", str(self.indent_hanging))
        if self.outline_level is not None:
            ol = etree.SubElement(pPr, f"{W}outlineLvl")
            ol.set(f"{W}val", str(self.outline_level))
        if self.keep_lines is True:
            etree.SubElement(pPr, f"{W}keepLines")
        if self.keep_next is True:
            etree.SubElement(pPr, f"{W}keepNext")
        if self.page_break_before is True:
            etree.SubElement(pPr, f"{W}pageBreakBefore")
        if self.widow_control is False:
            wc = etree.SubElement(pPr, f"{W}widowControl")
            wc.set(f"{W}val", "false")


@dataclass
class StyleDefinition:
    """Complete descriptor for one Word style."""

    style_id: str
    name: str
    type: str = "paragraph"  # paragraph, character, table, numbering
    based_on: str | None = None
    next_style: str | None = None
    linked_style: str | None = None
    is_default: bool = False
    is_custom: bool = True
    ui_priority: int | None = None
    run_props: StyleRunProps = field(default_factory=StyleRunProps)
    para_props: StyleParaProps = field(default_factory=StyleParaProps)
    hidden: bool = False

    @classmethod
    def from_element(cls, el: etree._Element) -> StyleDefinition:
        sid = el.get(f"{W}styleId", "")
        tp = el.get(f"{W}type", "paragraph")
        is_default = el.get(f"{W}default") == "1"
        is_custom = el.get(f"{W}customStyle") == "1"

        name_el = el.find(f"{W}name")
        name = name_el.get(f"{W}val", sid) if name_el is not None else sid

        based_on_el = el.find(f"{W}basedOn")
        based_on = based_on_el.get(f"{W}val") if based_on_el is not None else None

        next_el = el.find(f"{W}next")
        next_style = next_el.get(f"{W}val") if next_el is not None else None

        link_el = el.find(f"{W}link")
        linked = link_el.get(f"{W}val") if link_el is not None else None

        priority_el = el.find(f"{W}uiPriority")
        priority = int(priority_el.get(f"{W}val")) if priority_el is not None else None

        hidden = el.find(f"{W}semiHidden") is not None

        rp = StyleRunProps()
        rPr = el.find(f"{W}rPr")
        if rPr is not None:
            rp.bold = True if rPr.find(f"{W}b") is not None else None
            rp.italic = True if rPr.find(f"{W}i") is not None else None
            u = rPr.find(f"{W}u")
            if u is not None:
                rp.underline = u.get(f"{W}val", "single")
            rp.strike = True if rPr.find(f"{W}strike") is not None else None
            rp.small_caps = True if rPr.find(f"{W}smallCaps") is not None else None
            rp.all_caps = True if rPr.find(f"{W}caps") is not None else None
            sz = rPr.find(f"{W}sz")
            if sz is not None and sz.get(f"{W}val"):
                rp.font_size = float(sz.get(f"{W}val", "0"))
            rf = rPr.find(f"{W}rFonts")
            if rf is not None:
                rp.font_ascii = rf.get(f"{W}ascii") or None
                rp.font_hAnsi = rf.get(f"{W}hAnsi") or None
                rp.font_eastAsia = rf.get(f"{W}eastAsia") or None
                rp.font_cs = rf.get(f"{W}cs") or None
            c = rPr.find(f"{W}color")
            if c is not None and c.get(f"{W}val"):
                rp.color = c.get(f"{W}val")
            hl = rPr.find(f"{W}highlight")
            if hl is not None and hl.get(f"{W}val"):
                rp.highlight = hl.get(f"{W}val")

        pp = StyleParaProps()
        pPr = el.find(f"{W}pPr")
        if pPr is not None:
            jc = pPr.find(f"{W}jc")
            if jc is not None:
                pp.alignment = jc.get(f"{W}val")
            sp = pPr.find(f"{W}spacing")
            if sp is not None:
                before = sp.get(f"{W}before")
                after_val = sp.get(f"{W}after")
                line = sp.get(f"{W}line")
                if before:
                    pp.spacing_before = int(before)
                if after_val:
                    pp.spacing_after = int(after_val)
                if line:
                    pp.line_spacing = float(line)
            ind = pPr.find(f"{W}ind")
            if ind is not None:
                left = ind.get(f"{W}left")
                right = ind.get(f"{W}right")
                first = ind.get(f"{W}firstLine")
                hang = ind.get(f"{W}hanging")
                if left:
                    pp.indent_left = int(left)
                if right:
                    pp.indent_right = int(right)
                if first:
                    pp.indent_first_line = int(first)
                if hang:
                    pp.indent_hanging = int(hang)
            ol = pPr.find(f"{W}outlineLvl")
            if ol is not None and ol.get(f"{W}val"):
                pp.outline_level = int(ol.get(f"{W}val", "0"))

        return cls(
            style_id=sid, name=name, type=tp, based_on=based_on,
            next_style=next_style, linked_style=linked,
            is_default=is_default, is_custom=is_custom,
            ui_priority=priority, run_props=rp, para_props=pp, hidden=hidden,
        )

    def to_xml(self) -> etree._Element:
        el = etree.Element(f"{W}style")
        el.set(f"{W}type", self.type)
        el.set(f"{W}styleId", self.style_id)
        if self.is_default:
            el.set(f"{W}default", "1")
        if self.is_custom:
            el.set(f"{W}customStyle", "1")

        name_el = etree.SubElement(el, f"{W}name")
        name_el.set(f"{W}val", self.name)

        if self.based_on:
            bo = etree.SubElement(el, f"{W}basedOn")
            bo.set(f"{W}val", self.based_on)
        if self.next_style:
            nx = etree.SubElement(el, f"{W}next")
            nx.set(f"{W}val", self.next_style)
        if self.linked_style:
            lk = etree.SubElement(el, f"{W}link")
            lk.set(f"{W}val", self.linked_style)
        if self.ui_priority is not None:
            up = etree.SubElement(el, f"{W}uiPriority")
            up.set(f"{W}val", str(self.ui_priority))
        if self.hidden:
            etree.SubElement(el, f"{W}semiHidden")

        self.para_props.to_xml(el)
        self.run_props.to_xml(el)
        return el


def read_styles(docx_path: str) -> dict[str, StyleDefinition]:
    """Read all styles from a document."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        if "word/styles.xml" not in zf.namelist():
            return {}
        styles_xml = zf.read("word/styles.xml")

    root = etree.fromstring(styles_xml)
    styles: dict[str, StyleDefinition] = {}
    for el in root.findall(f"{W}style"):
        sd = StyleDefinition.from_element(el)
        if sd.style_id:
            styles[sd.style_id] = sd
    return styles


def _read_styles_root(docx_path: str) -> tuple[etree._Element, dict[str, bytes]]:
    """Read styles.xml root + other ZIP entries."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        styles_raw = zf.read("word/styles.xml") if "word/styles.xml" in zf.namelist() else None
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/styles.xml"}

    if styles_raw is None:
        root = etree.Element(f"{W}styles", nsmap={"w": W_NS})
        # Add default docDefaults
        docDefaults = etree.SubElement(root, f"{W}docDefaults")
        rPrDefault = etree.SubElement(docDefaults, f"{W}rPrDefault")
        rPr = etree.SubElement(rPrDefault, f"{W}rPr")
        etree.SubElement(rPr, f"{W}sz").set(f"{W}val", "22")
        etree.SubElement(rPr, f"{W}szCs").set(f"{W}val", "22")
    else:
        root = etree.fromstring(styles_raw)

    return root, other


def _write_styles(docx_path: str, root: etree._Element, other: dict[str, bytes],
                  output: str | None = None) -> str:
    out_path = output or docx_path
    styles_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_style.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct = other.get("[Content_Types].xml")
        if ct is not None:
            zf.writestr("[Content_Types].xml", ct)
        if "word/styles.xml" in other:
            other.pop("word/styles.xml")
        zf.writestr("word/styles.xml", styles_xml)
        for name, data in other.items():
            if name == "[Content_Types].xml":
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)
    return out_path


def create_style(
    docx_path: str,
    style_id: str,
    name: str,
    *,
    type: str = "paragraph",
    based_on: str | None = None,
    next_style: str | None = None,
    linked_style: str | None = None,
    run_props: StyleRunProps | None = None,
    para_props: StyleParaProps | None = None,
    ui_priority: int | None = None,
    output: str | None = None,
) -> dict:
    """Create a new style in the document."""
    root, other = _read_styles_root(docx_path)

    # Check for duplicate
    for el in root.findall(f"{W}style"):
        if el.get(f"{W}styleId") == style_id:
            return {"ok": False, "error": f"style '{style_id}' already exists"}

    sd = StyleDefinition(
        style_id=style_id, name=name, type=type,
        based_on=based_on, next_style=next_style, linked_style=linked_style,
        is_custom=True, ui_priority=ui_priority,
        run_props=run_props or StyleRunProps(),
        para_props=para_props or StyleParaProps(),
    )
    root.append(sd.to_xml())

    out_path = _write_styles(docx_path, root, other, output)
    return {"ok": True, "style_id": style_id, "path": out_path}


def modify_style(
    docx_path: str,
    style_id: str,
    *,
    name: str | None = None,
    based_on: str | None = None,
    next_style: str | None = None,
    linked_style: str | None = None,
    run_props: StyleRunProps | None = None,
    para_props: StyleParaProps | None = None,
    ui_priority: int | None = None,
    output: str | None = None,
) -> dict:
    """Modify an existing style. Only non-None fields are updated."""
    root, other = _read_styles_root(docx_path)

    found: etree._Element | None = None
    for el in root.findall(f"{W}style"):
        if el.get(f"{W}styleId") == style_id:
            found = el
            break

    if found is None:
        return {"ok": False, "error": f"style '{style_id}' not found"}

    if name is not None:
        name_el = found.find(f"{W}name")
        if name_el is not None:
            name_el.set(f"{W}val", name)
        else:
            nel = etree.SubElement(found, f"{W}name")
            nel.set(f"{W}val", name)

    if based_on is not None:
        bo = found.find(f"{W}basedOn")
        if bo is not None:
            bo.set(f"{W}val", based_on)
        else:
            nel = etree.SubElement(found, f"{W}basedOn")
            nel.set(f"{W}val", based_on)

    if next_style is not None:
        nx = found.find(f"{W}next")
        if nx is not None:
            nx.set(f"{W}val", next_style)
        else:
            nel = etree.SubElement(found, f"{W}next")
            nel.set(f"{W}val", next_style)

    if linked_style is not None:
        lk = found.find(f"{W}link")
        if lk is not None:
            lk.set(f"{W}val", linked_style)
        else:
            nel = etree.SubElement(found, f"{W}link")
            nel.set(f"{W}val", linked_style)

    if ui_priority is not None:
        up = found.find(f"{W}uiPriority")
        if up is not None:
            up.set(f"{W}val", str(ui_priority))
        else:
            nel = etree.SubElement(found, f"{W}uiPriority")
            nel.set(f"{W}val", str(ui_priority))

    if run_props is not None:
        # Remove existing rPr to rebuild clean
        existing_rPr = found.find(f"{W}rPr")
        if existing_rPr is not None:
            found.remove(existing_rPr)
        run_props.to_xml(found)

    if para_props is not None:
        existing_pPr = found.find(f"{W}pPr")
        if existing_pPr is not None:
            found.remove(existing_pPr)
        para_props.to_xml(found)

    out_path = _write_styles(docx_path, root, other, output)
    return {"ok": True, "style_id": style_id, "path": out_path}


def delete_style(
    docx_path: str,
    style_id: str,
    *,
    output: str | None = None,
) -> dict:
    """Delete a style from the document. Cannot delete the default style."""
    root, other = _read_styles_root(docx_path)

    found: etree._Element | None = None
    for el in root.findall(f"{W}style"):
        if el.get(f"{W}styleId") == style_id:
            if el.get(f"{W}default") == "1":
                return {"ok": False, "error": f"cannot delete default style '{style_id}'"}
            found = el
            break

    if found is None:
        return {"ok": False, "error": f"style '{style_id}' not found"}

    root.remove(found)
    out_path = _write_styles(docx_path, root, other, output)
    return {"ok": True, "style_id": style_id, "path": out_path}


def apply_style(
    docx_path: str,
    paragraph_indices: list[int],
    style_id: str,
    *,
    output: str | None = None,
) -> dict:
    """Apply a paragraph or character style to paragraphs."""
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    # Verify style exists
    styles_raw = other.get("word/styles.xml")
    if styles_raw is None:
        return {"ok": False, "error": "document has no styles.xml"}
    styles_root = etree.fromstring(styles_raw)
    target_style_el: etree._Element | None = None
    for el in styles_root.findall(f"{W}style"):
        if el.get(f"{W}styleId") == style_id:
            target_style_el = el
            break
    if target_style_el is None:
        return {"ok": False, "error": f"style '{style_id}' not found in document"}

    style_type = target_style_el.get(f"{W}type", "paragraph")

    paragraphs = [el for el in body if el.tag == f"{W}p"]
    applied = 0
    errors: list[str] = []

    for pi in paragraph_indices:
        if pi < 0 or pi >= len(paragraphs):
            errors.append(f"paragraph index {pi} out of range")
            continue
        para = paragraphs[pi]
        pPr = para.find(f"{W}pPr")
        if pPr is None:
            pPr = etree.Element(f"{W}pPr")
            para.insert(0, pPr)

        if style_type == "character":
            # Character styles are applied to the rPr of individual runs
            # For simplicity, apply to pPr/rPr as default run props
            pStyle = pPr.find(f"{W}pStyle")
            if pStyle is None:
                pStyle = etree.SubElement(pPr, f"{W}pStyle")
            pStyle.set(f"{W}val", style_id)
        else:
            pStyle = pPr.find(f"{W}pStyle")
            if pStyle is None:
                pStyle = etree.SubElement(pPr, f"{W}pStyle")
            pStyle.set(f"{W}val", style_id)

        applied += 1

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_apply_style.", suffix=".docx")
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

    return {"ok": True, "style_id": style_id, "applied": applied, "errors": errors or None, "path": out_path}
