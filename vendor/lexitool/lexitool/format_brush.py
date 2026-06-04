"""
format_brush.py — 格式刷

解决 TC INS 注入后新段落丢失缩进、段间距、样式的问题。
从参考段落复制 w:pPr 子元素到目标段落，支持选择性复制。
"""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from typing import Sequence

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

# 支持复制的 pPr 子元素标签
_COPY_MAP = {
    "indent":   qn("w:ind"),        # 缩进
    "spacing":  qn("w:spacing"),    # 段间距
    "style":    qn("w:pStyle"),     # 段落样式
    "numPr":    qn("w:numPr"),      # 编号/列表
    "jc":       qn("w:jc"),         # 对齐方式
    "outlineLvl": qn("w:outlineLvl"), # 大纲级别
}

_RUN_COPY_KEYS = {"font", "font-size", "run-props"}


def _ensure_pPr(para_el):
    pPr = para_el.find(qn("w:pPr"))
    if pPr is None:
        pPr = OxmlElement("w:pPr")
        para_el.insert(0, pPr)
    return pPr


def _style_name(para) -> str:
    style = getattr(para, "style", None)
    return style.name if style is not None else "Normal"


# --------------------------------------------------------------------------- #
# 主接口                                                                        #
# --------------------------------------------------------------------------- #

def _get_para_jc(para) -> str | None:
    """读取段落当前 w:jc 值（如 'center'/'both'），None 表示未显式设置。"""
    pPr = para._element.find(qn("w:pPr"))
    if pPr is None:
        return None
    jc_el = pPr.find(qn("w:jc"))
    if jc_el is None:
        return None
    return jc_el.get(qn("w:val"))


def _first_run_rPr(para):
    """返回段落第一个 run 的 rPr（deepcopy），没有则返回 None。"""
    for run_el in para._element.iter(qn("w:r")):
        rPr = run_el.find(qn("w:rPr"))
        if rPr is not None:
            return deepcopy(rPr)
    return None


def _copy_font_subset(base_rPr, target_rPr, copy: list[str]) -> bool:
    changed = False

    if "font" in copy:
        src = base_rPr.find(qn("w:rFonts"))
        if src is not None:
            existing = target_rPr.find(qn("w:rFonts"))
            if existing is None or _el_to_str(existing) != _el_to_str(src):
                if existing is not None:
                    target_rPr.remove(existing)
                target_rPr.append(deepcopy(src))
                changed = True

    if "font-size" in copy:
        for tag in (qn("w:sz"), qn("w:szCs")):
            src = base_rPr.find(tag)
            if src is None:
                continue
            existing = target_rPr.find(tag)
            if existing is None or _el_to_str(existing) != _el_to_str(src):
                if existing is not None:
                    target_rPr.remove(existing)
                target_rPr.append(deepcopy(src))
                changed = True

    return changed


def _clone_run_rPr_subset(base_rPr, copy: list[str]):
    """按 copy 子集提取 run 级格式，返回新的 w:rPr；无内容则返回 None。"""
    if base_rPr is None:
        return None

    if "run-props" in copy:
        return deepcopy(base_rPr)

    new_rPr = OxmlElement("w:rPr")
    _copy_font_subset(base_rPr, new_rPr, copy)
    return new_rPr if len(new_rPr) else None


def _apply_run_props(para, run_rPr, copy: list[str]) -> bool:
    """将指定 rPr 子集应用到目标段所有 run。"""
    if run_rPr is None:
        return False

    changed = False
    for run_el in para._element.iter(qn("w:r")):
        existing = run_el.find(qn("w:rPr"))
        if "run-props" in copy:
            if existing is not None and _el_to_str(existing) == _el_to_str(run_rPr):
                continue
            if existing is not None:
                run_el.remove(existing)
            run_el.insert(0, deepcopy(run_rPr))
            changed = True
            continue

        if existing is None:
            existing = OxmlElement("w:rPr")
            run_el.insert(0, existing)
        if _copy_font_subset(run_rPr, existing, copy):
            changed = True
    return changed


def _validate_doc_package(doc) -> None:
    """将文档写入内存并做 zip 完整性校验；失败时抛异常。"""
    import zipfile

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        bad_member = zf.testzip()
        if bad_member is not None:
            raise zipfile.BadZipFile(f"corrupt member: {bad_member}")


def _apply_one(
    paras,
    ref_pPr,
    ref_run_rPr,
    idx: int,
    copy: list[str],
    skip_if_jc: str | None,
) -> dict:
    if idx < 0 or idx >= len(paras):
        return {"changed": False, "applied": [], "skipped": "out_of_range"}
    tgt_para = paras[idx]
    if skip_if_jc is not None and _get_para_jc(tgt_para) == skip_if_jc:
        return {"changed": False, "applied": [], "skipped": f"skip_if_jc={skip_if_jc}"}
    tgt_pPr = _ensure_pPr(tgt_para._element)
    run_copy = [attr for attr in copy if attr in _RUN_COPY_KEYS]
    para_copy = [attr for attr in copy if attr in _COPY_MAP]

    changed = False
    applied: list[str] = []
    for attr in para_copy:
        tag = _COPY_MAP.get(attr)
        if tag is None:
            continue
        src_child = ref_pPr.find(tag)
        if src_child is None:
            continue
        existing = tgt_pPr.find(tag)
        src_xml = _el_to_str(src_child)
        if existing is not None and _el_to_str(existing) == src_xml:
            continue
        if existing is not None:
            tgt_pPr.remove(existing)
        tgt_pPr.append(deepcopy(src_child))
        changed = True
        applied.append(attr)

    if run_copy:
        run_rPr = _clone_run_rPr_subset(ref_run_rPr, run_copy)
        if _apply_run_props(tgt_para, run_rPr, run_copy):
            changed = True
            applied.extend(run_copy)

    return {"changed": changed, "applied": applied, "skipped": None}



def apply(
    doc,
    target_indices: Sequence[int],
    reference_index: int,
    copy: list[str] | None = None,
    skip_if_jc: str | None = None,
    safe: bool = False,
    validate_each: bool = False,
) -> dict:
    """
    从 reference_index 段落复制格式到 target_indices 中的各段落。

    Args:
        doc:              python-docx Document 对象
        target_indices:   需要修复格式的段落索引列表
        reference_index:  格式正确的参考段落索引
        copy:             选择性复制，支持 "indent" / "spacing" / "style" /
                          "numPr" / "jc" / "outlineLvl" /
                          "font" / "font-size" / "run-props"
                          默认 ["indent", "spacing"]
        skip_if_jc:       跳过当前 w:jc 等于此值的段落，如 "center"（防止误覆盖封面标题等）
        safe:             True 时仅复制段落级低风险属性（indent/spacing/jc/outlineLvl），
                          自动跳过 style/numPr 等更激进的复制项
        validate_each:    True 时每处理一个 target 都执行一次内存 zip 校验，
                          失败时返回触发段落索引，便于定位损坏源

    Returns:
        validate_each=False → 实际修改的段落索引列表
        validate_each=True  → {"modified": [...], "failed_at": int|None, "error": str|None}
    """
    if copy is None:
        copy = ["indent", "spacing"]

    if safe:
        safe_copy = {"indent", "spacing", "jc", "outlineLvl"}
        copy = [attr for attr in copy if attr in safe_copy]

    paras = doc.paragraphs
    ref_para = paras[reference_index]
    ref_pPr = ref_para._element.find(qn("w:pPr"))
    ref_run_rPr = _first_run_rPr(ref_para)

    if ref_pPr is None and ref_run_rPr is None:
        return {
            "modified": [],
            "details": [],
            "failed_at": None,
            "error": None,
            "effective_copy": copy,
        }

    modified = []
    details = []
    format_warnings = []
    try:
        from .tc_utils import assess_para_format_context
        style_rPr_map = extract_style_rPr_map(doc)
    except Exception:
        assess_para_format_context = None
        style_rPr_map = None
    for idx in target_indices:
        result = _apply_one(paras, ref_pPr, ref_run_rPr, idx, copy, skip_if_jc)
        warn_bundle = []
        if assess_para_format_context and result["changed"]:
            if any(a in result["applied"] for a in ("font", "font-size", "run-props")):
                first_run_rpr = _first_run_rPr(paras[idx])
                ctx = assess_para_format_context(paras[idx]._element, first_run_rpr, style_rPr_map=style_rPr_map)
                if ctx.get("warnings") or ctx.get("assumptions"):
                    warn_bundle.append(ctx)
        details.append({
            "index": idx,
            "applied": result["applied"],
            "skipped": result["skipped"],
            "format_context": warn_bundle,
        })
        if warn_bundle:
            format_warnings.append({"index": idx, "contexts": warn_bundle})
        if not result["changed"]:
            continue
        modified.append(idx)
        if validate_each:
            try:
                _validate_doc_package(doc)
            except Exception as e:
                return {
                    "modified": modified[:-1],
                    "details": details,
                    "failed_at": idx,
                    "error": str(e),
                    "effective_copy": copy,
                }

    return {
        "modified": modified,
        "details": details,
        "failed_at": None,
        "error": None,
        "effective_copy": copy,
        "format_warnings": format_warnings,
    }


def auto_fix(
    doc,
    para_range: tuple[int, int] | None = None,
    template_doc=None,
    copy: list[str] | None = None,
) -> list[int]:
    """
    按 style name 自动匹配参考段落，批量修复缩进/间距。

    逻辑：
    1. 扫描 template_doc（或文档本身 para_range 之外的段落）建立
       style_name → pPr 的参考映射（取每种样式的第一个有 pPr 的段落）
    2. 对 para_range 内的每个段落，用其 style name 查参考，
       将 indent / spacing 对齐到参考值

    Args:
        doc:          python-docx Document 对象
        para_range:   需要修复的段落范围 (start, end)，默认处理全文
        template_doc: 可选，从另一个文档提取参考样式
        copy:         默认 ["indent", "spacing"]（auto_fix 不改 style 本身）

    Returns:
        实际修改的段落索引列表
    """
    if copy is None:
        copy = ["indent", "spacing"]

    paras = doc.paragraphs
    start, end = para_range if para_range else (0, len(paras))

    # 建立 style → 参考 pPr 映射
    style_refs: dict[str, object] = {}
    source_paras = template_doc.paragraphs if template_doc else paras

    for para in source_paras:
        style_name = _style_name(para)
        if style_name not in style_refs:
            pPr = para._element.find(qn("w:pPr"))
            if pPr is not None:
                # 确保至少有 ind 或 spacing 可用
                has_useful = any(
                    pPr.find(qn(f"w:{t}")) is not None
                    for t in ("ind", "spacing")
                )
                if has_useful:
                    style_refs[style_name] = pPr

    modified = []
    for i in range(start, min(end, len(paras))):
        para = paras[i]
        style_name = _style_name(para)
        ref_pPr = style_refs.get(style_name)
        if ref_pPr is None:
            continue

        tgt_pPr = _ensure_pPr(para._element)
        changed = False
        for attr in copy:
            tag = _COPY_MAP.get(attr)
            if tag is None:
                continue
            src_child = ref_pPr.find(tag)
            if src_child is None:
                continue
            existing = tgt_pPr.find(tag)
            # 比较 XML 文本，只有不一致才修改
            src_xml = _el_to_str(src_child)
            if existing is not None and _el_to_str(existing) == src_xml:
                continue
            if existing is not None:
                tgt_pPr.remove(existing)
            tgt_pPr.append(deepcopy(src_child))
            changed = True

        if changed:
            modified.append(i)

    return modified


# --------------------------------------------------------------------------- #
# 辅助                                                                          #
# --------------------------------------------------------------------------- #

def get_pPr_summary(doc, para_range: tuple[int, int] | None = None) -> list[dict]:
    """
    调试用：返回段落范围内每段的 style / ind / spacing 摘要。
    方便确认哪些段落格式不一致。
    """
    paras = doc.paragraphs
    start, end = para_range if para_range else (0, len(paras))
    result = []
    for i in range(start, min(end, len(paras))):
        para = paras[i]
        pPr = para._element.find(qn("w:pPr"))
        ind = pPr.find(qn("w:ind")) if pPr is not None else None
        spacing = pPr.find(qn("w:spacing")) if pPr is not None else None
        result.append({
            "index": i,
            "style": _style_name(para),
            "text_preview": para.text[:40],
            "ind_left": ind.get(qn("w:left")) if ind is not None else None,
            "ind_hanging": ind.get(qn("w:hanging")) if ind is not None else None,
            "spacing_before": spacing.get(qn("w:before")) if spacing is not None else None,
            "spacing_after": spacing.get(qn("w:after")) if spacing is not None else None,
        })
    return result


def _el_to_str(el) -> str:
    """将 lxml element 序列化为字符串，用于比较是否相同。"""
    from lxml import etree
    return etree.tostring(el, encoding="unicode")


def set_outline_level(
    doc,
    target_indices: Sequence[int],
    level: int | None,
) -> list[int]:
    """
    直接设置段落的 w:outlineLvl 值。

    Args:
        doc:            python-docx Document 对象
        target_indices: 目标段落索引列表
        level:          1–9（对应 OOXML 0–8），或 None / 0 = 清除（变为正文级别）

    Returns:
        实际修改的段落索引列表

    说明：
        - w:outlineLvl 独立于 Heading 1/2/3 样式，控制 Word 导航窗格中的大纲层级
        - val=0 → 大纲1级，val=8 → 大纲9级，val=9 / 无此元素 → 正文（不出现在大纲）
        - 常见用途：自定义标题样式希望出现在导航窗格时，手动设置此值
    """
    tag = qn("w:outlineLvl")
    paras = doc.paragraphs

    # 转换用户级别（1-9）到 OOXML 值（0-8），None/0 表示清除
    if level is None or level <= 0:
        ooxml_val = None   # 清除模式
    else:
        ooxml_val = str(min(level - 1, 8))

    modified = []
    for idx in target_indices:
        if idx < 0 or idx >= len(paras):
            continue
        para = paras[idx]
        pPr = _ensure_pPr(para._element)
        existing = pPr.find(tag)

        if ooxml_val is None:
            # 清除大纲级别
            if existing is not None:
                pPr.remove(existing)
                modified.append(idx)
        else:
            if existing is not None:
                if existing.get(qn("w:val")) == ooxml_val:
                    continue   # 已是目标值，跳过
                pPr.remove(existing)
            from lxml import etree
            el = etree.SubElement(pPr, tag)
            el.set(qn("w:val"), ooxml_val)
            modified.append(idx)

    return modified


def extract_style_rPr_map(doc) -> dict:
    """提取每个 style 的 w:rPr；兼容 python-docx 与 OpenXML wrapper。"""
    result: dict = {}
    if hasattr(doc, "styles"):
        for style in doc.styles:
            rPr = style._element.find(qn("w:rPr"))
            if rPr is not None:
                result[style.name] = rPr
        return result

    rels = getattr(getattr(doc, "part", None), "rels", {}) or {}
    for rel in rels.values():
        reltype = getattr(rel, "reltype", "") or ""
        if not reltype.endswith("/styles") or getattr(rel, "target_part", None) is None:
            continue
        root = rel.target_part._element
        for style_el in root.findall(qn("w:style")):
            name_el = style_el.find(qn("w:name"))
            style_id = style_el.get(qn("w:styleId"), "")
            name = name_el.get(qn("w:val"), style_id) if name_el is not None else style_id
            rPr = style_el.find(qn("w:rPr"))
            if rPr is not None and name:
                result[name] = rPr
        break
    return result
