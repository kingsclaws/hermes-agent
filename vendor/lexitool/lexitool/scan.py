"""Document-wide text scanning for legal revision verification."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _read_document_root(path: str) -> etree._Element:
    with zipfile.ZipFile(path, "r") as zf:
        return etree.fromstring(zf.read("word/document.xml"))


def _parse_rels(zf: zipfile.ZipFile, rels_path: str) -> dict[str, str]:
    if rels_path not in set(zf.namelist()):
        return {}
    root = etree.fromstring(zf.read(rels_path))
    mapping: dict[str, str] = {}
    for rel in root.findall(f"{{{PKG_REL_NS}}}Relationship"):
        rid = rel.get("Id", "")
        target = rel.get("Target", "")
        if rid and target:
            mapping[rid] = target
    return mapping


def _header_footer_ref_types(path: str) -> dict[str, set[str]]:
    with zipfile.ZipFile(path, "r") as zf:
        doc_rels = _parse_rels(zf, "word/_rels/document.xml.rels")
        root = etree.fromstring(zf.read("word/document.xml"))
    result: dict[str, set[str]] = {}
    for tag_name, kind in (("headerReference", "header"), ("footerReference", "footer")):
        for ref in root.iter(f"{W}{tag_name}"):
            rid = ref.get(f"{{{R_NS}}}id")
            target = doc_rels.get(rid or "", "")
            if not target:
                continue
            part_path = f"word/{target}" if not target.startswith("word/") else target
            result.setdefault(part_path, set()).add(ref.get(f"{W}type", "default") or "default")
    return result


def _iter_header_footer_items(path: str):
    ref_types = _header_footer_ref_types(path)
    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            name = info.filename
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            basename = Path(name).name
            if basename.startswith("header"):
                kind = "header"
            elif basename.startswith("footer"):
                kind = "footer"
            else:
                continue
            root = etree.fromstring(zf.read(name))
            yield {
                "kind": kind,
                "part_path": name,
                "ref_types": sorted(ref_types.get(name) or ["unknown"]),
                "element": root,
                "location": f"{kind}:{name}",
            }


def _has_ancestor(el: etree._Element, tag: str) -> bool:
    parent = el.getparent()
    while parent is not None:
        if parent.tag == tag:
            return True
        parent = parent.getparent()
    return False


def _visible_text(el: etree._Element, view: str = "final") -> str:
    """Return text as shown in a TC view.

    view="final" excludes deletions and includes insertions.
    view="original" includes deletions and excludes insertions.
    view="all" includes all text, with no TC markup, for broad forensic scans.
    """
    view = (view or "final").lower()
    parts: list[str] = []
    for node in el.iter():
        if node.tag not in (f"{W}t", f"{W}delText", f"{W}tab"):
            continue
        in_del = _has_ancestor(node, f"{W}del")
        in_ins = _has_ancestor(node, f"{W}ins")
        if view == "final" and in_del:
            continue
        if view == "original" and in_ins:
            continue
        if node.tag == f"{W}tab":
            parts.append("\t")
        else:
            parts.append(node.text or "")
    return "".join(parts)


def _preview(text: str, start: int, end: int, context_chars: int) -> str:
    lo = max(0, start - context_chars)
    hi = min(len(text), end + context_chars)
    prefix = "..." if lo > 0 else ""
    suffix = "..." if hi < len(text) else ""
    return prefix + text[lo:hi].replace("\n", " ") + suffix


def _iter_body_items(root: etree._Element):
    body = root.find(f"{W}body")
    if body is None:
        return
    para_count = 0
    table_index = 0
    for child in body:
        actual = child
        if child.tag in (f"{W}sdt", f"{W}customXml"):
            sdt_content = child.find(f"{W}sdtContent")
            if sdt_content is not None and len(sdt_content) == 1:
                actual = sdt_content[0]
        if actual.tag == f"{W}p":
            para_count += 1
            yield {
                "kind": "paragraph",
                "para": para_count,
                "element": actual,
                "location": f"§{para_count}",
            }
        elif actual.tag == f"{W}tbl":
            rows = [r for r in actual if r.tag == f"{W}tr"]
            for row_idx, row in enumerate(rows):
                cells = [c for c in row if c.tag == f"{W}tc"]
                for col_idx, cell in enumerate(cells):
                    yield {
                        "kind": "table_cell",
                        "table_index": table_index,
                        "row": row_idx,
                        "col": col_idx,
                        "after_para": para_count,
                        "element": cell,
                        "location": (
                            f"table={table_index},row={row_idx},col={col_idx},"
                            f"after=§{para_count}"
                        ),
                    }
            table_index += 1


def scan_text(
    path: str,
    query: str,
    *,
    regex: bool = False,
    case_sensitive: bool = True,
    flexible_whitespace: bool = True,
    view: str = "final",
    include_tables: bool = True,
    include_headers_footers: bool = True,
    context_chars: int = 80,
    max_results: int = 200,
) -> dict[str, Any]:
    """Scan body paragraphs, table cells, and optionally header/footer parts."""
    if not query:
        return {"ok": False, "error": "query is required"}
    root = _read_document_root(path)
    flags = 0 if case_sensitive else re.IGNORECASE
    if regex:
        pattern_text = query
    elif flexible_whitespace and re.search(r"\s", query):
        parts = [re.escape(part) for part in re.split(r"\s+", query.strip()) if part]
        pattern_text = r"[\s\u00a0]+".join(parts) if parts else re.escape(query)
    else:
        pattern_text = re.escape(query)
    pattern = re.compile(pattern_text, flags)

    results: list[dict[str, Any]] = []
    total_matches = 0
    paragraph_targets: list[int] = []
    table_targets: list[dict[str, int]] = []
    header_footer_targets: list[dict[str, Any]] = []

    for item in _iter_body_items(root) or []:
        if item["kind"] == "table_cell" and not include_tables:
            continue
        text = _visible_text(item["element"], view=view)
        if not text:
            continue
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        total_matches += len(matches)
        if item["kind"] == "paragraph":
            paragraph_targets.append(int(item["para"]))
        else:
            table_targets.append({
                "table_index": int(item["table_index"]),
                "row": int(item["row"]),
                "col": int(item["col"]),
            })
        for match in matches:
            if len(results) >= max_results:
                continue
            payload = {
                "kind": item["kind"],
                "location": item["location"],
                "match": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "preview": _preview(text, match.start(), match.end(), context_chars),
            }
            for key in ("para", "table_index", "row", "col", "after_para"):
                if key in item:
                    payload[key] = item[key]
            results.append(payload)

    if include_headers_footers:
        for item in _iter_header_footer_items(path) or []:
            text = _visible_text(item["element"], view=view)
            if not text:
                continue
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            total_matches += len(matches)
            target = {
                "kind": item["kind"],
                "part_path": item["part_path"],
                "ref_types": item["ref_types"],
            }
            if target not in header_footer_targets:
                header_footer_targets.append(target)
            for match in matches:
                if len(results) >= max_results:
                    continue
                results.append({
                    "kind": item["kind"],
                    "location": item["location"],
                    "match": match.group(0),
                    "start": match.start(),
                    "end": match.end(),
                    "preview": _preview(text, match.start(), match.end(), context_chars),
                    "part_path": item["part_path"],
                    "ref_types": item["ref_types"],
                })

    paragraph_targets = sorted(set(paragraph_targets))
    return {
        "ok": True,
        "path": str(Path(path)),
        "query": query,
        "regex": regex,
        "case_sensitive": case_sensitive,
        "flexible_whitespace": flexible_whitespace,
        "view": view,
        "include_tables": include_tables,
        "include_headers_footers": include_headers_footers,
        "total_matches": total_matches,
        "returned": len(results),
        "truncated": total_matches > len(results),
        "paragraph_targets": paragraph_targets,
        "table_targets": table_targets[:max_results],
        "header_footer_targets": header_footer_targets[:max_results],
        "results": results,
        "next_step": (
            "For legal revision, read every paragraph_targets range and any "
            "header_footer_targets with lex_read before editing; use "
            "lex_edit(op='replace_header_footer', part_path=...) for header/footer "
            "hits. After editing, run lex_scan again in view='final' to confirm "
            "total_matches is zero or intentionally retained."
        ),
    }
