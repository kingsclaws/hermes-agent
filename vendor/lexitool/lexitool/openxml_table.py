"""Table structure operations for WordprocessingML documents.

Operations that modify table structure: delete tables, merge/split cells,
insert/delete rows and columns.  All operations work directly on the OOXML
zipfile + lxml layer.

Merged cells in OOXML use:
  - w:gridSpan (horizontal) — the cell spans N grid columns
  - w:vMerge (vertical)    — "restart" starts a merge, "continue" continues it
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from copy import deepcopy
from typing import Any

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


# ── Internal helpers ───────────────────────────────────────────────────────────


def _find_table_el(body: etree._Element, table_index: int) -> etree._Element | None:
    """Find the Nth w:tbl child of body (0-indexed)."""
    count = 0
    for child in body:
        if child.tag == f"{W}tbl":
            if count == table_index:
                return child
            count += 1
    return None


def _ensure_element(parent: etree._Element, tag: str, index: int = 0) -> etree._Element:
    """Return existing child with *tag*, or create and insert one."""
    el = parent.find(tag)
    if el is None:
        el = etree.Element(tag)
        parent.insert(index, el)
    return el


def _clear_cell_text(tc_el: etree._Element) -> None:
    """Remove all w:r elements from a cell, preserving w:pPr and w:tcPr."""
    for p_el in tc_el.findall(f"{W}p"):
        pPr = p_el.find(f"{W}pPr")
        for child in list(p_el):
            if child is not pPr:
                p_el.remove(child)


def _clone_row(row_el: etree._Element, clear_text: bool = True) -> etree._Element:
    """Deep-copy a w:tr element, optionally clearing cell text."""
    new_tr = deepcopy(row_el)
    if clear_text:
        for tc_el in new_tr.findall(f"{W}tc"):
            _clear_cell_text(tc_el)
            # Strip vMerge from clone to avoid unintended continuation
            tcPr = tc_el.find(f"{W}tcPr")
            if tcPr is not None:
                vmerge = tcPr.find(f"{W}vMerge")
                if vmerge is not None:
                    tcPr.remove(vmerge)
    return new_tr


def _ensure_tcPr(tc_el: etree._Element) -> etree._Element:
    return _ensure_element(tc_el, f"{W}tcPr", 0)


def _count_table_rows(tbl_el: etree._Element) -> int:
    return len(tbl_el.findall(f"{W}tr"))


def _count_row_cols(tr_el: etree._Element) -> int:
    return len(tr_el.findall(f"{W}tc"))


# ── Public API ────────────────────────────────────────────────────────────────


def delete_table(
    docx_path: str,
    table_index: int,
    *,
    output: str | None = None,
) -> dict:
    """Remove a table from the document by index (0-based)."""
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    tbl_el = _find_table_el(body, table_index)
    if tbl_el is None:
        return {"ok": False, "error": f"table {table_index} not found"}

    nrows = _count_table_rows(tbl_el)
    body.remove(tbl_el)

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_del_table.", suffix=".docx")
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

    return {"ok": True, "table_index": table_index, "rows_removed": nrows, "path": out_path}


def merge_cells(
    docx_path: str,
    table_index: int,
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
    *,
    output: str | None = None,
) -> dict:
    """Merge a rectangular region of cells.

    Uses gridSpan for horizontal span and vMerge for vertical span.
    All indices are 0-based.  end_row/end_col are inclusive.
    """
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    tbl_el = _find_table_el(body, table_index)
    if tbl_el is None:
        return {"ok": False, "error": f"table {table_index} not found"}

    rows = tbl_el.findall(f"{W}tr")
    if start_row >= len(rows) or end_row >= len(rows):
        return {"ok": False, "error": "row index out of range"}

    colspan = end_col - start_col + 1
    rowspan = end_row - start_row + 1

    for ri in range(start_row, end_row + 1):
        tr = rows[ri]
        cells = tr.findall(f"{W}tc")

        if ri == start_row:
            # First row of merge region — set gridSpan and vMerge restart
            for ci in range(start_col, end_col + 1):
                if ci >= len(cells):
                    break
                tc = cells[ci]
                tcPr = _ensure_tcPr(tc)

                if ci == start_col:
                    # Anchor cell — gridSpan + vMerge restart
                    if colspan > 1:
                        gs = tcPr.find(f"{W}gridSpan")
                        if gs is not None:
                            tcPr.remove(gs)
                        gs = etree.SubElement(tcPr, f"{W}gridSpan")
                        gs.set(f"{W}val", str(colspan))
                    if rowspan > 1:
                        vm = tcPr.find(f"{W}vMerge")
                        if vm is not None:
                            tcPr.remove(vm)
                        vm = etree.SubElement(tcPr, f"{W}vMerge")
                        vm.set(f"{W}val", "restart")
                else:
                    # Covered cell in first row — remove it later
                    pass
        else:
            # Subsequent rows
            for ci in range(start_col, end_col + 1):
                if ci >= len(cells):
                    break
                tc = cells[ci]
                tcPr = _ensure_tcPr(tc)

                if ci == start_col:
                    # Vertical continuation
                    vm = tcPr.find(f"{W}vMerge")
                    if vm is not None:
                        tcPr.remove(vm)
                    vm = etree.SubElement(tcPr, f"{W}vMerge")
                    # "continue" or just presence (empty = continue in some readers)
                    # Word prefers explicit "continue"
                    vm.set(f"{W}val", "")
                # Other cells in non-first rows are covered — remove them

    # Remove covered cells from the first row (cols after start_col)
    if colspan > 1:
        tr = rows[start_row]
        cells = list(tr.findall(f"{W}tc"))
        for ci in reversed(range(start_col + 1, min(end_col + 1, len(cells)))):
            tr.remove(cells[ci])

    # Remove covered cells from subsequent rows
    if rowspan > 1:
        for ri in range(start_row + 1, end_row + 1):
            tr = rows[ri]
            cells = list(tr.findall(f"{W}tc"))
            for ci in reversed(range(start_col, min(end_col + 1, len(cells)))):
                tr.remove(cells[ci])

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_merge_cells.", suffix=".docx")
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

    return {"ok": True, "table_index": table_index,
            "start_row": start_row, "start_col": start_col,
            "end_row": end_row, "end_col": end_col,
            "path": out_path}


def split_cell(
    docx_path: str,
    table_index: int,
    row: int,
    col: int,
    *,
    output: str | None = None,
) -> dict:
    """Undo a merge at the given cell position.

    Removes gridSpan and vMerge from the cell, and inserts placeholder
    cells to fill the space that was covered by the merge.
    """
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    tbl_el = _find_table_el(body, table_index)
    if tbl_el is None:
        return {"ok": False, "error": f"table {table_index} not found"}

    rows = tbl_el.findall(f"{W}tr")
    if row >= len(rows):
        return {"ok": False, "error": "row index out of range"}

    tr = rows[row]
    cells = tr.findall(f"{W}tc")
    if col >= len(cells):
        return {"ok": False, "error": "column index out of range"}

    tc = cells[col]
    tcPr = tc.find(f"{W}tcPr")

    # Read current merge state
    colspan = 1
    rowspan = 1

    if tcPr is not None:
        gs = tcPr.find(f"{W}gridSpan")
        if gs is not None:
            colspan = int(gs.get(f"{W}val", "1"))
            tcPr.remove(gs)

        vm = tcPr.find(f"{W}vMerge")
        if vm is not None:
            vm_val = vm.get(f"{W}val", "")
            tcPr.remove(vm)
            # If this is a restart, count how many rows are continued
            if vm_val == "restart":
                for ri in range(row + 1, len(rows)):
                    r_cells = rows[ri].findall(f"{W}tc")
                    if col < len(r_cells):
                        r_tcPr = r_cells[col].find(f"{W}tcPr")
                        if r_tcPr is not None:
                            r_vm = r_tcPr.find(f"{W}vMerge")
                            if r_vm is not None:
                                rowspan += 1
                                r_tcPr.remove(r_vm)
                            else:
                                break
                        else:
                            break
                    else:
                        break

    # Insert placeholder cells for the freed columns in this row
    if colspan > 1:
        tc_idx = list(tr).index(tc)
        for i in range(1, colspan):
            new_tc = etree.Element(f"{W}tc")
            new_tcPr = etree.SubElement(new_tc, f"{W}tcPr")
            p = etree.SubElement(new_tc, f"{W}p")
            tr.insert(tc_idx + i, new_tc)

    # Insert placeholder cells for freed rows
    if rowspan > 1:
        for ri in range(row + 1, row + rowspan):
            if ri < len(rows):
                r = rows[ri]
                for i in range(colspan):
                    new_tc = etree.Element(f"{W}tc")
                    new_tcPr = etree.SubElement(new_tc, f"{W}tcPr")
                    p = etree.SubElement(new_tc, f"{W}p")
                    r.append(new_tc)

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_split_cell.", suffix=".docx")
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

    return {"ok": True, "table_index": table_index,
            "row": row, "col": col,
            "split_colspan": colspan, "split_rowspan": rowspan,
            "path": out_path}


def insert_table_row(
    docx_path: str,
    table_index: int,
    position: int,
    row_data: list[str] | None = None,
    *,
    template_row: int | None = None,
    output: str | None = None,
) -> dict:
    """Insert a row into a table at the given position (0-based, after header).

    Clones an existing row to preserve formatting.  If position >= number of
    rows, appends at the end.
    """
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    tbl_el = _find_table_el(body, table_index)
    if tbl_el is None:
        return {"ok": False, "error": f"table {table_index} not found"}

    rows = tbl_el.findall(f"{W}tr")
    nrows = len(rows)
    if nrows == 0:
        return {"ok": False, "error": "table has no rows"}

    # Choose template row for cloning
    tpl_idx = template_row if template_row is not None else (0 if position > 0 else 0)
    if tpl_idx >= nrows:
        tpl_idx = nrows - 1
    new_tr = _clone_row(rows[tpl_idx], clear_text=True)

    # Fill in row_data if provided
    if row_data:
        cells = new_tr.findall(f"{W}tc")
        for ci, text in enumerate(row_data):
            if ci >= len(cells):
                break
            p_el = cells[ci].find(f"{W}p")
            if p_el is None:
                p_el = etree.SubElement(cells[ci], f"{W}p")
            r = etree.SubElement(p_el, f"{W}r")
            t = etree.SubElement(r, f"{W}t")
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            t.text = text

    # Insert at position
    if position >= nrows:
        tbl_el.append(new_tr)
        actual_position = nrows
    else:
        tbl_el.insert(position, new_tr)
        actual_position = position

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_insert_row.", suffix=".docx")
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

    return {"ok": True, "table_index": table_index,
            "inserted_at": actual_position, "path": out_path}


def delete_table_row(
    docx_path: str,
    table_index: int,
    row_index: int,
    *,
    output: str | None = None,
) -> dict:
    """Delete a row from a table by index (0-based)."""
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    tbl_el = _find_table_el(body, table_index)
    if tbl_el is None:
        return {"ok": False, "error": f"table {table_index} not found"}

    rows = tbl_el.findall(f"{W}tr")
    if row_index >= len(rows):
        return {"ok": False, "error": f"row {row_index} out of range (table has {len(rows)} rows)"}

    tbl_el.remove(rows[row_index])

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_del_row.", suffix=".docx")
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

    return {"ok": True, "table_index": table_index,
            "row_deleted": row_index, "path": out_path}


def insert_table_column(
    docx_path: str,
    table_index: int,
    position: int,
    *,
    output: str | None = None,
) -> dict:
    """Insert a column into a table at the given position.

    Adds a w:tc to each row at the position, and adds a w:gridCol to tblGrid.
    Position is 0-based; use -1 to append at the end.
    """
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    tbl_el = _find_table_el(body, table_index)
    if tbl_el is None:
        return {"ok": False, "error": f"table {table_index} not found"}

    rows = tbl_el.findall(f"{W}tr")

    # Determine actual position and col width to use
    if rows and position < 0:
        position = _count_row_cols(rows[0])

    # Try to determine a reasonable width from the grid
    col_width = "2400"  # default ~1 inch in twips
    grid = tbl_el.find(f"{W}tblGrid")
    if grid is not None:
        gcols = grid.findall(f"{W}gridCol")
        if gcols:
            # Use the average of existing column widths
            widths = [int(gc.get(f"{W}val", "2400")) for gc in gcols]
            col_width = str(sum(widths) // len(widths))
        # Add a new gridCol
        gc = etree.Element(f"{W}gridCol")
        gc.set(f"{W}w", col_width)
        if position < len(gcols):
            grid.insert(position, gc)
        else:
            grid.append(gc)

    for tr in rows:
        cells = tr.findall(f"{W}tc")
        new_tc = etree.Element(f"{W}tc")
        new_tcPr = etree.SubElement(new_tc, f"{W}tcPr")
        # Set column width
        tcW = etree.SubElement(new_tcPr, f"{W}tcW")
        tcW.set(f"{W}w", col_width)
        tcW.set(f"{W}type", "dxa")
        p = etree.SubElement(new_tc, f"{W}p")

        if position < len(cells):
            tr.insert(position, new_tc)
        else:
            tr.append(new_tc)

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_insert_col.", suffix=".docx")
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

    return {"ok": True, "table_index": table_index,
            "column_inserted_at": position, "path": out_path}


def delete_table_column(
    docx_path: str,
    table_index: int,
    col_index: int,
    *,
    output: str | None = None,
) -> dict:
    """Delete a column from a table by index (0-based)."""
    out_path = output or docx_path

    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": False, "error": "document has no body"}

    tbl_el = _find_table_el(body, table_index)
    if tbl_el is None:
        return {"ok": False, "error": f"table {table_index} not found"}

    # Remove gridCol
    grid = tbl_el.find(f"{W}tblGrid")
    if grid is not None:
        gcols = grid.findall(f"{W}gridCol")
        if col_index < len(gcols):
            grid.remove(gcols[col_index])

    # Remove cell from each row
    for tr in tbl_el.findall(f"{W}tr"):
        cells = tr.findall(f"{W}tc")
        if col_index < len(cells):
            tr.remove(cells[col_index])

    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_del_col.", suffix=".docx")
    os.close(fd)
    content_types_key = "[Content_Types].xml"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct = other.get(content_types_key)
        if ct is not None:
            zf.writestr(content_types_key, ct)
        zf.writestr("word/document.xml", doc_xml_out)
        for name, data in other.items():
            if name == content_types_key:
                continue
            zf.writestr(name, data)
    shutil.move(tmp, out_path)

    return {"ok": True, "table_index": table_index,
            "column_deleted": col_index, "path": out_path}


def list_tables(docx_path: str) -> dict:
    """List all tables in the document with basic metadata."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return {"ok": True, "tables": []}

    tables: list[dict[str, Any]] = []
    for tbl_el in body.findall(f"{W}tbl"):
        rows = tbl_el.findall(f"{W}tr")
        nrows = len(rows)
        ncols = _count_row_cols(rows[0]) if rows else 0

        # Read header text from first row
        header_texts: list[str] = []
        if rows:
            for tc in rows[0].findall(f"{W}tc"):
                texts = [t.text or "" for t in tc.iter(f"{W}t")]
                header_texts.append("".join(texts))

        # Total cell count
        total_cells = sum(len(tr.findall(f"{W}tc")) for tr in rows)

        tables.append({
            "index": len(tables),
            "rows": nrows,
            "cols": ncols,
            "total_cells": total_cells,
            "header_preview": header_texts[:6],  # first 6 header texts
        })

    return {"ok": True, "tables": tables}
