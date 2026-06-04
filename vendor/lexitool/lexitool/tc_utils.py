"""
tc_utils.py — Track Changes XML 底层工具

所有 w:ins / w:del 构造统一从这里发出，避免各模块重复实现。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

# --------------------------------------------------------------------------- #
# 内部辅助                                                                      #
# --------------------------------------------------------------------------- #

def _utc_now() -> str:
    """返回 Word 兼容的 ISO 8601 UTC 时间戳，如 2026-03-17T10:30:00Z"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_tc_id(doc) -> int:
    """
    扫描文档中所有 w:ins / w:del / w:comment* 的 w:id，返回 max+1。
    保证不与现有 TC 冲突。
    """
    body = doc.element.body
    max_id = 0
    for tag in (qn("w:ins"), qn("w:del"),
                qn("w:commentRangeStart"), qn("w:commentRangeEnd")):
        for el in body.iter(tag):
            try:
                max_id = max(max_id, int(el.get(qn("w:id"), 0)))
            except (ValueError, TypeError):
                pass
    return max_id + 1


# --------------------------------------------------------------------------- #
# w:rPr 构造                                                                    #
# --------------------------------------------------------------------------- #

def make_rPr(
    bold: bool = False,
    italic: bool = False,
    highlight: str | None = None,
    base_rPr=None,          # 可选：从现有 w:rPr element deepcopy 作为基础
) -> OxmlElement:
    """
    构造 w:rPr 元素。
    - bold=True  → 同时设 w:b + w:bCs（中文加粗必须两者都设）
    - italic=True → w:i + w:iCs
    - highlight  → w:highlight val="yellow"（或其他颜色值）
    - base_rPr   → deepcopy 现有 rPr 后追加/覆盖

    元素顺序遵循 OOXML 规范：b > bCs > i > iCs > highlight
    """
    rPr = deepcopy(base_rPr) if base_rPr is not None else OxmlElement("w:rPr")

    if bold:
        if rPr.find(qn("w:b")) is None:
            b = OxmlElement("w:b")
            rPr.append(b)
        if rPr.find(qn("w:bCs")) is None:
            bCs = OxmlElement("w:bCs")
            rPr.append(bCs)

    if italic:
        if rPr.find(qn("w:i")) is None:
            i = OxmlElement("w:i")
            rPr.append(i)
        if rPr.find(qn("w:iCs")) is None:
            iCs = OxmlElement("w:iCs")
            rPr.append(iCs)

    if highlight:
        existing_hl = rPr.find(qn("w:highlight"))
        if existing_hl is not None:
            rPr.remove(existing_hl)
        hl = OxmlElement("w:highlight")
        hl.set(qn("w:val"), highlight)
        rPr.append(hl)

    return rPr


def make_rPr_from_dict(d: dict) -> OxmlElement:
    """
    从配置字典构造 w:rPr。

    支持键（均为字符串值）：
      ascii, eastAsia, hAnsi, cs  → w:rFonts 属性（字体）
      sz, szCs                    → 字号（单位 half-points，如 "24" = 12pt）
      b, i                        → True/False，加粗/斜体
      color                       → 十六进制颜色，如 "FF0000"
      highlight                   → Word highlight 颜色名，如 "yellow"

    示例：
      make_rPr_from_dict({"eastAsia": "仿宋_GB2312", "sz": "24"})
    """
    rPr = OxmlElement("w:rPr")

    # 字体
    font_attrs = {k: d[k] for k in ("ascii", "eastAsia", "hAnsi", "cs") if k in d}
    if font_attrs:
        rFonts = OxmlElement("w:rFonts")
        _attr_map = {
            "ascii": qn("w:ascii"),
            "eastAsia": qn("w:eastAsia"),
            "hAnsi": qn("w:hAnsi"),
            "cs": qn("w:cs"),
        }
        for k, v in font_attrs.items():
            rFonts.set(_attr_map[k], v)
        rPr.append(rFonts)

    # 加粗
    if d.get("b"):
        rPr.append(OxmlElement("w:b"))
        rPr.append(OxmlElement("w:bCs"))

    # 斜体
    if d.get("i"):
        rPr.append(OxmlElement("w:i"))
        rPr.append(OxmlElement("w:iCs"))

    # 字号
    if "sz" in d:
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), str(d["sz"]))
        rPr.append(sz)
    if "szCs" in d:
        szCs = OxmlElement("w:szCs")
        szCs.set(qn("w:val"), str(d["szCs"]))
        rPr.append(szCs)

    # 颜色
    if "color" in d:
        color_el = OxmlElement("w:color")
        color_el.set(qn("w:val"), d["color"].lstrip("#").upper())
        rPr.append(color_el)

    # 高亮
    if "highlight" in d:
        hl = OxmlElement("w:highlight")
        hl.set(qn("w:val"), d["highlight"])
        rPr.append(hl)

    return rPr


def _resolve_rPr(
    para_el,
    inherit_rPr,
    style_rPr_map: dict | None,
) -> OxmlElement | None:
    """
    根据 inherit_rPr 策略解析出 base_rPr。

    inherit_rPr 取值：
      False / None  → 不继承（返回 None）
      True          → 从段落中第一个 w:del > w:r 的 rPr 复制
      "style"       → 纯样式继承，不设 rPr（返回 _STYLE_SENTINEL）
      "auto"        → 从段落 pStyle 查 style_rPr_map（需传入 style_rPr_map）
      Paragraph     → 从 python-docx Paragraph 的第一个 run 取 rPr
      Run           → 从 python-docx Run 取 rPr
      lxml element  → 直接 deepcopy 该 rPr 元素
    """
    if not inherit_rPr:
        return None

    if inherit_rPr is _STYLE_SENTINEL or inherit_rPr == "style":
        return _STYLE_SENTINEL   # 哨兵：调用方不设 rPr

    if inherit_rPr is True:
        # 从段落中已有 w:del 的第一个 run 取 rPr
        for del_el in para_el.iter(qn("w:del")):
            for r_el in del_el.findall(qn("w:r")):
                rPr = r_el.find(qn("w:rPr"))
                if rPr is not None:
                    return deepcopy(rPr)
        return None   # 找不到 → 不继承

    if inherit_rPr == "auto":
        # 从 pStyle 查 style_rPr_map
        if not style_rPr_map:
            return None
        pPr = para_el.find(qn("w:pPr"))
        pStyle = pPr.find(qn("w:pStyle")) if pPr is not None else None
        style_name = pStyle.get(qn("w:val"), "") if pStyle is not None else "Normal"
        entry = style_rPr_map.get(style_name)
        if entry is None:
            return None
        if isinstance(entry, dict):
            return make_rPr_from_dict(entry)
        return deepcopy(entry)   # lxml element

    # python-docx Paragraph
    if hasattr(inherit_rPr, "paragraphs"):  # Document — not supported
        return None
    if hasattr(inherit_rPr, "runs"):        # Paragraph
        para_obj = inherit_rPr
        for run in para_obj.runs:
            rPr = run._element.find(qn("w:rPr"))
            if rPr is not None:
                return deepcopy(rPr)
        return None
    if hasattr(inherit_rPr, "_element") and hasattr(inherit_rPr, "text"):  # Run
        rPr = inherit_rPr._element.find(qn("w:rPr"))
        return deepcopy(rPr) if rPr is not None else None

    # lxml element（直接传入 rPr）
    if hasattr(inherit_rPr, "tag"):
        return deepcopy(inherit_rPr)

    return None


# 哨兵对象：表示"不加 rPr，让 Word 从 style 继承"
_STYLE_SENTINEL = object()


# --------------------------------------------------------------------------- #
# w:r 构造                                                                      #
# --------------------------------------------------------------------------- #

def make_run(
    text: str,
    bold: bool = False,
    italic: bool = False,
    highlight: str | None = None,
    base_rPr=None,
) -> OxmlElement:
    """
    构造 w:r 元素。文本中的 \\t 自动转为 <w:tab/>。
    空文本返回空 run（调用方自行决定是否跳过）。
    """
    r = OxmlElement("w:r")

    if bold or italic or highlight or base_rPr is not None:
        rPr = make_rPr(bold=bold, italic=italic, highlight=highlight,
                       base_rPr=base_rPr)
        r.append(rPr)

    if text and "\t" in text:
        # Split on tab, create w:t + w:tab + w:t segments
        segments = text.split("\t")
        for i, seg in enumerate(segments):
            if seg:
                t = OxmlElement("w:t")
                t.text = seg
                if seg and (seg[0] == " " or seg[-1] == " "):
                    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                r.append(t)
            if i < len(segments) - 1:
                tab = OxmlElement("w:tab")
                r.append(tab)
    else:
        t = OxmlElement("w:t")
        t.text = text or ""
        if text and (text[0] == " " or text[-1] == " "):
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        r.append(t)

    return r


# --------------------------------------------------------------------------- #
# w:ins / w:del tag 构造                                                        #
# --------------------------------------------------------------------------- #

def make_tc_tag(
    tag_name: str,      # "w:ins" 或 "w:del"
    tc_id: int,
    author: str,
    date: str | None = None,
) -> OxmlElement:
    """
    构造 w:ins 或 w:del 标签（不含子元素）。
    date 默认为当前 UTC 时间。
    """
    el = OxmlElement(tag_name)
    el.set(qn("w:id"), str(tc_id))
    el.set(qn("w:author"), author)
    el.set(qn("w:date"), date or _utc_now())
    return el


def make_ins_run(
    text: str,
    tc_id: int,
    author: str,
    bold: bool = False,
    italic: bool = False,
    highlight: str | None = None,
    date: str | None = None,
    base_rPr=None,
) -> OxmlElement:
    """
    构造完整的 w:ins > w:r > w:t 结构（文本级 Track Changes）。
    """
    ins = make_tc_tag("w:ins", tc_id, author, date)
    r = make_run(text, bold=bold, italic=italic, highlight=highlight,
                 base_rPr=base_rPr)
    ins.append(r)
    return ins


# --------------------------------------------------------------------------- #
# 表格行级 TC（w:trPr > w:ins / w:del）                                        #
# --------------------------------------------------------------------------- #

def mark_row_as_inserted(tr_element, tc_id: int, author: str, date: str | None = None):
    """
    将 w:tr 标记为 TC INS（行级插入）。
    正确方式：在 w:trPr 内添加 w:ins，而非用 w:ins 包裹整个 w:tr。
    """
    trPr = tr_element.find(qn("w:trPr"))
    if trPr is None:
        trPr = OxmlElement("w:trPr")
        tr_element.insert(0, trPr)
    ins = make_tc_tag("w:ins", tc_id, author, date)
    trPr.append(ins)


def mark_row_as_deleted(tr_element, tc_id: int, author: str, date: str | None = None):
    """将 w:tr 标记为 TC DEL（行级删除）。"""
    trPr = tr_element.find(qn("w:trPr"))
    if trPr is None:
        trPr = OxmlElement("w:trPr")
        tr_element.insert(0, trPr)
    del_el = make_tc_tag("w:del", tc_id, author, date)
    trPr.append(del_el)


# --------------------------------------------------------------------------- #
# 段落级 TC                                                                     #
# --------------------------------------------------------------------------- #

def tc_del_paragraph(
    para_el,
    tc_id: int,
    author: str,
    date: str | None = None,
    delete_para_mark: bool = True,
) -> OxmlElement | None:
    """
    将段落标记为 TC DEL（段落级删除）。

    操作：
    1. 每个 w:r 的 w:t 改为 w:delText
    2. 每个 w:r 用 w:del 包裹
    3. 如 delete_para_mark=True，则在 w:pPr > w:rPr > w:del 标记段落结束符被删除

    para_el: python-docx Paragraph 对象 或 w:p lxml element

    Returns:
        删除前第一个 run 的 rPr（deepcopy），供 tc_ins_text(inherit_rPr=True) 使用。
        若段落无 run 则返回 None。
    """
    if hasattr(para_el, "_element"):
        para_el = para_el._element

    _date = date or _utc_now()

    # 预先保存第一个 run 的 rPr（在修改前）
    first_rPr: OxmlElement | None = None
    first_r = para_el.find(qn("w:r"))
    if first_r is not None:
        rPr_el = first_r.find(qn("w:rPr"))
        if rPr_el is not None:
            first_rPr = deepcopy(rPr_el)

    # ── 1. 将所有 w:r 包裹进 w:del，w:t → w:delText ── #
    for r_el in list(para_el.findall(qn("w:r"))):
        for t_el in r_el.findall(qn("w:t")):
            t_el.tag = qn("w:delText")
        del_wrap = make_tc_tag("w:del", tc_id, author, _date)
        idx = list(para_el).index(r_el)
        para_el.remove(r_el)
        del_wrap.append(r_el)
        para_el.insert(idx, del_wrap)
        tc_id += 1   # 每个 run 用独立 id（Word 规范要求 del 内每个 run 唯一）

    # ── 2. 可选：标记段落结束符删除（¶ mark deleted） ── #
    if delete_para_mark:
        pPr = para_el.find(qn("w:pPr"))
        if pPr is None:
            pPr = OxmlElement("w:pPr")
            para_el.insert(0, pPr)
        rPr = pPr.find(qn("w:rPr"))
        if rPr is None:
            rPr = OxmlElement("w:rPr")
            pPr.append(rPr)
        if rPr.find(qn("w:del")) is None:
            del_mark = make_tc_tag("w:del", tc_id, author, _date)
            rPr.append(del_mark)

    return first_rPr


def tc_ins_text(
    para_el,
    text: str,
    tc_id: int,
    author: str,
    position: str | int = "end",
    bold: bool = False,
    italic: bool = False,
    highlight: str | None = None,
    date: str | None = None,
    base_rPr=None,
    inherit_rPr=False,
    style_rPr_map: dict | None = None,
) -> OxmlElement:
    """
    在段落内以 TC INS 形式插入文字。

    Args:
        para_el:        python-docx Paragraph 或 w:p lxml element
        text:           插入文字
        tc_id:          Track Changes ID
        author:         作者
        position:       "end"（末尾）| "start"（开头）| 整数（第 n 个 run 之后）
        bold/italic/highlight: 额外格式（叠加在继承的 rPr 之上）
        base_rPr:       显式传入 rPr element（优先级最高）
        inherit_rPr:    rPr 继承策略（见下）
        style_rPr_map:  {style_name: rPr_el_or_dict}，配合 inherit_rPr="auto" 使用

    inherit_rPr 取值：
        False       → 不继承（默认）
        True        → 从段落中第一个 w:del > w:r 复制 rPr（配合 tc_del_paragraph 使用）
        "style"     → 不设 rPr，让 Word 从 pStyle 继承字体字号
        "auto"      → 从 style_rPr_map 按段落 pStyle 查找 rPr
        Paragraph   → 从指定 python-docx Paragraph 的第一个 run 复制 rPr
        Run         → 从指定 python-docx Run 复制 rPr
        lxml el     → 直接 deepcopy 该 rPr element

    Returns:
        插入的 w:ins element
    """
    if hasattr(para_el, "_element"):
        para_el = para_el._element

    # 解析 base_rPr
    if base_rPr is None and inherit_rPr is not False:
        resolved = _resolve_rPr(para_el, inherit_rPr, style_rPr_map)
        if resolved is _STYLE_SENTINEL:
            # 纯样式继承：构造无 rPr 的 run
            ins = make_tc_tag("w:ins", tc_id, author, date)
            r = OxmlElement("w:r")
            t = OxmlElement("w:t")
            t.text = text or ""
            if text and (text[0] == " " or text[-1] == " "):
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            r.append(t)
            ins.append(r)
            _insert_at(para_el, ins, position)
            return ins
        base_rPr = resolved

    ins = make_ins_run(text, tc_id, author,
                       bold=bold, italic=italic, highlight=highlight,
                       date=date, base_rPr=base_rPr)
    _insert_at(para_el, ins, position)
    return ins


def _insert_at(para_el, ins_el, position) -> None:
    """将 ins_el 插入 para_el 的指定位置。"""
    runs = para_el.findall(qn("w:r"))

    if position == "end" or not runs:
        para_el.append(ins_el)
    elif position == "start":
        first_content = (para_el.findall(qn("w:r")) or
                         para_el.findall(qn("w:ins")))
        if first_content:
            idx = list(para_el).index(first_content[0])
            para_el.insert(idx, ins_el)
        else:
            para_el.append(ins_el)
    elif isinstance(position, int):
        all_runs = para_el.findall(qn("w:r"))
        if position < len(all_runs):
            ref = all_runs[position]
            idx = list(para_el).index(ref) + 1
            para_el.insert(idx, ins_el)
        else:
            para_el.append(ins_el)
    else:
        para_el.append(ins_el)


def tc_ins_mixed(
    para_el,
    segments: list[tuple[str, bool]],
    tc_id: int,
    author: str,
    cfg=None,
    inherit_rPr=False,
    style_rPr_map: dict | None = None,
    date: str | None = None,
) -> list[OxmlElement]:
    """
    在段落末尾以 TC INS 形式插入混合内容：普通文字 + 律所 Note。

    Args:
        segments:  [(text, is_note), ...]
                   is_note=True → 自动叠加 B+I+HL，并加 note_prefix/suffix
        cfg:       DocConfig（提供 note_prefix/suffix/highlight）
        inherit_rPr: 普通文字 run 的 rPr 继承策略（同 tc_ins_text）

    Returns:
        插入的 w:ins elements 列表
    """
    if hasattr(para_el, "_element"):
        para_el = para_el._element

    note_prefix  = (getattr(cfg, "note_prefix",  "[JT Note: ") if cfg else "[JT Note: ")
    note_suffix  = (getattr(cfg, "note_suffix",  "]")          if cfg else "]")
    note_hl      = (getattr(cfg, "note_highlight","yellow")     if cfg else "yellow")
    _date        = date or _utc_now()

    # 解析 base_rPr（普通文字用）
    base_rPr = None
    if inherit_rPr is not False:
        resolved = _resolve_rPr(para_el, inherit_rPr, style_rPr_map)
        if resolved is not _STYLE_SENTINEL:
            base_rPr = resolved

    inserted = []
    for text, is_note in segments:
        if not text:
            continue
        if is_note:
            full_text = f"{note_prefix}{text}{note_suffix}"
            ins = make_ins_run(full_text, tc_id, author,
                               bold=True, italic=True, highlight=note_hl,
                               date=_date, base_rPr=base_rPr)
        else:
            ins = make_ins_run(text, tc_id, author,
                               date=_date, base_rPr=base_rPr)
        para_el.append(ins)
        inserted.append(ins)
        tc_id += 1

    return inserted


def _para_full_text(para_el) -> str:
    """获取段落完整文本，将 <w:tab/> 转为 \\t。"""
    parts = []
    for child in para_el.iter():
        tag = child.tag
        if tag == qn("w:t"):
            parts.append(child.text or "")
        elif tag == qn("w:tab"):
            parts.append("\t")
    return "".join(parts)


def _get_all_runs_with_pos(para_el) -> list[tuple]:
    pos = 0
    result = []
    for run_el in para_el.iter(qn("w:r")):
        text_parts = []
        for child in run_el.iter():
            if child.tag == qn("w:t"):
                text_parts.append(child.text or "")
            elif child.tag == qn("w:tab"):
                text_parts.append("\t")
        text = "".join(text_parts)
        result.append((run_el, pos, pos + len(text)))
        pos += len(text)
    return result


def _set_xml_space(t_el, text: str):
    _XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
    if text and (text[0] == " " or text[-1] == " "):
        t_el.set(_XML_SPACE, "preserve")
    else:
        t_el.attrib.pop(_XML_SPACE, None)


def _split_run_el(run_el, offset: int) -> tuple:
    t_el = run_el.find(qn("w:t"))
    if t_el is None:
        return run_el, None
    full = t_el.text or ""
    if offset <= 0:
        return None, run_el
    if offset >= len(full):
        return run_el, None

    left_text = full[:offset]
    right_text = full[offset:]
    t_el.text = left_text
    _set_xml_space(t_el, left_text)

    right_el = deepcopy(run_el)
    right_t = right_el.find(qn("w:t"))
    right_t.text = right_text
    _set_xml_space(right_t, right_text)
    run_el.addnext(right_el)
    return run_el, right_el


def tc_replace_first_in_para(
    para_el,
    old_text: str,
    new_text: str,
    tc_id: int,
    author: str,
    date: str | None = None,
    inherit_rPr: bool = True,
    style_rPr_map: dict | None = None,
    occurrence: int = 1,
    after_text: str | None = None,
    before_text: str | None = None,
):
    """
    在单段内对第一次命中的 old_text 做细颗粒度 TC 替换：
    - 仅将命中片段包成 w:del
    - 在其后插入 w:ins(new_text)

    当前版本按 paragraph 的 w:t 文本层定位，适合普通正文/定义条款中
    的精细替换；若 old_text 跨复杂 field/comment 边界，返回 ok=False。
    """
    if hasattr(para_el, "_element"):
        para_el = para_el._element

    full = _para_full_text(para_el)
    if not old_text:
        return {"ok": False, "reason": "empty_old_text"}
    search_from = 0
    if after_text:
        pos = full.find(after_text)
        if pos < 0:
            return {"ok": False, "reason": "after_text_not_found"}
        search_from = pos + len(after_text)

    hits = []
    pos = search_from
    while True:
        pos = full.find(old_text, pos)
        if pos < 0:
            break
        if before_text:
            bpos = full.find(before_text, pos + len(old_text))
            if bpos < 0:
                pos += len(old_text)
                continue
        hits.append(pos)
        pos += max(len(old_text), 1)

    if not hits:
        return {"ok": False, "reason": "old_text_not_found"}
    if occurrence <= 0 or occurrence > len(hits):
        return {"ok": False, "reason": "occurrence_out_of_range", "hits": len(hits)}
    start = hits[occurrence - 1]
    end = start + len(old_text)

    # split right boundary first, then left boundary
    for run_el, r_start, r_end in _get_all_runs_with_pos(para_el):
        if r_start < end < r_end:
            _split_run_el(run_el, end - r_start)
            break
    for run_el, r_start, r_end in _get_all_runs_with_pos(para_el):
        if r_start < start < r_end:
            _split_run_el(run_el, start - r_start)
            break

    target_runs = []
    for run_el, r_start, r_end in _get_all_runs_with_pos(para_el):
        if r_start >= start and r_end <= end and r_start < r_end:
            target_runs.append(run_el)

    if not target_runs:
        return {"ok": False, "reason": "target_runs_empty"}

    first_target = target_runs[0]
    base_rPr = first_target.find(qn("w:rPr"))
    if base_rPr is not None:
        base_rPr = deepcopy(base_rPr)
    elif inherit_rPr is not False:
        resolved = _resolve_rPr(para_el, inherit_rPr, style_rPr_map)
        if resolved is not _STYLE_SENTINEL:
            base_rPr = resolved

    _date = date or _utc_now()
    first_anchor = first_target
    while first_anchor.getparent() is not None and first_anchor.getparent() is not para_el:
        first_anchor = first_anchor.getparent()
    first_insert_idx = list(para_el).index(first_anchor)

    # wrap matched runs into del elements in place
    del_ids = []
    local_tc_id = tc_id
    for run_el in list(target_runs):
        for t_el in run_el.findall(qn("w:t")):
            t_el.tag = qn("w:delText")
        del_wrap = make_tc_tag("w:del", local_tc_id, author, _date)
        anchor = run_el
        while anchor.getparent() is not None and anchor.getparent() is not para_el:
            anchor = anchor.getparent()
        idx = list(para_el).index(anchor)
        para_el.remove(anchor)
        del_wrap.append(anchor)
        para_el.insert(idx, del_wrap)
        del_ids.append(str(local_tc_id))
        local_tc_id += 1

    ins = make_ins_run(new_text, local_tc_id, author, date=_date, base_rPr=base_rPr)
    para_el.insert(first_insert_idx + len(target_runs), ins)

    return {
        "ok": True,
        "start": start,
        "end": end,
        "deleted_ids": del_ids,
        "inserted_id": str(local_tc_id),
        "old_text": old_text,
        "new_text": new_text,
        "occurrence": occurrence,
        "hits": len(hits),
        "after_text": after_text,
        "before_text": before_text,
    }


def _extract_rpr_attrs(rPr_el) -> dict:
    if rPr_el is None:
        return {}
    attrs = {}
    rFonts = rPr_el.find(qn("w:rFonts"))
    if rFonts is not None:
        for key, qname_attr in (("eastAsia", qn("w:eastAsia")), ("ascii", qn("w:ascii")), ("hAnsi", qn("w:hAnsi"))):
            val = rFonts.get(qname_attr)
            if val:
                attrs[key] = val
    for tag_name, key in (("w:sz", "sz"), ("w:szCs", "szCs")):
        el = rPr_el.find(qn(tag_name))
        if el is not None and el.get(qn("w:val")):
            attrs[key] = el.get(qn("w:val"))
    attrs["bold"] = rPr_el.find(qn("w:b")) is not None or rPr_el.find(qn("w:bCs")) is not None
    attrs["italic"] = rPr_el.find(qn("w:i")) is not None or rPr_el.find(qn("w:iCs")) is not None
    return attrs


def _style_rpr_for_para(para_el, style_rPr_map: dict | None):
    if not style_rPr_map:
        return None
    pPr = para_el.find(qn("w:pPr"))
    pStyle = pPr.find(qn("w:pStyle")) if pPr is not None else None
    style_name = pStyle.get(qn("w:val"), "") if pStyle is not None else ""
    entry = style_rPr_map.get(style_name)
    if isinstance(entry, dict):
        return make_rPr_from_dict(entry)
    return deepcopy(entry) if entry is not None else None


def assess_para_format_context(para_el, candidate_rPr=None, style_rPr_map: dict | None = None) -> dict:
    """程序级格式感知：输出 candidate/邻域/样式三层上下文，并给出可决策提示。"""
    if hasattr(para_el, "_element"):
        para_el = para_el._element

    candidate_attrs = _extract_rpr_attrs(candidate_rPr)
    all_runs = []
    for run_el in para_el.iter(qn("w:r")):
        rPr = run_el.find(qn("w:rPr"))
        attrs = _extract_rpr_attrs(rPr)
        text = "".join(t.text or "" for t in run_el.findall(qn("w:t")))
        all_runs.append({"el": run_el, "attrs": attrs, "text": text})

    context_runs = [r["attrs"] for r in all_runs if r["attrs"]]

    def _majority(key):
        vals = [r.get(key) for r in context_runs if r.get(key) not in (None, "")]
        if not vals:
            return None
        from collections import Counter
        return Counter(vals).most_common(1)[0][0]

    majority = {
        "eastAsia": _majority("eastAsia"),
        "ascii": _majority("ascii"),
        "hAnsi": _majority("hAnsi"),
        "sz": _majority("sz"),
        "szCs": _majority("szCs"),
    }

    style_rPr = _style_rpr_for_para(para_el, style_rPr_map)
    style_attrs = _extract_rpr_attrs(style_rPr)

    # 近邻：优先看前后最近的显式 rPr
    neighbor_before = {}
    neighbor_after = {}
    if candidate_rPr is not None:
        candidate_parent = candidate_rPr.getparent() if hasattr(candidate_rPr, 'getparent') else None
        candidate_run = candidate_parent if candidate_parent is not None and candidate_parent.tag == qn("w:r") else None
        run_els = [r["el"] for r in all_runs]
        if candidate_run in run_els:
            idx = run_els.index(candidate_run)
            for j in range(idx - 1, -1, -1):
                if all_runs[j]["attrs"]:
                    neighbor_before = all_runs[j]["attrs"]
                    break
            for j in range(idx + 1, len(all_runs)):
                if all_runs[j]["attrs"]:
                    neighbor_after = all_runs[j]["attrs"]
                    break

    warnings = []
    assumptions = []
    decisions = []

    for key, label in (("eastAsia", "eastAsia字体"), ("ascii", "ascii字体"), ("hAnsi", "hAnsi字体"), ("sz", "字号"), ("szCs", "字号(szCs)")):
        got = candidate_attrs.get(key)
        left = neighbor_before.get(key)
        right = neighbor_after.get(key)
        maj = majority.get(key)
        sty = style_attrs.get(key)

        strong_expected = left if left and right and left == right else None
        expected = strong_expected or maj or sty

        if got and expected and got != expected:
            severity = "strong" if strong_expected and got != strong_expected else "review"
            warnings.append({
                "severity": severity,
                "key": key,
                "message": f"{label} 与上下文不一致：candidate={got!r}, expected={expected!r}",
                "neighbor_before": left,
                "neighbor_after": right,
                "majority": maj,
                "style": sty,
            })
        elif not got and expected:
            assumptions.append({
                "key": key,
                "message": f"{label} 未显式设置；若需与上下文一致，可考虑对齐到 {expected!r}",
                "neighbor_before": left,
                "neighbor_after": right,
                "majority": maj,
                "style": sty,
            })

    if warnings:
        if any(w["severity"] == "strong" for w in warnings):
            decisions.append("high_risk_mismatch")
        else:
            decisions.append("review_needed")
    elif assumptions:
        decisions.append("assumption_check")
    else:
        decisions.append("aligned_or_no_context")

    return {
        "ok": len(warnings) == 0,
        "candidate": candidate_attrs,
        "neighbor_before": neighbor_before,
        "neighbor_after": neighbor_after,
        "context_majority": majority,
        "style_context": style_attrs,
        "warnings": warnings,
        "assumptions": assumptions,
        "decisions": decisions,
    }


def cleanup_empty_runs_in_para(para_el) -> int:
    """清理段落中不承载任何文本、tab、br 的空 run；尽量不动有修订意义的包装层。"""
    if hasattr(para_el, "_element"):
        para_el = para_el._element
    removed = 0
    changed = True
    while changed:
        changed = False
        for run_el in list(para_el.iter(qn("w:r"))):
            text_bits = ''.join((t.text or '') for t in run_el.findall(qn('w:t')) + run_el.findall(qn('w:delText')))
            has_payload = bool(text_bits) or run_el.find(qn('w:tab')) is not None or run_el.find(qn('w:br')) is not None
            if has_payload:
                continue
            parent = run_el.getparent()
            if parent is None:
                continue
            # if wrapped by w:ins/w:del and becomes empty, remove wrapper instead of naked run only
            target = run_el
            if parent.tag in (qn('w:ins'), qn('w:del')):
                parent.remove(run_el)
                if len(parent) == 0:
                    gp = parent.getparent()
                    if gp is not None:
                        gp.remove(parent)
                removed += 1
                changed = True
                break
            else:
                parent.remove(run_el)
                removed += 1
                changed = True
                break
    return removed
