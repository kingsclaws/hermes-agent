"""
comment_ops.py — Word 批注（Comment）操作：添加、列出、删除。

Word comments are stored in word/comments.xml and referenced from
word/document.xml via w:commentRangeStart / w:commentRangeEnd / w:commentReference.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


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


def add_comment(docx_path: str, para: int, text: str, *,
                author: str = "agent",
                output: str | None = None) -> CommentResult:
    """
    Add a Word comment to a paragraph.

    para: paragraph index (0-based, matches python-docx)
    text: comment text
    author: comment author name
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
    crs = etree.Element(f"{W}commentRangeStart")
    crs.set(f"{W}id", str(cmt_id))
    cre = etree.Element(f"{W}commentRangeEnd")
    cre.set(f"{W}id", str(cmt_id))
    cref = etree.Element(f"{W}r")
    cr_an = etree.SubElement(cref, f"{W}commentReference")
    cr_an.set(f"{W}id", str(cmt_id))

    # Insert at end of paragraph: commentRangeStart, commentRangeEnd, then commentReference run
    p.append(crs)
    p.append(cre)
    p.append(cref)

    other["word/comments.xml"] = etree.tostring(cmt_root, xml_declaration=True,
                                                  encoding="UTF-8", standalone=True)
    _write_docx_zf(out, etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                        standalone=True), other)
    return CommentResult(ok=True, message=f"Comment {cmt_id} added to paragraph {para}",
                         data=[{"id": cmt_id, "author": author, "text": text}],
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

    # Build comment → para map via commentRangeStart in document
    cmt_para: dict[int, int] = {}
    para_num = 0
    for child in doc_root.iter(f"{W}p"):
        para_num += 1
        for crs in child.iter(f"{W}commentRangeStart"):
            try:
                cid = int(crs.get(f"{W}id", ""))
                cmt_para[cid] = para_num
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
