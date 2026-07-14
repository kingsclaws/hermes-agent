from __future__ import annotations

import re


_GENERIC_OCR_PATTERNS = (
    r"\bpymupdf\b",
    r"\bfitz\b",
    r"\bpymupdf4llm\b",
    r"\bmarker[-_]?pdf\b",
    r"\bmarker_single\b",
    r"\btesseract\b",
    r"\bpdftoppm\b",
)

_LEX_NATIVE_TOOL_PATTERNS = (
    r"\blex-ocr\b",
    r"\blex_read\b",
    r"\blex_stats\b",
    r"\blex_edit\b",
    r"\blex_format\b",
    r"\blex_ref\b",
    r"\blex_project_init\b",
    r"\blex_diff\b",
    r"\blex_deliver\b",
    r"\blex_gate_check\b",
    r"\bfrom\s+lexitool\b",
    r"\bimport\s+lexitool\b",
    r"\blexitool\.",
)


def state_db_write_attempt_reason(text: str | None) -> str | None:
    """Return a rejection reason for manual Hermes state DB writes."""
    if not text:
        return None

    lowered = text.lower()
    touches_state_db = (
        "state.db" in lowered
        or "~/.hermes" in lowered
        or "/root/.hermes" in lowered
    )
    writes_project_table = (
        "insert" in lowered
        or "update" in lowered
        or "delete" in lowered
        or "replace" in lowered
    ) and "projects" in lowered
    uses_sqlite = "sqlite3" in lowered or "sqlite3.connect" in lowered

    if touches_state_db and writes_project_table and uses_sqlite:
        return (
            "Manual Hermes state DB writes are blocked. Use native project tools "
            "instead: `legal_project_create` to register an existing project directory, "
            "`project_select` to activate it, and `project_status` to verify."
        )
    return None


def native_lex_tool_attempt_reason(text: str | None) -> str | None:
    """Return a rejection reason for shell/Python attempts to call Lex tools."""
    if not text:
        return None

    lowered = text.lower()
    for pattern in _LEX_NATIVE_TOOL_PATTERNS:
        if re.search(pattern, lowered):
            return (
                "Shell/Python Lex tool calls are blocked in Lex Hermes. "
                "Use native Hermes tool calls instead: `lex_ocr` for PDFs/scans, "
                "`lex_read` for DOCX reading, `lex_edit` for atomic edits, "
                "`lex_ref` for cross-references, and `lex_project_init` for "
                "project intake."
            )
    return None


def generic_ocr_attempt_reason(text: str | None) -> str | None:
    """Return a rejection reason when text tries generic OCR before lex_ocr."""
    if not text:
        return None

    lowered = text.lower()
    if "lex_ocr" in lowered or "lex-ocr" in lowered:
        return None

    for pattern in _GENERIC_OCR_PATTERNS:
        if re.search(pattern, lowered):
            return (
                "Generic OCR/PDF extraction is blocked for Lex Hermes legal workflows. "
                "Use the native `lex_ocr` tool first for local legal PDFs/scans. "
                "Only use pymupdf/marker/tesseract/terminal OCR after `lex_ocr` fails "
                "or the user explicitly asks for a non-Lex extractor."
            )
    return None


def lex_harness_guard_reason(text: str | None) -> tuple[str, str] | None:
    """Return (error_code, reason) when Lex harness policy blocks execution."""
    reason = state_db_write_attempt_reason(text)
    if reason:
        return "native_project_tool_required", reason

    reason = native_lex_tool_attempt_reason(text)
    if reason:
        return "native_lex_tool_required", reason

    reason = generic_ocr_attempt_reason(text)
    if reason:
        return "lex_ocr_required", reason

    return None
