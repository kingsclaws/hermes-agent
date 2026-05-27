"""
openxml_core.py — OpenXML-first core helpers for lex_docx

目标：
- 统一 paragraph index 语义（以 python-docx doc.paragraphs 为 CLI 主坐标）
- 直接基于 OpenXML 渲染段落预览（含 Track Changes 标记）
- 提供稳定的 revision / comment / range 辅助函数

说明：
- 本模块不负责 CLI 输出，只负责 OpenXML 真相层。
- 现阶段先服务 tc_ops / review inspect；后续可继续承接 numbering / clean / safe replace。
"""
from __future__ import annotations

from dataclasses import dataclass
from docx.oxml.ns import qn


COMMENTS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
HEADERS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"


@dataclass(frozen=True)
class RevisionRecord:
    el: object
    tc_type: str
    level: str
    para: int | None
    tc_id: str
    author: str
    date: str
    text: str


@dataclass(frozen=True)
class CommentRecord:
    comment_id: str
    author: str
    initials: str
    date: str
    text: str
    para: int | None
    para_text: str
    quoted_text: str


def el_key(el) -> str | None:
    if el is None:
        return None
    try:
        return el.getroottree().getpath(el)
    except Exception:
        return None


def author_ok(el, author_filter: str | None) -> bool:
    return author_filter is None or el.get(qn("w:author")) == author_filter


def ancestor_paragraph(el):
    cur = el
    while cur is not None:
        if cur.tag == qn("w:p"):
            return cur
        cur = cur.getparent()
    return None


def in_para_range(para_idx: int | None, para_range: tuple[int, int] | None) -> bool:
    if para_range is None:
        return True
    if para_idx is None:
        return False
    return para_range[0] <= para_idx < para_range[1]


def paragraph_index_map(doc) -> dict[str, int]:
    """Return {stable_path(<w:p>): index_in_doc_paragraphs}."""
    result: dict[str, int] = {}
    for idx, para in enumerate(doc.paragraphs):
        key = el_key(para._element)
        if key:
            result[key] = idx
    return result


def paragraph_preview_map(doc, max_len: int = 120) -> dict[int, str]:
    result: dict[int, str] = {}
    for idx, para in enumerate(doc.paragraphs):
        text = node_preview_text(para._element)
        result[idx] = text[:max_len]
    return result


def paragraph_preview_slice(doc, para_range: tuple[int, int], max_len: int = 120) -> list[dict]:
    lo, hi = para_range
    preview_map = paragraph_preview_map(doc, max_len=max_len)
    return [
        {"index": idx, "text": preview_map.get(idx, "")}
        for idx in range(lo, min(hi, len(doc.paragraphs)))
    ]


def node_preview_text(el) -> str:
    if el.tag in (qn("w:t"), qn("w:delText")):
        return el.text or ""
    if el.tag == qn("w:tab"):
        return "	"
    if el.tag == qn("w:br"):
        return "\n"
    if el.tag == qn("w:ins"):
        inner = "".join(node_preview_text(child) for child in el)
        return f"[+{inner}+]" if inner else ""
    if el.tag == qn("w:del"):
        inner = "".join(node_preview_text(child) for child in el)
        return f"[-{inner}-]" if inner else ""
    return "".join(node_preview_text(child) for child in el)


def collect_body_ins(body, author_filter: str | None):
    result = []
    for ins_el in body.iter(qn("w:ins")):
        parent = ins_el.getparent()
        if parent is not None and parent.tag == qn("w:trPr"):
            continue
        if author_ok(ins_el, author_filter):
            result.append(ins_el)
    return result


def collect_body_del(body, author_filter: str | None, *, skip_para_mark: bool = True):
    result = []
    for del_el in body.iter(qn("w:del")):
        parent = del_el.getparent()
        if parent is None:
            continue
        if parent.tag == qn("w:trPr"):
            continue
        if skip_para_mark and parent.tag == qn("w:rPr"):
            gp = parent.getparent()
            if gp is not None and gp.tag == qn("w:pPr"):
                continue
        if author_ok(del_el, author_filter):
            result.append(del_el)
    return result


def revision_level(el) -> str:
    parent = el.getparent()
    if parent is not None and parent.tag == qn("w:trPr"):
        return "row"
    if parent is not None and parent.tag == qn("w:rPr"):
        gp = parent.getparent()
        if gp is not None and gp.tag == qn("w:pPr"):
            return "para_mark"
    return "text"


def revision_text(el, tc_type: str, max_len: int = 120) -> str:
    text_tag = qn("w:t") if tc_type == "ins" else qn("w:delText")
    parts: list[str] = []
    for t_el in el.iter(text_tag):
        if t_el.text:
            parts.append(t_el.text)
    return "".join(parts)[:max_len]


def iter_revision_records(doc, author_filter: str | None = None,
                          para_range: tuple[int, int] | None = None,
                          type_filter: str | None = None):
    para_map = paragraph_index_map(doc)
    for el in doc.element.body.iter():
        if el.tag not in (qn("w:ins"), qn("w:del")):
            continue
        if not author_ok(el, author_filter):
            continue
        tc_type = "ins" if el.tag == qn("w:ins") else "del"
        if type_filter is not None and tc_type != type_filter:
            continue
        p_el = ancestor_paragraph(el)
        para_idx = para_map.get(el_key(p_el)) if p_el is not None else None
        if not in_para_range(para_idx, para_range):
            continue
        yield RevisionRecord(
            el=el,
            tc_type=tc_type,
            level=revision_level(el),
            para=para_idx,
            tc_id=el.get(qn("w:id"), "?"),
            author=el.get(qn("w:author"), ""),
            date=el.get(qn("w:date"), ""),
            text=revision_text(el, tc_type),
        )


def quoted_text_map(doc, para_range: tuple[int, int] | None = None) -> dict[str, str]:
    para_map = paragraph_index_map(doc)
    starts: dict[str, object] = {}
    quoted: dict[str, str] = {}

    for el in doc.element.body.iter():
        if el.tag == qn("w:commentRangeStart"):
            cid = el.get(qn("w:id"), "")
            p_el = ancestor_paragraph(el)
            para_idx = para_map.get(el_key(p_el)) if p_el is not None else None
            if cid and in_para_range(para_idx, para_range):
                starts[cid] = el
        elif el.tag == qn("w:commentRangeEnd"):
            cid = el.get(qn("w:id"), "")
            start_el = starts.get(cid)
            if not cid or start_el is None:
                continue
            start_parent = start_el.getparent()
            end_parent = el.getparent()
            if start_parent is None or end_parent is None or start_parent is not end_parent:
                continue
            siblings = list(start_parent)
            try:
                i0 = siblings.index(start_el)
                i1 = siblings.index(el)
            except ValueError:
                continue
            if i1 <= i0:
                continue
            parts: list[str] = []
            for sib in siblings[i0 + 1:i1]:
                for t_el in sib.iter(qn("w:t")):
                    if t_el.text:
                        parts.append(t_el.text)
            quoted[cid] = "".join(parts)[:200]
    return quoted


def comment_paragraph_map(doc, para_range: tuple[int, int] | None = None) -> dict[str, int]:
    para_map = paragraph_index_map(doc)
    comment_to_para: dict[str, int] = {}
    body = doc.element.body
    for el in body.iter():
        if el.tag == qn("w:commentRangeStart"):
            cid = el.get(qn("w:id"), "")
            p_el = ancestor_paragraph(el)
            para_idx = para_map.get(el_key(p_el)) if p_el is not None else None
            if cid and in_para_range(para_idx, para_range):
                comment_to_para[cid] = para_idx
        elif el.tag == qn("w:commentReference"):
            cid = el.get(qn("w:id"), "")
            p_el = ancestor_paragraph(el)
            para_idx = para_map.get(el_key(p_el)) if p_el is not None else None
            if cid and cid not in comment_to_para and in_para_range(para_idx, para_range):
                comment_to_para[cid] = para_idx
    return comment_to_para


def iter_comment_records(doc, author_filter: str | None = None,
                         para_range: tuple[int, int] | None = None,
                         text_max_len: int = 400):
    preview_map = paragraph_preview_map(doc)
    quoted_map = quoted_text_map(doc, para_range=para_range)
    comment_to_para = comment_paragraph_map(doc, para_range=para_range)

    try:
        for rel in doc.part.rels.values():
            if COMMENTS_REL not in rel.reltype:
                continue
            comments_root = rel.target_part._element
            for comment_el in comments_root.findall(qn("w:comment")):
                author = comment_el.get(qn("w:author"), "")
                if author_filter is not None and author != author_filter:
                    continue
                cid = comment_el.get(qn("w:id"), "")
                para_idx = comment_to_para.get(cid)
                if not in_para_range(para_idx, para_range):
                    continue
                text = "".join(t.text or "" for t in comment_el.iter(qn("w:t")))[:text_max_len]
                yield CommentRecord(
                    comment_id=cid,
                    author=author,
                    initials=comment_el.get(qn("w:initials"), ""),
                    date=comment_el.get(qn("w:date"), ""),
                    text=text,
                    para=para_idx,
                    para_text=preview_map.get(para_idx, "") if para_idx is not None else "",
                    quoted_text=quoted_map.get(cid, ""),
                )
            break
    except Exception:
        return
