"""Image add/remove/list for WordprocessingML documents.

Images are stored in word/media/, referenced via relationships in
word/_rels/document.xml.rels, and appear in the document body as
w:drawing > wp:inline > a:graphic > a:graphicData > pic:pic elements.

Follows the drawingML model from dolanmiu/docx ImageRun.
"""
from __future__ import annotations

import imghdr
import os
import shutil
import struct
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

W = f"{{{W_NS}}}"
WP = f"{{{WP_NS}}}"
A = f"{{{A_NS}}}"
PIC = f"{{{PIC_NS}}}"

IMAGE_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"

# MIME types by extension
MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".svg": "image/svg+xml",
}


@dataclass
class ImageInfo:
    para_index: int
    width_emu: int
    height_emu: int
    relationship_id: str
    image_name: str
    content_type: str


def _find_para_by_index(body: etree._Element, para_idx: int) -> etree._Element | None:
    count = 0
    for el in body.iter():
        if el.tag == f"{W}p":
            if count == para_idx:
                return el
            count += 1
    return None


def _image_dimensions(filepath: str) -> tuple[int, int]:
    """Return (width_px, height_px) by reading image headers directly."""
    try:
        with open(filepath, "rb") as f:
            header = f.read(32)

        img_type = imghdr.what(None, h=header)
        if img_type == "png" and len(header) >= 24:
            w, h = struct.unpack(">II", header[16:24])
            return w, h
        elif img_type == "gif" and len(header) >= 10:
            w, h = struct.unpack("<HH", header[6:10])
            return w, h
        elif img_type == "jpeg":
            # JPEG dimensions require scanning for SOF marker
            f.seek(0)
            data = f.read()
            i = 2
            while i < len(data):
                if data[i] != 0xFF:
                    break
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5: i + 9])
                    return w, h
                length = struct.unpack(">H", data[i + 2: i + 4])[0]
                i += 2 + length
            return 0, 0
        elif img_type == "bmp" and len(header) >= 26:
            # BMP stores width/height as signed 32-bit LE at offset 18
            w = struct.unpack_from("<i", header, 18)[0]
            h = abs(struct.unpack_from("<i", header, 22)[0])
            return w, h
    except Exception:
        pass
    return 0, 0


def _px_to_emu(px: int, dpi: int = 96) -> int:
    """Convert pixels to EMU at given DPI."""
    return int(px * 914400 / dpi)


def _ensure_image_part(
    other: dict[str, bytes],
    image_path: str,
    image_name: str,
    rels_root: etree._Element,
    content_types_root: etree._Element | None,
) -> str:
    """Copy image into ZIP members, add relationship and content type.

    Returns the relationship ID (rId).
    """
    ext = Path(image_path).suffix.lower()
    mime = MIME_MAP.get(ext, "application/octet-stream")

    with open(image_path, "rb") as f:
        img_bytes = f.read()

    media_path = f"word/media/{image_name}"
    other[media_path] = img_bytes

    # Ensure content type
    if content_types_root is not None:
        ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
        # Check for existing Default for this extension
        found = False
        for d in content_types_root.findall(f"{{{ct_ns}}}Default"):
            if d.get("Extension") == ext.lstrip("."):
                found = True
                break
        if not found:
            default = etree.SubElement(content_types_root, f"{{{ct_ns}}}Default")
            default.set("Extension", ext.lstrip("."))
            default.set("ContentType", mime)

    # Add relationship
    from .openxml_opc import ensure_relationship
    rid = ensure_relationship(rels_root, IMAGE_REL_TYPE,
                               f"media/{image_name}", target_mode="Internal")
    return rid


def _build_drawing(rid: str, width_emu: int, height_emu: int,
                   image_name: str) -> etree._Element:
    """Build a w:drawing > wp:inline > a:graphic > pic:pic element."""
    unique_id = abs(hash(image_name)) % (10 ** 10)

    drawing = etree.Element(f"{W}drawing")

    inline = etree.SubElement(drawing, f"{WP}inline")
    inline.set("distT", "0")
    inline.set("distB", "0")
    inline.set("distL", "0")
    inline.set("distR", "0")

    extent = etree.SubElement(inline, f"{WP}extent")
    extent.set("cx", str(width_emu))
    extent.set("cy", str(height_emu))

    effectExtent = etree.SubElement(inline, f"{WP}effectExtent")
    effectExtent.set("l", "0")
    effectExtent.set("t", "0")
    effectExtent.set("r", "0")
    effectExtent.set("b", "0")

    docPr = etree.SubElement(inline, f"{WP}docPr")
    docPr.set("id", str(unique_id))
    docPr.set("name", image_name)

    cNvGraphicFramePr = etree.SubElement(inline, f"{WP}cNvGraphicFramePr")

    graphic = etree.SubElement(inline, f"{A}graphic")

    graphicData = etree.SubElement(graphic, f"{A}graphicData")
    graphicData.set("uri", "http://schemas.openxmlformats.org/drawingml/2006/picture")

    pic = etree.SubElement(graphicData, f"{PIC}pic")

    nvPicPr = etree.SubElement(pic, f"{PIC}nvPicPr")
    cNvPr = etree.SubElement(nvPicPr, f"{PIC}cNvPr")
    cNvPr.set("id", str(unique_id + 1))
    cNvPr.set("name", image_name)
    cNvPicPr = etree.SubElement(nvPicPr, f"{PIC}cNvPicPr")

    blipFill = etree.SubElement(pic, f"{PIC}blipFill")
    blip = etree.SubElement(blipFill, f"{A}blip")
    blip.set(f"{{{REL_NS}}}embed", rid)
    stretch = etree.SubElement(blipFill, f"{A}stretch")
    etree.SubElement(stretch, f"{A}fillRect")

    spPr = etree.SubElement(pic, f"{PIC}spPr")
    xfrm = etree.SubElement(spPr, f"{A}xfrm")
    off = etree.SubElement(xfrm, f"{A}off")
    off.set("x", "0")
    off.set("y", "0")
    ext = etree.SubElement(xfrm, f"{A}ext")
    ext.set("cx", str(width_emu))
    ext.set("cy", str(height_emu))
    prstGeom = etree.SubElement(spPr, f"{A}prstGeom")
    prstGeom.set("prst", "rect")
    etree.SubElement(prstGeom, f"{A}avLst")

    return drawing


def add_image(
    docx_path: str,
    image_path: str,
    *,
    para: int = 1,
    offset: int = -1,
    width: int | None = None,
    height: int | None = None,
    dpi: int = 96,
    output: str | None = None,
) -> dict:
    """Insert an inline image into the document.

    Args:
        docx_path: Path to .docx file.
        image_path: Path to image file (PNG, JPEG, GIF, BMP).
        para: 1-indexed paragraph number.
        offset: Character offset within paragraph (-1 = end).
        width: Desired display width in pixels (None = use image's native width).
        height: Desired display height in pixels (None = use native or aspect ratio).
        dpi: DPI for px-to-EMU conversion (default 96).
        output: Output path.
    """
    out_path = output or docx_path

    if not os.path.exists(image_path):
        return {"ok": False, "error": f"image file not found: {image_path}"}

    ext = Path(image_path).suffix.lower()
    if ext not in MIME_MAP:
        return {"ok": False, "error": f"unsupported image format: {ext}"}

    img_w, img_h = _image_dimensions(image_path)
    if img_w == 0 or img_h == 0:
        img_w, img_h = 300, 200  # sensible default

    # Calculate display dimensions
    display_w = width if width is not None else img_w
    display_h = height if height is not None else (
        int(display_w * img_h / img_w) if img_w else display_w
    )

    w_emu = _px_to_emu(display_w, dpi)
    h_emu = _px_to_emu(display_h, dpi)

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

    # Determine next image number
    img_num = 1
    for name in other:
        if name.startswith("word/media/image") and name.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp")):
            try:
                n = int(name.split("image")[1].split(".")[0])
                if n >= img_num:
                    img_num = n + 1
            except ValueError:
                pass

    image_name = f"image{img_num}{ext}"

    # Ensure relationships part exists
    from .openxml_opc import rels_member_for_part

    rels_path = rels_member_for_part("word/document.xml")
    rels_bytes = other.get(rels_path)
    if rels_bytes is not None:
        rels_root = etree.fromstring(rels_bytes)
    else:
        rels_root = etree.Element(
            f"{{{PKG_REL_NS}}}Relationships",
            nsmap={None: PKG_REL_NS},
        )

    # Content types
    ct_key = "[Content_Types].xml"
    ct_bytes = other.get(ct_key)
    ct_root = etree.fromstring(ct_bytes) if ct_bytes else None

    rid = _ensure_image_part(other, image_path, image_name, rels_root, ct_root)

    other[rels_path] = etree.tostring(rels_root, xml_declaration=True,
                                       encoding="UTF-8", standalone="yes")
    if ct_root is not None:
        other[ct_key] = etree.tostring(ct_root, xml_declaration=True,
                                        encoding="UTF-8", standalone="yes")

    # Build drawing element
    drawing = _build_drawing(rid, w_emu, h_emu, image_name)

    # Wrap in a run element
    run_el = etree.Element(f"{W}r")
    run_el.append(drawing)

    if offset < 0:
        para_el.append(run_el)
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
                    para_el.insert(idx + 1, run_el)
                    inserted = True
                    break
                char_count += tlen
            if inserted:
                break
        if not inserted:
            para_el.append(run_el)

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_image.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct = other.get(ct_key)
        if ct is not None:
            zf.writestr(ct_key, ct)
        zf.writestr("word/document.xml", doc_xml_out)
        for name, data in other.items():
            if name == ct_key:
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "image_name": image_name, "rid": rid,
            "width_emu": w_emu, "height_emu": h_emu,
            "width_px": display_w, "height_px": display_h,
            "para": para, "path": out_path}


def remove_image(
    docx_path: str,
    relationship_id: str | None = None,
    image_name: str | None = None,
    *,
    output: str | None = None,
) -> dict:
    """Remove an image by relationship ID or image name.

    Removes the drawing element from the document, the relationship,
    and the image file from word/media/.
    """
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    # If we have image_name but not rid, find rid from the drawing reference
    rid = relationship_id
    if rid is None and image_name is not None:
        # Search for the drawing that references this image
        for blip in root.iter(f"{A}blip"):
            embed = blip.get(f"{{{REL_NS}}}embed")
            if embed:
                rid = embed
                break

    if rid is None:
        return {"ok": False, "error": "relationship_id or image_name required"}

    # Remove drawing elements
    drawings_removed = 0
    for drawing in list(root.iter(f"{W}drawing")):
        for blip in drawing.iter(f"{A}blip"):
            if blip.get(f"{{{REL_NS}}}embed") == rid:
                # Remove the parent run
                run_el = drawing.getparent()
                if run_el is not None and run_el.tag == f"{W}r":
                    para = run_el.getparent()
                    if para is not None:
                        para.remove(run_el)
                drawings_removed += 1
                break

    # Remove relationship
    from .openxml_opc import rels_member_for_part
    rels_path = rels_member_for_part("word/document.xml")
    rels_bytes = other.get(rels_path)
    if rels_bytes is not None:
        rels_root = etree.fromstring(rels_bytes)
        for rel in rels_root.findall(f"{{{PKG_REL_NS}}}Relationship"):
            if rel.get("Id") == rid:
                target = rel.get("Target", "")
                rels_root.remove(rel)
                # If target is in word/media/, remove it
                media_path = f"word/{target}" if not target.startswith("word/") else target
                if media_path in other:
                    del other[media_path]
                    # If image_name not set, extract it
                    if image_name is None:
                        image_name = Path(target).name
                break
        other[rels_path] = etree.tostring(rels_root, xml_declaration=True,
                                           encoding="UTF-8", standalone="yes")

    if drawings_removed == 0:
        return {"ok": False, "error": f"no drawing found with rid={rid}"}

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_rm_image.", suffix=".docx")
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

    return {"ok": True, "rid": rid, "image_name": image_name,
            "drawings_removed": drawings_removed, "path": out_path}


def list_images(docx_path: str) -> dict:
    """List all images in the document with their metadata."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")

    # Build rid -> target map from rels
    from .openxml_opc import rels_member_for_part
    rels_path = rels_member_for_part("word/document.xml")
    rid_map: dict[str, str] = {}
    rels_bytes = other.get(rels_path)
    if rels_bytes is not None:
        rels_root = etree.fromstring(rels_bytes)
        for rel in rels_root.findall(f"{{{PKG_REL_NS}}}Relationship"):
            if rel.get("Type", "").endswith("/image"):
                rid_map[rel.get("Id", "")] = rel.get("Target", "")

    images: list[dict[str, Any]] = []
    para_idx = 0
    for el in body.iter():
        if el.tag == f"{W}p":
            para_idx += 1
        elif el.tag == f"{W}drawing":
            for blip in el.iter(f"{A}blip"):
                rid = blip.get(f"{{{REL_NS}}}embed", "")
                if rid:
                    target = rid_map.get(rid, "")
                    # Read extents
                    extent = el.find(f".//{WP}extent")
                    cx = int(extent.get("cx", "0")) if extent is not None else 0
                    cy = int(extent.get("cy", "0")) if extent is not None else 0
                    images.append({
                        "para": para_idx,
                        "rid": rid,
                        "target": target,
                        "width_emu": cx,
                        "height_emu": cy,
                    })

    return {"ok": True, "images": images}
