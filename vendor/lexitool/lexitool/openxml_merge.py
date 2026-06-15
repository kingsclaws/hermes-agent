"""Document merge and split for WordprocessingML documents.

Merge inserts one document's body content into another, reconciling
relationships and media parts.  Split extracts a paragraph range into
a new standalone document.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

W = f"{{{W_NS}}}"


def _find_para_by_index(body: etree._Element, para_idx: int) -> etree._Element | None:
    count = 0
    for el in body.iter():
        if el.tag == f"{W}p":
            if count == para_idx:
                return el
            count += 1
    return None


def _next_free_id(rels_root: etree._Element) -> int:
    """Return the next unused rId number from an existing rels document."""
    max_id = 0
    for rel in rels_root.findall(f"{{{PKG_REL_NS}}}Relationship"):
        rid = rel.get("Id", "")
        if rid.startswith("rId"):
            try:
                n = int(rid[3:])
                if n > max_id:
                    max_id = n
            except ValueError:
                pass
    return max_id + 1


def merge_documents(
    base_path: str,
    insert_path: str,
    after_para: int = 0,
    *,
    output: str | None = None,
) -> dict:
    """Insert one document's body content into another, after a given paragraph.

    Copies referenced media parts and updates relationships so inserted
    images and other linked resources remain valid.

    Args:
        base_path: Path to the base .docx file.
        insert_path: Path to the .docx to insert.
        after_para: 1-indexed paragraph after which to insert. 0 = at beginning.
        output: Output path (default: overwrite base).
    """
    out_path = output or base_path

    with zipfile.ZipFile(base_path, "r") as zf_base:
        base_doc = zf_base.read("word/document.xml")
        base_other = {name: zf_base.read(name) for name in zf_base.namelist()
                      if name != "word/document.xml"}

    with zipfile.ZipFile(insert_path, "r") as zf_ins:
        ins_doc = zf_ins.read("word/document.xml")
        ins_other = {name: zf_ins.read(name) for name in zf_ins.namelist()
                     if name != "word/document.xml"}

    base_root = etree.fromstring(base_doc)
    base_body = base_root.find(f"{W}body")
    if base_body is None:
        return {"ok": False, "error": "base document has no body"}

    ins_root = etree.fromstring(ins_doc)
    ins_body = ins_root.find(f"{W}body")
    if ins_body is None:
        return {"ok": False, "error": "insert document has no body"}

    # ── Collect insert document's body children ─────────────────────────────
    ins_children = list(ins_body)

    # ── Reconcile relationships ─────────────────────────────────────────────
    # Map old rId → new rId for the base document's rels
    base_rels_key = "word/_rels/document.xml.rels"
    ins_rels_key = "word/_rels/document.xml.rels"

    base_rels_bytes = base_other.get(base_rels_key)
    if base_rels_bytes is not None:
        base_rels_root = etree.fromstring(base_rels_bytes)
    else:
        base_rels_root = etree.Element(
            f"{{{PKG_REL_NS}}}Relationships",
            nsmap={None: PKG_REL_NS},
        )
        base_rels_key_out = base_rels_key

    ins_rels_root = None
    ins_rels_bytes = ins_other.get(ins_rels_key)
    if ins_rels_bytes is not None:
        ins_rels_root = etree.fromstring(ins_rels_bytes)

    rid_map: dict[str, str] = {}  # old rId → new rId
    next_rid = _next_free_id(base_rels_root)

    if ins_rels_root is not None:
        for rel in ins_rels_root.findall(f"{{{PKG_REL_NS}}}Relationship"):
            old_rid = rel.get("Id", "")
            rel_type = rel.get("Type", "")
            target = rel.get("Target", "")

            new_rid = f"rId{next_rid}"
            next_rid += 1
            rid_map[old_rid] = new_rid

            # Copy the referenced part if it exists
            src_part = target
            if target.startswith("media/"):
                src_part = f"word/{target}"
            elif target.startswith(".."):
                # External references: skip
                pass

            if src_part in ins_other and src_part not in base_other:
                base_other[src_part] = ins_other[src_part]

            # Add new relationship to base rels
            new_rel = etree.SubElement(base_rels_root, f"{{{PKG_REL_NS}}}Relationship")
            new_rel.set("Id", new_rid)
            new_rel.set("Type", rel_type)
            new_rel.set("Target", target)
            target_mode = rel.get("TargetMode")
            if target_mode:
                new_rel.set("TargetMode", target_mode)

    # ── Remap rIds in inserted content ──────────────────────────────────────
    if rid_map:
        # Remap all rId attributes and references
        r_embed = f"{{{REL_NS}}}embed"
        r_link = f"{{{REL_NS}}}link"
        r_id = f"{{{REL_NS}}}id"

        for child in ins_children:
            for el in child.iter():
                for attr_name in (r_embed, r_link, r_id):
                    old_val = el.get(attr_name, "")
                    if old_val in rid_map:
                        el.set(attr_name, rid_map[old_val])

    # ── Insert children into base body ──────────────────────────────────────
    if after_para <= 0:
        # Insert at beginning (after any sectPr at body level isn't typically an issue)
        first_child = base_body[0] if len(base_body) > 0 else None
        for child in reversed(ins_children):
            base_body.insert(0, deepcopy(child))
    else:
        para_el = _find_para_by_index(base_body, after_para - 1)
        if para_el is None:
            return {"ok": False, "error": f"paragraph {after_para} not found in base"}

        # Find para_el's position in body
        body_list = list(base_body)
        try:
            insert_pos = body_list.index(para_el) + 1
        except ValueError:
            # para_el is nested (e.g., inside a table) — insert after its table ancestor
            insert_pos = len(body_list)
            for ancestor in para_el.iterancestors():
                if ancestor in body_list:
                    insert_pos = body_list.index(ancestor) + 1
                    break

        for child in reversed(ins_children):
            base_body.insert(insert_pos, deepcopy(child))

    # ── Reconcile content types ─────────────────────────────────────────────
    ct_key = "[Content_Types].xml"
    base_ct = base_other.get(ct_key)
    ins_ct = ins_other.get(ct_key)
    if base_ct is not None and ins_ct is not None:
        base_ct_root = etree.fromstring(base_ct)
        ins_ct_root = etree.fromstring(ins_ct)

        for child in ins_ct_root:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "Default":
                ext = child.get("Extension", "")
                mime = child.get("ContentType", "")
                found = False
                for d in base_ct_root:
                    d_tag = d.tag.split("}")[-1] if "}" in d.tag else d.tag
                    if d_tag == "Default" and d.get("Extension") == ext:
                        found = True
                        break
                if not found:
                    new_d = etree.SubElement(base_ct_root, f"{{{CT_NS}}}Default")
                    new_d.set("Extension", ext)
                    new_d.set("ContentType", mime)
            elif tag == "Override":
                part_name = child.get("PartName", "")
                mime = child.get("ContentType", "")
                found = False
                for ov in base_ct_root:
                    ov_tag = ov.tag.split("}")[-1] if "}" in ov.tag else ov.tag
                    if ov_tag == "Override" and ov.get("PartName") == part_name:
                        found = True
                        break
                if not found:
                    new_ov = etree.SubElement(base_ct_root, f"{{{CT_NS}}}Override")
                    new_ov.set("PartName", part_name)
                    new_ov.set("ContentType", mime)

        base_other[ct_key] = etree.tostring(base_ct_root, xml_declaration=True,
                                             encoding="UTF-8", standalone="yes")

    # ── Write output ────────────────────────────────────────────────────────
    base_other[base_rels_key] = etree.tostring(base_rels_root, xml_declaration=True,
                                                encoding="UTF-8", standalone="yes")

    base_doc_out = etree.tostring(base_root, xml_declaration=True,
                                   encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_merge.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if ct_key in base_other:
            zf.writestr(ct_key, base_other[ct_key])
        zf.writestr("word/document.xml", base_doc_out)
        for name, data in base_other.items():
            if name == ct_key:
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "elements_inserted": len(ins_children),
            "after_para": after_para, "path": out_path}


def split_document(
    docx_path: str,
    para_start: int,
    para_end: int,
    *,
    output: str,
) -> dict:
    """Extract a paragraph range into a new standalone document.

    Args:
        docx_path: Source .docx file.
        para_start: First paragraph to include (1-indexed).
        para_end: Last paragraph to include (1-indexed, inclusive).
        output: Path for the new .docx file.
    """
    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    # Collect paragraphs in the range
    selected: list[etree._Element] = []
    para_idx = 0
    for child in list(body):
        if child.tag == f"{W}p":
            para_idx += 1
            if para_start <= para_idx <= para_end:
                selected.append(deepcopy(child))
        elif child.tag == f"{W}tbl":
            # Include tables between paragraphs in the range
            if selected:
                selected.append(deepcopy(child))

    if not selected:
        return {"ok": False, "error": f"no paragraphs found in range {para_start}-{para_end}"}

    # Build new document body
    new_root = etree.Element(
        f"{W}document",
        nsmap={
            "w": W_NS,
            "r": REL_NS,
        },
    )
    new_body = etree.SubElement(new_root, f"{W}body")

    for child in selected:
        new_body.append(child)

    # Add minimal sectPr
    sectPr = etree.SubElement(new_body, f"{W}sectPr")
    pgSz = etree.SubElement(sectPr, f"{W}pgSz")
    pgSz.set(f"{W}w", "11906")  # A4 width
    pgSz.set(f"{W}h", "16838")  # A4 height
    pgMar = etree.SubElement(sectPr, f"{W}pgMar")
    pgMar.set(f"{W}top", "1440")
    pgMar.set(f"{W}right", "1440")
    pgMar.set(f"{W}bottom", "1440")
    pgMar.set(f"{W}left", "1440")
    pgMar.set(f"{W}header", "720")
    pgMar.set(f"{W}footer", "720")

    # Preserve styles.xml, settings.xml, etc. (copy directly)
    new_doc_xml = etree.tostring(new_root, xml_declaration=True,
                                  encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_split.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct_key = "[Content_Types].xml"
        ct = other.get(ct_key)
        if ct is not None:
            zf.writestr(ct_key, ct)
        zf.writestr("word/document.xml", new_doc_xml)
        # Copy style/settings/font parts but not headers/footnotes
        for name, data in other.items():
            if name in (ct_key, "word/document.xml"):
                continue
            # Include styles, settings, themes, fonts, numbering
            if any(name.startswith(p) for p in (
                "word/styles", "word/settings", "word/theme",
                "word/fontTable", "word/numbering",
                "word/_rels", "word/media",
            )):
                zf.writestr(name, data)
    shutil.move(tmp, output)

    return {"ok": True, "paragraphs_extracted": len(selected),
            "para_range": f"{para_start}-{para_end}", "path": output}
