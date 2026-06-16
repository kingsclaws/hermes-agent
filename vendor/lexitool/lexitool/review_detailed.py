"""
review_detailed.py — 带格式标记的详细文档预览。

lex_docx review inspect --rich 的输出引擎。
"""
from __future__ import annotations

from docx import Document
from docx.oxml.ns import qn


def _resolve_run_format(run) -> dict:
    """解析 run 的格式：字体、字号、粗体、斜体、下划线、高亮。"""
    info: dict = {}
    font = run.font
    rPr = run._element.find(qn("w:rPr"))

    if rPr is not None:
        rFonts = rPr.find(qn("w:rFonts"))
        if rFonts is not None:
            info["font"] = (
                rFonts.get(qn("w:eastAsia"))
                or rFonts.get(qn("w:ascii"))
                or rFonts.get(qn("w:hAnsi"))
                or ""
            )
    if not info.get("font") and font.name:
        info["font"] = font.name

    sz_val = None
    if rPr is not None:
        sz_el = rPr.find(qn("w:sz"))
        if sz_el is not None:
            try:
                sz_val = int(sz_el.get(qn("w:val"), "0")) / 2
            except (ValueError, TypeError):
                pass
    if sz_val is None and font.size:
        sz_val = font.size.pt
    if sz_val is not None:
        info["size"] = round(sz_val, 1)

    if rPr is not None:
        if rPr.find(qn("w:b")) is not None:
            info["bold"] = True
        if rPr.find(qn("w:i")) is not None:
            info["italic"] = True
        if rPr.find(qn("w:u")) is not None:
            info["underline"] = True
        hl = rPr.find(qn("w:highlight"))
        if hl is not None:
            info["highlight"] = hl.get(qn("w:val"), "yellow")
    if "bold" not in info and font.bold:
        info["bold"] = True
    if "italic" not in info and font.italic:
        info["italic"] = True
    if "underline" not in info and font.underline:
        info["underline"] = True

    return info


def _format_run_markup(run) -> tuple[str, dict]:
    text = run.text
    fmt = _resolve_run_format(run)
    if fmt.get("bold") and fmt.get("italic"):
        text = f"***{text}***"
    elif fmt.get("bold"):
        text = f"**{text}**"
    elif fmt.get("italic"):
        text = f"*{text}*"
    if fmt.get("underline"):
        text = f"__{text}__"
    if fmt.get("highlight"):
        text = f"=={text}=="
    return text, fmt


def _para_format_summary(para) -> str:
    pf = para.paragraph_format
    parts = []
    if pf.alignment is not None:
        align_map = {0: "左对齐", 1: "居中", 2: "右对齐", 3: "两端对齐", 4: "分散对齐"}
        parts.append(align_map.get(pf.alignment, str(pf.alignment)))
    space_before = None
    space_after = None
    if pf.space_before is not None:
        try:
            space_before = pf.space_before.pt
        except Exception:
            space_before = pf.space_before
    if pf.space_after is not None:
        try:
            space_after = pf.space_after.pt
        except Exception:
            space_after = pf.space_after
    if space_before:
        parts.append(f"段前{space_before}pt")
    if space_after:
        parts.append(f"段后{space_after}pt")
    ls = pf.line_spacing
    if ls is not None:
        try:
            parts.append(f"行距{ls}pt" if isinstance(ls, (int, float)) else str(ls))
        except Exception:
            pass
    if pf.page_break_before:
        parts.append("分页前")
    return " / ".join(parts) if parts else ""


def _para_style_name(para) -> str:
    pPr = para._element.find(qn("w:pPr"))
    if pPr is not None:
        pStyle = pPr.find(qn("w:pStyle"))
        if pStyle is not None:
            return pStyle.get(qn("w:val"), "")
    return ""


def _has_page_break(para) -> bool:
    el = para._element
    pPr = el.find(qn("w:pPr"))
    if pPr is not None:
        pb = pPr.find(qn("w:pageBreakBefore"))
        if pb is not None:
            return True
    for br in el.iter(qn("w:br")):
        if br.get(qn("w:type")) == "page":
            return True
    return False


def _extract_tc_text(para) -> str:
    """从 OpenXML 提取 TC 修订内容（w:ins → w:t, w:del → w:delText）。"""
    el = para._element
    parts = []
    for ins in el.iter(qn("w:ins")):
        ins_text = "".join(t.text or "" for t in ins.iter(qn("w:t")))
        if ins_text:
            parts.append(f"[+{ins_text}+]")
    for d in el.iter(qn("w:del")):
        del_text = "".join(dt.text or "" for dt in d.iter(qn("w:delText")))
        if del_text:
            parts.append(f"[-{del_text}-]")
    return " " + " ".join(parts) if parts else ""


def rich_inspect(docx_path: str, para_range: tuple[int, int] | None = None,
                 max_paras: int = 100) -> list[dict]:
    """
    详细格式预览。

    Returns:
        [
            {
                "index": int,
                "style": str,
                "text": str,          # 原始全文
                "markup": str,        # 带 **加粗** *斜体* + TC 修订标记
                "runs": [...],
                "para_format": str,
                "page_break": bool,
            },
        ]
    """
    doc = Document(docx_path)
    paras = doc.paragraphs

    lo = 0
    hi = len(paras) - 1
    if para_range:
        lo, hi = para_range
        hi = min(hi, len(paras) - 1)

    result = []
    for idx in range(lo, min(hi + 1, len(paras))):
        para = paras[idx]
        tc_suffix = _extract_tc_text(para)
        page_break = _has_page_break(para)

        # Run-level markup
        full_markup = ""
        run_details = []
        for r in para.runs:
            markup_text, fmt = _format_run_markup(r)
            full_markup += markup_text
            run_details.append({
                "text": r.text,
                "font": fmt.get("font", ""),
                "size": fmt.get("size"),
                "bold": fmt.get("bold", False),
                "italic": fmt.get("italic", False),
                "underline": fmt.get("underline", False),
                "highlight": fmt.get("highlight", ""),
            })

        # Append TC content to markup
        if tc_suffix:
            full_markup += tc_suffix

        result.append({
            "index": idx,
            "style": _para_style_name(para),
            "text": para.text,
            "markup": full_markup if full_markup else para.text,
            "runs": run_details,
            "para_format": _para_format_summary(para),
            "page_break": page_break,
        })

        if len(result) >= max_paras:
            break

    return result


def review_workflow(docx_path: str) -> dict:
    """End-to-end legal review workflow: read → discover → audit → recommend.

    Composes existing lexitool modules into a single structured report.
    Read-only — does not modify the document.

    Returns a dict with phases:
        document_profile, clause_inventory, term_audit, lint_results,
        recommendations, verification_checklist.
    """
    from .markup import lex_read, lex_read_structured
    from .clause_ops import list_clauses
    from .defined_terms import term_format_audit_all
    from .lint import check as lint_check

    result: dict = {
        "document_profile": {},
        "clause_inventory": {},
        "term_audit": {},
        "lint_results": {},
        "recommendations": [],
        "verification_checklist": [],
    }

    # Phase 1: Document profile
    try:
        stats_raw = lex_read(docx_path, mode="stats")
        result["document_profile"]["stats"] = stats_raw
    except Exception as exc:
        result["document_profile"]["error"] = str(exc)

    try:
        paras = lex_read_structured(docx_path, include_comments=True)
        heading_count = sum(1 for p in paras if p["role"] == "heading")
        body_count = sum(1 for p in paras if p["role"] == "body")
        spacer_count = sum(1 for p in paras if p["role"] == "spacer")
        tc_paras = [p["para_num"] for p in paras if p["tc_count"] > 0]
        result["document_profile"].update({
            "total_paragraphs": len(paras),
            "heading_count": heading_count,
            "body_count": body_count,
            "spacer_count": spacer_count,
            "paragraphs_with_tc": len(tc_paras),
            "tc_paragraph_numbers": tc_paras[:50],
        })
    except Exception as exc:
        result["document_profile"]["structured_error"] = str(exc)

    # Phase 2: Clause inventory
    try:
        clauses = list_clauses(docx_path)
        result["clause_inventory"] = {
            "total_clauses": len(clauses),
            "clauses": [
                {
                    "num": c.para_num if hasattr(c, "para_num") else i + 1,
                    "level": c.level if hasattr(c, "level") else "",
                    "heading": c.heading[:200] if hasattr(c, "heading") else "",
                }
                for i, c in enumerate(clauses[:80])
            ],
        }
    except Exception as exc:
        result["clause_inventory"]["error"] = str(exc)

    # Phase 3: Defined term audit
    try:
        audit = term_format_audit_all(docx_path)
        result["term_audit"] = {
            "total_terms": audit.get("total_terms", 0) if isinstance(audit, dict) else 0,
            "formatted_count": audit.get("formatted_count", 0) if isinstance(audit, dict) else 0,
            "unformatted": audit.get("unformatted", []) if isinstance(audit, dict) else [],
        }
    except Exception as exc:
        result["term_audit"]["error"] = str(exc)

    # Phase 4: Lint
    try:
        lint = lint_check(docx_path)
        result["lint_results"] = {
            "total_rules": len(lint) if isinstance(lint, list) else 0,
            "failures": [
                r for r in (lint if isinstance(lint, list) else [])
                if isinstance(r, dict) and not r.get("pass", True)
            ],
        }
    except Exception as exc:
        result["lint_results"]["error"] = str(exc)

    # Phase 5: Recommendations
    recs: list[str] = []
    tc_count = result["document_profile"].get("paragraphs_with_tc", 0)
    if tc_count:
        recs.append(f"Document has {tc_count} paragraphs with tracked changes — review and accept/reject before finalizing.")
    unformatted = result["term_audit"].get("unformatted", [])
    if unformatted:
        recs.append(f"{len(unformatted)} defined terms are not bolded — run lex_bold_terms on affected paragraphs.")
    lint_failures = result["lint_results"].get("failures", [])
    if lint_failures:
        recs.append(f"{len(lint_failures)} lint rules failed — run lex_lint with fix mode to auto-correct.")
    spacer_count = result["document_profile"].get("spacer_count", 0)
    if spacer_count > 10:
        recs.append(f"Document has {spacer_count} empty paragraphs — consider cleanup with lex_cleanup.")
    if not recs:
        recs.append("Document looks clean. Verify formatting against convention profile and proceed to finalize.")
    result["recommendations"] = recs

    # Phase 6: Verification checklist
    result["verification_checklist"] = [
        "Accept or reject all tracked changes",
        "Verify defined terms are consistently bolded",
        "Check heading numbering is continuous",
        "Validate page breaks and section breaks",
        "Audit headers and footers for stale entities",
        "Confirm font consistency across body text",
        "Check table formatting consistency",
        "Verify cross-references resolve correctly",
    ]

    return result


def format_rich_output(paras: list[dict]) -> str:
    """格式化为 agent 可读的文本输出。"""
    lines = []
    for p in paras:
        idx = p["index"]
        style = f" [{p['style']}]" if p["style"] else ""
        label = f"P[{idx}]{style}"

        if p["page_break"]:
            lines.append(f"P[{idx}]{style}: --- 分页符 ---")

        markup = p.get("markup", "")
        if markup.strip():
            # 取字体/字号信息
            unique_fonts = set()
            unique_sizes = set()
            for r in p["runs"]:
                if r["font"]:
                    unique_fonts.add(r["font"])
                if r["size"]:
                    unique_sizes.add(r["size"])
            fmt_info = ""
            if unique_fonts or unique_sizes:
                font_str = "/".join(sorted(unique_fonts)) if unique_fonts else ""
                size_str = "/".join(f"{s}pt" for s in sorted(unique_sizes)) if unique_sizes else ""
                fmt_info = f"（{font_str}{', ' if font_str and size_str else ''}{size_str}）" if (font_str or size_str) else ""

            lines.append(f"{label}: {markup[:500]}")
            if len(markup) > 500:
                lines.append(f"         (全文共 {len(markup)} 字符，截断至 500)")

            para_fmt = p["para_format"]
            if para_fmt or fmt_info:
                parts_list = [p for p in [para_fmt, fmt_info] if p]
                lines.append(f"         格式: {' / '.join(parts_list)}")
        else:
            lines.append(f"{label}: (空)")

        if p.get("text") and not p["text"].strip() and not p["page_break"]:
            lines.append(f"         空段（{len(p['text'])} 个空白字符）")

    return "\n".join(lines)
