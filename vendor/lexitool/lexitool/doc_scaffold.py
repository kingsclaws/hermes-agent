"""
doc_scaffold.py — 模板克隆 / 主体替换，全程无 python-docx。

技术路径：纯 ZIP + lxml，直接对 word/document.xml 做文本替换。

强制流程：
1. 先解析 mapping JSON
2. 输出 mapping 预览（哪些段命中、替换前预览、替换后预览）
3. 用户确认后才写入
4. 写完后自动做残留扫描（旧主体名计数应为 0）
"""
from __future__ import annotations

import json
import zipfile
import re
import tempfile
import os
import sys
from pathlib import Path
from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _scan_xml_strings(xml_bytes: bytes) -> list[str]:
    """Extract all text strings from a piece of XML."""
    try:
        root = etree.fromstring(xml_bytes)
    except Exception:
        return []
    texts: list[str] = []
    for el in root.iter():
        if el.text:
            texts.append(el.text)
        if el.tail:
            texts.append(el.tail)
    return texts


def _text_replacements_in_xml(xml_bytes: bytes,
                                old: str, new: str) -> list[tuple[str, str]]:
    """Return [(before, after)] for each matched text node."""
    root = etree.fromstring(xml_bytes)
    changes: list[tuple[str, str]] = []
    for el in root.iter():
        for attr in ("text", "tail"):
            val = getattr(el, attr, None)
            if val is not None and old in val:
                changes.append((val, val.replace(old, new)))
    return changes


def _apply_replacements(xml_bytes: bytes,
                       replacements: dict[str, str]) -> bytes:
    """Apply a dict of {old: new} replacements to raw XML bytes."""
    result = xml_bytes
    for old, new in replacements.items():
        result = result.replace(old.encode("utf-8"), new.encode("utf-8"))
    return result


def scaffold_preview(template_path: str, mapping: dict) -> dict:
    """
    对模板做 mapping 预览，返回命中段落和替换前后对比。

    Returns:
        {
          "ok": True,
          "hits": [
            {
              "part": "word/document.xml",
              "before": "...old_entity...",
              "after": "...new_entity...",
              "count": N,
            },
            ...
          ],
          "total_hits": N,
        }
    """
    hits: list[dict] = []
    all_old = []
    for k, v in mapping.items():
        if isinstance(v, str) and v and k.startswith("old_"):
            all_old.append((v, mapping.get(k.replace("old_", "new_", 1), "")))

    # also handle signatory dict
    if "signatory" in mapping:
        s = mapping["signatory"]
        if isinstance(s, dict):
            for old, new in s.items():
                all_old.append((str(old), str(new)))

    for old, new in all_old:
        if not old:
            continue
        with zipfile.ZipFile(template_path, "r") as zf:
            for name in zf.namelist():
                if not name.endswith(".xml"):
                    continue
                try:
                    data = zf.read(name)
                except Exception:
                    continue
                changes = _text_replacements_in_xml(data, old, new)
                if changes:
                    for before, after in changes:
                        hits.append({
                            "part": name,
                            "old": old,
                            "new": new,
                            "before": before,
                            "after": after,
                            "count": len(changes),
                        })

    return {
        "ok": True,
        "hits": hits,
        "total_hits": sum(h.get("count", 0) for h in hits),
        "old_values": [old for old, _ in all_old if old],
    }


def scaffold_apply(template_path: str, output_path: str,
                   mapping: dict, dry_run: bool = False) -> dict:
    """
    对模板应用 mapping 并输出新文件。

    Args:
        template_path: 模板文件
        output_path:   输出文件
        mapping:       {old_entity: new_entity, old_date: new_date, ...}
        dry_run:       True = 只预览不写入

    Returns:
        {"ok": True, "replacements": [...], "output": path, "residual_scan": {...}}
    """
    all_replacements: dict[str, str] = {}

    # scalar replacements
    for k, v in mapping.items():
        if isinstance(v, str) and k.startswith("old_"):
            new_key = k.replace("old_", "new_", 1)
            if new_key in mapping:
                all_replacements[mapping[k]] = mapping[new_key]

    # signatory dict
    if "signatory" in mapping:
        s = mapping["signatory"]
        if isinstance(s, dict):
            for old, new in s.items():
                all_replacements[str(old)] = str(new)

    # date replacements
    if "old_date" in mapping and "new_date" in mapping:
        all_replacements[mapping["old_date"]] = mapping["new_date"]

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "replacements": all_replacements,
            "output": output_path,
        }

    fd, tmp = tempfile.mkstemp(prefix="lex_docx_scaffold.", suffix=".docx")
    os.close(fd)

    with zipfile.ZipFile(template_path, "r") as zin, \
         zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.endswith(".xml"):
                data = _apply_replacements(data, all_replacements)
            zout.writestr(info, data)

    os.replace(tmp, output_path)

    # residual scan
    residual = residual_scan(output_path,
                            [old for old in all_replacements])

    return {
        "ok": True,
        "dry_run": False,
        "replacements": all_replacements,
        "output": output_path,
        "residual_scan": residual,
    }


def residual_scan(docx_path: str, old_values: list[str]) -> dict:
    """
    扫描文档中是否还有旧名称残留。

    Returns:
        {
          "ok": True,
          "residual_found": True/False,
          "details": [
            {"value": "...", "count": N, "locations": ["word/document.xml"]},
          ]
        }
    """
    details: list[dict] = []
    for old in old_values:
        if not old:
            continue
        count = 0
        locations: list[str] = []
        with zipfile.ZipFile(docx_path, "r") as zf:
            for name in zf.namelist():
                if not name.endswith(".xml"):
                    continue
                try:
                    data = zf.read(name)
                    if old.encode("utf-8") in data:
                        c = data.count(old.encode("utf-8"))
                        count += c
                        locations.append(name)
                except Exception:
                    pass
        if count > 0:
            details.append({"value": old, "count": count, "locations": locations})

    return {
        "ok": True,
        "residual_found": len(details) > 0,
        "details": details,
    }
