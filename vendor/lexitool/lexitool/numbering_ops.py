"""
numbering_ops.py — 编号检查与重置

目标：
1. inspect 当前段落 own/effective numPr
2. restart 某一段之后、某一层级的编号引用，并尽量只改编号，不碰缩进/段距/样式/字体
"""
from __future__ import annotations

from copy import deepcopy

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from . import doctor


def _numbering_root(doc):
    numbering_part = getattr(getattr(doc, "part", None), "numbering_part", None)
    if numbering_part is not None:
        return numbering_part._element
    rels = getattr(getattr(doc, "part", None), "rels", {}) or {}
    for rel in rels.values():
        reltype = getattr(rel, "reltype", "") or ""
        if reltype.endswith("/numbering") and getattr(rel, "target_part", None) is not None:
            return rel.target_part._element
    raise ValueError("document has no numbering part")


def _ensure_pPr(para_el):
    pPr = para_el.find(qn("w:pPr"))
    if pPr is None:
        pPr = OxmlElement("w:pPr")
        para_el.insert(0, pPr)
    return pPr


def _parse_range(para_range, total: int) -> tuple[int, int]:
    start, end = para_range or (0, total)
    return max(0, start), min(end, total)


def _para_outline_level_user(para) -> int | None:
    pPr = para._element.find(qn("w:pPr"))
    if pPr is None:
        return None
    ol = pPr.find(qn("w:outlineLvl"))
    if ol is None:
        return None
    try:
        val = int(ol.get(qn("w:val"), 9))
        return None if val >= 9 else val + 1
    except (ValueError, TypeError):
        return None


def inspect_numbering(
    doc,
    *,
    para_range: tuple[int, int] | None = None,
    styles: list[str] | None = None,
    outline_levels: list[int] | None = None,
    preview_len: int = 60,
) -> list[dict]:
    style_info = doctor._build_style_info(doc)
    paras = doc.paragraphs
    start, end = _parse_range(para_range, len(paras))

    results = []
    for idx in range(start, end):
        para = paras[idx]
        style_name = doctor._para_style_name(para, style_info)
        if styles is not None and style_name not in styles:
            continue

        outline_level = _para_outline_level_user(para)
        if outline_levels is not None and outline_level not in outline_levels:
            continue

        own_numpr = doctor._para_own_numpr(para)
        effective_numpr = doctor._effective_numpr(para, style_info)
        if own_numpr is None and effective_numpr is None:
            continue

        text = para.text or ""
        results.append({
            "index": idx,
            "style": style_name,
            "outline_level": outline_level,
            "own_numpr": own_numpr,
            "effective_numpr": effective_numpr,
            "numId": effective_numpr.get("numId") if effective_numpr else None,
            "ilvl": effective_numpr.get("ilvl") if effective_numpr else None,
            "text": text[:preview_len] + ("…" if len(text) > preview_len else ""),
        })
    return results


def _remove_numpr(pPr):
    existing = pPr.find(qn("w:numPr"))
    if existing is not None:
        pPr.remove(existing)


def _set_numpr(para, num_id: str, ilvl: str) -> None:
    pPr = _ensure_pPr(para._element)
    _remove_numpr(pPr)
    numpr = OxmlElement("w:numPr")
    ilvl_el = OxmlElement("w:ilvl")
    ilvl_el.set(qn("w:val"), str(ilvl))
    numid_el = OxmlElement("w:numId")
    numid_el.set(qn("w:val"), str(num_id))
    numpr.append(ilvl_el)
    numpr.append(numid_el)
    pPr.append(numpr)


def _next_num_id(doc) -> int:
    numbering_el = _numbering_root(doc)
    nums = numbering_el.findall(qn("w:num"))
    max_id = 0
    for num in nums:
        try:
            max_id = max(max_id, int(num.get(qn("w:numId"), 0)))
        except (TypeError, ValueError):
            pass
    return max_id + 1


def _clone_num_with_start_overrides(doc, base_num_id: str, ilvls: list[str], start_at: int) -> str:
    numbering_el = _numbering_root(doc)

    base_num = None
    for num in numbering_el.findall(qn("w:num")):
        if num.get(qn("w:numId")) == str(base_num_id):
            base_num = num
            break
    if base_num is None:
        raise ValueError(f"base numId not found: {base_num_id}")

    new_num = deepcopy(base_num)
    new_num_id = str(_next_num_id(doc))
    new_num.set(qn("w:numId"), new_num_id)

    existing_overrides = list(new_num.findall(qn("w:lvlOverride")))
    for item in existing_overrides:
        new_num.remove(item)

    for ilvl in sorted(set(ilvls), key=lambda x: int(x)):
        lvl_override = OxmlElement("w:lvlOverride")
        lvl_override.set(qn("w:ilvl"), str(ilvl))
        start_override = OxmlElement("w:startOverride")
        start_override.set(qn("w:val"), str(start_at))
        lvl_override.append(start_override)
        new_num.append(lvl_override)

    numbering_el.append(new_num)
    return new_num_id


def restart_numbering(
    doc,
    *,
    start_para: int,
    styles: list[str] | None = None,
    outline_levels: list[int] | None = None,
    start_at: int = 1,
    dry_run: bool = False,
    multilevel_link: bool = False,
    linked_levels: list[int] | None = None,
) -> dict:
    candidates = inspect_numbering(
        doc,
        para_range=(start_para, len(doc.paragraphs)),
        styles=styles,
        outline_levels=outline_levels,
    )
    if not candidates:
        return {
            "changed": [],
            "created_numId": None,
            "start_at": start_at,
            "dry_run": dry_run,
            "multilevel_link": multilevel_link,
            "linked_levels": linked_levels or [],
        }

    first = candidates[0]
    effective = first["effective_numpr"]
    if not effective:
        return {
            "changed": [],
            "created_numId": None,
            "start_at": start_at,
            "dry_run": dry_run,
            "multilevel_link": multilevel_link,
            "linked_levels": linked_levels or [],
        }

    base_num_id = effective["numId"]
    target_ilvl = effective["ilvl"]
    allowed_ilvls = {str(target_ilvl)}
    if multilevel_link and linked_levels:
        allowed_ilvls.update(str(level) for level in linked_levels)

    filtered = [
        item for item in candidates
        if item["numId"] == base_num_id and item["ilvl"] in allowed_ilvls
    ]

    new_num_id = None if dry_run else _clone_num_with_start_overrides(doc, base_num_id, list(allowed_ilvls), start_at)

    changed = []
    for item in filtered:
        changed.append({
            "index": item["index"],
            "style": item["style"],
            "text": item["text"],
            "old_numId": item["numId"],
            "old_ilvl": item["ilvl"],
            "new_numId": new_num_id if not dry_run else f"(new from {base_num_id})",
            "new_ilvl": item["ilvl"],
        })
        if not dry_run:
            _set_numpr(doc.paragraphs[item["index"]], new_num_id, item["ilvl"])

    return {
        "changed": changed,
        "created_numId": new_num_id,
        "start_at": start_at,
        "dry_run": dry_run,
        "multilevel_link": multilevel_link,
        "linked_levels": sorted(int(x) for x in allowed_ilvls if x != str(target_ilvl)),
        "target_ilvl": target_ilvl,
    }


def find_section_scope(doc, heading_para: int) -> tuple[int, int]:
    paras = doc.paragraphs
    if heading_para < 0 or heading_para >= len(paras):
        raise IndexError(f"heading para out of range: {heading_para}")

    start_outline = _para_outline_level_user(paras[heading_para])
    if start_outline is None:
        return heading_para, len(paras)

    for idx in range(heading_para + 1, len(paras)):
        level = _para_outline_level_user(paras[idx])
        if level is not None and level <= start_outline:
            return heading_para, idx
    return heading_para, len(paras)
