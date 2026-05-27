"""
doc_create.py — 从零创建标准 OPC 骨架的 .docx，全程无 python-docx。

覆盖场景：
- 标准 OPC 骨架（[Content_Types].xml / word/document.xml / word/styles.xml 等）
- 全局字体/行距预设
- 居中标题段落（自动加粗、字号可配）
- meta 行（案号/日期/编制人等，居中或左对齐可配）

技术路径：纯 ZIP + lxml，不依赖 python-docx。
"""
from __future__ import annotations

import zipfile
import tempfile
import os
from pathlib import Path
from lxml import etree

# ── OPC namespaces ────────────────────────────────────────────────────────────
CT_NS    = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS   = "http://schemas.openxmlformats.org/package/2006/relationships"
REL2_NS  = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W_NS     = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/><Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.table+xml"/><Override PartName="/word/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>'

_ROOT_RELS = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'

_WORD_RELS = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/></Relationships>'


def _font_face(name: str) -> str:
    """Normalise font name to west/eastAsia."""
    return name


def _make_body(title: str, meta_lines: list[str], font_face: str,
               font_size_half_pt: float, line_spacing: float,
               title_size_half_pt: float) -> bytes:
    W = f"{{{W_NS}}}"
    paras: list[etree._Element] = []

    def run(el_p: etree._Element, text: str, bold: bool = False,
            sz: float | None = None, center: bool = False,
            italic: bool = False) -> None:
        r = etree.SubElement(el_p, f"{W}r")
        if bold or italic or sz or center:
            rpr = etree.SubElement(r, f"{W}rPr")
            if bold:
                etree.SubElement(rpr, f"{W}b")
            if italic:
                etree.SubElement(rpr, f"{W}i")
            if sz:
                etree.SubElement(rpr, f"{W}sz").set(f"{W}val", str(int(sz)))
                etree.SubElement(rpr, f"{W}szCs").set(f"{W}val", str(int(sz)))
            if center:
                jc = etree.SubElement(rpr, f"{W}jc")
                jc.set(f"{W}val", "center")
        t = etree.SubElement(r, f"{W}t")
        t.text = text
        rPr = r.find(f"{W}rPr")
        if rPr is not None:
            rpr_font = etree.SubElement(rPr, f"{W}rFonts")
            rpr_font.set(f"{W}ascii", font_face)
            rpr_font.set(f"{W}hAnsi", font_face)
            rpr_font.set(f"{W}eastAsia", font_face)
            if rPr.find(f"{W}jc") is None:
                jc = etree.SubElement(rPr, f"{W}jc")
                jc.set(f"{W}val", "left")

    def para(extra_ppr_kids: list | None = None) -> etree._Element:
        p = etree.Element(f"{W}p")
        pPr = etree.SubElement(p, f"{W}pPr")
        if line_spacing and line_spacing != 1.0:
            spacing = etree.SubElement(pPr, f"{W}spacing")
            spacing.set(f"{W}line", str(int(line_spacing * 240)))
            spacing.set(f"{W}lineRule", "auto")
        if extra_ppr_kids:
            for child in extra_ppr_kids:
                pPr.append(child)
        return p

    def ppr_jc(val: str) -> etree._Element:
        jc = etree.Element(f"{W}jc")
        jc.set(f"{W}val", val)
        return jc

    def ppr_sizing(sz: float) -> list:
        sz_el = etree.Element(f"{W}sz")
        sz_el.set(f"{W}val", str(int(sz)))
        sz_el_cs = etree.Element(f"{W}szCs")
        sz_el_cs.set(f"{W}val", str(int(sz)))
        return [sz_el, sz_el_cs]

    # ── Title ──
    if title:
        p = para([ppr_jc("center"), *ppr_sizing(title_size_half_pt)])
        b = etree.SubElement(p, f"{W}pPr")
        b_rpr = etree.SubElement(b, f"{W}rPr")
        etree.SubElement(b_rpr, f"{W}b")
        etree.SubElement(b_rpr, f"{W}sz").set(f"{W}val", str(int(title_size_half_pt)))
        etree.SubElement(b_rpr, f"{W}szCs").set(f"{W}val", str(int(title_size_half_pt)))
        rFonts = etree.SubElement(b_rpr, f"{W}rFonts")
        rFonts.set(f"{W}ascii", font_face)
        rFonts.set(f"{W}hAnsi", font_face)
        rFonts.set(f"{W}eastAsia", font_face)
        r = etree.SubElement(p, f"{W}r")
        rpr = etree.SubElement(r, f"{W}rPr")
        etree.SubElement(rpr, f"{W}b")
        etree.SubElement(rpr, f"{W}sz").set(f"{W}val", str(int(title_size_half_pt)))
        etree.SubElement(rpr, f"{W}szCs").set(f"{W}val", str(int(title_size_half_pt)))
        rf = etree.SubElement(rpr, f"{W}rFonts")
        rf.set(f"{W}ascii", font_face)
        rf.set(f"{W}hAnsi", font_face)
        rf.set(f"{W}eastAsia", font_face)
        t = etree.SubElement(r, f"{W}t")
        t.text = title
        paras.append(p)

    # ── Meta lines ──
    for ml in meta_lines:
        p = para([ppr_jc("center")])
        run(p, ml, sz=font_size_half_pt, center=True)
        paras.append(p)

    # ── Body (empty placeholder) ──
    p = para()
    run(p, "", sz=font_size_half_pt)
    paras.append(p)

    body = etree.Element(f"{W}body")
    for par in paras:
        body.append(par)

    # sectPr
    sect = etree.SubElement(body, f"{W}sectPr")
    pgSz = etree.SubElement(sect, f"{W}pgSz")
    pgSz.set(f"{W}w", "12240")   # A4 width in twips
    pgSz.set(f"{W}h", "15840")   # A4 height
    pgSz.set(f"{W}orient", "portrait")
    pgSz_m = etree.SubElement(sect, f"{W}pgMar")
    pgSz_m.set(f"{W}top", "1800")
    pgSz_m.set(f"{W}right", "1800")
    pgSz_m.set(f"{W}bottom", "1800")
    pgSz_m.set(f"{W}left", "1800")
    pgSz_m.set(f"{W}header", "720")
    pgSz_m.set(f"{W}footer", "720")
    pgSz_m.set(f"{W}gutter", "0")

    doc = etree.Element(f"{W}document",
                        nsmap={"w": W_NS,
                               "r": REL2_NS,
                               "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006"})
    doc.set(f"{{{REL2_NS}}}id", "{{rId1000000}}")
    doc.set(f"{{{REL2_NS}}}contentType",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")
    doc.append(body)
    return etree.tostring(doc, xml_declaration=True, encoding="UTF-8", standalone=True)


def _make_styles(font_face: str, font_size_half_pt: float,
                 line_spacing: float) -> bytes:
    W = f"{{{W_NS}}}"
    root = etree.Element(f"{W}styles",
                         nsmap={"w": W_NS})
    # default Normal style
    ns = etree.SubElement(root, f"{W}style")
    ns.set(f"{W}type", "paragraph")
    ns.set(f"{W}styleId", "Normal")
    nname = etree.SubElement(ns, f"{W}name")
    nname.set(f"{W}val", "Normal")
    nrpr = etree.SubElement(ns, f"{W}rPr")
    rf = etree.SubElement(nrpr, f"{W}rFonts")
    rf.set(f"{W}ascii", font_face)
    rf.set(f"{W}hAnsi", font_face)
    rf.set(f"{W}eastAsia", font_face)
    etree.SubElement(nrpr, f"{W}sz").set(f"{W}val", str(int(font_size_half_pt)))
    etree.SubElement(nrpr, f"{W}szCs").set(f"{W}val", str(int(font_size_half_pt)))
    if line_spacing != 1.0:
        etree.SubElement(nrpr, f"{W}spacing").set(f"{W}line",
                                                   str(int(line_spacing * 240)))

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _make_settings() -> bytes:
    W = f"{{{W_NS}}}"
    root = etree.Element(f"{W}settings",
                         nsmap={"w": W_NS})
    etree.SubElement(root, f"{W}zoom").set(f"{W}percent", "100")
    etree.SubElement(root, f"{W}proofState").set(f"{W}spelling", "clean")
    etree.SubElement(root, f"{W}defaultTabStop").set(f"{W}val", "720")
    compat = etree.SubElement(root, f"{W}compat")
    compat.set(f"{W}name", "compatibility")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _make_font_table(font_face: str) -> bytes:
    W = f"{{{W_NS}}}"
    root = etree.Element(f"{W}tbl",
                         nsmap={"w": W_NS})
    # minimal fontTable — just one font entry
    f = etree.SubElement(root, f"{W}font")
    f.set(f"{W}name", font_face)
    f.set(f"{W}panose", "020B0503020204020204")
    f.set(f"{W}charset", "0")
    f.set(f"{W}family", "roman")
    f.set(f"{W}pitch", "variable")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _make_theme() -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme"><a:themeElements><a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="1F497D"/></a:dk2><a:lt2><a:srgbClr val="EEF1F8"/></a:lt2></a:clrScheme></a:themeElements></a:theme>'


def _write_opc(output_path: str,
               title: str = "",
               meta_lines: list[str] | None = None,
               font_face: str = "宋体",
               font_size_half_pt: float = 22.0,      # 11pt
               title_size_half_pt: float = 28.0,      # 14pt
               line_spacing: float = 1.0) -> None:
    if meta_lines is None:
        meta_lines = []

    fd, tmp_path = tempfile.mkstemp(prefix="lex_docx_create.", suffix=".docx")
    os.close(fd)

    body_xml   = _make_body(title, meta_lines, font_face, font_size_half_pt,
                            line_spacing, title_size_half_pt)
    styles_xml = _make_styles(font_face, font_size_half_pt, line_spacing)
    settings_xml = _make_settings()
    font_tbl_xml = _make_font_table(font_face)
    theme_xml    = _make_theme()

    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("word/_rels/document.xml.rels", _WORD_RELS)
        zf.writestr("word/document.xml", body_xml)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/settings.xml", settings_xml)
        zf.writestr("word/fontTable.xml", font_tbl_xml)
        zf.writestr("word/theme/theme1.xml", theme_xml)

    import shutil
    try:
        os.replace(tmp_path, output_path)
    except OSError:
        # Cross-device: copy then delete temp
        shutil.copy2(tmp_path, output_path)
        os.unlink(tmp_path)


# ── Public CLI entry ─────────────────────────────────────────────────────────

def create_document(
    output: str,
    title: str = "",
    meta: str = "",
    font_song: str = "宋体",
    font_roman: str = "Times New Roman",
    font_size: float = 11.0,
    line_spacing: str = "single",
) -> dict:
    """
    创建空白 DOCX 文件。

    Args:
        output:       输出路径
        title:        文档标题（居中加粗）
        meta:         元数据行，多行用 \\n 分隔
        font_song:    中文字体
        font_roman:   西文字体
        font_size:    正文字号（pt）
        line_spacing: 行距类型，单词：single / 1.5 / double

    Returns:
        {"ok": True, "path": output}
    """
    import shlex
    meta_lines = [ln.strip() for ln in meta.split("\\n") if ln.strip()]

    spacing_map = {"single": 1.0, "1.5": 1.5, "double": 2.0}
    spacing = spacing_map.get(line_spacing, 1.0)

    font_size_half = float(font_size) * 2
    title_size_half = float(font_size) * 2 + 6   # 标题比正文大半号

    _write_opc(
        output,
        title=title,
        meta_lines=meta_lines,
        font_face=font_song,
        font_size_half_pt=font_size_half,
        title_size_half_pt=title_size_half,
        line_spacing=spacing,
    )
    return {"ok": True, "path": output}
