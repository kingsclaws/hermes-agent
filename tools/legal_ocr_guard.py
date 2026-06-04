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
