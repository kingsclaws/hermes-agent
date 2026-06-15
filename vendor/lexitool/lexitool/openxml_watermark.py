"""Watermark add/remove for WordprocessingML documents.

Watermarks in OOXML are VML shapes embedded in header parts (word/header*.xml).
Each section can have its own header.  This module adds/removes a diagonal
text watermark across all section headers.

Borrows the watermark pattern from xceedsoftware/DocX.
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
VML_NS = "urn:schemas-microsoft-com:vml"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

W = f"{{{W_NS}}}"
V = f"{{{VML_NS}}}"


@dataclass
class WatermarkInfo:
    text: str
    font: str
    font_size: str
    color: str
    opacity: str
    layout: str  # "diagonal" or "horizontal"


def _header_members(other: dict[str, bytes]) -> list[str]:
    """Return ZIP member names for all header parts."""
    return sorted(n for n in other if n.startswith("word/header") and n.endswith(".xml"))


def add_watermark(
    docx_path: str,
    text: str = "DRAFT",
    *,
    font: str = "Calibri",
    font_size: str = "72pt",
    color: str = "#C0C0C0",
    opacity: str = "0.5",
    layout: str = "diagonal",
    output: str | None = None,
) -> dict:
    """Add a text watermark to all section headers.

    Creates VML text shapes in each header part.  If a header doesn't exist
    yet, one is created.  Existing watermark shapes are replaced.
    """
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}
        doc_xml = zf.read("word/document.xml") if "word/document.xml" in zf.namelist() else None

    headers = _header_members(other)
    if not headers:
        # Create a default header part
        header_xml = _make_empty_header()
        other["word/header1.xml"] = header_xml
        headers = ["word/header1.xml"]

    updated = 0
    for hdr_name in headers:
        hdr_bytes = other[hdr_name]
        new_bytes = _inject_watermark_vml(hdr_bytes, text, font, font_size, color, opacity, layout)
        if new_bytes != hdr_bytes:
            other[hdr_name] = new_bytes
            updated += 1

    fd, tmp = tempfile.mkstemp(prefix="lex_watermark.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct = other.get("[Content_Types].xml")
        if ct is not None:
            zf.writestr("[Content_Types].xml", ct)
        if doc_xml is not None:
            zf.writestr("word/document.xml", doc_xml)
        for name, data in other.items():
            if name in ("[Content_Types].xml", "word/document.xml"):
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "watermark_text": text, "headers_updated": updated, "path": out_path}


def remove_watermark(docx_path: str, *, output: str | None = None) -> dict:
    """Remove all VML watermark shapes from all header parts."""
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}
        doc_xml = zf.read("word/document.xml") if "word/document.xml" in zf.namelist() else None

    cleaned = 0
    for name in list(other.keys()):
        if not (name.startswith("word/header") and name.endswith(".xml")):
            continue
        hdr_bytes = other[name]
        new_bytes = _strip_watermark_vml(hdr_bytes)
        if new_bytes != hdr_bytes:
            other[name] = new_bytes
            cleaned += 1

    fd, tmp = tempfile.mkstemp(prefix="lex_rm_watermark.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct = other.get("[Content_Types].xml")
        if ct is not None:
            zf.writestr("[Content_Types].xml", ct)
        if doc_xml is not None:
            zf.writestr("word/document.xml", doc_xml)
        for name, data in other.items():
            if name in ("[Content_Types].xml", "word/document.xml"):
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "headers_cleaned": cleaned, "path": out_path}


def list_watermarks(docx_path: str) -> dict:
    """Read watermark text from all headers."""
    found: list[dict[str, Any]] = []
    with zipfile.ZipFile(docx_path, "r") as zf:
        headers = [n for n in zf.namelist() if n.startswith("word/header") and n.endswith(".xml")]
        for hdr_name in headers:
            root = etree.fromstring(zf.read(hdr_name))
            for shape in root.iter(f"{V}shape"):
                textpath = shape.find(f"{V}textpath")
                if textpath is not None and textpath.get("string"):
                    found.append({
                        "header": hdr_name,
                        "text": textpath.get("string"),
                        "font": shape.find(f"{V}textpath") is not None,
                    })

    return {"ok": True, "watermarks": found}


# ── Internal helpers ─────────────────────────────────────────────────────────


def _make_empty_header() -> bytes:
    """Create a minimal empty header XML."""
    root = etree.Element(
        f"{W}hdr",
        nsmap={
            "w": W_NS,
            "r": REL_NS,
            "v": VML_NS,
            "o": "urn:schemas-microsoft-com:office:office",
            "w10": "urn:schemas-microsoft-com:office:word",
        },
    )
    p = etree.SubElement(root, f"{W}p")
    pPr = etree.SubElement(p, f"{W}pPr")
    jc = etree.SubElement(pPr, f"{W}jc")
    jc.set(f"{W}val", "center")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")


def _inject_watermark_vml(
    header_xml: bytes,
    text: str,
    font: str,
    font_size: str,
    color: str,
    opacity: str,
    layout: str,
) -> bytes:
    """Inject or replace watermark VML in a header XML byte string."""
    root = etree.fromstring(header_xml)

    # Remove all existing watermark shapes and pict elements
    for pict in root.findall(f"{W}pict"):
        root.remove(pict)

    # Build the VML watermark
    shape = etree.Element(f"{V}shape")
    shape.set("id", "PowerPlusWaterMarkObject")
    shape.set("style", (
        f"position:absolute;left:0;text-align:center;"
        f"margin-top:0;margin-left:0;width:500pt;height:150pt;"
        f"z-index:-251658240;mso-position-horizontal:center;"
        f"mso-position-horizontal-relative:margin;"
        f"mso-position-vertical:center;"
        f"mso-position-vertical-relative:margin;"
        f"rotation:-30" if layout == "diagonal" else "rotation:0"
    ))
    shape.set("fillcolor", color.replace("#", ""))
    shape.set("fillopacity", opacity)
    shape.set("stroked", "f")
    shape.set("coordsize", "21600,21600")

    fill = etree.SubElement(shape, f"{V}fill")
    fill.set("opacity", opacity)

    textpath = etree.SubElement(shape, f"{V}textpath")
    textpath.set("style", f"font-family:{font};font-size:{font_size}")
    textpath.set("fitpath", "t")
    textpath.set("string", text)

    # Wrap in w:pict inside w:p, and append to hdr
    pict = etree.Element(f"{W}pict")
    pict.append(shape)

    p = etree.Element(f"{W}p")
    pPr = etree.SubElement(p, f"{W}pPr")
    jc = etree.SubElement(pPr, f"{W}jc")
    jc.set(f"{W}val", "center")
    p.append(pict)

    root.append(p)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")


def _strip_watermark_vml(header_xml: bytes) -> bytes:
    """Remove all pict/shape watermark elements from a header."""
    root = etree.fromstring(header_xml)
    changed = False

    for pict in list(root.findall(f"{W}pict")):
        root.remove(pict)
        changed = True

    # Also scan for shapes not wrapped in pict
    for shape in list(root.iter(f"{V}shape")):
        pid = shape.get("id", "")
        if "WaterMark" in pid or "PowerPlus" in pid:
            parent = shape.getparent()
            if parent is not None:
                parent.remove(shape)
            changed = True

    if not changed:
        return header_xml

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
