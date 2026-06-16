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


def ensure_relationship(
    root: etree._Element,
    rel_type: str,
    target: str,
    *,
    target_mode: str | None = None,
) -> str:
    for rel in root.findall(f"{{{PKG_REL_NS}}}Relationship"):
        if (
            rel.get("Type") == rel_type
            and rel.get("Target") == target
            and (rel.get("TargetMode") or None) == (target_mode or None)
        ):
            return rel.get("Id", "rId1")
    return append_relationship(root, rel_type, target, target_mode=target_mode)


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


def ensure_override_content_type(
    root: etree._Element,
    part_name: str,
    content_type: str,
) -> bool:
    part_name = (part_name or "").strip()
    if not part_name:
        raise ValueError("part_name is required")
    if not part_name.startswith("/"):
        part_name = "/" + part_name
    if not content_type:
        raise ValueError("content_type is required")

    for item in root.findall(f"{{{CT_NS}}}Override"):
        if item.get("PartName") == part_name:
            if item.get("ContentType") == content_type:
                return False
            item.set("ContentType", content_type)
            return True

    item = etree.SubElement(root, f"{{{CT_NS}}}Override")
    item.set("PartName", part_name)
    item.set("ContentType", content_type)
    return True


class RelationshipGraph:
    """In-memory graph of all OPC relationships loaded from a package.

    Parses every .rels file in the zip at construction time and indexes
    relationships by source, target, and type for efficient lookups.

    Usage:
        with zipfile.ZipFile(docx_path) as zf:
            graph = RelationshipGraph(zf)
            rel = graph.resolve("word/document.xml", "rId5")
            back_refs = graph.reverse_lookup("word/styles.xml")
            headers = graph.list_by_type(
                "http://schemas.openxmlformats.org/officeDocument/2006/"
                "relationships/header"
            )
            print(graph.audit())
    """

    def __init__(self, zf: zipfile.ZipFile):
        # source_rels_path → {rId: Relationship}
        self._source_map: dict[str, dict[str, Relationship]] = {}
        # resolved_target → [(source_rels_path, rId, Relationship)]
        self._target_index: dict[str, list[tuple[str, str, Relationship]]] = {}
        # rel_type → [(source_rels_path, rId, Relationship)]
        self._type_index: dict[str, list[tuple[str, str, Relationship]]] = {}

        for name in zf.namelist():
            if not name.endswith(".rels"):
                continue
            rels = parse_relationships_xml(zf.read(name))
            if not rels:
                continue
            self._source_map[name] = rels
            for rid, rel in rels.items():
                if rel.target:
                    resolved = resolve_target_path(name, rel.target)
                else:
                    resolved = ""
                self._target_index.setdefault(resolved, []).append(
                    (name, rid, rel)
                )
                self._type_index.setdefault(rel.rel_type, []).append(
                    (name, rid, rel)
                )

    def resolve(self, source: str, rId: str) -> Relationship | None:
        """Resolve a relationship ID from a source part path.

        Args:
            source: Source part path, e.g. "word/document.xml".
            rId: Relationship ID, e.g. "rId5".
        """
        rels_file = rels_member_for_part(source)
        rels = self._source_map.get(rels_file, {})
        return rels.get(rId)

    def reverse_lookup(self, target: str) -> list[tuple[str, str, Relationship]]:
        """Find all relationships pointing to a resolved target path.

        Returns list of (source_rels_path, rId, Relationship) tuples.
        """
        return self._target_index.get(target, [])

    def list_by_type(
        self, rel_type: str
    ) -> list[tuple[str, str, Relationship]]:
        """List all relationships of a given type URI.

        Returns list of (source_rels_path, rId, Relationship) tuples.
        """
        return self._type_index.get(rel_type, [])

    def audit(self) -> dict:
        """Return a summary of all relationship types and counts."""
        summary: dict[str, dict] = {}
        for rel_type, items in self._type_index.items():
            short = rel_type.split("/")[-1] if "/" in rel_type else rel_type
            summary[short] = {"type": rel_type, "count": len(items)}
        return {
            "total_rels_files": len(self._source_map),
            "total_relationships": sum(
                len(r) for r in self._source_map.values()
            ),
            "by_type": summary,
        }


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
