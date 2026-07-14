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


def _clean_empty_tc_markers(root: etree._Element) -> int:
    """Remove empty <w:ins>/<w:del> tags that have no <w:r> children.

    These cause Word to show "unreadable content" errors on open.
    Returns number of tags removed.
    """
    removed = 0
    for tag in (f"{W}ins", f"{W}del"):
        for el in list(root.iter(tag)):
            has_run = el.find(f"{W}r") is not None
            if not has_run:
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)
                    removed += 1
    return removed


def _write_docx(path: str, doc_xml: bytes, other: dict[str, bytes],
                output: str | None = None) -> None:
    """写回 docx。修复：确保 XML 声明与 Word 完全兼容（双引号 standalone）。"""
    # Clean empty TC markers before writing
    try:
        root = etree.fromstring(doc_xml)
        _clean_empty_tc_markers(root)
        doc_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    except Exception:
        pass  # best-effort

    out_path = output or path
    fd, tmp = tempfile.mkstemp(prefix="lex_docx_edit.", suffix=".docx")
    import os as _os
    _os.close(fd)
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
        return EditResult(ok=True, para=para, text=text, tc_mode=True, tc_applied=True, tc_id=tid,
                          message=f"TC 插入段落 {para} 完成（id={tid}）",
                          path=output or docx_path)
    else:
        p.append(_make_run(text, bold=bold, italic=italic, font=font, sz=sz))
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
    doc_xml, other = _read_docx(docx_path)
    root = etree.fromstring(doc_xml)
    p = _find_para(root, para)
    if p is None:
        return EditResult(ok=False, message=f"段落 {para} 不存在", path=docx_path)

    sz = float(font_size) * 2
    tid = _next_tc_id(root)

    if tc:
        # TC 模式：内联替换 — 在匹配位置将旧文本包裹为 <w:del>，
        # 紧跟 <w:ins> 插入新文本。仅移除和替换包含匹配文本的 w:r 元素，
        # 保留不相关的 w:r 及其他段落子元素。
        from datetime import datetime
        dt = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

        # 收集段落直接 w:r 子元素的文本
        w_r_children = [child for child in p if child.tag == f"{W}r"]

        # Build text map: for each w:r, compute its text and character range
        run_texts = []
        char_pos = 0
        for r_el in w_r_children:
            text = "".join(t.text or "" for t in r_el.findall(f"{W}t"))
            run_texts.append((r_el, text, char_pos, char_pos + len(text)))
            char_pos += len(text)

        full_text = "".join(t for _, t, _, _ in run_texts)
        full_text_norm = _normalize_quotes(full_text)
        old_norm = _normalize_quotes(old)
        if old_norm not in full_text_norm:
            return EditResult(ok=False, para=para, text=old,
                              message=f"段落 {para} 中未找到 '{old}'",
                              path=docx_path)

        pos = full_text_norm.find(old_norm)
        end_pos = pos + len(old)

        # Classify w:r elements relative to the match
        before_runs = []
        match_runs = []
        after_runs = []

        for r_el, text, r_start, r_end in run_texts:
            if r_end <= pos:
                before_runs.append(r_el)
            elif r_start >= end_pos:
                after_runs.append(r_el)
            else:
                match_runs.append(r_el)

        if not match_runs:
            return EditResult(ok=False, para=para, text=old,
                              message=f"段落 {para} 中未找到 '{old}' 的边界",
                              path=docx_path)

        # Compute before/after text within the matched runs
        match_start_pos = run_texts[w_r_children.index(match_runs[0])][2]
        match_text = "".join(
            "".join(t.text or "" for t in r_el.findall(f"{W}t"))
            for r_el in match_runs
        )
        match_end_pos = match_start_pos + len(match_text)
        before_in_match = full_text[match_start_pos:pos]
        after_in_match = full_text[end_pos:match_end_pos]

        # Remove only the matched w:r elements
        insert_idx = list(p).index(match_runs[0]) if match_runs else 0
        for r_el in match_runs:
            p.remove(r_el)

        # Build replacement: before_part + w:del(old) + w:ins(new) + after_part
        new_elems = []

        if before_in_match:
            new_elems.append(_make_run(before_in_match, font=font, sz=sz))

        del_el = etree.Element(f"{W}del")
        del_el.set(f"{W}id", str(tid))
        del_el.set(f"{W}author", author)
        del_el.set(f"{W}date", dt)
        d_run = _make_run(old, font=font, sz=sz)
        for t in d_run.iter(f"{W}t"):
            t.tag = f"{W}delText"
        del_el.append(d_run)
        new_elems.append(del_el)

        tid2 = tid + 1
        ins_el = etree.Element(f"{W}ins")
        ins_el.set(f"{W}id", str(tid2))
        ins_el.set(f"{W}author", author)
        ins_el.set(f"{W}date", dt)
        ins_el.append(_make_run(new, bold=bold, italic=italic, font=font, sz=sz))
        new_elems.append(ins_el)

        if after_in_match:
            new_elems.append(_make_run(after_in_match, font=font, sz=sz))

        for i, elem in enumerate(new_elems):
            p.insert(insert_idx + i, elem)

        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=f"{old}→{new}", tc_mode=True, tc_applied=True,
                          tc_id=tid, message=f"TC 替换段落 {para}：{old}→{new}",
                          path=output or docx_path)
    else:
        # 直接替换：支持跨 run 文本匹配
        # 收集段落中所有 w:t 元素的文本
        t_elements = list(p.iter(f"{W}t"))
        texts = [(i, t.text or "") for i, t in enumerate(t_elements)]
        full_text = "".join(t for _, t in texts)

        full_text_norm = _normalize_quotes(full_text)
        old_norm = _normalize_quotes(old)
        if old_norm not in full_text_norm:
            return EditResult(ok=False, para=para, text=old,
                              message=f"段落 {para} 中未找到 '{old}'",
                              path=docx_path)

        # 执行替换（保持在原始文本上的位置，只标准化引号用于匹配）
        pos = full_text_norm.find(old_norm)
        new_full = full_text[:pos] + new + full_text[pos + len(old):]

        # 重新分配文本到 w:t 元素
        # 策略：将新文本放入第一个 w:t，清空其余
        if t_elements:
            t_elements[0].text = new_full
            for t in t_elements[1:]:
                t.text = ""

        _write_docx(docx_path, etree.tostring(root, xml_declaration=True,
                                              encoding="UTF-8", standalone=True),
                    other, output=output)
        return EditResult(ok=True, para=para, text=f"{old}→{new}", tc_mode=False, tc_applied=False,
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
    """将弯引号（Word自动弯引号）标准化为直引号以匹配搜索。

    同时将全角字符标准化为半角ASCII，确保1:1码位映射以支持
    _locate_replacement_span中的索引保持不变量。
    """
    from .tc_utils import _normalize_fullwidth
    text = _normalize_fullwidth(text)
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


def _find_cell_by_text(table: etree._Element, text: str) -> etree._Element | None:
	"""Find the first w:tc in a table that contains the given text."""
	text_norm = _normalize_quotes(text)
	for tc in table.iter(f"{W}tc"):
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
			max_cols = max(max_cols, len(cells))
			row_texts = []
			for cell in cells:
				text = _get_cell_text(cell).strip()
				if len(text) > max_cell_chars:
					text = text[:max_cell_chars].rstrip() + "..."
				row_texts.append(text)
			row_previews.append(row_texts)
		for row in rows[preview_rows:]:
			max_cols = max(max_cols, len([tc for tc in row if tc.tag == f"{W}tc"]))

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
		row_cells = [tc_el for tc_el in rows[row_idx] if tc_el.tag == f"{W}tc"]
		if col_idx < 0 or col_idx >= len(row_cells):
			errors.append(f"Cell ({row_idx},{col_idx}) out of range")
			continue

		tc_el = row_cells[col_idx]
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
                           output: str | None = None) -> EditResult:
	"""
	Insert a block of paragraphs after a specified paragraph number.

	paragraphs: list of dicts with keys:
	  - text (str, required)
	  - bold (bool, default False)
	  - page_break_before (bool, default False): insert a page break before this paragraph
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
	inserted = 0
	tc_mode = tc

	for pg in paragraphs:
		text = pg.get("text", "")
		bold = pg.get("bold", False)
		page_break = pg.get("page_break_before", False)
		pg_font = pg.get("font", font)
		pg_sz = pg.get("sz", sz)

		new_p = etree.Element(f"{W}p")

		# Paragraph-level run properties: set font / size here so that
		# individual runs don't need their own rPr, avoiding double-nesting
		# with the document default style.
		pPr = etree.SubElement(new_p, f"{W}pPr")
		pPr_rPr = etree.SubElement(pPr, f"{W}rPr")
		pPr_rFonts = etree.SubElement(pPr_rPr, f"{W}rFonts")
		pPr_rFonts.set(f"{W}ascii", pg_font)
		pPr_rFonts.set(f"{W}hAnsi", pg_font)
		pPr_rFonts.set(f"{W}eastAsia", pg_font)
		etree.SubElement(pPr_rPr, f"{W}sz").set(f"{W}val", str(int(pg_sz)))
		etree.SubElement(pPr_rPr, f"{W}szCs").set(f"{W}val", str(int(pg_sz)))

		if page_break:
			pB = etree.SubElement(pPr, f"{W}pageBreakBefore")
			r_pb = etree.SubElement(new_p, f"{W}r")
			br = etree.SubElement(r_pb, f"{W}br")
			br.set(f"{W}type", "page")

		if text:
			# Build the run with only bold in rPr — font / size are
			# already set at the paragraph level above.
			run = etree.Element(f"{W}r")
			if bold:
				run_rPr = etree.SubElement(run, f"{W}rPr")
				etree.SubElement(run_rPr, f"{W}b")
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
	                  message=f"Inserted {inserted} paragraphs after paragraph {after_para}",
	                  tc_mode=tc_mode,
	                  path=output or docx_path)
