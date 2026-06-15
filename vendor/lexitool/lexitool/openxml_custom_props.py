"""Custom document properties for WordprocessingML documents.

Custom properties are stored in docProps/custom.xml using the OPC custom
properties schema with variant types (vt:lpwstr, vt:i4, vt:r8, vt:bool, vt:date).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from typing import Any

from lxml import etree

CUSTOM_PROPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

VT = f"{{{VT_NS}}}"
PROPS = f"{{{CUSTOM_PROPS_NS}}}"

FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"

# Type mapping: Python type → VT element name
TYPE_MAP = {
    str: f"{VT}lpwstr",
    int: f"{VT}i4",
    float: f"{VT}r8",
    bool: f"{VT}bool",
}
VT_REVERSE = {
    "lpwstr": str,
    "i4": int,
    "r8": float,
    "bool": bool,
    "date": str,  # stored as ISO string
}


def _ensure_custom_xml(other: dict[str, bytes]) -> etree._Element:
    """Get or create the custom.xml properties root."""
    ct_key = "[Content_Types].xml"
    part_key = "docProps/custom.xml"

    if part_key in other:
        return etree.fromstring(other[part_key])

    root = etree.Element(
        f"{PROPS}Properties",
        nsmap={
            None: CUSTOM_PROPS_NS,
            "vt": VT_NS,
        },
    )
    other[part_key] = etree.tostring(root, xml_declaration=True,
                                      encoding="UTF-8", standalone="yes")

    # Ensure content type override
    if ct_key in other:
        ct_root = etree.fromstring(other[ct_key])
        ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
        part_ct = "application/vnd.openxmlformats-officedocument.custom-properties+xml"

        found = False
        for ov in ct_root.findall(f"{{{ct_ns}}}Override"):
            if ov.get("PartName") == f"/{part_key}":
                found = True
                break
        if not found:
            override = etree.SubElement(ct_root, f"{{{ct_ns}}}Override")
            override.set("PartName", f"/{part_key}")
            override.set("ContentType", part_ct)
            other[ct_key] = etree.tostring(ct_root, xml_declaration=True,
                                            encoding="UTF-8", standalone="yes")

    return root


def _next_pid(props_root: etree._Element) -> int:
    """Get the next available property ID."""
    max_pid = 1
    for prop in props_root.findall(f"{PROPS}property"):
        pid = prop.get("pid", "0")
        try:
            p = int(pid)
            if p >= max_pid:
                max_pid = p + 1
        except ValueError:
            pass
    return max_pid


def set_custom_property(
    docx_path: str,
    name: str,
    value: Any,
    *,
    output: str | None = None,
) -> dict:
    """Set a custom document property. Creates the part if it doesn't exist.

    Args:
        docx_path: Path to .docx file.
        name: Property name.
        value: Property value (str, int, float, or bool).
        output: Output path.
    """
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}
        doc_xml = zf.read("word/document.xml") if "word/document.xml" in zf.namelist() else None

    props_root = _ensure_custom_xml(other)

    # Determine VT element
    value_type = type(value)
    vt_tag = TYPE_MAP.get(value_type, f"{VT}lpwstr")
    vt_local = vt_tag.split("}")[-1]

    # Store as string for all types
    vt_text = str(value)
    if value_type is bool:
        vt_text = "true" if value else "false"

    # Check if property already exists (update) or create new
    existing = None
    for prop in props_root.findall(f"{PROPS}property"):
        if prop.get("name") == name:
            existing = prop
            break

    if existing is not None:
        # Update existing
        vt_el = existing[0] if len(existing) > 0 else None
        if vt_el is not None and vt_el.tag == vt_tag:
            vt_el.text = vt_text
        else:
            # Replace the VT element
            for child in list(existing):
                existing.remove(child)
            vt_el = etree.SubElement(existing, vt_tag)
            vt_el.text = vt_text
    else:
        # Create new
        prop = etree.SubElement(props_root, f"{PROPS}property")
        prop.set("fmtid", FMTID)
        prop.set("pid", str(_next_pid(props_root)))
        prop.set("name", name)
        vt_el = etree.SubElement(prop, vt_tag)
        vt_el.text = vt_text

    other["docProps/custom.xml"] = etree.tostring(props_root, xml_declaration=True,
                                                    encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_prop.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct_key = "[Content_Types].xml"
        ct = other.get(ct_key)
        if ct is not None:
            zf.writestr(ct_key, ct)
        if doc_xml is not None:
            zf.writestr("word/document.xml", doc_xml)
        for n, data in other.items():
            if n in (ct_key, "word/document.xml"):
                continue
            zf.writestr(n, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "name": name, "value": value,
            "type": vt_local, "path": out_path}


def get_custom_properties(docx_path: str) -> dict:
    """Read all custom document properties."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        if "docProps/custom.xml" not in zf.namelist():
            return {"ok": True, "properties": {}}
        props_xml = zf.read("docProps/custom.xml")

    props_root = etree.fromstring(props_xml)
    result: dict[str, Any] = {}

    for prop in props_root.findall(f"{PROPS}property"):
        name = prop.get("name", "")
        if not name:
            continue
        # Read the first VT child
        for child in prop:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            text = child.text or ""
            if tag == "i4":
                result[name] = int(text)
            elif tag == "r8":
                result[name] = float(text)
            elif tag == "bool":
                result[name] = text.lower() == "true"
            else:
                result[name] = text
            break

    return {"ok": True, "properties": result}


def remove_custom_property(
    docx_path: str,
    name: str,
    *,
    output: str | None = None,
) -> dict:
    """Remove a custom document property by name."""
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}
        doc_xml = zf.read("word/document.xml") if "word/document.xml" in zf.namelist() else None

    part_key = "docProps/custom.xml"
    if part_key not in other:
        return {"ok": False, "error": f"property '{name}' not found (no custom.xml)"}

    props_root = etree.fromstring(other[part_key])
    removed = False
    for prop in props_root.findall(f"{PROPS}property"):
        if prop.get("name") == name:
            props_root.remove(prop)
            removed = True
            break

    if not removed:
        return {"ok": False, "error": f"property '{name}' not found"}

    other[part_key] = etree.tostring(props_root, xml_declaration=True,
                                      encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_rm_prop.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct_key = "[Content_Types].xml"
        ct = other.get(ct_key)
        if ct is not None:
            zf.writestr(ct_key, ct)
        if doc_xml is not None:
            zf.writestr("word/document.xml", doc_xml)
        for n, data in other.items():
            if n in (ct_key, "word/document.xml"):
                continue
            zf.writestr(n, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "name": name, "removed": True, "path": out_path}
