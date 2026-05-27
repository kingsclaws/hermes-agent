"""
new_table_ops.py — 插入新表格，全程无 python-docx。

支持的表格类型：
- grid    : 普通网格表（边框 + 标题行加粗）
- kv      : KV 二列表（字段名—值，底色交替）
- merged  : 含横向/纵向合并单元格（--merged-spec 描述合并）
- nested  : 含嵌套子表（--nested-spec 描述子表）
- diagonal: 斜线表头（第一行使用对角线边框）

技术路径：直接构造 OOXML <w:tbl> XML，注入到 word/document.xml。
"""
from __future__ import annotations

import json
import zipfile
import tempfile
import os
from pathlib import Path
from lxml import etree

W_NS   = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W      = f"{{{W_NS}}}"


# ── XML element builders ──────────────────────────────────────────────────────

def _rpr(sz: float = 22.0, bold: bool = False, italic: bool = False,
         font: str = "宋体") -> etree._Element:
    rpr = etree.Element(f"{W}rPr")
    rf = etree.SubElement(rpr, f"{W}rFonts")
    rf.set(f"{W}ascii", font)
    rf.set(f"{W}hAnsi", font)
    rf.set(f"{W}eastAsia", font)
    etree.SubElement(rpr, f"{W}sz").set(f"{W}val", str(int(sz)))
    etree.SubElement(rpr, f"{W}szCs").set(f"{W}val", str(int(sz)))
    if bold:
        etree.SubElement(rpr, f"{W}b")
    if italic:
        etree.SubElement(rpr, f"{W}i")
    return rpr


def _text_run(text: str, bold: bool = False, italic: bool = False,
              sz: float = 22.0, font: str = "宋体",
              center: bool = False) -> etree._Element:
    r = etree.Element(f"{W}r")
    rpr = _rpr(sz=sz, bold=bold, italic=italic, font=font)
    if center:
        jc = etree.SubElement(rpr, f"{W}jc")
        jc.set(f"{W}val", "center")
    r.append(rpr)
    t = etree.SubElement(r, f"{W}t")
    t.text = text or ""
    return r


def _para(children: list, center: bool = False,
          spacing_before: int = 0, spacing_after: int = 60,
          spacing_line: int = 240) -> etree._Element:
    p = etree.Element(f"{W}p")
    pPr = etree.SubElement(p, f"{W}pPr")
    sp = etree.SubElement(pPr, f"{W}spacing")
    sp.set(f"{W}before", str(spacing_before))
    sp.set(f"{W}after", str(spacing_after))
    sp.set(f"{W}line", str(spacing_line))
    sp.set(f"{W}lineRule", "auto")
    if center:
        jc = etree.SubElement(pPr, f"{W}jc")
        jc.set(f"{W}val", "center")
    for child in children:
        p.append(child)
    return p


def _tc(text: str, bold: bool = False, italic: bool = False,
        sz: float = 22.0, font: str = "宋体",
        center: bool = False,
        shading_fill: str = "",
        vAlign: str = "center",
        width: str = "",
        extra_tcPr_children: list | None = None) -> etree._Element:
    """Build a <w:tc> cell element."""
    tc = etree.Element(f"{W}tc")
    tcPr = etree.SubElement(tc, f"{W}tcPr")
    if width:
        tcW = etree.SubElement(tcPr, f"{W}tcW")
        tcW.set(f"{W}w", width)
        tcW.set(f"{W}type", "dxa")
    if shading_fill:
        shd = etree.SubElement(tcPr, f"{W}shd")
        shd.set(f"{W}val", "clear")
        shd.set(f"{W}color", "auto")
        shd.set(f"{W}fill", shading_fill)
    va = etree.SubElement(tcPr, f"{W}vAlign")
    va.set(f"{W}val", vAlign)
    if extra_tcPr_children:
        for child in extra_tcPr_children:
            tcPr.append(child)
    p = _para([_text_run(text, bold=bold, italic=italic, sz=sz, font=font,
                          center=center)],
              center=center)
    tc.append(p)
    return tc


def _diagonal_tc(text: str, label1: str = "", label2: str = "",
                 sz: float = 22.0, font: str = "宋体",
                 shading_fill: str = "D9D9D9") -> etree._Element:
    """
    Build a diagonal (split) header cell using top-left to bottom-right diagonal border.

    text:    display text in upper triangle
    label1:  text for lower-left region (below diagonal)
    label2:  text for upper-right region (above diagonal) — optional
    """
    tc = etree.Element(f"{W}tc")
    tcPr = etree.SubElement(tc, f"{W}tcPr")

    # Cell borders with diagonal
    tcBorders = etree.SubElement(tcPr, f"{W}tcBorders")
    for side in ("top", "left", "bottom", "right"):
        b = etree.SubElement(tcBorders, f"{W}{side}")
        b.set(f"{W}val", "single")
        b.set(f"{W}sz", "4")
        b.set(f"{W}color", "000000")
    # Diagonal: top-left to bottom-right
    tl2br = etree.SubElement(tcBorders, f"{W}tl2br")
    tl2br.set(f"{W}val", "single")
    tl2br.set(f"{W}sz", "4")
    tl2br.set(f"{W}color", "000000")

    # Width: full row width
    tcW = etree.SubElement(tcPr, f"{W}tcW")
    tcW.set(f"{W}w", "2000")
    tcW.set(f"{W}type", "dxa")

    if shading_fill:
        shd = etree.SubElement(tcPr, f"{W}shd")
        shd.set(f"{W}val", "clear")
        shd.set(f"{W}color", "auto")
        shd.set(f"{W}fill", shading_fill)

    # Two-run paragraph: upper text + lower text (visually split by diagonal)
    # Upper triangle: text right-aligned / centered
    p = etree.Element(f"{W}p")
    pPr = etree.SubElement(p, f"{W}pPr")
    etree.SubElement(pPr, f"{W}spacing").set(f"{W}line", "240")

    # Upper triangle text
    r1 = etree.SubElement(p, f"{W}r")
    rpr1 = etree.SubElement(r1, f"{W}rPr")
    rf1 = etree.SubElement(rpr1, f"{W}rFonts")
    rf1.set(f"{W}ascii", font)
    rf1.set(f"{W}hAnsi", font)
    rf1.set(f"{W}eastAsia", font)
    etree.SubElement(rpr1, f"{W}sz").set(f"{W}val", str(int(sz)))
    jc1 = etree.SubElement(rpr1, f"{W}jc")
    jc1.set(f"{W}val", "center")
    t1 = etree.SubElement(r1, f"{W}t")
    t1.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t1.text = text or ""

    # Lower-left label (smaller, italic)
    if label1:
        r2 = etree.SubElement(p, f"{W}r")
        rpr2 = etree.SubElement(r2, f"{W}rPr")
        rf2 = etree.SubElement(rpr2, f"{W}rFonts")
        rf2.set(f"{W}ascii", font)
        rf2.set(f"{W}hAnsi", font)
        rf2.set(f"{W}eastAsia", font)
        etree.SubElement(rpr2, f"{W}sz").set(f"{W}val", str(int(sz * 0.8)))
        etree.SubElement(rpr2, f"{W}i")
        jc2 = etree.SubElement(rpr2, f"{W}jc")
        jc2.set(f"{W}val", "center")
        # line break before label
        br1 = etree.SubElement(r2, f"{W}br")
        t2 = etree.SubElement(r2, f"{W}t")
        t2.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t2.text = label1

    tc.append(p)
    return tc


def _grid_col(w: str) -> etree._Element:
    gc = etree.Element(f"{W}gridCol")
    gc.set(f"{W}w", w)
    return gc


def _row(cells: list, trHeight: str = "460") -> etree._Element:
    tr = etree.Element(f"{W}tr")
    trPr = etree.SubElement(tr, f"{W}trPr")
    trH = etree.SubElement(trPr, f"{W}trHeight")
    trH.set(f"{W}val", trHeight)
    trH.set(f"{W}hRule", "atLeast")
    for cell in cells:
        tr.append(cell)
    return tr


def _tbl_borders(style: str = "single") -> etree._Element:
    bd = etree.Element(f"{W}tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = etree.SubElement(bd, f"{W}{side}")
        b.set(f"{W}val", style)
        b.set(f"{W}sz", "4")
        b.set(f"{W}space", "0")
        b.set(f"{W}color", "000000")
    return bd


def _make_tbl_element(
    rows_data: list[list[str]],
    headers: list[str] | None = None,
    style: str = "grid",
    font: str = "宋体",
    sz: float = 22.0,
    col_widths: list[str] | None = None,
    shading_alt: str = "E8E8E8",
    merged_spec: list | None = None,
    nested_spec: list | None = None,
    diagonal_labels: list | None = None,  # for diagonal type: list of lower-left labels
) -> etree._Element:
    """
    Build a <w:tbl> element from row/header data.

    merged_spec:   list of {"row": r, "col": c, "rowspan": n, "colspan": m}
    nested_spec:   list of {"row": r, "col": c, "nested_data": [[...], [...]]}
    diagonal_labels: for diagonal headers: lower-left text for each header cell
    """
    ncols = len(col_widths) if col_widths else (len(rows_data[0]) if rows_data else 1)
    if headers and len(headers) > ncols:
        ncols = len(headers)

    # Grid
    grid = etree.Element(f"{W}tblGrid")
    default_w = str(1800 if not col_widths else int(col_widths[0]))
    for w in (col_widths or [default_w] * ncols):
        grid.append(_grid_col(w))

    # Table properties
    tbl = etree.Element(f"{W}tbl")
    tblPr = etree.SubElement(tbl, f"{W}tblPr")
    tblStyle = etree.SubElement(tblPr, f"{W}tblStyle")
    tblStyle.set(f"{W}val", "TableGrid")
    tblW = etree.SubElement(tblPr, f"{W}tblW")
    total_w = sum(int(w) for w in (col_widths or [1800] * ncols))
    tblW.set(f"{W}w", str(total_w))
    tblW.set(f"{W}type", "dxa")
    tblPr.append(_tbl_borders("single"))
    tblJc = etree.SubElement(tblPr, f"{W}jc")
    tblJc.set(f"{W}val", "center")

    # Table look
    look = etree.SubElement(tblPr, f"{W}tblLook")
    look.set(f"{W}val", "04A0")
    look.set(f"{W}firstRow", "1")
    look.set(f"{W}lastRow", "0")
    look.set(f"{W}firstColumn", "1")
    look.set(f"{W}lastColumn", "0")
    look.set(f"{W}noHBand", "0")
    look.set(f"{W}noVBand", "1")

    tbl.append(grid)

    # ── merged_spec helpers ────────────────────────────────────────────────
    def _is_start(r: int, c: int) -> bool:
        if not merged_spec:
            return False
        return any(ms["row"] == r and ms["col"] == c for ms in merged_spec)

    def _is_covered(r: int, c: int) -> bool:
        if not merged_spec:
            return False
        for ms in merged_spec:
            rs, cs = ms["row"], ms["col"]
            re = rs + ms.get("rowspan", 1)
            ce = cs + ms.get("colspan", 1)
            if rs < r and cs < c and r < re and c < ce:
                return True
        return False

    def _get_merge(r: int, c: int):
        if not merged_spec:
            return None
        for ms in merged_spec:
            if ms["row"] == r and ms["col"] == c:
                return ms
        return None

    def _build_tc(text: str, row_i: int, col_i: int,
                  bold: bool = False, fill: str = "") -> etree._Element:
        ms = _get_merge(row_i, col_i)
        extra: list = []
        if ms:
            rs, cs = ms["row"], ms["col"]
            re = rs + ms.get("rowspan", 1)
            ce = cs + ms.get("colspan", 1)
            if rs < row_i or cs < col_i:
                # covered cell — only add vMerge continue
                vmerge = etree.Element(f"{W}vMerge")
                vmerge.set(f"{W}val", "continue")
                extra.append(vmerge)
            else:
                if ms.get("rowspan", 1) > 1:
                    vmerge = etree.Element(f"{W}vMerge")
                    vmerge.set(f"{W}val", "restart")
                    extra.append(vmerge)
                if ms.get("colspan", 1) > 1:
                    gs = etree.SubElement(etree.Element(f"{W}tcPr"), f"{W}gridSpan")
                    gs.set(f"{W}val", str(ms["colspan"]))
                    extra.insert(0, gs)
        return _tc(text, bold=bold, sz=sz, font=font,
                   shading_fill=fill, extra_tcPr_children=extra)

    # ── Header row ─────────────────────────────────────────────────────────
    if headers:
        if style == "diagonal":
            # Diagonal header: first row uses diagonal cells
            diag_cells: list[etree._Element] = []
            hcols = headers + [""] * (ncols - len(headers))
            d_labels = diagonal_labels or [""]
            for ci, htext in enumerate(hcols):
                lbl = d_labels[ci] if ci < len(d_labels) else ""
                dcell = _diagonal_tc(htext, label1=lbl, sz=sz,
                                      font=font, shading_fill="D9D9D9")
                diag_cells.append(dcell)
            tbl.append(_row(diag_cells))
        else:
            header_cells: list[etree._Element] = []
            hcols = headers + [""] * (ncols - len(headers))
            for ci, htext in enumerate(hcols):
                fill = "D9D9D9"
                # covered cells are skipped by _is_covered
                if _is_covered(0, ci):
                    continue
                header_cells.append(_build_tc(htext, 0, ci, bold=True, fill=fill))
            tbl.append(_row(header_cells))

    # ── Data rows ───────────────────────────────────────────────────────────
    for ri, row in enumerate(rows_data):
        row_i = ri + (1 if headers else 0)
        row_cells: list[etree._Element] = []
        rcols = row + [""] * (ncols - len(row))
        alt_fill = shading_alt if (ri % 2 == 1) else ""

        for ci, ctext in enumerate(rcols):
            if _is_covered(row_i, ci):
                # Placeholder cell for merged area
                c = _tc("", sz=sz, font=font, shading_fill=alt_fill)
                vmerge = etree.SubElement(c.find(f"{W}tcPr"), f"{W}vMerge")
                vmerge.set(f"{W}val", "continue")
                row_cells.append(c)
            else:
                # Check for nested spec
                nested_ms = None
                if nested_spec:
                    for nms in nested_spec:
                        if nms["row"] == row_i and nms["col"] == ci:
                            nested_ms = nms
                            break
                if nested_ms:
                    # Build cell with nested table
                    c = _tc("", sz=sz, font=font, shading_fill=alt_fill)
                    nested_data = nested_ms.get("nested_data", [["嵌套内容"]])
                    nested_tbl = _make_tbl_element(
                        nested_data,
                        headers=None,
                        style="grid",
                        font=font,
                        sz=sz * 0.9,
                        col_widths=None,
                        shading_alt=shading_alt,
                    )
                    c.append(nested_tbl)
                    row_cells.append(c)
                else:
                    row_cells.append(_build_tc(ctext, row_i, ci, fill=alt_fill))
        tbl.append(_row(row_cells))

    return tbl


def _insert_tbl_at_para(body: etree._Element, tbl_el: etree._Element,
                         para_index: int | None = None,
                         after_text: str | None = None) -> None:
    """
    Insert tbl_el into body.
    If para_index is given, insert after that paragraph.
    If after_text is given, find the first paragraph containing that text.
    Otherwise append at end (before sectPr).
    """
    body_children = list(body)

    if after_text is not None:
        for i, el in enumerate(body_children):
            if el.tag == f"{W}p":
                texts = [t.text for t in el.iter() if t.tag == f"{W}t" and t.text]
                joined = "".join(texts)
                if after_text in joined:
                    para_index = i
                    break

    if para_index is not None and para_index < len(body_children):
        body.insert(para_index + 1, tbl_el)
    else:
        sectPr = body.find(f"{W}sectPr")
        if sectPr is not None:
            sect_idx = list(body).index(sectPr)
            body.insert(sect_idx, tbl_el)
        else:
            body.append(tbl_el)


# ── Public API ────────────────────────────────────────────────────────────────

def insert_table(
    docx_path: str,
    at: int | None = None,
    after: str | None = None,
    type: str = "grid",
    cols: int = 2,
    rows: int = 3,
    headers: list[str] | None = None,
    data: list[list[str]] | None = None,
    col_widths: list[str] | None = None,
    merged_spec: list | None = None,
    nested_spec: list | None = None,
    diagonal_labels: list | None = None,
    font: str = "宋体",
    font_size: float = 11.0,
    shading_alt: str = "E8E8E8",
) -> dict:
    """
    在 docx 中插入新表格。

    Args:
        docx_path:       目标文件路径（会就地修改）
        at:              段落索引（从 0 起），在此段落之后插入表格
        after:           定位文字：插入到第一个包含此文字的段落之后
        type:            表格类型：grid / kv / merged / nested / diagonal
        cols:            列数（type=grid/kv 时使用）
        rows:            行数（不含表头）
        headers:         表头文本列表
        data:            数据 [[row1col1, row1col2, ...], [row2col1, ...]]
        col_widths:      每列宽度（dxa，单位 1/20 pt），如 ["2000","4000"]
        merged_spec:     合并单元格规格：
                         [{"row": r, "col": c, "rowspan": n, "colspan": m}, ...]
                         row/col 从 0 起（header 行 = 0）
                         例如表头横跨两列: [{"row":0,"col":0,"rowspan":1,"colspan":2}]
                         第一列数据行合并: [{"row":1,"col":0,"rowspan":3,"colspan":1}]
        nested_spec:     嵌套子表规格：
                         [{"row": r, "col": c, "nested_data": [[...]]}, ...]
        diagonal_labels: diagonal 表头的左下角标签列表（与 headers 对应）
        font:             字体
        font_size:        字号（pt）
        shading_alt:      奇数行底色（hex，无则 ""）

    Returns:
        {"ok": True, "table_rows": N, "table_cols": M}
    """
    sz = float(font_size) * 2  # half-points

    # Build default data if not given
    if data is None:
        data = [["单元格"] * cols for _ in range(rows)]

    if headers is None:
        headers = [f"列{i+1}" for i in range(cols)]

    if type == "kv":
        flat_data: list[list[str]] = []
        for row in data:
            if len(row) >= 2:
                flat_data.append([row[0], row[1]])
        data = flat_data if flat_data else [["键", "值"]]

    # For merged type with no explicit merged_spec, auto-generate sensible defaults
    # based on the shape of the data (e.g., first col spanning all data rows)
    if type == "merged" and not merged_spec:
        # Default: first column of data rows (row index = 1 if headers else 0)
        start_row = 1 if headers else 0
        n_data_rows = len(data)
        if n_data_rows >= 1:
            merged_spec = [
                {"row": start_row, "col": 0, "rowspan": n_data_rows, "colspan": 1}
            ]

    # Build tbl element
    tbl_el = _make_tbl_element(
        rows_data=data,
        headers=headers,
        style=type,
        font=font,
        sz=sz,
        col_widths=col_widths,
        shading_alt=shading_alt,
        merged_spec=merged_spec,
        nested_spec=nested_spec,
        diagonal_labels=diagonal_labels,
    )

    fd, tmp = tempfile.mkstemp(prefix="lex_docx_newtable.", suffix=".docx")
    os.close(fd)

    with zipfile.ZipFile(docx_path, "r") as zin, \
         zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data_bytes = zin.read(info.filename)
            if info.filename == "word/document.xml":
                root = etree.fromstring(data_bytes)
                body = root.find(f".//{W}body")
                if body is not None:
                    _insert_tbl_at_para(body, tbl_el, para_index=at, after_text=after)
                    data_bytes = etree.tostring(root, xml_declaration=True,
                                               encoding="UTF-8", standalone=True)
            zout.writestr(info, data_bytes)

    os.replace(tmp, docx_path)

    ncols = len(headers) if headers else cols
    nrows = len(data) + (1 if headers else 0)
    return {"ok": True, "table_rows": nrows, "table_cols": ncols}
