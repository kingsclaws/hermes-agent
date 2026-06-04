"""
doc_stats.py — 快速文档摘要，全程无 python-docx。

lex_docx stats <docx>  输出：
- 段落数 / 表格数 / 图片数（InlineDrawing）
- Track Changes 总数（ins / del）
- 字体分布
- 表格内文字总览
"""
from __future__ import annotations

import zipfile
from lxml import etree
from collections import Counter

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W    = f"{{{W_NS}}}"


def doc_stats(docx_path: str) -> dict:
    tc_ins = 0
    tc_del = 0
    para_count = 0
    table_count = 0
    image_count = 0
    fonts: Counter = Counter()
    table_summaries: list[dict] = []

    with zipfile.ZipFile(docx_path, "r") as zf:
        root = etree.fromstring(zf.read("word/document.xml"))

        for el in root.iter():
            if el.tag == f"{W}p":
                para_count += 1
                for rpr in el.iter(f"{W}rPr"):
                    for rFonts in rpr.iter(f"{W}rFonts"):
                        for attr in ("ascii", "hAnsi", "eastAsia"):
                            v = rFonts.get(f"{{{W_NS}}}{attr}")
                            if v:
                                fonts[v] += 1
            elif el.tag == f"{W}tbl":
                table_count += 1
                rows = list(el.iter(f"{W}tr"))
                cells_texts: list[str] = []
                for row in rows:
                    row_txt = " | ".join(
                        "".join(t.text or "" for t in tc.iter(f"{W}t"))
                        for tc in row.iter(f"{W}tc")
                    )
                    cells_texts.append(row_txt)
                table_summaries.append({
                    "table_index": table_count - 1,
                    "rows": len(rows),
                    "preview": " || ".join(cells_texts[:3]),
                })
            elif el.tag == f"{W}ins":
                tc_ins += 1
            elif el.tag == f"{W}del":
                tc_del += 1

        image_count = sum(1 for _ in root.iter(f"{W}drawing"))

    return {
        "ok": True,
        "paragraphs": para_count,
        "tables": table_count,
        "images": image_count,
        "tc_total": tc_ins + tc_del,
        "tc_ins": tc_ins,
        "tc_del": tc_del,
        "font_distribution": dict(fonts.most_common(10)),
        "table_summaries": table_summaries,
    }
