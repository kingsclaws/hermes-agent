"""
project_init.py — Directory-scanning project initialiser for lex-hermes.

Scans a directory for .docx and .pdf files, extracts content from all
documents, detects key legal entities, and builds an enriched project
context before creating the project scaffold.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── Document extraction helpers ─────────────────────────────────────────────

def _extract_docx_text(filepath: Path) -> str:
    """Extract paragraph text from a .docx file.

    Tries python-docx first, falls back to raw ZIP XML parsing.
    """
    try:
        from docx import Document
        doc = Document(str(filepath))
        paragraphs = []
        for para in doc.paragraphs:
            style = para.style.name if para.style else ""
            text = para.text
            if not text:
                continue
            if "Heading" in style:
                paragraphs.append(f"## {text}")
            else:
                paragraphs.append(text)
        return "\n\n".join(paragraphs)
    except ImportError:
        import zipfile
        from xml.etree import ElementTree as ET
        with zipfile.ZipFile(str(filepath)) as zf:
            xml_content = zf.read("word/document.xml")
        tree = ET.fromstring(xml_content)
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        texts = []
        for node in tree.iter(f"{{{ns}}}t"):
            if node.text:
                texts.append(node.text)
        return "\n".join(texts)


def _extract_pdf_text(filepath: Path, language: str = "ch") -> dict:
    """Extract text from a PDF via MinerU Agent API (free tier).

    Returns {"ok": True, "text": "...", ...} or {"ok": False, "error": "..."}
    """
    try:
        from lexitool.ocr import parse_pdf
        result = parse_pdf(str(filepath), language=language, prefer_precise=False)
        if result.get("ok"):
            return {"ok": True, "text": result["markdown"], "api": result.get("api", "agent")}
        return {"ok": False, "error": result.get("error", "OCR failed")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Entity detection ────────────────────────────────────────────────────────

def _detect_entities(all_text: str) -> dict:
    """Extract key legal entities from combined document text using regex."""
    parties = set()
    party_patterns = [
        r'(?:甲方|乙方|丙方|原告|被告|申请人|被申请人|第三人|委托人|受托人|保证人|出质人|抵押权人|债权人|债务人)[：:]\s*([^\n]{2,60})',
        r'(?:Party [ABCD]|Plaintiff|Defendant|Claimant|Respondent)[：:]\s*([^\n]{2,80})',
    ]
    for pat in party_patterns:
        for m in re.finditer(pat, all_text):
            parties.add(m.group(1).strip())

    dates = sorted(set(re.findall(
        r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?', all_text
    )))[:20]

    amounts = sorted(set(re.findall(
        r'(?:CNY|RMB|USD|EUR|港币|美元|欧元)?\s*'
        r'(?:[¥￥]\s*)?[\d,]+(?:\.\d{1,2})?\s*(?:万|万元|元|亿|yuan|million|billion)?',
        all_text, re.IGNORECASE
    )), key=len, reverse=True)[:15]

    laws = sorted(set(re.findall(r'《([^》]{2,60})》', all_text)))[:20]

    case_numbers = sorted(set(re.findall(
        r'[（(]\d{4}[）)]\S{2,10}[字第]\d+号', all_text
    )))[:10]

    return {
        "parties": sorted(parties),
        "dates": list(dates),
        "amounts": list(amounts),
        "laws_referenced": list(laws),
        "case_numbers": list(case_numbers),
    }


# ── Project context builder ─────────────────────────────────────────────────

def _build_project_context(
    project_name: str,
    client_name: str,
    goal: str,
    dir_path: str,
    results: list[dict],
    entities: dict,
) -> str:
    """Assemble an enriched project-context.md from scan results."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ok_files = [r for r in results if r["status"] == "ok"]
    failed_files = [r for r in results if r["status"] == "failed"]

    lines = [
        f"# Project Context — {project_name}",
        "",
        "## Project Facts",
        f"- 项目名称：{project_name}",
        f"- 客户名称：{client_name}",
        f"- 源文件目录：{dir_path}",
        f"- 创建日期：{now}",
        f"- 当前目标：{goal}",
        "",
        "## Document Inventory",
        "",
        "| # | Filename | Type | Status | Size |",
        "|---|----------|------|--------|------|",
    ]

    for i, r in enumerate(results, 1):
        status = ":white_check_mark:" if r["status"] == "ok" else ":x:"
        chars = r.get("char_count", 0)
        size_str = f"{chars:,} chars" if chars else "-"
        lines.append(f"| {i} | {r['filename']} | {r['type'].upper()} | {status} | {size_str} |")

    lines += [
        "",
        f"**Total:** {len(results)} files ({len(ok_files)} OK, {len(failed_files)} failed)",
        "",
        "## Document Summaries",
        "",
    ]

    for r in ok_files:
        text = r.get("text", "")
        preview = text[:2000].strip()
        if len(text) > 2000:
            preview += "\n\n[...truncated...]"
        lines += [
            f"### {r['filename']}",
            "",
            preview,
            "",
        ]

    lines += [
        "## Key Entities Detected",
        "",
    ]

    if entities.get("parties"):
        lines.append("### Parties")
        for p in entities["parties"]:
            lines.append(f"- {p}")
        lines.append("")

    if entities.get("dates"):
        lines.append("### Dates")
        for d in entities["dates"][:10]:
            lines.append(f"- {d}")
        lines.append("")

    if entities.get("amounts"):
        lines.append("### Monetary Amounts")
        for a in entities["amounts"][:10]:
            lines.append(f"- {a}")
        lines.append("")

    if entities.get("laws_referenced"):
        lines.append("### Laws / Regulations Referenced")
        for l in entities["laws_referenced"][:15]:
            lines.append(f"- 《{l}》")
        lines.append("")

    if entities.get("case_numbers"):
        lines.append("### Case Numbers")
        for c in entities["case_numbers"]:
            lines.append(f"- {c}")
        lines.append("")

    lines += [
        "## Active Tasks",
        "",
        "（Coordinator 维护活跃任务清单）",
        "",
        "## Constraints / Working Rules",
        "",
        "- 所有修改在 Track Changes 模式下进行",
        "- 段落编号不可变（审阅完成前不增删整段）",
        "- 交付截止日期：（待补充）",
        "",
        "## Logistics Support",
        "",
        f"- 源文件目录：{dir_path}",
        "- 文档模板：（待补充）",
        "- 相关法规：（见上方 Laws Referenced）",
        "",
        "## Recent Decisions",
        "",
        "（Coordinator 记录关键决策）",
    ]

    return "\n".join(lines)


# ── Main entry point ────────────────────────────────────────────────────────

def scan_and_init_project(
    dir_path: str,
    project_name: str,
    client_name: str,
    goal: str,
    *,
    language: str = "ch",
    recursive: bool = False,
    project_parent_dir: str | None = None,
) -> dict:
    """Scan a directory for legal documents and initialise a project.

    Args:
        dir_path: Directory containing .docx and .pdf source files.
        project_name: Short project identifier.
        client_name: Client or organisation name.
        goal: Project goal description.
        language: Document language for OCR (default 'ch').
        recursive: Scan subdirectories recursively.
        project_parent_dir: Where to create the project directory.
            Default: parent of dir_path.

    Returns:
        {"ok": True, "project_dir": "...", "files_scanned": N, ...}
    """
    source_dir = Path(dir_path).resolve()
    if not source_dir.is_dir():
        return {"ok": False, "error": f"Not a directory: {dir_path}"}

    # ── 1. Discover files ──
    pattern = "**/*" if recursive else "*"
    docx_files = sorted(source_dir.glob(f"{pattern}.docx"))
    pdf_files = sorted(source_dir.glob(f"{pattern}.pdf"))
    all_files = docx_files + pdf_files

    if not all_files:
        return {"ok": False, "error": f"No .docx or .pdf files found in {dir_path}"}

    # ── 2. Process each file ──
    results: list[dict] = []
    total_chars = 0

    for fpath in all_files:
        suffix = fpath.suffix.lower()
        try:
            if suffix == ".docx":
                text = _extract_docx_text(fpath)
                results.append({
                    "filename": fpath.name,
                    "type": "docx",
                    "status": "ok",
                    "text": text,
                    "char_count": len(text),
                })
            elif suffix == ".pdf":
                r = _extract_pdf_text(fpath, language=language)
                if r["ok"]:
                    results.append({
                        "filename": fpath.name,
                        "type": "pdf",
                        "status": "ok",
                        "text": r["text"],
                        "char_count": len(r["text"]),
                        "ocr_api": r.get("api", "agent"),
                    })
                else:
                    results.append({
                        "filename": fpath.name,
                        "type": "pdf",
                        "status": "failed",
                        "error": r["error"],
                    })
            if results and results[-1].get("status") == "ok":
                total_chars += results[-1].get("char_count", 0)
        except Exception as e:
            results.append({
                "filename": fpath.name,
                "type": suffix.lstrip("."),
                "status": "failed",
                "error": str(e),
            })

    # ── 3. Combine text and detect entities ──
    all_text = "\n\n".join(
        r.get("text", "") for r in results if r["status"] == "ok"
    )
    entities = _detect_entities(all_text)

    # ── 4. Build enriched project context ──
    context_md = _build_project_context(
        project_name, client_name, goal, str(source_dir), results, entities
    )

    # ── 5. Create project scaffold ──
    parent = Path(project_parent_dir).resolve() if project_parent_dir else source_dir.parent
    project_dir = parent / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        from hermes_cli.project_commands import _create_scaffolding
        _create_scaffolding(project_dir, project_name, client_name, goal)
    except ImportError:
        # Fallback: create minimal scaffold ourselves
        hermes_dir = project_dir / ".hermes-project"
        roles_dir = hermes_dir / "roles"
        hermes_dir.mkdir(parents=True, exist_ok=True)
        roles_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta = {
            "name": project_name, "client": client_name, "goal": goal,
            "cwd": str(project_dir), "created": now, "toolsets": ["lexitool"],
        }
        (hermes_dir / "project-meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n"
        )

    # ── 6. Write enriched project-context.md ──
    context_path = project_dir / ".hermes-project" / "project-context.md"
    context_path.write_text(context_md)

    # ── 7. Write document inventory ──
    docs_dir = project_dir / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    inventory = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "files": [
            {
                "filename": r["filename"],
                "type": r["type"],
                "status": r["status"],
                "char_count": r.get("char_count", 0),
                "error": r.get("error") if r["status"] == "failed" else None,
            }
            for r in results
        ],
    }
    (docs_dir / "inventory.json").write_text(
        json.dumps(inventory, indent=2, ensure_ascii=False) + "\n"
    )

    # ── 8. Copy updated role files if available ──
    _copy_role_files(project_dir)

    ok_count = sum(1 for r in results if r["status"] == "ok")
    fail_count = sum(1 for r in results if r["status"] == "failed")

    return {
        "ok": True,
        "project_dir": str(project_dir),
        "files_scanned": len(all_files),
        "docx_count": len(docx_files),
        "pdf_count": len(pdf_files),
        "ok_count": ok_count,
        "failed_count": fail_count,
        "failed_files": [
            {"filename": r["filename"], "error": r.get("error", "unknown")}
            for r in results if r["status"] == "failed"
        ],
        "total_chars_extracted": total_chars,
        "entities_detected": {
            k: v for k, v in entities.items() if v
        },
        "context_preview": context_md[:2000],
    }


def _copy_role_files(project_dir: Path) -> None:
    """Copy updated role files from the lexitool package if available."""
    roles_src = Path(__file__).resolve().parent.parent.parent / ".hermes-project" / "roles"
    roles_dst = project_dir / ".hermes-project" / "roles"
    if roles_src.is_dir():
        roles_dst.mkdir(parents=True, exist_ok=True)
        for src_file in roles_src.glob("*.md"):
            dst_file = roles_dst / src_file.name
            if not dst_file.exists():
                dst_file.write_text(src_file.read_text())
