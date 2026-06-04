"""
toc_ops.py — 目录（TOC）生成与刷新，全程无 python-docx。

lex_docx toc <docx> --generate
lex_docx toc <docx> --refresh

技术路径：直接注入 OOXML TOC field（<w:fldSimple> / <w:fldChar>），
基于 document.xml 中的 Heading1/2/3 样式或 w:outlineLvl 段落。
"""
from __future__ import annotations

import zipfile
import tempfile
import os
from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W    = f"{{{W_NS}}}"


def _build_toc_field(level_from: int = 1, level_to: int = 3,
                     style_names: tuple[str, ...] = ("Heading 1", "Heading 2", "Heading 3")) -> etree._Element:
    """Build a <w:p> containing a TOC field that covers heading levels."""
    p = etree.Element(f"{W}p")
    pPr = etree.SubElement(p, f"{W}pPr")
    pStyle = etree.SubElement(pPr, f"{W}pStyle")
    pStyle.set(f"{W}val", "TOC Heading")

    # Field begin
    r1 = etree.SubElement(p, f"{W}r")
    rPr1 = etree.SubElement(r1, f"{W}rPr")
    etree.SubElement(rPr1, f"{W}i")
    fld1 = etree.SubElement(r1, f"{W}fldChar")
    fld1.set(f"{W}fldCharType", "begin")

    r2 = etree.SubElement(p, f"{W}r")
    instr = etree.SubElement(r2, f"{W}instrText")
    instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instr.text = f' TOC \\o "{level_from}-{level_to}" \\h \\z \\u '

    r3 = etree.SubElement(p, f"{W}r")
    fld3 = etree.SubElement(r3, f"{W}fldChar")
    fld3.set(f"{W}fldCharType", "separate")

    # Placeholder line
    r4 = etree.SubElement(p, f"{W}r")
    t4 = etree.SubElement(r4, f"{W}t")
    t4.text = "[请在 Word 中右键点击目录，选择「更新域」以生成完整目录]"

    r5 = etree.SubElement(p, f"{W}r")
    fld5 = etree.SubElement(r5, f"{W}fldChar")
    fld5.set(f"{W}fldCharType", "end")

    return p


def toc_generate(docx_path: str,
                 level_from: int = 1,
                 level_to: int = 3,
                 position: str = "after-title",
                 out: str | None = None) -> dict:
    """
    在文档中生成 TOC 域。

    Args:
        docx_path:  目标文档
        level_from: 起始级别（1=Heading1）
        level_to:   结束级别（3=Heading3）
        position:   插入位置：after-title / end-of-body
        out:        输出路径（None=就地覆盖）

    Returns:
        {"ok": True, "toc_inserted": True, "note": "..."}
    """
    output = out or docx_path

    # Build TOC field paragraph
    toc_p = _build_toc_field(level_from, level_to)

    # Also need a TOC Heading style — add to styles.xml if missing
    def _add_toc_style(styles_bytes: bytes) -> bytes:
        root = etree.fromstring(styles_bytes)
        existing = [s.get(f"{{{W_NS}}}styleId") for s in root.iter(f"{W}style")]
        if "TOC Heading" not in existing:
            ts = etree.SubElement(root, f"{W}style")
            ts.set(f"{W}type", "paragraph")
            ts.set(f"{W}styleId", "TOC Heading")
            tn = etree.SubElement(ts, f"{W}name")
            tn.set(f"{W}val", "toc heading")
            etree.SubElement(ts, f"{W}basedOn").set(f"{W}val", "Normal")
            etree.SubElement(ts, f"{W}uiPriority").set(f"{W}val", "99")
            etree.SubElement(ts, f"{W}unhideWhenUsed")
            rpr = etree.SubElement(ts, f"{W}rPr")
            etree.SubElement(rpr, f"{W}b")
            etree.SubElement(rpr, f"{W}sz").set(f"{W}val", "28")
            etree.SubElement(rpr, f"{W}szCs").set(f"{W}val", "28")
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    fd, tmp = tempfile.mkstemp(prefix="lex_docx_toc.", suffix=".docx")
    os.close(fd)

    with zipfile.ZipFile(docx_path, "r") as zin, \
         zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "word/document.xml":
                root = etree.fromstring(data)
                body = root.find(f".//{W}body")
                if body is not None:
                    body_children = list(body)
                    if position == "after-title":
                        # Insert after the first non-empty Heading1 paragraph
                        inserted = False
                        for i, child in enumerate(body_children):
                            if child.tag == f"{W}p":
                                pStyle = child.find(f".//{W}pStyle")
                                style_val = pStyle.get(f"{{{W_NS}}}val", "") if pStyle is not None else ""
                                if style_val in ("Heading1", "1", "Title"):
                                    body.insert(i + 1, toc_p)
                                    inserted = True
                                    break
                        if not inserted:
                            # Fallback: insert after first paragraph
                            body.insert(1 if len(body_children) > 1 else 0, toc_p)
                    else:
                        sectPr = body.find(f"{W}sectPr")
                        if sectPr is not None:
                            body.insert(list(body).index(sectPr), toc_p)
                        else:
                            body.append(toc_p)
                    data = etree.tostring(root, xml_declaration=True,
                                        encoding="UTF-8", standalone=True)
            elif info.filename == "word/styles.xml":
                data = _add_toc_style(data)
            zout.writestr(info, data)

    os.replace(tmp, output)
    return {
        "ok": True,
        "toc_inserted": True,
        "note": "TOC 域已注入。请在 Word 中打开文档，右键点击目录并选择「更新域」以生成完整目录。",
        "output": output,
    }


def toc_refresh(docx_path: str, out: str | None = None) -> dict:
    """
    刷新已有 TOC 域：将 TOC instrText 段后面的占位文本刷新，
    提示用户在 Word 中更新域。
    """
    output = out or docx_path
    hit = 0

    fd, tmp = tempfile.mkstemp(prefix="lex_docx_toc_refresh.", suffix=".docx")
    os.close(fd)

    with zipfile.ZipFile(docx_path, "r") as zin, \
         zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "word/document.xml":
                root = etree.fromstring(data)
                body = root.find(f".//{W}body")
                if body is not None:
                    # Iterate paragraphs and find those with TOC instrText
                    for p in body.iter(f"{W}p"):
                        instr_texts = [it.text or "" for it in p.iter(f"{W}instrText")]
                        if not any("TOC" in it for it in instr_texts):
                            continue
                        # Found TOC paragraph — find the run AFTER 'separate'
                        p_children = list(p)
                        sep_run_idx = None
                        for idx, child in enumerate(p_children):
                            if child.tag == f"{W}r":
                                fld_chars = [fc for fc in child.iter(f"{W}fldChar")]
                                for fc in fld_chars:
                                    if fc.get(f"{{{W_NS}}}fldCharType") == "separate":
                                        sep_run_idx = idx
                                        break
                                if sep_run_idx is not None:
                                    break
                        if sep_run_idx is not None:
                            # Look at runs after the separate-run
                            for sib in p_children[sep_run_idx + 1:]:
                                if sib.tag == f"{W}r":
                                    for t in sib.iter(f"{W}t"):
                                        if t.text and "[" in t.text:
                                            t.text = "[请在 Word 中右键点击目录，选择「更新域」以生成完整目录]"
                                            hit += 1
                                            break
                                elif sib.tag == f"{W}fldChar":
                                    # Hit end-of-field before finding placeholder
                                    break
                data = etree.tostring(root, xml_declaration=True,
                                    encoding="UTF-8", standalone=True)
            zout.writestr(info, data)

    os.replace(tmp, output)
    return {
        "ok": True,
        "toc_refreshed": hit > 0,
        "note": (
            f"已标记 {hit} 处 TOC 需在 Word 中更新域。"
            if hit > 0
            else "未找到 TOC 域，请使用 --generate 生成。"
        ),
        "output": output,
    }
