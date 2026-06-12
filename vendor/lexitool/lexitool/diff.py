"""
diff.py — Document comparison (tracked-changes redline) via quicompare.

Wraps the quicompare Java CLI to produce Word and/or PDF redline documents
comparing an original and revised .docx file.

Operations:
  - redline:  Produce a tracked-changes comparison document
  - status:   Check whether quicompare is available on this system
"""
from __future__ import annotations

import json
import logging
import os
import difflib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
import zipfile

from lxml import etree

logger = logging.getLogger(__name__)

_QUICOMPARE_BIN = "/usr/local/bin/quicompare"
_DEFAULT_CONFIG = "/root/.config/quicompare/config.json"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


def _find_quicompare() -> Optional[str]:
    """Locate the quicompare binary. Returns path or None."""
    if os.path.isfile(_QUICOMPARE_BIN) and os.access(_QUICOMPARE_BIN, os.X_OK):
        return _QUICOMPARE_BIN
    # Fallback to PATH search
    found = shutil.which("quicompare")
    return found


def is_available() -> bool:
    """Check whether quicompare is installed and runnable."""
    qc = _find_quicompare()
    if not qc:
        return False
    try:
        result = subprocess.run(
            [qc, "--help"], capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def redline(
    original: str,
    revised: str,
    output_dir: Optional[str] = None,
    author: Optional[str] = None,
    pdf: bool = False,
    word_prefix: Optional[str] = None,
    word_suffix: Optional[str] = None,
    granularity: str = "char",
    no_stats_page: bool = False,
) -> dict:
    """Compare two .docx files and produce a tracked-changes redline.

    Args:
        original:   Path to the original (older) .docx file.
        revised:    Path to the revised (newer) .docx file.
        output_dir: Directory for output files (default: same folder as revised).
        author:     Author name shown in tracked changes (default: from config).
        pdf:        Also generate a PDF redline.
        word_prefix: Prefix for the Word output filename (default: "[Redline] - ").
        word_suffix: Suffix for the Word output filename.
        granularity: "char" (default) or "word" for change tracking level.
        no_stats_page: Skip the comparison statistics page.

    Returns:
        {
            "ok": True,
            "output_word": "/path/to/[Redline] - revised.docx",
            "output_pdf": "/path/to/[Redline] - revised.pdf" or None,
            "author": "...",
            "granularity": "char",
        }
    """
    qc = _find_quicompare()
    if not qc:
        return {
            "ok": False,
            "error": "quicompare not found. Install it: bash /root/.hermes/tools/lex-workspace/tools/quicompare/install.sh",
        }

    if not os.path.isfile(original):
        return {"ok": False, "error": f"Original file not found: {original}"}
    if not os.path.isfile(revised):
        return {"ok": False, "error": f"Revised file not found: {revised}"}

    cmd = [qc, os.path.abspath(original), os.path.abspath(revised)]

    if output_dir:
        cmd.extend(["--output-dir", os.path.abspath(output_dir)])
    if author:
        cmd.extend(["--author", author])
    if pdf:
        cmd.append("--pdf")
    if word_prefix:
        cmd.extend(["--word-prefix", word_prefix])
    if word_suffix:
        cmd.extend(["--word-suffix", word_suffix])
    if granularity == "word":
        cmd.extend(["--granularity", "word"])
    if no_stats_page:
        cmd.append("--no-stats-page")

    revised_path = Path(revised)
    parent_dir = Path(output_dir) if output_dir else revised_path.parent

    # Snapshot files before comparison so we can identify the output
    before = set()
    for entry in parent_dir.iterdir():
        try:
            before.add(entry.resolve())
        except OSError:
            pass

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return {
                "ok": False,
                "error": f"quicompare failed (exit {result.returncode}): {result.stderr[:500]}",
            }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "quicompare timed out after 120s"}
    except Exception as e:
        return {"ok": False, "error": f"quicompare execution failed: {e}"}

    # Find the output file — it'll be a new .docx in the output dir
    output_word_actual = None
    output_pdf = None
    for entry in parent_dir.iterdir():
        try:
            rp = entry.resolve()
        except OSError:
            continue
        if rp in before:
            continue
        if entry.suffix == ".docx" and entry != revised_path.resolve():
            output_word_actual = str(entry)
        elif entry.suffix == ".pdf" and pdf:
            output_pdf = str(entry)

    return {
        "ok": True,
        "output_word": output_word_actual,
        "output_pdf": output_pdf,
        "author": author or _read_config_author(),
        "granularity": granularity,
    }


def _read_config_author() -> str:
    """Read the default author from quicompare config."""
    try:
        if os.path.isfile(_DEFAULT_CONFIG):
            cfg = json.loads(Path(_DEFAULT_CONFIG).read_text(encoding="utf-8"))
            return cfg.get("author", "quicompare")
    except Exception:
        pass
    return "quicompare"


def _para_final_text(para_el) -> str:
    parts: list[str] = []
    for child in para_el:
        if child.tag == f"{W}del":
            continue
        if child.tag == f"{W}ins":
            for t in child.iter(f"{W}t"):
                parts.append(t.text or "")
            continue
        for el in child.iter():
            if el.tag == f"{W}t":
                parts.append(el.text or "")
            elif el.tag == f"{W}tab":
                parts.append("\t")
    return "".join(parts).strip()


def _read_final_paragraphs(path: str) -> list[dict]:
    with zipfile.ZipFile(path, "r") as zf:
        doc_xml = zf.read("word/document.xml")
    root = etree.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        return []
    result: list[dict] = []
    para = 0
    for child in body:
        if child.tag != f"{W}p":
            continue
        para += 1
        text = _para_final_text(child)
        result.append({
            "para": para,
            "text": text,
            "tc_insertions": len(child.findall(f".//{W}ins")),
            "tc_deletions": len(child.findall(f".//{W}del")),
        })
    return result


def _classify_change(text: str) -> list[str]:
    categories: list[tuple[str, tuple[str, ...]]] = [
        ("parties/definitions", ("以下简称", "定义", "管理人", "项目公司", "运营主体", "支持方")),
        ("transaction-structure", ("受让", "转让", "公开挂牌", "招投标", "标的资产", "股权", "房屋买卖")),
        ("obligations/support", ("承诺", "支持", "足额", "义务", "违约", "资金缺口", "督促")),
        ("conditions/effectiveness", ("生效", "前提", "签署", "审批", "流程", "中标")),
        ("term/termination", ("持续有效", "终止", "届满", "到期", "履行完毕")),
        ("liability/security", ("担保", "债务承担", "不可撤销", "责任")),
        ("documents", ("法律文件", "合同", "协议", "通知", "函件")),
    ]
    return [label for label, keys in categories if any(k in text for k in keys)] or ["general"]


def _compact(text: str, limit: int = 260) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def summary(original: str, revised: str, *, limit: int = 80) -> dict:
    """Produce a paragraph-level legal review summary without external redline tools."""
    if not os.path.isfile(original):
        return {"ok": False, "error": f"Original file not found: {original}"}
    if not os.path.isfile(revised):
        return {"ok": False, "error": f"Revised file not found: {revised}"}

    old_paras = _read_final_paragraphs(original)
    new_paras = _read_final_paragraphs(revised)
    old_texts = [p["text"] for p in old_paras]
    new_texts = [p["text"] for p in new_paras]
    matcher = difflib.SequenceMatcher(None, old_texts, new_texts, autojunk=False)

    changes: list[dict] = []
    category_counts: dict[str, int] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_block = "\n".join(old_texts[i1:i2])
        new_block = "\n".join(new_texts[j1:j2])
        combined = f"{old_block}\n{new_block}"
        cats = _classify_change(combined)
        for cat in cats:
            category_counts[cat] = category_counts.get(cat, 0) + 1
        changes.append({
            "type": tag,
            "original_paras": [old_paras[k]["para"] for k in range(i1, i2)],
            "revised_paras": [new_paras[k]["para"] for k in range(j1, j2)],
            "categories": cats,
            "original": _compact(old_block),
            "revised": _compact(new_block),
        })

    tc_revised = {
        "insertions": sum(p["tc_insertions"] for p in new_paras),
        "deletions": sum(p["tc_deletions"] for p in new_paras),
        "paragraphs_with_tc": sum(1 for p in new_paras if p["tc_insertions"] or p["tc_deletions"]),
    }

    return {
        "ok": True,
        "original": os.path.abspath(original),
        "revised": os.path.abspath(revised),
        "paragraphs": {"original": len(old_paras), "revised": len(new_paras)},
        "changes_total": len(changes),
        "category_counts": category_counts,
        "revised_tracked_changes": tc_revised,
        "changes": changes[: max(1, int(limit or 80))],
        "truncated": len(changes) > max(1, int(limit or 80)),
        "next_reads": [
            {
                "tool": "lex_read",
                "args": {
                    "path": os.path.abspath(revised),
                    "paras": c["revised_paras"][:5],
                    "show_tc": "all",
                    "include_comments": True,
                },
            }
            for c in changes[:10]
            if c.get("revised_paras")
        ],
    }
