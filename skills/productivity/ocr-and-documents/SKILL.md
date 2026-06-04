---
name: ocr-and-documents
description: "Extract text from legal PDFs and scanned documents."
version: 2.4.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [PDF, Documents, Research, Arxiv, Text-Extraction, OCR]
    related_skills: [powerpoint]
---

# PDF & Document Extraction

This skill handles legal PDFs, scanned due-diligence materials, and general PDF extraction. In Lex Hermes legal workflows, native tools are mandatory for local legal documents.

## When to Use

Use this skill when the user asks to read, OCR, summarize, or extract text from PDFs, scanned legal materials, business licenses, IDs, articles of association, partnership agreements, approvals, property certificates, or project base documents.

## Prerequisites

The session should expose the native `lex_ocr` tool. If `lex_ocr` is unavailable, state that native Lex OCR is not exposed in this session before considering fallback extraction.

## How to Run

For local legal PDFs and scans, call native `lex_ocr` directly.

```text
lex_ocr(file_path="/workingfile/project/营业执照.pdf", language="ch")
lex_ocr(file_path="/workingfile/project/章程.pdf", language="ch", page_range="1-10")
```

Do not run `lex-ocr` in `terminal`. Do not install or import `pymupdf`, `marker-pdf`, or `tesseract` before trying native `lex_ocr`.

For remote PDF URLs, use `web_extract` first.

```text
web_extract(urls=["https://example.com/report.pdf"])
```

## Quick Reference

`lex_ocr`: Primary tool for local legal PDFs and scanned documents.

`web_extract`: Primary tool for remote PDF URLs.

`vision_analyze`: Only for actual image files, not PDFs.

`lex_read`: Use for `.docx` files. Do not OCR Word documents.

## Procedure

1. Identify whether the file is local or remote.
2. For local legal PDFs/scans, call `lex_ocr`.
3. For `.docx`, call `lex_read` instead of OCR.
4. For remote PDFs, call `web_extract`.
5. After OCR, report source file, page range, API/method used, and whether extraction appears complete.

## Pitfalls

Do not call `terminal` with `lex-ocr`.

Do not use `execute_code` to import OCR libraries for legal PDFs.

Do not call `vision_analyze` on PDFs.

Do not batch OCR through shell loops unless native `lex_ocr` is unavailable and Master explicitly approves fallback.

## Verification

Confirm OCR output has meaningful Chinese text, key legal identifiers, and enough content length. If OCR fails, report the exact failure and then ask whether to use fallback extraction.
