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
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from lxml import etree

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
    _os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", doc_xml)
        for name, data in other.items():
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
    tc_id: int | None = None
    path: str = ""


def insert_text(docx_path: str, para: int, text: str, *,
                tc: bool = False,
                author: str = "agent",
                bold: bool = False, italic: bool = False,
                font: str = "宋体", font_size: float = 11.0,
                output: str | None = None) -> EditResult:
    """
    在指定段落末尾插入文字。

    tc=False（默认）：直接插入 w:r 文本（无修订标记）
    tc=True：通过 <w:ins> 注入 Track Changes
    """
    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)
    p = _find_para(root, para)
    if p is None:
        return EditResult(ok=False, message=f"段落 {para} 不存在", path=docx_path)

    sz = float(font_size) * 2  # half-points

    if tc:
        tid = _next_tc_id(root)
        from datetime import datetime
        ins = etree.Element(f"{W}ins")
        ins.set(f"{W}id", str(tid))
        ins.set(f"{W}author", author)
        ins.set(f"{W}date", datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        ins.append(_make_run(text, bold=bold, italic=italic, font=font, sz=sz))
        p.append(ins)
        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=text, tc_mode=True, tc_id=tid,
                          message=f"TC 插入段落 {para} 完成（id={tid}）",
                          path=output or docx_path)
    else:
        p.append(_make_run(text, bold=bold, italic=italic, font=font, sz=sz))
        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=text, tc_mode=False,
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
    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)
    p = _find_para(root, para)
    if p is None:
        return EditResult(ok=False, message=f"段落 {para} 不存在", path=docx_path)

    sz = float(font_size) * 2
    tid = _next_tc_id(root)

    if tc:
        # TC 模式：旧 → <w:del>，新 → <w:ins>
        from datetime import datetime
        dt = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

        # 在段落末尾追加 <w:del>（旧文本）+ <w:ins>（新文本）
        del_el = etree.Element(f"{W}del")
        del_el.set(f"{W}id", str(tid))
        del_el.set(f"{W}author", author)
        del_el.set(f"{W}date", dt)
        d_run = _make_run(old, font=font, sz=sz)
        for t in d_run.iter(f"{W}t"):
            t.tag = f"{W}delText"
        del_el.append(d_run)
        p.append(del_el)

        tid2 = tid + 1
        ins = etree.Element(f"{W}ins")
        ins.set(f"{W}id", str(tid2))
        ins.set(f"{W}author", author)
        ins.set(f"{W}date", dt)
        ins.append(_make_run(new, bold=bold, italic=italic, font=font, sz=sz))
        p.append(ins)

        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=f"{old}→{new}", tc_mode=True,
                          tc_id=tid, message=f"TC 替换段落 {para}：{old}→{new}",
                          path=output or docx_path)
    else:
        # 直接替换：在 w:t 节点中替换文本
        replaced = 0
        for t in p.iter(f"{W}t"):
            if t.text and old in t.text:
                t.text = t.text.replace(old, new, 1)
                replaced += 1
                break
        if replaced == 0:
            return EditResult(ok=False, para=para, text=old,
                              message=f"段落 {para} 中未找到 '{old}'",
                              path=docx_path)
        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=f"{old}→{new}", tc_mode=False,
                          message=f"直接替换段落 {para}：{old}→{new}",
                          path=output or docx_path)


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
        return EditResult(ok=True, para=para, text=to_del, tc_mode=True,
                          tc_id=tid, message=f"TC 删除段落 {para} 完成",
                          path=output or docx_path)
    else:
        if text:
            # 仅删除匹配文本
            for t in p.iter(f"{W}t"):
                if t.text and text in t.text:
                    t.text = t.text.replace(text, "", 1)
                    break
        else:
            # 清空所有 w:t
            for t in p.iter(f"{W}t"):
                t.text = None
        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=text or "(全部)", tc_mode=False,
                          message=f"直接删除段落 {para} 完成",
                          path=output or docx_path)


# ── 原地 TC 替换（保留原 run 格式、跨 run 支持） ─────────────────────────────


def _normalize_quotes(text: str) -> str:
    """将弯引号（Word自动弯引号）标准化为直引号以匹配搜索。"""
    return text.replace('\u201c', '\u0022').replace('\u201d', '\u0022').replace('\u2018', '\u0027').replace('\u2019', '\u0027')


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
    return EditResult(ok=True, para=para, text=f"{old}→{new}", tc_mode=True,
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
    return EditResult(ok=True, para=para, tc_mode=True, tc_id=tid,
                      message=f"段落 {para} 已标记为删除（TC）",
                      path=output or docx_path)
