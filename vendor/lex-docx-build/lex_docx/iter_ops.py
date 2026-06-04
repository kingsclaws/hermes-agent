from __future__ import annotations

from copy import deepcopy
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm


def _iter_block_items(document):
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            yield ('p', child)
        elif child.tag == qn('w:tbl'):
            yield ('tbl', child)


def _p_text(p_el):
    texts = []
    for t in p_el.iter(qn('w:t')):
        texts.append(t.text or '')
    return ''.join(texts)


def _num_type(text: str, has_numpr: bool) -> str:
    if has_numpr:
        return 'auto-numbering'
    s = (text or '').strip()
    bullets = ('•', '·', '●', '○', '-', '—', '*')
    if s.startswith(bullets):
        return 'manual-bullet'
    import re
    if re.match(r'^[0-9]+[.)、]\s*', s) or re.match(r'^[（(][0-9一二三四五六七八九十]+[）)]', s):
        return 'manual-numbering'
    return 'plain'


def export_structure(docx_path: str, preview_len: int = 120, tree: bool = False, include_table_cells: bool = False, table_cell_preview_len: int = 60) -> dict:
    doc = Document(docx_path)
    items = []
    para_idx = -1
    tbl_idx = -1
    for kind, el in _iter_block_items(doc):
        if kind == 'p':
            para_idx += 1
            txt = _p_text(el)
            pPr = el.find(qn('w:pPr'))
            style = None
            has_numpr = False
            outline = None
            if pPr is not None:
                pStyle = pPr.find(qn('w:pStyle'))
                if pStyle is not None:
                    style = pStyle.get(qn('w:val'))
                has_numpr = pPr.find(qn('w:numPr')) is not None
                ol = pPr.find(qn('w:outlineLvl'))
                if ol is not None:
                    outline = ol.get(qn('w:val'))
            level = 0
            if outline is not None:
                try:
                    level = int(outline) + 1
                except Exception:
                    level = 0
            items.append({
                'kind': 'paragraph',
                'block_index': len(items),
                'para_index': para_idx,
                'para_id': el.get(qn('w14:paraId')),
                'style': style,
                'outline_level': outline,
                'numbering': _num_type(txt, has_numpr),
                'text_preview': txt if preview_len == 0 else txt[:preview_len],
                'text_len': len(txt),
                'level': level,
                'block_path': f'body/p[{para_idx}]',
            })
        else:
            tbl_idx += 1
            rows = el.findall('.//' + qn('w:tr'))
            cells = el.findall('.//' + qn('w:tc'))
            table_item = {
                'kind': 'table',
                'block_index': len(items),
                'table_index': tbl_idx,
                'rows': len(rows),
                'cells': len(cells),
                'level': 0,
                'block_path': f'body/tbl[{tbl_idx}]',
            }
            if include_table_cells:
                row_items = []
                tr_nodes = el.findall(qn('w:tr'))
                for r_i, tr in enumerate(tr_nodes):
                    tc_nodes = tr.findall(qn('w:tc'))
                    for c_i, tc in enumerate(tc_nodes):
                        cell_txt = ''.join((t.text or '') for t in tc.iter(qn('w:t')))
                        row_items.append({
                            'row': r_i,
                            'col': c_i,
                            'path': f'body/tbl[{tbl_idx}]/tr[{r_i}]/tc[{c_i}]',
                            'text_preview': cell_txt if table_cell_preview_len == 0 else cell_txt[:table_cell_preview_len],
                            'text_len': len(cell_txt),
                        })
                table_item['cells_preview'] = row_items
            items.append(table_item)
    out = {'ok': True, 'docx': docx_path, 'items': items, 'counts': {'paragraphs': para_idx+1, 'tables': tbl_idx+1, 'blocks': len(items)}}
    if tree:
        out['tree'] = [{'idx': i['block_index'], 'path': i.get('block_path'), 'kind': i['kind'], 'level': i.get('level', 0), 'preview': i.get('text_preview','')} for i in items]
    return out


def _get_body_paras(body):
    return [c for c in body.iterchildren() if c.tag == qn('w:p')]


def insert_blank_paragraph(docx_path: str, para: int, position: str = 'after', count: int = 1, out: str | None = None, dry_run: bool = False) -> dict:
    doc = Document(docx_path)
    body = doc.element.body
    paras = _get_body_paras(body)
    if para < 0 or para >= len(paras):
        raise ValueError(f'para out of range: {para} (0..{len(paras)-1})')
    anchor = paras[para]
    inserted = 0
    for _ in range(max(1, count)):
        newp = OxmlElement('w:p')
        if position == 'before':
            anchor.addprevious(newp)
        else:
            anchor.addnext(newp)
            anchor = newp
        inserted += 1
    save_to = out or docx_path
    if dry_run:
        return {'ok': True, 'dry_run': True, 'would_insert': inserted, 'position': position, 'anchor_para': para, 'save_to': save_to, 'change_report': {'op':'para-insert','count': inserted, 'anchor_para': para}}
    doc.save(save_to)
    return {'ok': True, 'inserted': inserted, 'save_to': save_to, 'position': position, 'anchor_para': para, 'change_report': {'op':'para-insert','count': inserted, 'anchor_para': para}}


def insert_page_break(docx_path: str, para: int, out: str | None = None, dry_run: bool = False) -> dict:
    doc = Document(docx_path)
    body = doc.element.body
    paras = _get_body_paras(body)
    if para < 0 or para >= len(paras):
        raise ValueError(f'para out of range: {para} (0..{len(paras)-1})')
    anchor = paras[para]
    p = OxmlElement('w:p')
    r = OxmlElement('w:r')
    br = OxmlElement('w:br')
    br.set(qn('w:type'), 'page')
    r.append(br)
    p.append(r)
    anchor.addnext(p)
    save_to = out or docx_path
    if dry_run:
        return {'ok': True, 'dry_run': True, 'would_insert': 'page-break', 'after_para': para, 'save_to': save_to, 'change_report': {'op':'page-break','after_para': para}}
    doc.save(save_to)
    return {'ok': True, 'save_to': save_to, 'after_para': para, 'change_report': {'op':'page-break','after_para': para}}


def insert_section_break(docx_path: str, para: int, out: str | None = None, dry_run: bool = False) -> dict:
    doc = Document(docx_path)
    body = doc.element.body
    paras = _get_body_paras(body)
    if para < 0 or para >= len(paras):
        raise ValueError(f'para out of range: {para} (0..{len(paras)-1})')
    anchor = paras[para]
    body_sect = body.find(qn('w:sectPr'))
    if body_sect is None:
        raise ValueError('sectPr not found in body')
    pPr = anchor.find(qn('w:pPr'))
    if pPr is None:
        pPr = OxmlElement('w:pPr')
        anchor.insert(0, pPr)
    for old in pPr.findall(qn('w:sectPr')):
        pPr.remove(old)
    pPr.append(deepcopy(body_sect))
    save_to = out or docx_path
    if dry_run:
        return {'ok': True, 'dry_run': True, 'would_insert': 'section-break', 'after_para': para, 'save_to': save_to, 'type': 'next-page-section', 'change_report': {'op':'section-break','after_para': para}}
    doc.save(save_to)
    return {'ok': True, 'save_to': save_to, 'after_para': para, 'type': 'next-page-section', 'change_report': {'op':'section-break','after_para': para}}


def apply_page_setup(docx_path: str, scope: str = 'document', section: int | None = None, section_start: int | None = None, section_end: int | None = None,
                     paper: str = 'A4', orientation: str = 'portrait',
                     margin_top_mm: float | None = None, margin_bottom_mm: float | None = None,
                     margin_left_mm: float | None = None, margin_right_mm: float | None = None,
                     out: str | None = None, dry_run: bool = False) -> dict:
    doc = Document(docx_path)
    sec_targets = []
    if scope == 'section':
        if section is None:
            raise ValueError('--section is required when --scope section')
        sec_targets = [doc.sections[section]]
    elif scope == 'section-range':
        if section_start is None or section_end is None:
            raise ValueError('--section-start/--section-end are required when --scope section-range')
        all_secs = list(doc.sections)
        sec_targets = all_secs[section_start:section_end+1]
        if not sec_targets:
            raise ValueError('empty section range')
    else:
        sec_targets = list(doc.sections)

    paper_map = {'A4': (210, 297), 'A3': (297, 420), 'LETTER': (216, 279)}
    pw, ph = paper_map.get((paper or 'A4').upper(), (210, 297))

    for s in sec_targets:
        if (orientation or 'portrait').lower() == 'landscape':
            s.orientation = WD_ORIENT.LANDSCAPE
            s.page_width = Mm(ph)
            s.page_height = Mm(pw)
        else:
            s.orientation = WD_ORIENT.PORTRAIT
            s.page_width = Mm(pw)
            s.page_height = Mm(ph)
        if margin_top_mm is not None:
            s.top_margin = Mm(float(margin_top_mm))
        if margin_bottom_mm is not None:
            s.bottom_margin = Mm(float(margin_bottom_mm))
        if margin_left_mm is not None:
            s.left_margin = Mm(float(margin_left_mm))
        if margin_right_mm is not None:
            s.right_margin = Mm(float(margin_right_mm))

    save_to = out or docx_path
    if dry_run:
        return {'ok': True, 'dry_run': True, 'save_to': save_to, 'scope': scope, 'sections_changed': len(sec_targets), 'paper': paper.upper(), 'orientation': orientation, 'change_report': {'op':'page-setup','scope': scope, 'sections_changed': len(sec_targets)}}
    doc.save(save_to)
    return {'ok': True, 'save_to': save_to, 'scope': scope, 'sections_changed': len(sec_targets), 'paper': paper.upper(), 'orientation': orientation, 'change_report': {'op':'page-setup','scope': scope, 'sections_changed': len(sec_targets)}}
