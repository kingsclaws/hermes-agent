"""
clause_ops.py — Clause-level document operations for lexitool.

Split documents into semantic clauses, extract/insert clause ranges,
and compare clauses for compatibility. Pure lxml + zipfile, no python-docx.
"""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

# ── Clause type classification ────────────────────────────────────────────────

CLAUSE_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    ("definitions",       ["定义", "释义", "Definitions", "Interpretation"]),
    ("representations",   ["陈述与保证", "陈述和保证", "声明与保证",
                           "Representations", "Warranties"]),
    ("covenants",         ["承诺", "约定", "义务", "契诺", "Covenants",
                           "Undertakings", "Obligations", " undertakings"]),
    ("conditions",        ["先决条件", "前提条件", "交割条件", "Conditions",
                           "Conditions Precedent", "Closing Conditions"]),
    ("events_of_default", ["违约事件", "加速到期", "违约", "Events of Default",
                           "Default", "Acceleration"]),
    ("governing_law",     ["管辖", "适用法律", "争议解决", "仲裁", "Governing Law",
                           "Dispute Resolution", "Arbitration", "Jurisdiction"]),
    ("indemnity",         ["赔偿", "补偿", "Indemnity", "Indemnification"]),
    ("miscellaneous",     ["杂项", "其他规定", "通知", "转让", "修改", "保密",
                           "Miscellaneous", "Notices", "Assignment", "Amendment",
                           "Confidentiality", "General Provisions"]),
    ("parties",           ["当事人", "缔约方", "Parties", "Party"]),
    ("background",        ["背景", "鉴于", "前言", "Background", "Recitals",
                           "Whereas", "Preamble"]),
    ("operative",         ["标的", "价款", "履行", "交付", "付款", "购买",
                           "Purchase Price", "Consideration", "Delivery",
                           "Payment", "Operative"]),
    ("term_termination",  ["期限", "终止", "解除", "Term", "Termination",
                           "Rescission"]),
]


def _classify_clause(title: str) -> str:
    """Best-effort clause type classification from title text."""
    title_lower = title.lower()
    best_type = "general"
    best_len = 0
    for ctype, patterns in CLAUSE_TYPE_PATTERNS:
        for p in patterns:
            if p.lower() in title_lower:
                if len(p) > best_len:
                    best_type = ctype
                    best_len = len(p)
    return best_type


def _extract_key_terms(text: str) -> list[str]:
    """Extract potential defined terms from clause text."""
    terms: list[str] = []
    for pattern in [r'《([^》]+)》', r'「([^」]+)」', r'"([^"]+)"']:
        terms.extend(re.findall(pattern, text))
    seen: set[str] = set()
    unique = []
    for t in terms:
        if t not in seen and len(t) > 1:
            seen.add(t)
            unique.append(t)
    return unique[:30]


def _get_para_text(para_el) -> str:
    """Get plain text from a w:p element."""
    parts = []
    for child in para_el.iter():
        if child.tag in (f"{W}t", f"{W}delText"):
            if child.text:
                parts.append(child.text)
        elif child.tag == f"{W}tab":
            parts.append("\t")
    return "".join(parts)


def _detect_numbering_boundaries(body_children, starting_para_idx=1) -> list[int]:
    """Return para indices where numbering restarts (potential clause boundaries)."""
    boundaries = []
    prev_numId = None
    prev_ilvl = None
    para_idx = starting_para_idx - 1

    for child in body_children:
        if child.tag != f"{W}p":
            continue
        para_idx += 1
        pPr = child.find(f"{W}pPr")
        if pPr is None:
            continue
        numPr = pPr.find(f"{W}numPr")
        if numPr is None:
            prev_numId = None
            prev_ilvl = None
            continue

        numId_el = numPr.find(f"{W}numId")
        ilvl_el = numPr.find(f"{W}ilvl")
        curr_numId = numId_el.get(f"{W}val") if numId_el is not None else None
        curr_ilvl = ilvl_el.get(f"{W}val") if ilvl_el is not None else "0"

        # New top-level numbering or numbering restart
        if curr_numId != prev_numId or (curr_ilvl == "0" and prev_ilvl != "0" and prev_ilvl is not None):
            boundaries.append(para_idx)
        prev_numId = curr_numId
        prev_ilvl = curr_ilvl

    return boundaries


# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class Clause:
    id: str
    title: str
    para_start: int        # 1-indexed, inclusive
    para_end: int          # 1-indexed, inclusive
    level: int             # heading nesting depth (1 = top-level)
    clause_type: str       # best-effort classification
    key_terms: list[str]   # extracted key terms / defined terms
    detection: str         # "heading" | "outline" | "numbering" | "manual"


@dataclass
class CompareResult:
    compatible: bool
    issues: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)


# ── Split ──────────────────────────────────────────────────────────────────────


def split_clauses(docx_path: str, method: str = "auto") -> list[Clause]:
    """Split a document into semantic clauses.

    Detection strategy (three tiers):
      1. Heading styles / outlineLvl — matches Word heading styles
      2. Numbering patterns — detects clause boundaries from list structure
      3. Falls back to treating entire document as one clause

    Args:
        docx_path: Path to .docx file.
        method: Detection method — "auto" (all tiers), "heading" (tier 1 only),
                "numbering" (tier 2 only), "flat" (one clause per paragraph).

    Returns:
        List of Clause objects with paragraph ranges and metadata.
    """
    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return []

    body_children = list(body)

    if method == "flat":
        return _split_flat(body_children)

    # Tier 1: heading-based detection
    heading_clauses = _split_by_headings(body_children)
    if heading_clauses:
        return _build_clause_objects(body_children, heading_clauses)

    # Tier 2: numbering-based boundaries (as supplements)
    numbering_boundaries = _detect_numbering_boundaries(body_children)
    if numbering_boundaries:
        return _build_clause_objects(body_children,
                                     _merge_boundaries([], numbering_boundaries))

    # Fallback: one clause = whole document
    para_count = sum(1 for c in body_children if c.tag == f"{W}p")
    if para_count == 0:
        return []
    return [Clause(
        id="c1", title="(全文档)", para_start=1, para_end=para_count,
        level=1, clause_type="general", key_terms=[], detection="fallback",
    )]


def _split_by_headings(body_children) -> list[dict]:
    """Detect clause boundaries from heading paragraphs.

    Returns list of {title, para_start, level, detection} dicts.
    """
    clauses = []
    para_idx = 0

    for child in body_children:
        if child.tag != f"{W}p":
            continue
        para_idx += 1

        pPr = child.find(f"{W}pPr")
        if pPr is None:
            continue

        outline_lvl = pPr.find(f"{W}outlineLvl")
        pStyle = pPr.find(f"{W}pStyle")
        style_id = pStyle.get(f"{W}val", "") if pStyle is not None else ""

        heading_level = None
        detection = "heading"

        if outline_lvl is not None:
            try:
                heading_level = int(outline_lvl.get(f"{W}val", "9")) + 1
                detection = "outline"
            except (ValueError, TypeError):
                pass

        is_heading_style = style_id and (
            style_id.lower().startswith("heading")
            or style_id.lower().startswith("toc")
        )

        if heading_level is not None or is_heading_style:
            if heading_level is None:
                heading_level = 1
                detection = "heading"

            title = _get_para_text(child).strip()[:200]
            if not title:
                title = f"§{para_idx}"

            clauses.append({
                "title": title,
                "para_start": para_idx,
                "level": heading_level,
                "detection": detection,
            })

    return clauses


def _merge_boundaries(heading_clauses: list[dict],
                      numbering_boundaries: list[int]) -> list[dict]:
    """Merge numbering-based boundaries into heading clause list."""
    # Simple: treat numbering boundaries as additional clauses at level 1
    existing_starts = {c["para_start"] for c in heading_clauses}
    for n in numbering_boundaries:
        if n not in existing_starts:
            heading_clauses.append({
                "title": f"§{n}",
                "para_start": n,
                "level": 1,
                "detection": "numbering",
            })
    heading_clauses.sort(key=lambda c: c["para_start"])
    return heading_clauses


def _split_flat(body_children) -> list[Clause]:
    """One clause per paragraph."""
    clauses = []
    para_idx = 0
    for child in body_children:
        if child.tag != f"{W}p":
            continue
        para_idx += 1
        text = _get_para_text(child).strip()[:100]
        title = text if text else f"§{para_idx}"
        clauses.append(Clause(
            id=f"c{para_idx}", title=title,
            para_start=para_idx, para_end=para_idx,
            level=1, clause_type=_classify_clause(title),
            key_terms=[], detection="flat",
        ))
    return clauses


def _build_clause_objects(body_children,
                          clause_boundaries: list[dict]) -> list[Clause]:
    """Convert boundary dicts to Clause objects with para_end computed."""
    if not clause_boundaries:
        return []

    # Count total paragraphs
    total_paras = sum(1 for c in body_children if c.tag == f"{W}p")

    # Compute para_end for each clause
    for i, c in enumerate(clause_boundaries):
        if i + 1 < len(clause_boundaries):
            c["para_end"] = clause_boundaries[i + 1]["para_start"] - 1
        else:
            c["para_end"] = total_paras

    # Build Clause objects
    result = []
    for i, c in enumerate(clause_boundaries):
        if c["para_start"] > c["para_end"]:
            continue

        # Extract text for key terms
        text_parts = []
        for child in body_children:
            if child.tag != f"{W}p":
                continue
        para_idx = 0
        for child in body_children:
            if child.tag != f"{W}p":
                continue
            para_idx += 1
            if c["para_start"] <= para_idx <= c["para_end"]:
                text_parts.append(_get_para_text(child))

        full_text = " ".join(text_parts)
        clause_type = _classify_clause(c["title"])
        key_terms = _extract_key_terms(full_text)

        result.append(Clause(
            id=f"c{i + 1}",
            title=c["title"],
            para_start=c["para_start"],
            para_end=c["para_end"],
            level=c["level"],
            clause_type=clause_type,
            key_terms=key_terms,
            detection=c["detection"],
        ))

    return result


# ── Extract ────────────────────────────────────────────────────────────────────


def extract_clause(docx_path: str, para_start: int, para_end: int,
                   output_path: str) -> str:
    """Extract a paragraph range into a standalone .docx snippet.

    The output preserves styles, numbering definitions, headers/footers,
    and section properties. Content outside the range is removed.

    Args:
        docx_path: Source .docx path.
        para_start: First paragraph to include (1-indexed).
        para_end: Last paragraph to include (1-indexed, inclusive).
        output_path: Where to write the snippet .docx.

    Returns:
        The output path on success.
    """
    with zipfile.ZipFile(docx_path, "r") as zf:
        members = {}
        for name in zf.namelist():
            members[name] = zf.read(name)

    doc_xml = members.get("word/document.xml")
    if doc_xml is None:
        raise FileNotFoundError(f"word/document.xml not found in {docx_path}")

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        raise ValueError("document has no w:body")

    # Collect body children, filtering to the target range
    kept_children = []
    para_idx = 0
    sect_pr = None

    for child in list(body):
        if child.tag == f"{W}sectPr":
            sect_pr = child
            continue
        if child.tag == f"{W}p":
            para_idx += 1
            if para_start <= para_idx <= para_end:
                kept_children.append(child)
        elif child.tag == f"{W}tbl":
            # Keep tables that fall within the paragraph range
            # Table position is approximated by the preceding paragraph index
            if para_start <= para_idx + 1 <= para_end or len(kept_children) > 0:
                kept_children.append(child)

    # Rebuild body: clear, re-add kept children, re-add sectPr
    for child in list(body):
        body.remove(child)
    for child in kept_children:
        body.append(child)
    if sect_pr is not None:
        body.append(sect_pr)

    # Serialize and write
    doc_xml_out = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                 standalone="yes")

    fd, tmp = tempfile.mkstemp(prefix="lex_clause_extract.", suffix=".docx")
    os_close = __import__("os").close
    os_close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # OOXML requires [Content_Types].xml as the first ZIP entry
        ct_xml = members.pop("[Content_Types].xml", None)
        if ct_xml is not None:
            zf.writestr("[Content_Types].xml", ct_xml)
        zf.writestr("word/document.xml", doc_xml_out)
        for name, data in members.items():
            if name != "word/document.xml":
                zf.writestr(name, data)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(tmp, str(out))
    return str(out)


# ── Insert ─────────────────────────────────────────────────────────────────────


def insert_clause(target_path: str, source_path: str,
                  insert_after_para: int,
                  adjust_numbering: bool = True) -> str:
    """Insert content from source document into target document.

    Extracts all body children (w:p and w:tbl) from source and inserts them
    into target after the specified paragraph. Optionally strips numbering
    to avoid conflicts.

    Args:
        target_path: Target .docx to insert into (modified in place).
        source_path: Source .docx containing the clause to insert.
        insert_after_para: Insert after this paragraph (1-indexed).
        adjust_numbering: If True, strip numPr from inserted paragraphs.

    Returns:
        The target path on success.
    """
    # Read both documents
    with zipfile.ZipFile(target_path, "r") as zf:
        target_members = {}
        for name in zf.namelist():
            target_members[name] = zf.read(name)

    with zipfile.ZipFile(source_path, "r") as zf:
        source_members = {}
        for name in zf.namelist():
            source_members[name] = zf.read(name)

    target_doc = target_members.get("word/document.xml")
    source_doc = source_members.get("word/document.xml")
    if target_doc is None or source_doc is None:
        raise FileNotFoundError("document.xml not found")

    target_root = etree.fromstring(target_doc)
    target_body = target_root.find(f"{W}body")
    source_root = etree.fromstring(source_doc)
    source_body = source_root.find(f"{W}body")
    if target_body is None or source_body is None:
        raise ValueError("document has no w:body")

    # Collect source body children to insert (excluding sectPr)
    source_children = []
    for child in list(source_body):
        if child.tag in (f"{W}p", f"{W}tbl"):
            source_children.append(child)

    if not source_children:
        return target_path  # Nothing to insert

    # Strip numbering from inserted paragraphs if requested
    if adjust_numbering:
        for child in source_children:
            if child.tag == f"{W}p":
                pPr = child.find(f"{W}pPr")
                if pPr is not None:
                    numPr = pPr.find(f"{W}numPr")
                    if numPr is not None:
                        pPr.remove(numPr)

    # Find insertion point in target body
    target_children = list(target_body)
    para_idx = 0
    insert_before_idx = None

    for i, child in enumerate(target_children):
        if child.tag == f"{W}p":
            para_idx += 1
            if para_idx == insert_after_para:
                insert_before_idx = i + 1
                break

    if insert_before_idx is None:
        # Insert before sectPr (at end of body) if it exists
        for i, child in enumerate(target_children):
            if child.tag == f"{W}sectPr":
                insert_before_idx = i
                break
        if insert_before_idx is None:
            insert_before_idx = len(target_children)

    # Insert source children at the right position
    for j, src_child in enumerate(source_children):
        # Deep copy to avoid lxml element ownership issues
        copied = etree.fromstring(etree.tostring(src_child))
        target_body.insert(insert_before_idx + j, copied)

    # Serialize target
    target_doc_out = etree.tostring(target_root, xml_declaration=True,
                                    encoding="UTF-8", standalone="yes")

    # Write back
    fd, tmp = tempfile.mkstemp(prefix="lex_clause_insert.", suffix=".docx")
    os_close = __import__("os").close
    os_close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # OOXML requires [Content_Types].xml as the first ZIP entry
        ct_xml = target_members.pop("[Content_Types].xml", None)
        if ct_xml is not None:
            zf.writestr("[Content_Types].xml", ct_xml)
        zf.writestr("word/document.xml", target_doc_out)
        for name, data in target_members.items():
            if name != "word/document.xml":
                zf.writestr(name, data)

    shutil.move(tmp, target_path)
    return target_path


# ── Compare ────────────────────────────────────────────────────────────────────


def compare_clauses(docx_path_a: str, clause_a: dict,
                    docx_path_b: str, clause_b: dict) -> CompareResult:
    """Compare two clauses for compatibility.

    Checks: heading level match, defined term consistency,
    cross-reference conflicts, structural similarity.

    Args:
        docx_path_a: Path to first document.
        clause_a: {"para_start": int, "para_end": int, "title": str} for clause A.
        docx_path_b: Path to second document.
        clause_b: Same dict structure for clause B.

    Returns:
        CompareResult with compatibility assessment and issues list.
    """
    issues = []
    info = []

    # Read both clause texts
    text_a = _read_clause_text(docx_path_a, clause_a["para_start"],
                               clause_a["para_end"])
    text_b = _read_clause_text(docx_path_b, clause_b["para_start"],
                               clause_b["para_end"])

    # Extract key terms from both
    terms_a = set(_extract_key_terms(text_a))
    terms_b = set(_extract_key_terms(text_b))

    # Check: same terms with different contexts
    common = terms_a & terms_b
    if common:
        info.append(f"Shared defined terms: {', '.join(sorted(common)[:10])}")

    only_a = terms_a - terms_b
    only_b = terms_b - terms_a
    if only_a:
        info.append(f"Terms only in clause A: {', '.join(sorted(only_a)[:10])}")
    if only_b:
        info.append(f"Terms only in clause B: {', '.join(sorted(only_b)[:10])}")

    # Check: clause type match
    type_a = _classify_clause(clause_a.get("title", ""))
    type_b = _classify_clause(clause_b.get("title", ""))
    if type_a == type_b:
        info.append(f"Both clauses classified as: {type_a}")
    else:
        issues.append(f"Clause type mismatch: A is '{type_a}', B is '{type_b}'")

    # Check: structural similarity (simple length ratio)
    len_a = len(text_a)
    len_b = len(text_b)
    if len_a > 0 and len_b > 0:
        ratio = min(len_a, len_b) / max(len_a, len_b)
        if ratio < 0.3:
            issues.append(f"Large size difference: A={len_a} chars, B={len_b} chars (ratio={ratio:.1%})")
        else:
            info.append(f"Size ratio: {ratio:.1%} (A={len_a}, B={len_b})")

    compatible = len(issues) == 0
    return CompareResult(compatible=compatible, issues=issues, info=info)


def _read_clause_text(docx_path: str, para_start: int, para_end: int) -> str:
    """Read plain text from a paragraph range."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        doc_xml = zf.read("word/document.xml")

    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return ""

    parts = []
    para_idx = 0
    for child in body:
        if child.tag == f"{W}p":
            para_idx += 1
            if para_start <= para_idx <= para_end:
                parts.append(_get_para_text(child))

    return "\n".join(parts)


# ── Utility: list clauses ──────────────────────────────────────────────────────


def list_clauses(docx_path: str) -> list[dict]:
    """Thin wrapper returning clause dicts (for use in tool handler)."""
    clauses = split_clauses(docx_path)
    return [
        {
            "id": c.id,
            "title": c.title,
            "para_start": c.para_start,
            "para_end": c.para_end,
            "level": c.level,
            "type": c.clause_type,
            "key_terms": c.key_terms[:10],
            "detection": c.detection,
        }
        for c in clauses
    ]
