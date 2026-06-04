"""
header_footer_ops.py — Header & Footer 审查工具（zipfile XML 级）

直接基于 OPC(zip) + WordprocessingML 读取 header/footer XML，
不依赖 python-docx 接口（python-docx 会漏 textbox、图片旁文字等）。

功能：
  1. 列出所有 header/footer XML 部件及完整文本
  2. 从 document.xml 的 sectPr 解析 section ↔ header/footer 映射
  3. 检测"可能残留旧实体"的全大写实体名

用法：
    from lex_docx.header_footer_ops import audit_all, build_section_map
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

# ── Namespaces ─────────────────────────────────────────────────────────────── #
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _qn(tag: str) -> str:
    """Quick QName helper."""
    ns, local = tag.split(":")
    return f"{{{'w' if ns == 'w' else _R_NS}}}{local}"


def _wqn(tag: str) -> str:
    return f"{{{_W_NS}}}{tag}"


def _rqn(tag: str) -> str:
    return f"{{{_R_NS}}}{tag}"


# ── Low-level text extraction ─────────────────────────────────────────────── #

def _extract_text_from_xml(xml_bytes: bytes) -> str:
    """
    从任意 WordprocessingML XML (header/footer/document) 中提取全部 w:t 文本。
    含 textbox (w:txbxContent) 内的文字。
    """
    root = etree.fromstring(xml_bytes)
    parts: list[str] = []
    for el in root.iter():
        if el.tag == _wqn("t") and el.text:
            parts.append(el.text)
    return "".join(parts)


def _has_textbox(xml_bytes: bytes) -> bool:
    root = etree.fromstring(xml_bytes)
    return root.find(f".//{_wqn('txbxContent')}") is not None


# ── Part discovery via zipfile ────────────────────────────────────────────── #

def _discover_parts(zf: zipfile.ZipFile) -> dict[str, dict]:
    """
    扫描 zip 中所有 word/header*.xml 和 word/footer*.xml，
    返回 {internal_path: {text, has_textbox, filename}}。
    """
    results: dict[str, dict] = {}
    for info in zf.infolist():
        name = info.filename
        if not name.startswith("word/"):
            continue
        basename = name.split("/")[-1]
        if not (basename.startswith("header") or basename.startswith("footer")):
            continue
        if not basename.endswith(".xml"):
            continue
        raw = zf.read(name)
        results[name] = {
            "filename": name,
            "basename": basename,
            "text": _extract_text_from_xml(raw),
            "has_textbox": _has_textbox(raw),
            "kind": "header" if basename.startswith("header") else "footer",
        }
    return results


# ── Section mapping from document.xml ─────────────────────────────────────── #

def _parse_section_map(zf: zipfile.ZipFile) -> list[dict]:
    """
    从 word/document.xml 的 w:sectPr 中解析 section → header/footer 映射。

    每个 section 返回:
      {
        "section_idx": int,
        "sectPr_source": "body_last" | "pPr",
        "headers": [{"rId": ..., "type": "default"|"first"|"even", "part": ...}],
        "footers": [{"rId": ..., "type": "default"|"first"|"even", "part": ...}],
      }

    r:id → part path 映射通过 word/_rels/document.xml.rels 解析。
    """
    doc_rels = _parse_rels(zf, "word/_rels/document.xml.rels")
    doc_xml = zf.read("word/document.xml")
    root = etree.fromstring(doc_xml)
    body = root.find(_wqn("body"))
    if body is None:
        return []

    sections: list[dict] = []
    idx = 0

    # Helper to extract references from a sectPr
    def _refs_from_sectpr(sect_pr) -> dict:
        hdrs, ftrs = [], []
        for ref in sect_pr.iter(_wqn("headerReference")):
            rid = ref.get(_rqn("id"))
            htype = ref.get(_wqn("type"), "default")
            part = doc_rels.get(rid, "")
            if rid:
                hdrs.append({"rId": rid, "type": htype, "part": part})
        for ref in sect_pr.iter(_wqn("footerReference")):
            rid = ref.get(_rqn("id"))
            ftype = ref.get(_wqn("type"), "default")
            part = doc_rels.get(rid, "")
            if rid:
                ftrs.append({"rId": rid, "type": ftype, "part": part})
        return {"headers": hdrs, "footers": ftrs}

    # sectPr can appear as direct child of body (last section)
    # or inside w:pPr (section breaks)
    for child in list(body):
        tag = child.tag
        if tag == _wqn("sectPr"):
            refs = _refs_from_sectpr(child)
            sections.append({
                "section_idx": idx,
                "sectPr_source": "body_last",
                **refs,
            })
            idx += 1
        elif tag == _wqn("p"):
            pPr = child.find(_wqn("pPr"))
            if pPr is not None:
                sectPr = pPr.find(_wqn("sectPr"))
                if sectPr is not None:
                    refs = _refs_from_sectpr(sectPr)
                    sections.append({
                        "section_idx": idx,
                        "sectPr_source": "pPr",
                        **refs,
                    })
                    idx += 1

    return sections


def _parse_rels(zf: zipfile.ZipFile, rels_path: str) -> dict[str, str]:
    """Parse a .rels file → {rId: target_path}."""
    raw = zf.read(rels_path) if rels_path in [i.filename for i in zf.infolist()] else None
    if raw is None:
        return {}
    root = etree.fromstring(raw)
    mapping: dict[str, str] = {}
    for rel in root.findall(f"{{{_PKG_REL_NS}}}Relationship"):
        rid = rel.get("Id", "")
        target = rel.get("Target", "")
        if rid and target:
            mapping[rid] = target
    return mapping


# ── Public API ────────────────────────────────────────────────────────────── #

def audit_all(docx_path: str) -> dict:
    """
    全面审查文档的所有 header/footer。

    返回:
      {
        "parts": {path: {text, has_textbox, kind, basename}},
        "sections": [{section_idx, headers: [{rId, type, part, text}],
                                  footers: [{rId, type, part, text}]}],
      }
    """
    with zipfile.ZipFile(docx_path, "r") as zf:
        parts = _discover_parts(zf)
        section_map = _parse_section_map(zf)

    # Enrich section map with actual text
    for sec in section_map:
        for hdr in sec["headers"]:
            # part is like "header1.xml" — resolve to "word/header1.xml"
            target = hdr.get("part", "")
            # target from rels is relative (e.g., "header1.xml")
            full_path = f"word/{target}" if not target.startswith("word/") else target
            hdr["part_path"] = full_path
            p = parts.get(full_path)
            hdr["text"] = p["text"] if p else ""
            hdr["has_textbox"] = p["has_textbox"] if p else False
        for ftr in sec["footers"]:
            target = ftr.get("part", "")
            full_path = f"word/{target}" if not target.startswith("word/") else target
            ftr["part_path"] = full_path
            p = parts.get(full_path)
            ftr["text"] = p["text"] if p else ""
            ftr["has_textbox"] = p["has_textbox"] if p else False

    return {"parts": parts, "sections": section_map}


def detect_stale_entities(docx_path: str) -> list[dict]:
    """
    检测 header/footer 中与正文第一段公司名不同的全大写实体名，
    标记为"可能残留旧实体"。

    返回:
      [{part, text, suspect_entities: [str]}]
    """
    with zipfile.ZipFile(docx_path, "r") as zf:
        parts = _discover_parts(zf)
        # Get first paragraph text from document.xml
        doc_xml = zf.read("word/document.xml")
        root = etree.fromstring(doc_xml)
        body = root.find(_wqn("body"))
        first_para_text = ""
        if body is not None:
            for child in list(body):
                if child.tag == _wqn("p"):
                    texts = [el.text or "" for el in child.iter(_wqn("t"))]
                    first_para_text = "".join(texts)
                    break

    # Extract ALL_CAPS entity-like tokens (e.g. "ROKID CORPORATION LTD")
    # Pattern: 2+ consecutive uppercase words (at least 2 chars each)
    entity_pattern = re.compile(
        r'\b([A-Z][A-Z0-9 &,.]+\b(?:\s+[A-Z][A-Z0-9 &,.]+\b)+)'
    )

    # Get entities from first paragraph as "expected" entities
    expected = set()
    for m in entity_pattern.finditer(first_para_text):
        expected.add(m.group(0).strip())

    results = []
    for path, info in sorted(parts.items()):
        text = info["text"]
        if not text.strip():
            continue
        suspects = []
        for m in entity_pattern.finditer(text):
            ent = m.group(0).strip()
            # Skip if it appears in the first paragraph (it's expected)
            if ent not in expected:
                suspects.append(ent)
        if suspects:
            results.append({
                "part": path,
                "text": text[:200],
                "suspect_entities": suspects,
            })
    return results
