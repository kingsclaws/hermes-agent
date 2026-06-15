"""Reusable OPC helpers for WordprocessingML packages.

This module keeps low-level package relationship and content-type handling in
one place. Editing code should use these helpers instead of re-parsing .rels
files ad hoc, so future features such as hyperlinks, comments, images, and
header/footer edits share the same path semantics.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass

from lxml import etree

PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

W = f"{{{W_NS}}}"
R = f"{{{R_NS}}}"


@dataclass(frozen=True)
class Relationship:
    r_id: str
    rel_type: str
    target: str
    target_mode: str | None = None


def normalize_member_path(member_path: str) -> str:
    path = (member_path or "").replace("\\", "/").lstrip("/")
    if not path:
        raise ValueError("empty OPC member path")
    return posixpath.normpath(path)


def rels_member_for_part(member_path: str) -> str:
    member_path = normalize_member_path(member_path)
    folder = posixpath.dirname(member_path)
    filename = posixpath.basename(member_path)
    if folder:
        return f"{folder}/_rels/{filename}.rels"
    return f"_rels/{filename}.rels"


def resolve_target_path(source_member: str, target: str) -> str:
    source_member = normalize_member_path(source_member)
    target = (target or "").strip()
    if not target:
        raise ValueError("empty relationship target")
    if target.startswith("/"):
        return normalize_member_path(target)
    return normalize_member_path(posixpath.join(posixpath.dirname(source_member), target))


def parse_relationships_xml(xml_bytes: bytes) -> dict[str, Relationship]:
    root = etree.fromstring(xml_bytes)
    relationships: dict[str, Relationship] = {}
    for rel in root.findall(f"{{{PKG_REL_NS}}}Relationship"):
        rid = rel.get("Id", "")
        if not rid:
            continue
        relationships[rid] = Relationship(
            r_id=rid,
            rel_type=rel.get("Type", "") or "",
            target=rel.get("Target", "") or "",
            target_mode=rel.get("TargetMode"),
        )
    return relationships


def read_relationships(zf: zipfile.ZipFile, rels_path: str) -> dict[str, Relationship]:
    names = set(zf.namelist())
    if rels_path not in names:
        return {}
    return parse_relationships_xml(zf.read(rels_path))


def next_relationship_id(relationships: dict[str, Relationship] | list[str] | set[str]) -> str:
    ids = relationships.keys() if isinstance(relationships, dict) else relationships
    max_num = 0
    for rid in ids:
        match = re.fullmatch(r"rId(\d+)", str(rid))
        if match:
            max_num = max(max_num, int(match.group(1)))
    return f"rId{max_num + 1}"


def append_relationship(
    root: etree._Element,
    rel_type: str,
    target: str,
    *,
    target_mode: str | None = None,
    preferred_id: str | None = None,
) -> str:
    existing = {
        rel.get("Id", "")
        for rel in root.findall(f"{{{PKG_REL_NS}}}Relationship")
        if rel.get("Id")
    }
    rid = preferred_id if preferred_id and preferred_id not in existing else next_relationship_id(existing)
    rel = etree.SubElement(root, f"{{{PKG_REL_NS}}}Relationship")
    rel.set("Id", rid)
    rel.set("Type", rel_type)
    rel.set("Target", target)
    if target_mode:
        rel.set("TargetMode", target_mode)
    return rid


def ensure_default_content_type(
    root: etree._Element,
    extension: str,
    content_type: str,
) -> bool:
    extension = (extension or "").strip().lstrip(".")
    if not extension:
        raise ValueError("extension is required")
    if not content_type:
        raise ValueError("content_type is required")

    for item in root.findall(f"{{{CT_NS}}}Default"):
        if item.get("Extension") == extension:
            if item.get("ContentType") == content_type:
                return False
            item.set("ContentType", content_type)
            return True

    item = etree.SubElement(root, f"{{{CT_NS}}}Default")
    item.set("Extension", extension)
    item.set("ContentType", content_type)
    return True


def document_relationships(zf: zipfile.ZipFile) -> dict[str, Relationship]:
    return read_relationships(zf, "word/_rels/document.xml.rels")


def header_footer_ref_types(zf: zipfile.ZipFile) -> dict[str, set[str]]:
    """Return OPC part paths for headers/footers referenced by document sections."""
    rels = document_relationships(zf)
    if "word/document.xml" not in set(zf.namelist()):
        return {}
    root = etree.fromstring(zf.read("word/document.xml"))
    result: dict[str, set[str]] = {}
    for tag_name in ("headerReference", "footerReference"):
        for ref in root.iter(f"{W}{tag_name}"):
            rid = ref.get(f"{R}id")
            rel = rels.get(rid or "")
            if not rel or not rel.target:
                continue
            part_path = resolve_target_path("word/document.xml", rel.target)
            result.setdefault(part_path, set()).add(ref.get(f"{W}type", "default") or "default")
    return result
