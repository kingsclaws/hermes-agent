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
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_QUICOMPARE_BIN = "/usr/local/bin/quicompare"
_DEFAULT_CONFIG = "/root/.config/quicompare/config.json"


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
