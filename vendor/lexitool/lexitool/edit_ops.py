"""
edit_ops.py — 纯 OpenXML 基础编辑操作（insert / replace / delete），全程无 python-docx。

两种模式：
1. 直接编辑（默认）：直接在 document.xml 中修改 w:t 文本
2. Track Changes 模式（--tc）：通过 <w:ins> / <w:del> 注入修订

设计原则：
- 所有操作以 paragraph index 作为定位坐标（与 python-docx doc.paragraphs 语义一致）
- 任何操作都不依赖 python-docx 库，仅使用 zipfile + lxml
- TC 模式复用 tc_utils 的底层 XML 构造，但对 docx 的读写操作由本模块直接接管
"""
from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from lxml import etree

from .openxml_runmap import editable_touched_spans, render_paragraph, replace_span, text_in_span

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

# ── Docx 读取/写入基础 ──────────────────────────────────────────────────────

def _read_docx(path: str) -> tuple[bytes, dict[str, bytes]]:
    """读入 docx，返回 (document_xml_bytes, other_files_dict)。"""
    with zipfile.ZipFile(path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {}
        for name in zf.namelist():
            if name != "word/document.xml":
                other[name] = zf.read(name)
    return doc_xml, other


def _write_docx(path: str, doc_xml: bytes, other: dict[str, bytes],
                output: str | None = None) -> None:
    """写回 docx。修复：确保 XML 声明与 Word 完全兼容（双引号 standalone）。"""
    out_path = output or path
    fd, tmp = tempfile.mkstemp(prefix="lex_docx_edit.", suffix=".docx")
    import os as _os
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # OOXML requires [Content_Types].xml as the first ZIP entry
        ct_xml = other.get("[Content_Types].xml")
        if ct_xml is not None:
            zf.writestr("[Content_Types].xml", ct_xml)
        zf.writestr("word/document.xml", doc_xml)
        for name, data in other.items():
            if name == "[Content_Types].xml":
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)


# ── 段落定位 ─────────────────────────────────────────────────────────────────

def _find_para(root: etree._Element, para_idx: int) -> etree._Element | None:
    """按 paragraph index（与 python-docx 的 doc.paragraphs 语义一致）找到 w:p 元素。"""
    count = 0
    for el in root.iter():
        if el.tag == f"{W}p":
            if count == para_idx:
                return el
            count += 1
    return None


def _get_para_text(para: etree._Element) -> str:
    """获取段落纯文本，将 <w:tab/> 转为 \\t。"""
    parts = []
    for child in para.iter():
        if child.tag == f"{W}t":
            parts.append(child.text or "")
        elif child.tag == f"{W}tab":
            parts.append("\t")
    return "".join(parts)


def _find_runs(para: etree._Element) -> list[etree._Element]:
    """获取段落中所有 w:r 元素。"""
    return [child for child in para if child.tag == f"{W}r"]


def _make_run(text: str, bold: bool = False, italic: bool = False,
              font: str = "宋体", sz: float = 22.0) -> etree._Element:
    """构造一个 w:r 元素。"""
    r = etree.Element(f"{W}r")
    rPr = etree.SubElement(r, f"{W}rPr")
    rFonts = etree.SubElement(rPr, f"{W}rFonts")
    rFonts.set(f"{W}ascii", font)
    rFonts.set(f"{W}hAnsi", font)
    rFonts.set(f"{W}eastAsia", font)
    etree.SubElement(rPr, f"{W}sz").set(f"{W}val", str(int(sz)))
    etree.SubElement(rPr, f"{W}szCs").set(f"{W}val", str(int(sz)))
    if bold:
        etree.SubElement(rPr, f"{W}b")
    if italic:
        etree.SubElement(rPr, f"{W}i")
    t = etree.SubElement(r, f"{W}t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    return r


def _make_text_run_from_rpr(text: str, base_rPr=None, *, deleted: bool = False) -> etree._Element:
    r = etree.Element(f"{W}r")
    if base_rPr is not None:
        r.append(copy.deepcopy(base_rPr))
    t = etree.SubElement(r, f"{W}delText" if deleted else f"{W}t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    return r


def _first_run_rpr(para: etree._Element):
    for run in para.iter(f"{W}r"):
        rPr = run.find(f"{W}rPr")
        if rPr is not None:
            return rPr
    pPr = para.find(f"{W}pPr")
    return pPr.find(f"{W}rPr") if pPr is not None else None


def _clone_inheritable_ppr(source_para: etree._Element) -> etree._Element:
    """Clone paragraph formatting that is safe for inserted body text.

    Numbering and section properties are intentionally not inherited by default:
    legal drafting often inserts explanatory paragraphs after numbered clauses,
    and copying numPr/sectPr would silently change document structure.
    """
    pPr = etree.Element(f"{W}pPr")
    src_pPr = source_para.find(f"{W}pPr")
    if src_pPr is None:
        return pPr
    for child in src_pPr:
        local = etree.QName(child).localname
        if local in {
            "pStyle", "ind", "spacing", "jc", "tabs", "keepNext",
            "keepLines", "pageBreakBefore", "widowControl", "outlineLvl",
            "contextualSpacing", "bidi", "rPr",
        }:
            pPr.append(copy.deepcopy(child))
    return pPr


def _ensure_child(parent: etree._Element, tag: str) -> etree._Element:
    child = parent.find(tag)
    if child is None:
        child = etree.SubElement(parent, tag)
    return child


def _set_pstyle(pPr: etree._Element, style: str | None) -> None:
    if not style:
        return
    pStyle = _ensure_child(pPr, f"{W}pStyle")
    pStyle.set(f"{W}val", str(style))


def _parse_size_half_points(value) -> str:
    if value is None:
        raise ValueError("size is required")
    if isinstance(value, (int, float)):
        numeric = float(value)
        # Values above 80 are almost certainly already half-points.
        half_points = numeric if numeric > 80 else numeric * 2
    else:
        text = str(value).strip().lower()
        if text.endswith("pt"):
            half_points = float(text[:-2].strip()) * 2
        else:
            numeric = float(text)
            half_points = numeric if numeric > 80 else numeric * 2
    return str(int(round(half_points)))


def _parse_indent_twips(value) -> str:
    if value is None:
        raise ValueError("indent is required")
    if isinstance(value, (int, float)):
        return str(int(round(float(value) * 240)))
    text = str(value).strip().lower()
    if text.endswith("ch"):
        return str(int(round(float(text[:-2].strip()) * 240)))
    if text.endswith("cm"):
        return str(int(round(float(text[:-2].strip()) * 567)))
    if text.endswith("in"):
        return str(int(round(float(text[:-2].strip()) * 1440)))
    if text.endswith("pt"):
        return str(int(round(float(text[:-2].strip()) * 20)))
    return str(int(round(float(text) * 240)))


def _apply_insert_format(pPr: etree._Element, rPr: etree._Element, fmt: dict | None) -> None:
    if not fmt:
        return

    _set_pstyle(pPr, fmt.get("style") or fmt.get("style_id"))

    if fmt.get("align"):
        jc = _ensure_child(pPr, f"{W}jc")
        jc.set(f"{W}val", str(fmt["align"]))

    if fmt.get("spacing"):
        spacing = _ensure_child(pPr, f"{W}spacing")
        spacing.set(f"{W}line", str(int(float(fmt["spacing"]) * 240)))
        spacing.set(f"{W}lineRule", "auto")

    if fmt.get("indent") is not None:
        ind = _ensure_child(pPr, f"{W}ind")
        ind.set(f"{W}firstLine", _parse_indent_twips(fmt["indent"]))

    font_name = fmt.get("font")
    if font_name:
        rFonts = _ensure_child(rPr, f"{W}rFonts")
        rFonts.set(f"{W}ascii", str(font_name))
        rFonts.set(f"{W}hAnsi", str(font_name))
        rFonts.set(f"{W}eastAsia", str(font_name))

    if fmt.get("size") is not None:
        sz_val = _parse_size_half_points(fmt["size"])
        _ensure_child(rPr, f"{W}sz").set(f"{W}val", sz_val)
        _ensure_child(rPr, f"{W}szCs").set(f"{W}val", sz_val)

    if fmt.get("color"):
        _ensure_child(rPr, f"{W}color").set(f"{W}val", str(fmt["color"]).lstrip("#"))

    if fmt.get("highlight"):
        _ensure_child(rPr, f"{W}highlight").set(f"{W}val", str(fmt["highlight"]))


def _strip_review_visuals(rPr: etree._Element, *, keep: set[str] | None = None) -> None:
    keep = keep or set()
    for local in ("highlight", "color"):
        if local in keep:
            continue
        child = rPr.find(f"{W}{local}")
        if child is not None:
            rPr.remove(child)


def _tc_tag(tag: str, tc_id: int, author: str, date: str) -> etree._Element:
    el = etree.Element(f"{W}{tag}")
    el.set(f"{W}id", str(tc_id))
    el.set(f"{W}author", author)
    el.set(f"{W}date", date)
    return el


def _replace_text_with_tc_precise(
    para: etree._Element,
    start: int,
    end: int,
    new: str,
    *,
    tc_id: int,
    author: str,
    date: str,
) -> bool:
    rendered = render_paragraph(para)
    touched = editable_touched_spans(rendered, start, end)
    if not touched:
        return False

    runs: list[etree._Element] = []
    for span in touched:
        run = span.run
        if run is not None and run not in runs:
            runs.append(run)
    if not runs:
        return False

    first_run = runs[0]
    parent = first_run.getparent()
    if parent is None:
        return False
    if any(run.getparent() is not parent for run in runs):
        return False
    insert_idx = list(parent).index(first_run)

    new_elems: list[etree._Element] = []
    for span in touched:
        run = span.run
        if run is None:
            return False
        rPr = run.find(f"{W}rPr")
        before = span.text[: max(0, start - span.start)] if span is touched[0] else ""
        middle = text_in_span(span, start, end)
        after = span.text[max(0, end - span.start):] if span is touched[-1] else ""

        if before:
            new_elems.append(_make_text_run_from_rpr(before, rPr))
        if middle:
            del_el = _tc_tag("del", tc_id, author, date)
            del_el.append(_make_text_run_from_rpr(middle, rPr, deleted=True))
            new_elems.append(del_el)
            tc_id += 1
        if span is touched[-1] and new:
            ins_el = _tc_tag("ins", tc_id, author, date)
            ins_el.append(_make_text_run_from_rpr(new, rPr))
            new_elems.append(ins_el)
            tc_id += 1
        if after:
            new_elems.append(_make_text_run_from_rpr(after, rPr))

    for run in runs:
        run_parent = run.getparent()
        if run_parent is parent:
            parent.remove(run)
    for offset, elem in enumerate(new_elems):
        parent.insert(insert_idx + offset, elem)
    return True


def _inject_tc_ins(root: etree._Element, para: etree._Element,
                   text: str, author: str = "agent",
                   tc_id: int | None = None) -> None:
    """在段落末尾注入 <w:ins> 修订插入。"""
    if tc_id is None:
        tc_id = _next_tc_id(root)
    ins = etree.Element(f"{W}ins")
    ins.set(f"{W}id", str(tc_id))
    ins.set(f"{W}author", author)
    from datetime import datetime
    ins.set(f"{W}date", datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    ins.append(_make_run(text))
    para.append(ins)


def _inject_tc_del(root: etree._Element, para: etree._Element,
                   old_text: str, author: str = "agent",
                   tc_id: int | None = None) -> None:
    """将段落中指定的旧文本包裹为 <w:del> 修订删除。"""
    if tc_id is None:
        tc_id = _next_tc_id(root)
    # 找到包含旧文本的 w:t 并创建删除标记
    from datetime import datetime
    del_el = etree.Element(f"{W}del")
    del_el.set(f"{W}id", str(tc_id))
    del_el.set(f"{W}author", author)
    del_el.set(f"{W}date", datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    run = _make_run(old_text)
    # 将 w:t 改为 w:delText
    for t in run.iter(f"{W}t"):
        t.tag = f"{W}delText"
    del_el.append(run)
    para.append(del_el)


def _next_tc_id(root: etree._Element) -> int:
    """找到文档中最大 w:id 值 + 1（扫描所有元素，防止与 bookmark/footnote 等已有 id 冲突）。"""
    max_id = 0
    for el in root.iter():
        val = el.get(f"{{{W_NS}}}id")
        if val:
            try:
                max_id = max(max_id, int(val))
            except ValueError:
                pass
    return max_id + 1


# ── Public API（底层 OpenXML 直写） ──────────────────────────────────────────

@dataclass
class EditResult:
    """编辑操作的结果。"""
    ok: bool
    message: str = ""
    para: int | None = None
    text: str = ""
    tc_mode: bool = False
    tc_applied: bool = False
    tc_id: int | None = None
    path: str = ""


def insert_text(docx_path: str, para: int, text: str, *,
                tc: bool = False,
                author: str = "agent",
                bold: bool = False, italic: bool = False,
                font: str = "宋体", font_size: float = 11.0,
                inherit_format: bool = True,
                output: str | None = None) -> EditResult:
    """
    在指定段落末尾插入文字。

    tc=False（默认）：直接插入 w:r 文本（无修订标记）
    tc=True：通过 <w:ins> 注入 Track Changes

    当 inherit_format=True（默认）时，从段落继承字体的 rPr，
    除非调用者显式传入了 font / font_size / bold / italic。
    """
    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)
    p = _find_para(root, para)
    if p is None:
        return EditResult(ok=False, message=f"段落 {para} 不存在", path=docx_path)

    sz = float(font_size) * 2  # half-points

    # Resolve formatting: inherit from paragraph when no explicit overrides
    base_rPr = _first_run_rpr(p) if inherit_format else None

    if base_rPr is not None and font == "宋体" and font_size == 11.0 and not bold and not italic:
        # Full inheritance — no explicit overrides
        run = _make_text_run_from_rpr(text, base_rPr)
    elif base_rPr is not None:
        # Partial inheritance — explicit overrides applied on top of clone
        rPr = copy.deepcopy(base_rPr)
        rFonts = rPr.find(f"{W}rFonts")
        if rFonts is None:
            rFonts = etree.SubElement(rPr, f"{W}rFonts")
        if font != "宋体":
            rFonts.set(f"{W}ascii", font)
            rFonts.set(f"{W}hAnsi", font)
            rFonts.set(f"{W}eastAsia", font)
        sz_el = rPr.find(f"{W}sz")
        if sz_el is None:
            sz_el = etree.SubElement(rPr, f"{W}sz")
        sz_el.set(f"{W}val", str(int(sz)))
        szCs_el = rPr.find(f"{W}szCs")
        if szCs_el is None:
            szCs_el = etree.SubElement(rPr, f"{W}szCs")
        szCs_el.set(f"{W}val", str(int(sz)))
        if bold:
            if rPr.find(f"{W}b") is None:
                etree.SubElement(rPr, f"{W}b")
        if italic:
            if rPr.find(f"{W}i") is None:
                etree.SubElement(rPr, f"{W}i")
        run = _make_text_run_from_rpr(text, rPr)
    else:
        run = _make_run(text, bold=bold, italic=italic, font=font, sz=sz)

    if tc:
        tid = _next_tc_id(root)
        from datetime import datetime
        ins = etree.Element(f"{W}ins")
        ins.set(f"{W}id", str(tid))
        ins.set(f"{W}author", author)
        ins.set(f"{W}date", datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        ins.append(run)
        p.append(ins)
        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=text, tc_mode=True, tc_applied=True, tc_id=tid,
                          message=f"TC 插入段落 {para} 完成（id={tid}）",
                          path=output or docx_path)
    else:
        p.append(run)
        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=text, tc_mode=False, tc_applied=False,
                          message=f"直接插入段落 {para} 完成",
                          path=output or docx_path)


def replace_text(docx_path: str, para: int, old: str, new: str, *,
                 tc: bool = False,
                 author: str = "agent",
                 bold: bool = False, italic: bool = False,
                 font: str = "宋体", font_size: float = 11.0,
                 output: str | None = None) -> EditResult:
    """
    在指定段落中替换文字（默认第一个匹配）。

    tc=False（默认）：直接替换 w:t 中的文本
    tc=True：旧文本包为 <w:del>，新文本注入 <w:ins>
    """
    if not old or not isinstance(old, str) or not old.strip():
        return EditResult(ok=False, message="'old' text is required for replace; use delete_paragraph_tc to delete a paragraph", path=docx_path)
    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)
    p = _find_para(root, para)
    if p is None:
        return EditResult(ok=False, message=f"段落 {para} 不存在", path=docx_path)

    sz = float(font_size) * 2
    tid = _next_tc_id(root)

    if tc:
        dt = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        rendered = render_paragraph(p)
        located = _locate_replacement_span(rendered.text, old)
        if located is None:
            return EditResult(ok=False, para=para, text=old,
                              message=f"段落 {para} 中未找到 '{old}'",
                              path=docx_path)

        pos, end_pos, actual_old = located
        if not _replace_text_with_tc_precise(
            p,
            pos,
            end_pos,
            new,
            tc_id=tid,
            author=author,
            date=dt,
        ):
            return EditResult(ok=False, para=para, text=old,
                              message=f"段落 {para} 中未找到 '{old}' 的文本边界",
                              path=docx_path)

        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=f"{actual_old}→{new}", tc_mode=True, tc_applied=True,
                          tc_id=tid, message=f"TC 替换段落 {para}：{old}→{new}",
                          path=output or docx_path)
    else:
        rendered = render_paragraph(p)

        located = _locate_replacement_span(rendered.text, old)
        if located is None:
            return EditResult(ok=False, para=para, text=old,
                              message=f"段落 {para} 中未找到 '{old}'",
                              path=docx_path)

        pos, end_pos, actual_old = located
        if not replace_span(rendered, pos, end_pos, new):
            return EditResult(ok=False, para=para, text=old,
                              message=f"段落 {para} 中未找到 '{old}' 的文本边界",
                              path=docx_path)

        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=f"{actual_old}→{new}", tc_mode=False, tc_applied=False,
                          message=f"直接替换段落 {para}：{old}→{new}",
                          path=output or docx_path)


def replace_text_all(docx_path: str, targets: list[int], old: str, new: str, *,
                     tc: bool = True,
                     author: str = "agent",
                     font_size: float = 11.0,
                     output: str | None = None) -> dict:
    """
    Cross-paragraph batch replacement.  Finds `old` in each target paragraph
    and replaces it with `new`.  Returns per-paragraph results.

    targets: list of 0-based paragraph indices
    """
    results = []
    ok_count = 0
    for para_idx in targets:
        res = replace_text(docx_path, para_idx, old, new,
                          tc=tc, author=author, font_size=font_size,
                          output=output)
        results.append({
            "para": para_idx + 1,
            "ok": res.ok,
            "message": res.message,
            "tc_applied": res.tc_applied,
        })
        if res.ok:
            ok_count += 1
    return {
        "ok": ok_count == len(targets),
        "total": len(targets),
        "succeeded": ok_count,
        "failed": len(targets) - ok_count,
        "results": results,
        "message": f"Replaced in {ok_count}/{len(targets)} paragraphs",
    }


def delete_text(docx_path: str, para: int, *,
                text: str | None = None,
                tc: bool = False,
                author: str = "agent",
                output: str | None = None) -> EditResult:
    """
    删除段落中的文字。

    不指定 text（默认）：删除整段文本（保留空段落）
    指定 text：仅删除与该文本匹配的部分

    tc=False（默认）：直接清空 w:t 内容
    tc=True：内容包裹为 <w:del> 修订删除
    """
    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)
    p = _find_para(root, para)
    if p is None:
        return EditResult(ok=False, message=f"段落 {para} 不存在", path=docx_path)

    if tc:
        tid = _next_tc_id(root)
        from datetime import datetime
        dt = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        to_del = text or _get_para_text(p)

        del_el = etree.Element(f"{W}del")
        del_el.set(f"{W}id", str(tid))
        del_el.set(f"{W}author", author)
        del_el.set(f"{W}date", dt)
        d_run = _make_run(to_del)
        for t in d_run.iter(f"{W}t"):
            t.tag = f"{W}delText"
        del_el.append(d_run)
        p.append(del_el)

        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=to_del, tc_mode=True, tc_applied=True,
                          tc_id=tid, message=f"TC 删除段落 {para} 完成",
                          path=output or docx_path)
    else:
        if text:
            # 仅删除匹配文本（标准化引号匹配）
            for t in p.iter(f"{W}t"):
                if t.text:
                    t_norm = _normalize_quotes(t.text)
                    text_norm = _normalize_quotes(text)
                    if text_norm in t_norm:
                        pos = t_norm.find(text_norm)
                        t.text = t.text[:pos] + t.text[pos + len(text):]
                        break
        else:
            # 清空所有 w:t
            for t in p.iter(f"{W}t"):
                t.text = None
        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=text or "(全部)", tc_mode=False, tc_applied=False,
                          message=f"直接删除段落 {para} 完成",
                          path=output or docx_path)


def trim_paragraph(docx_path: str, para: int, *,
                   chars: int = 1,
                   from_end: bool = True,
                   output: str | None = None) -> EditResult:
    """
    删除段落开始或末尾的指定数量字符。

    chars: 要删除的字符数（默认 1）
    from_end: True=从末尾删除，False=从开头删除
    """
    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)
    p = _find_para(root, para)
    if p is None:
        return EditResult(ok=False, message=f"段落 {para} 不存在", path=docx_path)

    t_nodes = list(p.iter(f"{W}t"))
    if not t_nodes:
        return EditResult(ok=False, message=f"段落 {para} 无文本可删除", path=docx_path)

    full_text = _get_para_text(p)
    if len(full_text) < chars:
        return EditResult(ok=False, message=f"段落 {para} 仅有 {len(full_text)} 字符，无法删除 {chars} 字符", path=docx_path)

    if from_end:
        # 从末尾删除：从最后一个 w:t 开始向前删除字符
        remaining = chars
        for t in reversed(t_nodes):
            if t.text and remaining > 0:
                tlen = len(t.text)
                if tlen >= remaining:
                    t.text = t.text[:tlen - remaining]
                    break
                else:
                    remaining -= tlen
                    t.text = ""
    else:
        # 从开头删除：从第一个 w:t 开始向后删除字符
        remaining = chars
        for t in t_nodes:
            if t.text and remaining > 0:
                tlen = len(t.text)
                if tlen >= remaining:
                    t.text = t.text[remaining:]
                    break
                else:
                    remaining -= tlen
                    t.text = ""

    _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                          encoding="UTF-8", standalone=True),
                other, output=output)
    return EditResult(ok=True, para=para, tc_applied=False,
                      message=f"段落 {para} {'末尾' if from_end else '开头'}删除 {chars} 字符",
                      path=output or docx_path)


# ── 原地 TC 替换（保留原 run 格式、跨 run 支持） ─────────────────────────────


def _normalize_quotes(text: str) -> str:
    """将弯引号（Word自动弯引号）标准化为直引号以匹配搜索。"""
    return text.replace('\u201c', '\u0022').replace('\u201d', '\u0022').replace('\u2018', '\u0027').replace('\u2019', '\u0027')


def _locate_replacement_span(full_text: str, old: str) -> tuple[int, int, str] | None:
    """Locate replacement text with quote and whitespace tolerance.

    Returns (start, end, actual_text).  The span always indexes the original
    full_text, so callers can preserve exactly what Word contains in <w:del>.
    """
    if old in full_text:
        pos = full_text.find(old)
        return pos, pos + len(old), old

    full_norm = _normalize_quotes(full_text)
    old_norm = _normalize_quotes(old)
    if old_norm in full_norm:
        pos = full_norm.find(old_norm)
        return pos, pos + len(old_norm), full_text[pos:pos + len(old_norm)]

    stripped = (old or "").strip()
    if stripped and stripped != old:
        located = _locate_replacement_span(full_text, stripped)
        if located:
            return located

    if re.search(r"\s", old or ""):
        parts = [re.escape(_normalize_quotes(part)) for part in re.split(r"\s+", old_norm.strip()) if part]
        if parts:
            m = re.search(r"[\s\u00a0]+".join(parts), full_norm)
            if m:
                return m.start(), m.end(), full_text[m.start():m.end()]

    return None


def _locate_text_in_para(p: etree._Element, target: str) -> list[tuple]:
    """
    在段落中定位 target 文本的位置，返回 [(w:t_element, offset_start), ...]。
    跨多个 w:t 节点连续匹配。
    """
    # 获取所有 w:t 节点及其文本
    t_nodes = []
    for t in p.iter(f"{W}t"):
        if t.text:
            t_nodes.append(t)
    
    # 将所有文本拼起来找（标准化引号以匹配Word弯引号）
    raw_text = ''.join(t.text for t in t_nodes)
    full_text = _normalize_quotes(raw_text)
    target_norm = _normalize_quotes(target)
    idx = full_text.find(target_norm)
    if idx < 0:
        return []
    
    # 定位到具体的 w:t 节点
    result = []
    remaining = target
    char_pos = 0
    start_found = False
    
    for t in t_nodes:
        tlen = len(t.text)
        if not start_found:
            if char_pos + tlen > idx:
                # 这个 t 包含匹配起点
                offset = idx - char_pos
                if offset > 0:
                    # 有前缀文本，需要匹配从 offset 开始
                    take = min(tlen - offset, len(remaining))
                else:
                    take = min(tlen, len(remaining))
                result.append((t, offset, take))
                remaining = remaining[take:]
                start_found = True
                if not remaining:
                    break
            char_pos += tlen
        else:
            take = min(tlen, len(remaining))
            result.append((t, 0, take))
            remaining = remaining[take:]
            if not remaining:
                break
    
    return result if not remaining else []


def _get_run_of_t(t: etree._Element) -> etree._Element | None:
    """找到 w:t 的父级 w:r。"""
    parent = t.getparent()
    if parent is not None and parent.tag == f"{W}r":
        return parent
    return None


def replace_text_in_place(docx_path: str, para: int, old: str, new: str, *,
                          author: str = "agent",
                          font: str = "Times New Roman",
                          output: str | None = None) -> EditResult:
    """
    原地替换段落中的文本，保留原 run 的格式。支持跨 run 文本匹配。
    
    与 replace_text(tc=True) 不同：
    - replace_text 在段落末尾追加 <w:del> + <w:ins>
    - replace_text_in_place 在原 run 位置生成 <w:del>(旧run) + <w:ins>(新run)
    """
    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)
    p = _find_para(root, para)
    if p is None:
        return EditResult(ok=False, message=f"段落 {para} 不存在", path=docx_path)
    
    locations = _locate_text_in_para(p, old)
    if not locations:
        return EditResult(ok=False, para=para, text=old,
                          message=f"段落 {para} 中未找到 '{old}'",
                          path=docx_path)
    
    tid = _next_tc_id(root)
    from datetime import datetime
    dt = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # 先收集所有匹配的 run（保留格式用第一个 run 的 rPr）
    matched_run_els = []
    for t_el, offset, take in locations:
        run_el = _get_run_of_t(t_el)
        if run_el is not None:
            matched_run_els.append((run_el, t_el, offset, take))
    
    if not matched_run_els:
        return EditResult(ok=False, para=para, text=old,
                          message=f"段落 {para} 中匹配的 run 不存在",
                          path=docx_path)
    
    first_rPr = matched_run_els[0][0].find(f"{W}rPr")
    first_parent = matched_run_els[0][0].getparent()
    # 记录第一个 run_el 在父元素中的位置（用于之后插入 ins）
    first_pos = list(first_parent).index(matched_run_els[0][0])
    
    # 创建唯一的 <w:ins>（用第一个 run 的格式）
    ins = etree.Element(f"{W}ins")
    ins.set(f"{W}id", str(tid)); tid += 1
    ins.set(f"{W}author", author); ins.set(f"{W}date", dt)
    ir = etree.SubElement(ins, f"{W}r")
    if first_rPr is not None:
        from copy import deepcopy
        ir.append(deepcopy(first_rPr))
    it = etree.SubElement(ir, f"{W}t")
    it.text = new
    it.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    
    # 为每个匹配位置创建 <w:del>，从后往前处理
    for run_el, t_el, offset, take in reversed(matched_run_els):
        rPr = run_el.find(f"{W}rPr")
        old_text_part = t_el.text[offset:offset+take]
        
        d = etree.Element(f"{W}del")
        d.set(f"{W}id", str(tid)); tid += 1
        d.set(f"{W}author", author); d.set(f"{W}date", dt)
        dr = etree.SubElement(d, f"{W}r")
        if rPr is not None:
            from copy import deepcopy
            dr.append(deepcopy(rPr))
        dt_el = etree.SubElement(dr, f"{W}delText")
        dt_el.text = old_text_part
        dt_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        
        parent = run_el.getparent()
        pos = list(parent).index(run_el)
        parent.insert(pos, d)
        parent.remove(run_el)
    
    # 在第一个 del 的位置插入 <w:ins>
    first_parent.insert(first_pos, ins)
    
    _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                          encoding="UTF-8", standalone=True),
                other, output=output)
    return EditResult(ok=True, para=para, text=f"{old}→{new}", tc_mode=True, tc_applied=True,
                      tc_id=tid, message=f"原地 TC 替换段落 {para}：{old}→{new}",
                      path=output or docx_path)


# ── 段落级 TC 删除 ──────────────────────────────────────────────────────────


def delete_paragraph_tc(docx_path: str, para: int, *,
                        author: str = "agent",
                        output: str | None = None) -> EditResult:
    """
    整段标记为 TC 删除（包裹 <w:del>），保留段落框架。

    将段落 p 的所有内容包裹在 <w:del> 中，并添加 <w:del> 属性。
    段落本身保留（空段落），通过 TC 标记显示为删除。
    """
    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)
    p = _find_para(root, para)
    if p is None:
        return EditResult(ok=False, message=f"段落 {para} 不存在", path=docx_path)

    tid = _next_tc_id(root)
    from datetime import datetime
    dt = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    # 收集段落中所有直接子元素（非 pPr）
    pPr = p.find(f"{W}pPr")
    children = [c for c in list(p) if c != pPr]

    # 用 <w:del> 包裹每个子元素
    for child in reversed(children):
        d = etree.Element(f"{W}del")
        d.set(f"{W}id", str(tid)); tid += 1
        d.set(f"{W}author", author); d.set(f"{W}date", dt)
        p.remove(child)
        d.append(child)
        # 插入在 pPr 之后
        if pPr is not None:
            pPr.addnext(d)
        else:
            p.insert(0, d)

    _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                          encoding="UTF-8", standalone=True),
                other, output=output)
    return EditResult(ok=True, para=para, tc_mode=True, tc_applied=True, tc_id=tid,
                      message=f"段落 {para} 已标记为删除（TC）",
                      path=output or docx_path)


# ── Table helpers ──────────────────────────────────────────────────────────────

def _find_table(root: etree._Element, table_index: int) -> etree._Element | None:
	"""Find the Nth w:tbl element at body level (0-indexed)."""
	count = 0
	body = root.find(f"{W}body")
	if body is None:
		return None
	for el in body:
		if el.tag == f"{W}tbl":
			if count == table_index:
				return el
			count += 1
	return None


def _get_grid_span(cell: etree._Element) -> int:
	"""Return the gridSpan value for a cell (default 1)."""
	tcPr = cell.find(f"{W}tcPr")
	if tcPr is None:
		return 1
	grid_span = tcPr.find(f"{W}gridSpan")
	if grid_span is None:
		return 1
	try:
		return int(grid_span.get(f"{W}val", "1"))
	except (ValueError, TypeError):
		return 1


def _get_vmerge(cell: etree._Element) -> str | None:
	"""Return the vMerge value for a cell: 'restart', 'continue', or None."""
	tcPr = cell.find(f"{W}tcPr")
	if tcPr is None:
		return None
	vmerge = tcPr.find(f"{W}vMerge")
	if vmerge is None:
		return None
	return vmerge.get(f"{W}val", "continue") or "continue"


def _row_logical_cells(row: etree._Element) -> list[tuple[etree._Element, int, int]]:
	"""Return (cell_element, logical_start_col, grid_span) for each physical cell.

	This resolves the logical column positions accounting for horizontal
	merges (gridSpan).  A cell with gridSpan=3 occupies three logical
	columns; the next physical cell starts at logical column 3.
	"""
	physical = [el for el in row if el.tag == f"{W}tc"]
	logical = []
	col = 0
	for tc in physical:
		span = _get_grid_span(tc)
		logical.append((tc, col, span))
		col += span
	return logical


def _physical_cell_at_logical(row: etree._Element, logical_col: int) -> etree._Element | None:
	"""Return the physical w:tc covering *logical_col*, or None."""
	for tc, start, span in _row_logical_cells(row):
		if start <= logical_col < start + span:
			return tc
	return None


def _find_cell_by_text(table: etree._Element, text: str) -> etree._Element | None:
	"""Find the first w:tc in a table that contains the given text.

	Skips vMerge=continue placeholder cells (logically covered from above).
	"""
	text_norm = _normalize_quotes(text)
	for tc in table.iter(f"{W}tc"):
		if _get_vmerge(tc) == "continue":
			continue
		cell_text = _get_cell_text(tc)
		if text_norm in _normalize_quotes(cell_text):
			return tc
	return None


def _get_cell_text(tc: etree._Element) -> str:
	"""Get the plain text content of a w:tc element."""
	parts = []
	for el in tc.iter():
		if el.tag == f"{W}t":
			parts.append(el.text or "")
		elif el.tag == f"{W}tab":
			parts.append("\t")
	return "".join(parts)


def _set_cell_text(tc: etree._Element, new_text: str, bold: bool = False,
                   font: str = "宋体", sz: float = 22.0) -> None:
	"""Replace all text content in a w:tc with new_text."""
	# Remove all existing w:p elements and their contents
	for p in tc.findall(f"{W}p"):
		tc.remove(p)
	# Create a new w:p with the new text
	new_p = etree.SubElement(tc, f"{W}p")
	new_p.append(_make_run(new_text, bold=bold, font=font, sz=sz))


def _get_cell_paragraphs(tc: etree._Element) -> list[etree._Element]:
	"""Get all w:p elements inside a w:tc."""
	return tc.findall(f"{W}p")


def list_tables(docx_path: str, *, preview_rows: int = 3,
                max_cell_chars: int = 80) -> dict:
	"""List body-level tables with stable body order and short content previews."""
	preview_rows = max(int(preview_rows), 0)
	max_cell_chars = max(int(max_cell_chars), 1)
	doc_xml, _other = _read_docx(docx_path)
	root = etree.fromstring(doc_xml)
	body = root.find(f"{W}body")
	if body is None:
		return {"path": docx_path, "tables": []}

	tables = []
	body_para_count = 0
	table_index = 0
	for child in body:
		if child.tag == f"{W}p":
			body_para_count += 1
			continue
		if child.tag != f"{W}tbl":
			continue

		rows = [el for el in child if el.tag == f"{W}tr"]
		row_previews = []
		max_cols = 0
		for row in rows[:preview_rows]:
			cells = [tc for tc in row if tc.tag == f"{W}tc"]
			_logical_n = sum(_get_grid_span(c) for c in cells)
			max_cols = max(max_cols, _logical_n)
			row_texts = []
			for cell in cells:
				text = _get_cell_text(cell).strip()
				if len(text) > max_cell_chars:
					text = text[:max_cell_chars].rstrip() + "..."
				row_texts.append(text)
			row_previews.append(row_texts)
		for row in rows[preview_rows:]:
			_logical_n = sum(_get_grid_span(c) for c in row if c.tag == f"{W}tc")
			max_cols = max(max_cols, _logical_n)

		flat_preview = " | ".join(
			cell for row in row_previews for cell in row if cell
		)
		tables.append({
			"table_index": table_index,
			"after_para": body_para_count,
			"rows": len(rows),
			"cols": max_cols,
			"preview_rows": row_previews,
			"preview": flat_preview[:500],
		})
		table_index += 1

	return {"path": docx_path, "tables": tables}


def set_table_cells_by_position(docx_path: str, table_index: int,
                                cells: list[dict], *,
                                tc: bool = False,
                                author: str = "agent",
                                font: str = "宋体", font_size: float = 11.0,
                                output: str | None = None) -> EditResult:
	"""Set table cells by zero-indexed row/column coordinates in one write pass."""
	if not cells:
		return EditResult(ok=False, message="No cells provided", path=docx_path)

	doc_xml, other = _read_docx(docx_path)
	root = etree.fromstring(doc_xml)
	table = _find_table(root, table_index)
	if table is None:
		return EditResult(ok=False, message=f"Table {table_index} not found", path=docx_path)

	rows = [el for el in table if el.tag == f"{W}tr"]
	sz = float(font_size) * 2
	changed = 0
	errors = []
	targets = []

	for item in cells:
		try:
			row_idx = int(item["row"])
			col_idx = int(item["col"])
		except (KeyError, TypeError, ValueError):
			errors.append(f"Invalid cell coordinate: {item!r}")
			continue

		if row_idx < 0 or row_idx >= len(rows):
			errors.append(f"Row {row_idx} out of range")
			continue

		# Use gridSpan-aware logical column → physical cell mapping
		tc_el = _physical_cell_at_logical(rows[row_idx], col_idx)
		if tc_el is None:
			physical = [tc for tc in rows[row_idx] if tc.tag == f"{W}tc"]
			_logical_cols = sum(_get_grid_span(c) for c in physical)
			errors.append(f"Cell ({row_idx},{col_idx}) out of range (logical columns: {_logical_cols})")
			continue

		old_text = item.get("old_text")
		current_text = _get_cell_text(tc_el)
		if old_text is not None and _normalize_quotes(old_text) not in _normalize_quotes(current_text):
			errors.append(f"Cell ({row_idx},{col_idx}) does not contain expected old_text")
			continue

		new_text = str(item.get("text", ""))
		cell_bold = bool(item.get("bold", False))
		targets.append((tc_el, current_text, new_text, cell_bold))

	if errors:
		return EditResult(ok=False,
		                  message="Validation failed; no cells changed: " + "; ".join(errors[:5]),
		                  path=docx_path)

	for tc_el, current_text, new_text, cell_bold in targets:
		if tc and current_text:
			tid = _next_tc_id(root)
			from datetime import datetime
			dt = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

			for p in _get_cell_paragraphs(tc_el):
				del_el = etree.Element(f"{W}del")
				del_el.set(f"{W}id", str(tid)); tid += 1
				del_el.set(f"{W}author", author); del_el.set(f"{W}date", dt)
				pPr = p.find(f"{W}pPr")
				children = [c for c in p if c is not pPr]
				for child in reversed(children):
					p.remove(child)
					del_el.append(child)
				if pPr is not None:
					pPr.addnext(del_el)
				else:
					p.insert(0, del_el)

			new_p = etree.SubElement(tc_el, f"{W}p")
			ins = etree.SubElement(new_p, f"{W}ins")
			ins.set(f"{W}id", str(tid))
			ins.set(f"{W}author", author); ins.set(f"{W}date", dt)
			ins.append(_make_run(new_text, bold=cell_bold, font=font, sz=sz))
		else:
			_set_cell_text(tc_el, new_text, bold=cell_bold, font=font, sz=sz)
		changed += 1

	if changed:
		_write_docx(docx_path, etree.tostring(root, xml_declaration=True,
		                                      encoding="UTF-8", standalone=True),
		            other, output=output)

	message = f"Set {changed}/{len(cells)} cells in table {table_index}"
	return EditResult(ok=True, tc_mode=tc, tc_applied=tc, message=message, path=output or docx_path)


# ── Table cell text editing ───────────────────────────────────────────────────

def replace_table_cell_text(docx_path: str, table_index: int, old: str, new: str, *,
                            tc: bool = False,
                            author: str = "agent",
                            bold: bool = False,
                            font: str = "宋体", font_size: float = 11.0,
                            output: str | None = None) -> EditResult:
	"""
	Replace text in a specific table cell (first cell whose text contains `old`).

	table_index: 0-indexed table number in the document body
	old: text to search for within the cell
	new: replacement text
	tc: if True, wraps old in w:del and new in w:ins
	"""
	doc_xml, other = _read_docx(docx_path)
	root = etree.fromstring(doc_xml)
	table = _find_table(root, table_index)
	if table is None:
		return EditResult(ok=False, message=f"Table {table_index} not found", path=docx_path)

	tc_el = _find_cell_by_text(table, old)
	if tc_el is None:
		return EditResult(ok=False, message=f"No cell in table {table_index} contains '{old}'",
		                  path=docx_path)

	sz = float(font_size) * 2

	if tc:
		tid = _next_tc_id(root)
		from datetime import datetime
		dt = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

		# Wrap old text paragraphs in w:del
		for p in _get_cell_paragraphs(tc_el):
			del_el = etree.Element(f"{W}del")
			del_el.set(f"{W}id", str(tid)); tid += 1
			del_el.set(f"{W}author", author); del_el.set(f"{W}date", dt)
			pPr = p.find(f"{W}pPr")
			# Move children to del_el EXCEPT pPr (which stays in p as anchor point)
			children = [c for c in p if c is not pPr]
			insert_anchor = pPr if pPr is not None else None
			for child in reversed(children):
				p.remove(child)
				del_el.append(child)
			if insert_anchor is not None:
				insert_anchor.addnext(del_el)
			else:
				p.insert(0, del_el)

		# Add new text in w:ins
		new_p = etree.SubElement(tc_el, f"{W}p")
		ins = etree.SubElement(new_p, f"{W}ins")
		ins.set(f"{W}id", str(tid))
		ins.set(f"{W}author", author); ins.set(f"{W}date", dt)
		ins.append(_make_run(new, bold=bold, font=font, sz=sz))

		_write_docx(docx_path, etree.tostring(root, xml_declaration=True,
		                                      encoding="UTF-8", standalone=True),
		            other, output=output)
		return EditResult(ok=True, tc_mode=True, tc_applied=True, tc_id=tid,
		                  message=f"TC replaced cell text in table {table_index}: '{old}' -> '{new}'",
		                  path=output or docx_path)
	else:
		_set_cell_text(tc_el, new, bold=bold, font=font, sz=sz)
		_write_docx(docx_path, etree.tostring(root, xml_declaration=True,
		                                      encoding="UTF-8", standalone=True),
		            other, output=output)
		return EditResult(ok=True, tc_applied=False,
		                  message=f"Replaced cell text in table {table_index}: '{old}' -> '{new}'",
		                  path=output or docx_path)


def replace_table_cell_text_all(docx_path: str, table_index: int,
                                replacements: list[dict], *,
                                tc: bool = False,
                                author: str = "agent",
                                font: str = "宋体", font_size: float = 11.0,
                                output: str | None = None) -> EditResult:
	"""
	Batch replace text across multiple cells in a single read/write pass.

	replacements: list of {"old": "...", "new": "...", "bold": bool}
	"""
	if not replacements:
		return EditResult(ok=False, message="No replacements provided", path=docx_path)

	doc_xml, other = _read_docx(docx_path)
	root = etree.fromstring(doc_xml)
	table = _find_table(root, table_index)
	if table is None:
		return EditResult(ok=False, message=f"Table {table_index} not found", path=docx_path)

	sz = float(font_size) * 2
	matched = 0

	for repl in replacements:
		old_text = repl["old"]
		new_text = repl["new"]
		cell_bold = repl.get("bold", False)

		tc_el = _find_cell_by_text(table, old_text)
		if tc_el is None:
			continue
		matched += 1

		if tc:
			tid = _next_tc_id(root)
			from datetime import datetime
			dt = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

			for p in _get_cell_paragraphs(tc_el):
				del_el = etree.Element(f"{W}del")
				del_el.set(f"{W}id", str(tid)); tid += 1
				del_el.set(f"{W}author", author); del_el.set(f"{W}date", dt)
				pPr = p.find(f"{W}pPr")
				# Move children to del_el EXCEPT pPr (which stays in p as anchor point)
				children = [c for c in p if c is not pPr]
				insert_anchor = pPr if pPr is not None else None
				for child in reversed(children):
					p.remove(child)
					del_el.append(child)
				if insert_anchor is not None:
					insert_anchor.addnext(del_el)
				else:
					p.insert(0, del_el)

			new_p = etree.SubElement(tc_el, f"{W}p")
			ins = etree.SubElement(new_p, f"{W}ins")
			ins.set(f"{W}id", str(tid))
			ins.set(f"{W}author", author); ins.set(f"{W}date", dt)
			ins.append(_make_run(new_text, bold=cell_bold, font=font, sz=sz))
		else:
			_set_cell_text(tc_el, new_text, bold=cell_bold, font=font, sz=sz)

	_write_docx(docx_path, etree.tostring(root, xml_declaration=True,
	                                      encoding="UTF-8", standalone=True),
	            other, output=output)
	return EditResult(ok=True, tc_mode=tc, tc_applied=tc,
	                  message=f"Batch replaced {matched}/{len(replacements)} cells in table {table_index}",
	                  path=output or docx_path)


# ── Table row insertion ───────────────────────────────────────────────────────

def insert_table_rows(docx_path: str, table_index: int, template_row_index: int,
                      rows_data: list[list[str]], *,
                      output: str | None = None) -> EditResult:
	"""
	Insert new rows into a table by copying a template row and replacing cell text.

	table_index: 0-indexed table number in the document body
	template_row_index: 0-indexed row within the table to use as a template
	rows_data: list of rows, each row is a list of cell text strings
	           (one string per cell in the template row)
	New rows are inserted AFTER the template row.
	"""
	if not rows_data:
		return EditResult(ok=False, message="No rows_data provided", path=docx_path)

	doc_xml, other = _read_docx(docx_path)
	root = etree.fromstring(doc_xml)
	table = _find_table(root, table_index)
	if table is None:
		return EditResult(ok=False, message=f"Table {table_index} not found", path=docx_path)

	# Find all w:tr in this table (not nested tables)
	rows = [el for el in table if el.tag == f"{W}tr"]
	if template_row_index < 0 or template_row_index >= len(rows):
		return EditResult(ok=False,
		                  message=f"Row {template_row_index} out of range (0-{len(rows)-1})",
		                  path=docx_path)

	template_row = rows[template_row_index]
	n_cells = len(template_row.findall(f"{W}tc"))
	inserted = 0

	# Use the live table children for insertion so indices don't go stale
	anchor = template_row

	for row_idx, cell_texts in enumerate(rows_data):
		new_row = copy.deepcopy(template_row)
		cells = new_row.findall(f"{W}tc")

		# Strip vMerge from cloned cells so inserted rows don't
		# accidentally continue a vertical merge from the template.
		for tc in cells:
			tcPr = tc.find(f"{W}tcPr")
			if tcPr is not None:
				vm = tcPr.find(f"{W}vMerge")
				if vm is not None:
					tcPr.remove(vm)

		for ci in range(min(len(cell_texts), len(cells))):
			_set_cell_text(cells[ci], cell_texts[ci])

		# Insert after the anchor element, then advance anchor
		anchor.addnext(new_row)
		anchor = new_row
		inserted += 1

	_write_docx(docx_path, etree.tostring(root, xml_declaration=True,
	                                      encoding="UTF-8", standalone=True),
	            other, output=output)
	return EditResult(ok=True, tc_applied=False,
	                  message=f"Inserted {inserted} rows into table {table_index} (copied from row {template_row_index})",
	                  path=output or docx_path)


# ── Table creation ────────────────────────────────────────────────────────────

def create_table(docx_path: str, after_para: int,
                 headers: list[str], rows: list[list[str]], *,
                 font: str = "宋体", font_size: float = 11.0,
                 output: str | None = None) -> EditResult:
    """
    Create a new table and insert it after the specified paragraph.

    after_para: lex_read paragraph number (§N, 1-indexed) to insert after.
                0 is accepted as a legacy alias for §1.
    headers: column header texts
    rows: list of rows, each row is a list of cell text strings
    """
    if not headers and not rows:
        return EditResult(ok=False, message="headers or rows required", path=docx_path)

    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return EditResult(ok=False, message="Document body not found", path=docx_path)

    all_paras = [el for el in body if el.tag == f"{W}p"]
    if not all_paras:
        return EditResult(ok=False, message="No body paragraphs found", path=docx_path)

    try:
        after_para_int = int(after_para)
    except (TypeError, ValueError):
        return EditResult(ok=False, message=f"Invalid after_para: {after_para!r}", path=docx_path)

    if after_para_int == 0:
        # Backward compatibility for the previous 0-indexed API. New callers
        # should pass lex_read's visible §N number, e.g. after_para=12.
        anchor_idx = 0
        visible_after_para = 1
    else:
        anchor_idx = after_para_int - 1
        visible_after_para = after_para_int

    if anchor_idx < 0 or anchor_idx >= len(all_paras):
        return EditResult(ok=False,
                          message=f"Paragraph §{after_para_int} out of range (§1-§{len(all_paras)})",
                          path=docx_path)

    anchor = all_paras[anchor_idx]
    ncols = max(len(headers), max((len(r) for r in rows), default=0))
    if ncols == 0:
        return EditResult(ok=False, message="No columns to create", path=docx_path)

    sz = float(font_size) * 2
    col_width = str(int(9000 / ncols))  # Distribute width in twips (~5000 pct ≈ page width)

    tbl = etree.Element(f"{W}tbl")

    # tblPr
    tblPr = etree.SubElement(tbl, f"{W}tblPr")
    etree.SubElement(tblPr, f"{W}tblStyle").set(f"{W}val", "TableGrid")
    tblW = etree.SubElement(tblPr, f"{W}tblW")
    tblW.set(f"{W}w", "5000"); tblW.set(f"{W}type", "pct")

    # tblGrid
    tblGrid = etree.SubElement(tbl, f"{W}tblGrid")
    for _ in range(ncols):
        gc = etree.SubElement(tblGrid, f"{W}gridCol")
        gc.set(f"{W}w", col_width)

    def _make_cell(text: str, bold: bool = False) -> etree._Element:
        tc = etree.Element(f"{W}tc")
        p = etree.SubElement(tc, f"{W}p")
        p.append(_make_run(text, bold=bold, font=font, sz=sz))
        return tc

    # Header row
    if headers:
        tr = etree.SubElement(tbl, f"{W}tr")
        for h in headers:
            tr.append(_make_cell(h or "", bold=True))
        # Pad missing header cells
        for _ in range(ncols - len(headers)):
            tr.append(_make_cell("", bold=True))

    # Data rows
    for row in rows:
        tr = etree.SubElement(tbl, f"{W}tr")
        for cell_text in row[:ncols]:
            tr.append(_make_cell(str(cell_text) if cell_text is not None else ""))
        for _ in range(ncols - len(row)):
            tr.append(_make_cell(""))

    # Insert directly in the body so the table lands before sectPr and exactly
    # after the requested lex_read paragraph, even in documents with sections.
    body_children = list(body)
    body.insert(body_children.index(anchor) + 1, tbl)

    _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                          encoding="UTF-8", standalone=True),
                other, output=output)
    row_count = (1 if headers else 0) + len(rows)
    return EditResult(ok=True, tc_applied=False,
                      message=f"Created table ({ncols} cols, {row_count} rows) after §{visible_after_para}",
                      path=output or docx_path)


# ── Color-based cleanup ───────────────────────────────────────────────────────

def _run_color(run: etree._Element) -> str | None:
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return None
    color = rpr.find(f"{W}color")
    if color is None:
        return None
    val = color.get(f"{W}val")
    return val.upper() if val else None


def _is_blue_run(run: etree._Element, colors: set[str]) -> bool:
    val = _run_color(run)
    if not val:
        return False
    return val in colors


def remove_blue_text(docx_path: str, *,
                     colors: set[str] | None = None,
                     output: str | None = None) -> EditResult:
    """Remove blue runs from document.xml.

    Legal templates often use blue text for internal drafting notes. This
    operation deletes whole blue runs directly; it does not apply Track Changes
    because these notes are usually non-client-facing template instructions.
    """
    blue_colors = {c.upper().lstrip("#") for c in (colors or {"0000FF", "0000CC", "0563C1", "2F5496"})}

    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)

    removed_runs = 0
    removed_chars = 0
    for run in list(root.iter(f"{W}r")):
        if not _is_blue_run(run, blue_colors):
            continue
        text = _get_para_text(run)
        removed_chars += len(text)
        parent = run.getparent()
        if parent is not None:
            parent.remove(run)
            removed_runs += 1

    if removed_runs:
        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)

    return EditResult(ok=True, tc_applied=False,
                      message=f"Removed {removed_runs} blue text runs ({removed_chars} chars)",
                      path=output or docx_path)


# ── Paragraph block insertion ─────────────────────────────────────────────────

def insert_paragraph_block(docx_path: str, after_para: int,
                           paragraphs: list[dict], *,
                           tc: bool = True,
                           author: str = "agent",
                           font: str = "宋体",
                           sz: float = 22.0,
                           default_format: dict | None = None,
                           inherit_format: bool = True,
                           skip_empty: bool = True,
                           output: str | None = None) -> EditResult:
	"""
	Insert a block of paragraphs after a specified paragraph number.

	paragraphs: list of dicts with keys:
	  - text (str, required)
	  - bold (bool, default False)
	  - page_break_before (bool, default False): insert a page break before this paragraph
	  - style / style_id (str, optional): paragraph style id
	  - format (dict, optional): paragraph/run format overrides
	  - font (str, optional): override font for this paragraph
	  - sz (float, optional): override font size in half-points for this paragraph

	When tc=True (default), inserted content is wrapped in w:ins elements
	for Track Changes visibility.
	"""
	if not paragraphs:
		return EditResult(ok=False, message="No paragraphs provided", path=docx_path)

	doc_xml, other = _read_docx(docx_path)
	root = etree.fromstring(doc_xml)
	body = root.find(f"{W}body")
	if body is None:
		return EditResult(ok=False, message="Document body not found", path=docx_path)

	all_paras = [el for el in body if el.tag == f"{W}p"]
	if after_para < 0 or after_para >= len(all_paras):
		return EditResult(ok=False,
		                  message=f"Paragraph {after_para} out of range (0-{len(all_paras)-1})",
		                  path=docx_path)

	anchor = all_paras[after_para]
	# Bug 2: when inherit_format is true and the anchor paragraph is empty
	# with no pStyle, walk backwards to find the nearest non-empty paragraph.
	if inherit_format:
		_anchor_text = _get_para_text(anchor).strip()
		_anchor_pPr = anchor.find(f"{W}pPr")
		_has_style = _anchor_pPr is not None and _anchor_pPr.find(f"{W}pStyle") is not None
		if not _anchor_text and not _has_style:
			for prev_idx in range(after_para - 1, -1, -1):
				prev = all_paras[prev_idx]
				if _get_para_text(prev).strip():
					anchor = prev
					break
	inserted = 0
	skipped_empty = 0
	tc_mode = tc

	for pg in paragraphs:
		text = pg.get("text", "")
		if isinstance(text, str):
			text = text.strip("\r\n")
		bold = pg.get("bold", False)
		page_break = pg.get("page_break_before", False)
		if skip_empty and not str(text).strip() and not page_break:
			skipped_empty += 1
			continue

		new_p = etree.Element(f"{W}p")
		pPr = _clone_inheritable_ppr(anchor) if inherit_format else etree.Element(f"{W}pPr")
		new_p.append(pPr)
		pPr_rPr = pPr.find(f"{W}rPr")
		if pPr_rPr is None:
			pPr_rPr = etree.SubElement(pPr, f"{W}rPr")

		base_rPr = _first_run_rpr(anchor)
		run_rPr = copy.deepcopy(base_rPr) if base_rPr is not None else copy.deepcopy(pPr_rPr)
		if run_rPr is None:
			run_rPr = etree.Element(f"{W}rPr")

		pg_format = dict(default_format or {})
		pg_format.update(pg.get("format") or {})
		# Collect style from all sources: default_format, pg.format, and pg-level shortcut
		if pg.get("style") or pg.get("style_id"):
			pg_format["style"] = pg.get("style") or pg.get("style_id")
		# Bug 1: apply paragraph style explicitly before run-level formatting
		if pg_format.get("style") or pg_format.get("style_id"):
			_set_pstyle(pPr, pg_format.get("style") or pg_format.get("style_id"))
		if pg.get("font"):
			pg_format["font"] = pg.get("font")
		elif not inherit_format and font:
			pg_format.setdefault("font", font)
		if pg.get("size") is not None:
			pg_format["size"] = pg.get("size")
		elif pg.get("sz") is not None:
			pg_format["size"] = int(pg.get("sz")) / 2
		elif not inherit_format and sz:
			pg_format.setdefault("size", int(sz) / 2)
		_apply_insert_format(pPr, pPr_rPr, pg_format)
		_apply_insert_format(pPr, run_rPr, pg_format)
		keep_visuals = {key for key in ("highlight", "color") if key in pg_format}
		_strip_review_visuals(pPr_rPr, keep=keep_visuals)
		_strip_review_visuals(run_rPr, keep=keep_visuals)

		if page_break:
			pB = etree.SubElement(pPr, f"{W}pageBreakBefore")
			r_pb = etree.SubElement(new_p, f"{W}r")
			br = etree.SubElement(r_pb, f"{W}br")
			br.set(f"{W}type", "page")

		if text:
			run = etree.Element(f"{W}r")
			run.append(copy.deepcopy(run_rPr))
			if bold:
				run_rPr_actual = run.find(f"{W}rPr")
				if run_rPr_actual is None:
					run_rPr_actual = etree.SubElement(run, f"{W}rPr")
				if run_rPr_actual.find(f"{W}b") is None:
					etree.SubElement(run_rPr_actual, f"{W}b")
			t_el = etree.SubElement(run, f"{W}t")
			t_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
			t_el.text = text

			if tc:
				tid = _next_tc_id(root)
				ins = etree.Element(f"{W}ins")
				ins.set(f"{W}id", str(tid))
				ins.set(f"{W}author", author)
				from datetime import datetime
				ins.set(f"{W}date", datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
				ins.append(run)
				new_p.append(ins)
			else:
				new_p.append(run)

		anchor.addnext(new_p)
		anchor = new_p  # subsequent paragraphs go after this one
		inserted += 1

	_write_docx(docx_path, etree.tostring(root, xml_declaration=True,
	                                      encoding="UTF-8", standalone=True),
	            other, output=output)
	return EditResult(ok=True, tc_applied=tc,
	                  message=(
	                      f"Inserted {inserted} paragraphs after paragraph {after_para}"
	                      + (f"; skipped {skipped_empty} empty paragraphs" if skipped_empty else "")
	                  ),
	                  tc_mode=tc_mode,
	                  path=output or docx_path)

# ── Whole-document find/replace ──────────────────────────────────────────────


def _normalize_quotes(text: str) -> str:
    """Replace smart/curly quotes with straight quotes for flexible matching."""
    return (text
            .replace("ʻ", "'").replace("ʼ", "'")
            .replace("“", '"').replace("”", '"')
            .replace("«", '"').replace("»", '"')
            .replace("–", "-").replace("—", "--"))


def find_and_replace_all(
    docx_path: str,
    find: str,
    replace: str,
    *,
    match_case: bool = True,
    whole_word: bool = False,
    regex: bool = False,
    tc: bool = True,
    author: str = "agent",
    include_headers_footers: bool = False,
    include_tables: bool = True,
    output: str | None = None,
) -> dict:
    """Replace all occurrences of *find* with *replace* across the entire document.

    Uses multi-strategy text matching:
    1. Exact match (optionally case-insensitive)
    2. Smart-quote normalized match
    3. Whitespace-flexible match

    Returns a dict with match_count, paragraphs_touched, and errors.
    """
    import re as _re

    out_path = output or docx_path

    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    # Build list of paragraph elements to search
    search_paras: list[etree._Element] = []
    for el in body:
        if el.tag == f"{W}p":
            search_paras.append(el)
        elif el.tag == f"{W}tbl" and include_tables:
            for tc in el.iter(f"{W}tc"):
                for p in tc.iter(f"{W}p"):
                    search_paras.append(p)

    # Build find pattern
    flags = 0 if match_case else _re.IGNORECASE
    if regex:
        try:
            pattern = _re.compile(find, flags)
        except _re.error as e:
            return {"ok": False, "error": f"invalid regex: {e}"}
    else:
        escaped = _re.escape(find)
        if whole_word:
            escaped = r"\b" + escaped + r"\b"
        pattern = _re.compile(escaped, flags)

    # Collect all matches: (paragraph_element, match_start, match_end)
    matches: list[tuple[etree._Element, int, int]] = []

    for para in search_paras:
        rendered = render_paragraph(para, include_deleted=True)
        para_text = rendered.text
        if not para_text:
            continue

        # Strategy 1: exact/case-sensitive
        for m in pattern.finditer(para_text):
            matches.append((para, m.start(), m.end()))

        # Strategy 2: smart-quote normalized
        if not matches:
            norm_text = _normalize_quotes(para_text)
            find_norm = _normalize_quotes(find)
            if find_norm != find:
                if regex:
                    try:
                        pn = _re.compile(find_norm, flags)
                    except _re.error:
                        pn = None
                else:
                    en = _re.escape(find_norm)
                    if whole_word:
                        en = r"\b" + en + r"\b"
                    pn = _re.compile(en, flags)
                if pn is not None:
                    for m in pn.finditer(norm_text):
                        matches.append((para, m.start(), m.end()))

        # Strategy 3: whitespace-flexible
        if not matches:
            collapsed = _re.sub(r"\s+", " ", para_text.strip())
            find_collapsed = _re.sub(r"\s+", " ", find.strip())
            idx = collapsed.lower().find(find_collapsed.lower())
            if idx >= 0:
                # Map back to original position
                orig_idx = para_text.lower().find(find_collapsed.lower())
                if orig_idx >= 0:
                    matches.append((para, orig_idx, orig_idx + len(find_collapsed)))

    # Deduplicate by (element_id, start)
    seen: set[tuple[int, int]] = set()
    unique: list[tuple[etree._Element, int, int]] = []
    for el, s, e in matches:
        key = (id(el), s)
        if key not in seen:
            seen.add(key)
            unique.append((el, s, e))

    # Group matches by paragraph, sort within each para in reverse order
    from collections import defaultdict
    by_para: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for el, s, e in unique:
        by_para[id(el)].append((s, e))

    para_map: dict[int, etree._Element] = {}
    for el, s, e in unique:
        para_map[id(el)] = el

    # Process paragraphs in reverse order, matches within each in reverse
    match_count = 0
    errors: list[str] = []

    # TC mode: pre-allocate id range.  Each replacement can consume several
    # del + ins ids; we re-scan after each call to stay safe.
    if tc:
        dt = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    for para in reversed(search_paras):
        pid = id(para)
        if pid not in by_para:
            continue
        # Sort matches in reverse start order
        span_matches = sorted(by_para[pid], key=lambda x: x[0], reverse=True)
        for s, e in span_matches:
            if tc:
                tid = _next_tc_id(root)
                ok = _replace_text_with_tc_precise(
                    para, s, e, replace,
                    tc_id=tid, author=author, date=dt,
                )
            else:
                rendered = render_paragraph(para, include_deleted=True)
                ok = replace_span(rendered, s, e, replace)
            if not ok:
                errors.append(f"failed to replace at offset {s}-{e} in a paragraph")
                continue
            match_count += 1

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_replace_all.", suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct = other.get("[Content_Types].xml")
        if ct is not None:
            zf.writestr("[Content_Types].xml", ct)
        zf.writestr("word/document.xml", doc_xml_out)
        for name, data in other.items():
            if name == "[Content_Types].xml":
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {
        "ok": True,
        "match_count": match_count,
        "paragraphs_touched": len(by_para),
        "errors": errors or None,
        "path": out_path,
    }


# ── Patch Transaction ─────────────────────────────────────────────────────────


@dataclass
class PatchEdit:
    """A single edit queued inside a PatchTransaction."""
    op: str
    kwargs: dict
    desc: str = ""


class PatchTransaction:
    """Bundle multiple edits into one planned, applied, and verified unit.

    Usage::

        pt = PatchTransaction("doc.docx", author="JT")
        pt.add("replace_text", para=2, old="甲方", new="乙方")
        pt.add("insert_text", para=3, text="新增条款内容")
        pt.plan()
        result = pt.apply()
        if result["applied"] == 2:
            print(pt.summary())
    """

    _SUPPORTED = {
        "insert_text", "replace_text", "replace_text_in_place",
        "insert_paragraph_block", "delete_text", "delete_paragraph_tc",
        "set_table_cells_by_position",
    }

    def __init__(self, docx_path: str, *, author: str = "agent"):
        self._path = docx_path
        self._author = author
        self._edits: list[PatchEdit] = []
        self._plan_result: dict = {}
        self._apply_result: dict = {}
        self._verify_result: dict = {}

    # ── fluent API ─────────────────────────────────────────────────────────

    def add(self, op: str, *, desc: str = "", **kwargs) -> "PatchTransaction":
        if op not in self._SUPPORTED:
            raise ValueError(f"unsupported patch op {op!r}; supported: {sorted(self._SUPPORTED)}")
        if "author" not in kwargs:
            kwargs["author"] = self._author
        self._edits.append(PatchEdit(op=op, kwargs=kwargs, desc=desc))
        return self

    @property
    def edit_count(self) -> int:
        return len(self._edits)

    # ── plan ────────────────────────────────────────────────────────────────

    def plan(self) -> dict:
        """Resolve all targets and validate BEFORE applying.

        Returns a dict with *valid* (bool), *edits_planned* (int), and
        *warnings* (list[str]) for things that are suspicious but not fatal.
        """
        warnings: list[str] = []
        doc_xml, other = _read_docx(self._path)
        root = etree.fromstring(doc_xml)

        for i, edit in enumerate(self._edits):
            kw = edit.kwargs
            para = kw.get("para")

            if para is not None:
                p = _find_para(root, para)
                if p is None:
                    warnings.append(f"[{i}] {edit.op}: paragraph {para} not found — will fail at apply")

        self._plan_result = {
            "valid": len([w for w in warnings if "not found" in w]) == 0,
            "edits_planned": len(self._edits),
            "warnings": warnings or None,
        }
        return self._plan_result

    # ── apply ───────────────────────────────────────────────────────────────

    def apply(self, *, stop_on_error: bool = False) -> dict:
        """Execute every queued edit.  Each edit writes through to the
        document immediately so later edits see the updated state.

        Set *stop_on_error* to ``True`` to abort on the first failure.
        """
        applied: list[dict] = []
        errors: list[str] = []

        for i, edit in enumerate(self._edits):
            try:
                result = self._dispatch(edit)
                if isinstance(result, EditResult):
                    if result.ok:
                        applied.append({"idx": i, "op": edit.op, "message": result.message})
                    else:
                        errors.append(f"[{i}] {edit.op}: {result.message}")
                        if stop_on_error:
                            break
                elif isinstance(result, dict):
                    if result.get("ok"):
                        applied.append({"idx": i, "op": edit.op, "message": result.get("message", "ok")})
                    else:
                        errors.append(f"[{i}] {edit.op}: {result.get('error', result.get('message', 'failed'))}")
                        if stop_on_error:
                            break
                else:
                    applied.append({"idx": i, "op": edit.op})
            except Exception as exc:
                errors.append(f"[{i}] {edit.op}: {exc}")
                if stop_on_error:
                    break

        self._apply_result = {
            "applied": len(applied),
            "total": len(self._edits),
            "details": applied,
            "errors": errors or None,
        }
        return self._apply_result

    def _dispatch(self, edit: PatchEdit):
        kw = dict(edit.kwargs)
        kw.setdefault("output", self._path)
        op = edit.op

        if op == "insert_text":
            return insert_text(self._path, **kw)
        elif op == "replace_text":
            return replace_text(self._path, **kw)
        elif op == "replace_text_in_place":
            return replace_text_in_place(self._path, **kw)
        elif op == "insert_paragraph_block":
            return insert_paragraph_block(self._path, **kw)
        elif op == "delete_text":
            return delete_text(self._path, **kw)
        elif op == "delete_paragraph_tc":
            return delete_paragraph_tc(self._path, **kw)
        elif op == "set_table_cells_by_position":
            return set_table_cells_by_position(self._path, **kw)
        else:
            raise ValueError(f"unknown op {op!r}")

    # ── verify ──────────────────────────────────────────────────────────────

    def verify(self) -> dict:
        """Re-read affected paragraphs and confirm expected changes.

        Each edit can carry optional *verify_contains* and
        *verify_not_contains* keys that are checked after apply.
        """
        affected: set[int] = set()
        checks: list[dict] = []
        mismatches: list[dict] = []

        for edit in self._edits:
            kw = edit.kwargs
            para = kw.get("para")
            if para is not None:
                affected.add(para)
            if "verify_contains" in kw or "verify_not_contains" in kw:
                checks.append({
                    "para": para,
                    "should_contain": kw.get("verify_contains"),
                    "should_not_contain": kw.get("verify_not_contains"),
                })

        if not affected and not checks:
            self._verify_result = {"verified": 0, "mismatches": None, "note": "no verification criteria"}
            return self._verify_result

        # Read back affected paragraphs via lex_read markup
        from .markup import lex_read as _lex_read
        try:
            text = _lex_read(self._path, paras=sorted(affected), mode="full", show_tc="all")
        except Exception as exc:
            self._verify_result = {"verified": 0, "mismatches": None, "error": str(exc)}
            return self._verify_result

        for check in checks:
            if check["should_contain"] and check["should_contain"] not in text:
                mismatches.append({**check, "issue": "expected text not found"})
            if check["should_not_contain"] and check["should_not_contain"] in text:
                mismatches.append({**check, "issue": "unexpected text still present"})

        self._verify_result = {
            "verified": len(affected),
            "checks_run": len(checks),
            "mismatches": mismatches or None,
        }
        return self._verify_result

    # ── summary ─────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Emit a structured change report combining plan, apply, and verify."""
        return {
            "path": self._path,
            "plan": self._plan_result,
            "apply": self._apply_result,
            "verify": self._verify_result,
        }


def verify_edits(
    docx_path: str,
    expected: list[dict],
) -> dict:
    """Verify that edits were applied correctly by re-reading affected paragraphs.

    Each entry in *expected* is a dict with:
        - ``para`` (int): 1-indexed paragraph number.
        - ``should_contain`` (list[str]): text snippets that must be present.
        - ``should_not_contain`` (list[str]): text snippets that must NOT be present.

    Returns a dict with keys: ``verified``, ``checks_run``, ``mismatches``.
    """
    from .markup import lex_read as _lex_read

    affected = sorted({e["para"] for e in expected if "para" in e})
    if not affected:
        return {"verified": 0, "checks_run": 0, "mismatches": None, "note": "no paragraphs to verify"}

    try:
        text = _lex_read(docx_path, paras=affected, mode="full", show_tc="all")
    except Exception as exc:
        return {"verified": 0, "checks_run": 0, "mismatches": None, "error": str(exc)}

    mismatches = []
    for check in expected:
        para = check.get("para")
        for snippet in check.get("should_contain") or []:
            if snippet not in text:
                mismatches.append({"para": para, "issue": "expected text not found", "expected": snippet})
        for snippet in check.get("should_not_contain") or []:
            if snippet in text:
                mismatches.append({"para": para, "issue": "unexpected text still present", "unexpected": snippet})

    return {
        "verified": len(affected),
        "checks_run": len(expected),
        "mismatches": mismatches or None,
    }
