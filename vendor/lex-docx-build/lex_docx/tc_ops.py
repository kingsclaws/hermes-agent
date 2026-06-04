"""
tc_ops.py — Track Changes accept/reject and comment/header cleanup

Functions:
  list_tc(doc, author_filter)      → list all w:ins / w:del entries
  accept_all(doc, author_filter)   → accept all tracked changes
  reject_all(doc, author_filter)   → reject all tracked changes
  clean_comments(doc)              → remove comment annotations from body + comments part
  clean_headers(doc)               → clear all header text content
"""
from __future__ import annotations

from docx.oxml.ns import qn

from . import openxml_core as ox


# ────────────────────────────────────────────────────────────────────────────── #
# Helpers                                                                         #
# ────────────────────────────────────────────────────────────────────────────── #

_COMMENTS_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
)
_HEADERS_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
)


def _author_ok(el, author_filter: str | None) -> bool:
    return ox.author_ok(el, author_filter)


def _el_key(el) -> str | None:
    return ox.el_key(el)


def _body_paragraph_index_map(doc) -> dict[str, int]:
    return ox.paragraph_index_map(doc)


def _ancestor_paragraph(el):
    return ox.ancestor_paragraph(el)


def _in_para_range(para_idx: int | None, para_range: tuple[int, int] | None) -> bool:
    return ox.in_para_range(para_idx, para_range)


def _collect_body_ins(body, author_filter):
    return ox.collect_body_ins(body, author_filter)


def _collect_body_del(body, author_filter, *, skip_para_mark: bool = True):
    return ox.collect_body_del(body, author_filter, skip_para_mark=skip_para_mark)


# ────────────────────────────────────────────────────────────────────────────── #
# list_tc                                                                         #
# ────────────────────────────────────────────────────────────────────────────── #

def list_tc(doc, author_filter: str | None = None,
            para_range: tuple[int, int] | None = None,
            type_filter: str | None = None) -> list[dict]:
    """
    List all tracked changes (w:ins / w:del) in the document body.
    """
    items: list[dict] = []
    for rec in ox.iter_revision_records(
        doc,
        author_filter=author_filter,
        para_range=para_range,
        type_filter=type_filter,
    ):
        items.append({
            "id": rec.tc_id,
            "type": rec.tc_type,
            "author": rec.author,
            "date": rec.date,
            "level": rec.level,
            "para": rec.para,
            "text": rec.text,
        })
    return items


def _quoted_text_map(doc, para_range: tuple[int, int] | None = None) -> dict[str, str]:
    return ox.quoted_text_map(doc, para_range=para_range)


def _tc_candidates(doc, author_filter: str | None = None,
                   para_range: tuple[int, int] | None = None,
                   type_filter: str | None = None) -> dict[str, list]:
    """Return filtered candidate XML elements for accept/reject actions."""
    body = doc.element.body
    para_map = _body_paragraph_index_map(doc)

    def _match(el, tc_type: str) -> bool:
        if not _author_ok(el, author_filter):
            return False
        if type_filter is not None and tc_type != type_filter:
            return False
        p_el = _ancestor_paragraph(el)
        para_idx = para_map.get(_el_key(p_el)) if p_el is not None else None
        return _in_para_range(para_idx, para_range)

    ins_text = [el for el in _collect_body_ins(body, author_filter) if _match(el, "ins")]
    del_text = [el for el in _collect_body_del(body, author_filter, skip_para_mark=True) if _match(el, "del")]

    row_ins = []
    row_del = []
    para_mark_del = []

    for tbl in list(body.iter(qn("w:tbl"))):
        for tr in list(tbl.findall(qn("w:tr"))):
            trPr = tr.find(qn("w:trPr"))
            if trPr is None:
                continue
            for ins_el in list(trPr.findall(qn("w:ins"))):
                if _match(ins_el, "ins"):
                    row_ins.append(ins_el)
            for del_el in list(trPr.findall(qn("w:del"))):
                if _match(del_el, "del"):
                    row_del.append(del_el)

    for p_el in list(body.iter(qn("w:p"))):
        pPr = p_el.find(qn("w:pPr"))
        if pPr is None:
            continue
        rPr = pPr.find(qn("w:rPr"))
        if rPr is None:
            continue
        for del_el in list(rPr.findall(qn("w:del"))):
            if _match(del_el, "del"):
                para_mark_del.append(del_el)

    return {
        "ins_text": ins_text,
        "del_text": del_text,
        "row_ins": row_ins,
        "row_del": row_del,
        "para_mark_del": para_mark_del,
    }


# ────────────────────────────────────────────────────────────────────────────── #
# accept_all                                                                      #
# ────────────────────────────────────────────────────────────────────────────── #

def accept_all(doc, author_filter: str | None = None,
               para_range: tuple[int, int] | None = None,
               type_filter: str | None = None) -> dict:
    """
    Accept tracked changes in the document, optionally filtered by author / range / type.

    type_filter:
      - "ins" → only accept insertions
      - "del" → only accept deletions
      - None  → accept both
    """
    body = doc.element.body
    stats = {
        "ins_accepted": 0,
        "del_accepted": 0,
        "row_ins_accepted": 0,
        "row_del_accepted": 0,
        "para_mark_cleaned": 0,
    }
    cands = _tc_candidates(doc, author_filter, para_range, type_filter)

    # ── 1. Table-row TC ───────────────────────────────────────────────── #
    rows_to_remove: set = set()
    for ins_el in cands["row_ins"]:
        trPr = ins_el.getparent()
        if trPr is not None and ins_el.getparent() is trPr:
            trPr.remove(ins_el)
            stats["row_ins_accepted"] += 1
    for del_el in cands["row_del"]:
        trPr = del_el.getparent()
        tr = trPr.getparent() if trPr is not None else None
        if tr is not None:
            rows_to_remove.add(tr)
            stats["row_del_accepted"] += 1
    for tr in rows_to_remove:
        parent = tr.getparent()
        if parent is not None:
            parent.remove(tr)

    # ── 2. Text-level w:ins → unwrap (keep children) ─────────────────── #
    for ins_el in cands["ins_text"]:
        parent = ins_el.getparent()
        if parent is None or ins_el.getparent() is None:
            continue
        idx = list(parent).index(ins_el)
        children = list(ins_el)
        for child in children:
            ins_el.remove(child)
        for i, child in enumerate(children):
            parent.insert(idx + i, child)
        parent.remove(ins_el)
        stats["ins_accepted"] += 1

    # ── 3. Text-level w:del → remove entirely ────────────────────────── #
    for del_el in cands["del_text"]:
        parent = del_el.getparent()
        if parent is not None and del_el.getparent() is parent:
            parent.remove(del_el)
            stats["del_accepted"] += 1

    # ── 4. Paragraph-mark deletion (pPr > rPr > del) → clean marker ──── #
    for del_el in cands["para_mark_del"]:
        rPr = del_el.getparent()
        if rPr is not None and del_el.getparent() is rPr:
            rPr.remove(del_el)
            stats["para_mark_cleaned"] += 1

    return stats


# ────────────────────────────────────────────────────────────────────────────── #
# reject_all                                                                      #
# ────────────────────────────────────────────────────────────────────────────── #

def reject_all(doc, author_filter: str | None = None,
               para_range: tuple[int, int] | None = None,
               type_filter: str | None = None) -> dict:
    """
    Reject tracked changes in the document, optionally filtered by author / range / type.

    type_filter:
      - "ins" → only reject insertions
      - "del" → only reject deletions
      - None  → reject both
    """
    body = doc.element.body
    stats = {
        "ins_rejected": 0,
        "del_rejected": 0,
        "row_ins_rejected": 0,
        "row_del_rejected": 0,
        "para_mark_cleaned": 0,
    }
    cands = _tc_candidates(doc, author_filter, para_range, type_filter)

    # ── 1. Table-row TC ───────────────────────────────────────────────── #
    rows_to_remove: set = set()
    for ins_el in cands["row_ins"]:
        trPr = ins_el.getparent()
        tr = trPr.getparent() if trPr is not None else None
        if tr is not None:
            rows_to_remove.add(tr)
            stats["row_ins_rejected"] += 1
    for del_el in cands["row_del"]:
        trPr = del_el.getparent()
        if trPr is not None and del_el.getparent() is trPr:
            trPr.remove(del_el)
            stats["row_del_rejected"] += 1
    for tr in rows_to_remove:
        parent = tr.getparent()
        if parent is not None:
            parent.remove(tr)

    # ── 2. Text-level w:ins → remove entirely ────────────────────────── #
    for ins_el in cands["ins_text"]:
        parent = ins_el.getparent()
        if parent is not None and ins_el.getparent() is parent:
            parent.remove(ins_el)
            stats["ins_rejected"] += 1

    # ── 3. Text-level w:del → unwrap (restore deleted text) ──────────── #
    for del_el in cands["del_text"]:
        parent = del_el.getparent()
        if parent is None or del_el.getparent() is not parent:
            continue
        for r_el in del_el.findall(qn("w:r")):
            for dt_el in r_el.findall(qn("w:delText")):
                dt_el.tag = qn("w:t")
        idx = list(parent).index(del_el)
        children = list(del_el)
        for child in children:
            del_el.remove(child)
        for i, child in enumerate(children):
            parent.insert(idx + i, child)
        parent.remove(del_el)
        stats["del_rejected"] += 1

    # ── 4. Paragraph-mark deletion (pPr > rPr > del) → restore ──────── #
    for del_el in cands["para_mark_del"]:
        rPr = del_el.getparent()
        if rPr is not None and del_el.getparent() is rPr:
            rPr.remove(del_el)
            stats["para_mark_cleaned"] += 1

    return stats


# ────────────────────────────────────────────────────────────────────────────── #
# clean_comments                                                                  #
# ────────────────────────────────────────────────────────────────────────────── #

def clean_comments(doc) -> dict:
    """
    Remove all comment annotations from the document.

    Removes from body:
      - w:commentRangeStart / w:commentRangeEnd elements
      - Runs containing w:commentReference, w:annotationRef, or
        rStyle val="CommentReference"

    Clears the comments OPC part if present (removes all w:comment children).

    Returns:
        dict with counts: range_starts, range_ends, ref_runs, comments_cleared
    """
    return clean_comments_filtered(doc)


def _node_preview_text(el) -> str:
    return ox.node_preview_text(el)


def _para_preview_map(doc, max_len: int = 120) -> dict[int, str]:
    return ox.paragraph_preview_map(doc, max_len=max_len)


def list_comments(doc, author_filter: str | None = None,
                  para_range: tuple[int, int] | None = None) -> list[dict]:
    """List all comments in the document via OpenXML-first records."""
    items: list[dict] = []
    for rec in ox.iter_comment_records(
        doc,
        author_filter=author_filter,
        para_range=para_range,
    ):
        items.append({
            "id": rec.comment_id,
            "author": rec.author,
            "initials": rec.initials,
            "date": rec.date,
            "text": rec.text,
            "para": rec.para,
            "para_text": rec.para_text,
            "quoted_text": rec.quoted_text,
        })
    return items


def clean_comments_filtered(doc, author_filter: str | None = None,
                            para_range: tuple[int, int] | None = None,
                            comment_ids: list[str] | None = None) -> dict:
    """
    Remove comment annotations, optionally filtered by author / paragraph range / explicit ids.

    Notes:
      - When filters are provided, only matching comment IDs are removed from body/comments part.
      - If neither filter is provided, behavior matches the old clean_comments(doc).
    """
    body = doc.element.body
    para_map = _body_paragraph_index_map(doc)
    stats = {
        "range_starts": 0,
        "range_ends": 0,
        "ref_runs": 0,
        "comments_cleared": 0,
        "comment_ids": [],
    }

    # 快路：无过滤时，保留原全量清理行为
    if author_filter is None and para_range is None:
        # ── 1. Remove w:commentRangeStart / w:commentRangeEnd ────────────── #
        for tag, key in (
            (qn("w:commentRangeStart"), "range_starts"),
            (qn("w:commentRangeEnd"), "range_ends"),
        ):
            for el in list(body.iter(tag)):
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)
                    stats[key] += 1

        # ── 2. Remove comment-reference runs ─────────────────────────────── #
        for r_el in list(body.iter(qn("w:r"))):
            is_comment_run = (
                r_el.find(qn("w:commentReference")) is not None
                or r_el.find(qn("w:annotationRef")) is not None
            )
            if not is_comment_run:
                rPr = r_el.find(qn("w:rPr"))
                if rPr is not None:
                    rStyle = rPr.find(qn("w:rStyle"))
                    if rStyle is not None and rStyle.get(qn("w:val")) == "CommentReference":
                        is_comment_run = True
            if is_comment_run:
                parent = r_el.getparent()
                if parent is not None:
                    parent.remove(r_el)
                    stats["ref_runs"] += 1

        # ── 3. Clear comments OPC part ────────────────────────────────────── #
        try:
            for rel in doc.part.rels.values():
                if _COMMENTS_REL in rel.reltype:
                    comments_root = rel.target_part._element
                    for comment_el in list(comments_root):
                        comments_root.remove(comment_el)
                    stats["comments_cleared"] += 1
                    break
        except Exception:
            pass
        return stats

    # 过滤模式：先确定要删的 comment IDs
    target_ids: set[str] = set(comment_ids or [])
    try:
        for rel in doc.part.rels.values():
            if _COMMENTS_REL not in rel.reltype:
                continue
            comments_root = rel.target_part._element
            for comment_el in comments_root.findall(qn("w:comment")):
                author = comment_el.get(qn("w:author"), "")
                cid = comment_el.get(qn("w:id"), "")
                if comment_ids is not None:
                    if cid in target_ids:
                        target_ids.add(cid)
                    continue
                if author_filter is not None and author != author_filter:
                    continue
                target_ids.add(cid)
            break
    except Exception:
        pass

    if para_range is not None:
        ranged_ids: set[str] = set()
        for el in body.iter():
            if el.tag not in (qn("w:commentRangeStart"), qn("w:commentReference")):
                continue
            cid = el.get(qn("w:id"), "")
            p_el = _ancestor_paragraph(el)
            para_idx = para_map.get(_el_key(p_el)) if p_el is not None else None
            if cid and _in_para_range(para_idx, para_range):
                ranged_ids.add(cid)
        target_ids = ranged_ids if author_filter is None else (target_ids & ranged_ids)

    stats["comment_ids"] = sorted(target_ids)
    if not target_ids:
        return stats

    # 1) 删 body 中的 range markers
    for tag, key in (
        (qn("w:commentRangeStart"), "range_starts"),
        (qn("w:commentRangeEnd"), "range_ends"),
    ):
        for el in list(body.iter(tag)):
            if el.get(qn("w:id"), "") not in target_ids:
                continue
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
                stats[key] += 1

    # 2) 删 body 中引用 run
    for r_el in list(body.iter(qn("w:r"))):
        ref = r_el.find(qn("w:commentReference"))
        if ref is None:
            continue
        if ref.get(qn("w:id"), "") not in target_ids:
            continue
        parent = r_el.getparent()
        if parent is not None:
            parent.remove(r_el)
            stats["ref_runs"] += 1

    # 3) 删 comments OPC part 中匹配的 comment
    try:
        for rel in doc.part.rels.values():
            if _COMMENTS_REL not in rel.reltype:
                continue
            comments_root = rel.target_part._element
            removed = 0
            for comment_el in list(comments_root.findall(qn("w:comment"))):
                if comment_el.get(qn("w:id"), "") in target_ids:
                    comments_root.remove(comment_el)
                    removed += 1
            if removed:
                stats["comments_cleared"] = removed
            break
    except Exception:
        pass

    return stats


# ────────────────────────────────────────────────────────────────────────────── #
# clean_headers                                                                   #
# ────────────────────────────────────────────────────────────────────────────── #

def clean_headers(doc, *, clear_text: bool = True, remove_refs: bool = False) -> dict:
    """
    Clean document headers.

    Args:
        doc:         python-docx Document
        clear_text:  Clear all text content from header parts (default True)
        remove_refs: Also remove w:headerReference elements from w:sectPr (default False)

    Returns:
        dict with counts: headers_cleared, header_refs_removed
    """
    stats = {"headers_cleared": 0, "header_refs_removed": 0}

    # ── 1. Clear header text content ─────────────────────────────────── #
    if clear_text:
        try:
            for rel in doc.part.rels.values():
                if _HEADERS_REL in rel.reltype:
                    hdr_root = rel.target_part._element
                    # Clear all w:t text in the header part
                    for t_el in hdr_root.iter(qn("w:t")):
                        t_el.text = ""
                    stats["headers_cleared"] += 1
        except Exception:
            pass

    # ── 2. Remove header references from w:sectPr ────────────────────── #
    if remove_refs:
        body = doc.element.body
        for sect_el in list(body.iter(qn("w:sectPr"))):
            for href in list(sect_el.findall(qn("w:headerReference"))):
                sect_el.remove(href)
                stats["header_refs_removed"] += 1

    return stats
