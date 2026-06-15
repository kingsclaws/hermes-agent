"""
comment_ops.py — Word 批注（Comment）操作：添加、列出、删除。

Word comments are stored in word/comments.xml and referenced from
word/document.xml via w:commentRangeStart / w:commentRangeEnd / w:commentReference.
"""
from __future__ import annotations

import copy
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from lxml import etree

from .openxml_opc import (
    CT_NS,
    PKG_REL_NS,
    ensure_override_content_type,
    ensure_relationship,
)
from .openxml_runmap import editable_touched_spans, render_paragraph, text_in_span

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


@dataclass
class CommentResult:
    ok: bool
    message: str = ""
    data: list[dict] = field(default_factory=list)
    path: str = ""


def _read_docx_zf(path: str) -> tuple[bytes, dict[str, bytes]]:
    with zipfile.ZipFile(path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
        other = {}
        for name in zf.namelist():
            if name != "word/document.xml":
                other[name] = zf.read(name)
    return doc_xml, other


def _write_docx_zf(path: str, doc_xml: bytes, other: dict[str, bytes]) -> None:
    fd, tmp = tempfile.mkstemp(prefix="lex_cmt.", suffix=".docx")
    import os as _os
    _os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        ct = other.get("[Content_Types].xml")
        if ct is not None:
            zf.writestr("[Content_Types].xml", ct)
        zf.writestr("word/document.xml", doc_xml)
        for name, data in other.items():
            if name == "[Content_Types].xml":
                continue
            zf.writestr(name, data)
    shutil.move(tmp, path)


OO_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

COMMENTS_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
COMMENTS_EXT_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"


def _ensure_content_type(other: dict[str, bytes], part_name: str, content_type: str) -> None:
    """Ensure [Content_Types].xml has an Override entry for the given part."""
    ct_bytes = other.get("[Content_Types].xml")
    if ct_bytes is None:
        return
    ct_root = etree.fromstring(ct_bytes)
    ensure_override_content_type(ct_root, part_name, content_type)
    other["[Content_Types].xml"] = etree.tostring(ct_root, xml_declaration=True,
                                                   encoding="UTF-8", standalone=True)


def _ensure_relationship(other: dict[str, bytes], rel_type: str, target: str) -> str:
    """Ensure word/_rels/document.xml.rels has a relationship entry. Returns the rId."""
    rels_key = "word/_rels/document.xml.rels"
    rels_bytes = other.get(rels_key)
    if rels_bytes is None:
        rels_root = etree.Element(f"{{{PKG_REL_NS}}}Relationships", nsmap={None: PKG_REL_NS})
    else:
        rels_root = etree.fromstring(rels_bytes)
    rid = ensure_relationship(rels_root, rel_type, target)
    other[rels_key] = etree.tostring(rels_root, xml_declaration=True,
                                      encoding="UTF-8", standalone=True)
    return rid


def _next_comment_id(comments_root: etree._Element) -> int:
    max_id = 0
    for el in comments_root.iter(f"{W}comment"):
        try:
            max_id = max(max_id, int(el.get(f"{W}id", "0")))
        except ValueError:
            pass
    return max_id + 1


def _ensure_comments_xml(other: dict[str, bytes]) -> tuple[bytes, bool]:
    """Ensure word/comments.xml exists; return (xml_bytes, is_new)."""
    if "word/comments.xml" in other:
        return other["word/comments.xml"], False
    comments = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
        ' mc:Ignorable="w14 w15 w16se w16cid w16 w16cex w16sdtdh"'
        ' xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
        ' xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
        "</w:comments>"
    )
    return comments.encode("utf-8"), True


def _find_para(root: etree._Element, para_idx: int) -> etree._Element | None:
    count = 0
    for el in root.iter():
        if el.tag == f"{W}p":
            if count == para_idx:
                return el
            count += 1
    return None


def _make_comment_reference_run(cmt_id: int) -> etree._Element:
    cref = etree.Element(f"{W}r")
    cr_an = etree.SubElement(cref, f"{W}commentReference")
    cr_an.set(f"{W}id", str(cmt_id))
    return cref


def _make_text_run_from_rpr(text: str, base_rPr=None) -> etree._Element:
    run = etree.Element(f"{W}r")
    if base_rPr is not None:
        run.append(copy.deepcopy(base_rPr))
    t = etree.SubElement(run, f"{W}t")
    t.text = text
    if text.startswith((" ", "\t", "\n")) or text.endswith((" ", "\t", "\n")):
        t.set(XML_SPACE, "preserve")
    return run


def _append_comment_marks(p, cmt_id: int) -> None:
    crs = etree.Element(f"{W}commentRangeStart")
    crs.set(f"{W}id", str(cmt_id))
    cre = etree.Element(f"{W}commentRangeEnd")
    cre.set(f"{W}id", str(cmt_id))
    p.append(crs)
    p.append(cre)
    p.append(_make_comment_reference_run(cmt_id))


def _insert_comment_marks_at_char_range(p, cmt_id, range_start, range_end) -> bool:
    """Insert commentRangeStart/End/Reference at a character range within a paragraph.

    Places commentRangeStart before the character at range_start,
    and commentRangeEnd + commentReference after the character at range_end.
    If range_start/range_end are None, marks are appended at paragraph end.
    """
    crs = etree.Element(f"{W}commentRangeStart")
    crs.set(f"{W}id", str(cmt_id))
    cre = etree.Element(f"{W}commentRangeEnd")
    cre.set(f"{W}id", str(cmt_id))

    if range_start is None and range_end is None:
        _append_comment_marks(p, cmt_id)
        return True

    rendered = render_paragraph(p)
    if not rendered.text:
        _append_comment_marks(p, cmt_id)
        return False

    start = 0 if range_start is None else int(range_start)
    end = len(rendered.text) if range_end is None else int(range_end)
    if start < 0 or end <= start or end > len(rendered.text):
        _append_comment_marks(p, cmt_id)
        return False

    touched = editable_touched_spans(rendered, start, end)
    if not touched:
        _append_comment_marks(p, cmt_id)
        return False

    runs: list[etree._Element] = []
    for span in touched:
        run = span.run
        if run is not None and run not in runs:
            runs.append(run)
    if not runs:
        _append_comment_marks(p, cmt_id)
        return False

    parent = runs[0].getparent()
    if parent is None or any(run.getparent() is not parent for run in runs):
        _append_comment_marks(p, cmt_id)
        return False

    insert_idx = list(parent).index(runs[0])
    new_elems: list[etree._Element] = []
    for span in touched:
        run = span.run
        if run is None:
            _append_comment_marks(p, cmt_id)
            return False
        rpr = run.find(f"{W}rPr")
        before = span.text[: max(0, start - span.start)] if span is touched[0] else ""
        middle = text_in_span(span, start, end)
        after = span.text[max(0, end - span.start):] if span is touched[-1] else ""

        if span is touched[0] and before:
            new_elems.append(_make_text_run_from_rpr(before, rpr))
        if span is touched[0]:
            new_elems.append(crs)
        if middle:
            new_elems.append(_make_text_run_from_rpr(middle, rpr))
        if span is touched[-1]:
            new_elems.append(cre)
            new_elems.append(_make_comment_reference_run(cmt_id))
            if after:
                new_elems.append(_make_text_run_from_rpr(after, rpr))

    for run in runs:
        if run.getparent() is parent:
            parent.remove(run)
    for offset, elem in enumerate(new_elems):
        parent.insert(insert_idx + offset, elem)
    return True


def add_comment(docx_path: str, para: int, text: str, *,
                author: str = "agent",
                range_start: int | None = None,
                range_end: int | None = None,
                output: str | None = None) -> CommentResult:
    """
    Add a Word comment to a paragraph.

    para: paragraph index (0-based, matches python-docx)
    text: comment text
    author: comment author name
    range_start: character offset where comment begins (None = whole paragraph)
    range_end: character offset where comment ends (None = whole paragraph)
    """
    out = output or docx_path
    if out != docx_path:
        shutil.copy2(docx_path, out)

    doc_xml, other = _read_docx_zf(out)
    root = etree.fromstring(doc_xml)
    p = _find_para(root, para)
    if p is None:
        return CommentResult(ok=False, message=f"Paragraph {para} not found", path=out)

    # Prepare comments.xml
    cmt_bytes, is_new = _ensure_comments_xml(other)
    cmt_root = etree.fromstring(cmt_bytes)

    # Ensure Content_Types and relationships are correct
    _ensure_content_type(other, "/word/comments.xml", COMMENTS_CT)
    _ensure_relationship(other,
        f"{OO_REL}/comments", "comments.xml")

    cmt_id = _next_comment_id(cmt_root)

    # Create comment element
    from datetime import datetime
    cmt = etree.SubElement(cmt_root, f"{W}comment")
    cmt.set(f"{W}id", str(cmt_id))
    cmt.set(f"{W}author", author)
    cmt.set(f"{W}date", datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    cmt.set(f"{W}initials", author[:4].upper())

    # Add paragraphs to comment
    for line in text.split("\n"):
        cmt_p = etree.SubElement(cmt, f"{W}p")
        cmt_r = etree.SubElement(cmt_p, f"{W}r")
        cmt_t = etree.SubElement(cmt_r, f"{W}t")
        cmt_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        cmt_t.text = line

    # Add comment reference marks to the paragraph
    anchored_precisely = _insert_comment_marks_at_char_range(p, cmt_id, range_start, range_end)

    other["word/comments.xml"] = etree.tostring(cmt_root, xml_declaration=True,
                                                  encoding="UTF-8", standalone=True)
    _write_docx_zf(out, etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                        standalone=True), other)
    return CommentResult(ok=True, message=f"Comment {cmt_id} added to paragraph {para}",
                         data=[{
                             "id": cmt_id,
                             "author": author,
                             "text": text,
                             "para": para,
                             "para_display": para + 1,
                             "anchored_precisely": anchored_precisely,
                         }],
                         path=out)


def list_comments(docx_path: str, *, para: int | None = None,
                  author: str | None = None) -> CommentResult:
    """List comments in a document, optionally filtered by paragraph index or author."""
    try:
        with zipfile.ZipFile(docx_path, "r") as zf:
            if "word/comments.xml" not in zf.namelist():
                return CommentResult(ok=True, message="No comments in document",
                                     data=[], path=docx_path)
            cmt_xml = zf.read("word/comments.xml")
            doc_xml = zf.read("word/document.xml")
    except Exception as e:
        return CommentResult(ok=False, message=str(e), path=docx_path)

    cmt_root = etree.fromstring(cmt_xml)
    doc_root = etree.fromstring(doc_xml)

    # Build comment → para map via commentRangeStart in document. para is 0-based;
    # para_display is 1-based for lex_read §N cross-checking.
    cmt_para: dict[int, int] = {}
    cmt_para_display: dict[int, int] = {}
    para_num = -1
    for child in doc_root.iter(f"{W}p"):
        para_num += 1
        for crs in child.iter(f"{W}commentRangeStart"):
            try:
                cid = int(crs.get(f"{W}id", ""))
                cmt_para[cid] = para_num
                cmt_para_display[cid] = para_num + 1
            except ValueError:
                pass

    results = []
    for cmt in cmt_root:
        if cmt.tag != f"{W}comment":
            continue
        cid = int(cmt.get(f"{W}id", "0"))
        c_author = cmt.get(f"{W}author", "unknown")
        c_date = cmt.get(f"{W}date", "")

        text_parts = []
        for r in cmt.iter(f"{W}r"):
            t = r.find(f"{W}t")
            if t is not None and t.text:
                text_parts.append(t.text)
        c_text = "".join(text_parts).strip()

        p_num = cmt_para.get(cid)

        if para is not None and p_num != para:
            continue
        if author is not None and c_author != author:
            continue

        results.append({
            "id": cid,
            "para": p_num,
            "para_display": cmt_para_display.get(cid),
            "author": c_author,
            "date": c_date,
            "text": c_text,
        })

    return CommentResult(ok=True, message=f"Found {len(results)} comments",
                         data=results, path=docx_path)


def remove_comment(docx_path: str, comment_id: int, *,
                   output: str | None = None) -> CommentResult:
    """Remove a single comment by ID, including its document reference marks."""
    out = output or docx_path
    if out != docx_path:
        shutil.copy2(docx_path, out)

    doc_xml, other = _read_docx_zf(out)

    # Remove from comments.xml
    if "word/comments.xml" not in other:
        return CommentResult(ok=False, message="No comments in document", path=out)
    cmt_root = etree.fromstring(other["word/comments.xml"])
    for cmt in cmt_root:
        if cmt.tag == f"{W}comment" and cmt.get(f"{W}id") == str(comment_id):
            cmt_root.remove(cmt)
            break

    # Remove comment marks from document.xml
    root = etree.fromstring(doc_xml)
    for crs in root.iter(f"{W}commentRangeStart"):
        if crs.get(f"{W}id") == str(comment_id):
            crs.getparent().remove(crs)
    for cre in root.iter(f"{W}commentRangeEnd"):
        if cre.get(f"{W}id") == str(comment_id):
            cre.getparent().remove(cre)
    for cr in root.iter(f"{W}commentReference"):
        if cr.get(f"{W}id") == str(comment_id):
            parent_run = cr.getparent()
            if parent_run is not None:
                parent_run.getparent().remove(parent_run)

    other["word/comments.xml"] = etree.tostring(cmt_root, xml_declaration=True,
                                                  encoding="UTF-8", standalone=True)
    _write_docx_zf(out, etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                        standalone=True), other)
    return CommentResult(ok=True, message=f"Comment {comment_id} removed", path=out)


def reply_comment(docx_path: str, comment_id: int, text: str, *,
                  author: str = "agent",
                  output: str | None = None) -> CommentResult:
    """Reply to an existing comment by appending a reply paragraph to its body.

    The reply appears inline within the same comment bubble in Word.
    """
    out = output or docx_path
    if out != docx_path:
        shutil.copy2(docx_path, out)

    doc_xml, other = _read_docx_zf(out)

    if "word/comments.xml" not in other:
        return CommentResult(ok=False, message="No comments in document", path=out)

    cmt_root = etree.fromstring(other["word/comments.xml"])

    # Find the target comment
    target_cmt = None
    for cmt in cmt_root:
        if cmt.tag == f"{W}comment" and cmt.get(f"{W}id") == str(comment_id):
            target_cmt = cmt
            break

    if target_cmt is None:
        return CommentResult(ok=False, message=f"Comment {comment_id} not found", path=out)

    # Add a reply separator paragraph then the reply text
    sep_p = etree.SubElement(target_cmt, f"{W}p")
    sep_r = etree.SubElement(sep_p, f"{W}r")
    sep_rPr = etree.SubElement(sep_r, f"{W}rPr")
    sep_b = etree.SubElement(sep_rPr, f"{W}b")
    sep_t = etree.SubElement(sep_r, f"{W}t")
    sep_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    sep_t.text = f"[回复 — {author}]: "

    for line in text.split("\n"):
        reply_p = etree.SubElement(target_cmt, f"{W}p")
        reply_r = etree.SubElement(reply_p, f"{W}r")
        reply_t = etree.SubElement(reply_r, f"{W}t")
        reply_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        reply_t.text = line

    other["word/comments.xml"] = etree.tostring(cmt_root, xml_declaration=True,
                                                  encoding="UTF-8", standalone=True)
    # No document.xml changes needed for reply — same comment ID, same anchors
    _write_docx_zf(out, doc_xml, other)
    return CommentResult(ok=True, message=f"Reply added to comment {comment_id}",
                         data=[{"id": comment_id, "author": author, "text": text}],
                         path=out)
