---
name: lexitool-operations
description: "Use native Lex tools for legal document operations."
version: 1.0.0
author: KingsClaws; Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Legal, DOCX, OCR, Lexitool, Track-Changes]
    category: legal
---

# Lexitool Operations Skill

This skill defines how Lex Hermes handles legal document operations. It does not replace the native tools; it tells you which native tool to call and when. Do not turn these tools into shell commands.

## When to Use

Use this skill when working with legal `.docx` documents, local PDFs, scanned legal materials, due-diligence files, syndicated-loan documents, template revisions, Track Changes, signing pages, tables, or cross-references.

## Prerequisites

The session should expose native Lex Hermes tools:

`lex_ocr`, `lex_project_init`, `lex_read`, `lex_stats`, `lex_edit`, `lex_ref`, `lex_diff`, `lex_deliver`, `lex_gate_check`, `lex_translation_review`, `legal_orchestrate`, `legal_workflow`.

If these tools are missing, state that the native Lex toolset is unavailable in this session. Do not silently fall back to `terminal` or `execute_code`.

## How to Run

Always call native tools directly:

```text
lex_ocr(file_path="/workingfile/project/营业执照.pdf", language="ch")
lex_read(path="/workingfile/project/D01.docx", mode="structure")
lex_translation_review(bilingual_path="/workingfile/project/bilingual.docx", instructions="Chinese controls; review clauses 9-16")
lex_edit(path="/workingfile/project/D01.docx", op="replace", target="§29", new_text="...", tc=true)
lex_ref(path="/workingfile/project/D01.docx", op="xref_audit")
```

Never run native Lex tools as terminal commands or direct Python imports.

## Quick Reference

`lex_ocr`: OCR for local legal PDFs and scans. Use before `vision_analyze`, `pymupdf`, `marker-pdf`, or `tesseract`.

`lex_project_init`: Scan a project directory, extract `.docx` and `.pdf` content, and create project context.

`lex_read`: Read `.docx` content. Use `mode="structure"` for headings/TOC; use `paras=[...]` for targeted reading.

`lex_stats`: Get document stats, Track Changes count, fonts, sections, and diagnostics.

`lex_edit`: Atomic `.docx` edits. Use `tc=true` and author `JT` unless Master specifies otherwise.

`lex_ref`: Bookmarks, fields, cross-references, hardcoded reference conversion, and xref audit.

`lex_diff`: Professional legal redline.

`lex_deliver`: Delivery package and reports.

`lex_gate_check`: Pre-delivery quality gates.

`lex_translation_review`: Native bilingual legal translation QA. Use this before generic proofread for Chinese-English translation checks.

`legal_orchestrate`: Multi-agent legal workflow orchestration for complex tasks.

`legal_workflow`: Create visible workflow plans, including `translation_quality_review` for node-based review orchestration.

## Procedure

For project intake:

1. Use `project_select` / `project_status` if project context exists.
2. Search the project directory for source files.
3. Use `lex_ocr` for PDFs and scanned materials.
4. Use `lex_read` for DOCX files.
5. Use `lex_project_init` for directory-level indexing.
6. Summarize parties, documents, approvals, collateral, missing materials, risks, and next steps.

For DOCX revision:

1. Copy the template or source document first if editing creates a deliverable.
2. Use `lex_read(mode="structure")` to understand layout.
3. Use targeted `lex_read(paras=[...])` before each edit.
4. Use `lex_edit` with Track Changes for atomic modifications.
5. Use `lex_ref` when cross-references are affected.
6. Verify with `lex_read`, `lex_stats`, and relevant audits.

For complex legal workflows:

1. Use `legal_orchestrate` when work involves multiple documents, multiple issue types, or edit-plus-review.
2. Use specialized reviewers for content, format, xref, TS consistency, and translation.
3. Keep orchestration visible: state what was delegated, to whom, and what output is expected.

For Chinese-English translation QA:

1. Use `legal_workflow(action="create_plan", workflow_type="translation_quality_review", ...)` when the user wants a visible workflow plan.
2. Use `lex_translation_review` for execution. If the Chinese and English are in one DOCX, pass `bilingual_path`; if separate, pass `source_path` and `translation_path`.
3. Require reviewers to report structured findings by paragraph, issue type, severity, source text, translation text, and suggested wording.
4. Do not use `lex_edit` until findings are aggregated and the user approves the specific wording changes.

## Pitfalls

Do not use `terminal` or `execute_code` to call `lexitool` Python APIs when native `lex_*` tools exist.

Do not use `vision_analyze` on PDFs. It accepts real image files only.

Do not install `pymupdf`, `marker-pdf`, or OCR packages before trying `lex_ocr`.

Do not create a new document from scratch when a legal template exists. Copy and modify the template.

Do not treat `[ref]` in `lex_read` as broken. Word may resolve it correctly. Use `lex_ref` audit.

Do not convert hardcoded references by guessing. Match exact clause number and heading bookmark.

Do not replace an entire paragraph containing a table marker; use table-specific operations or read context first.

## Verification

After OCR, confirm `char_count`, source file, API used, and whether output appears complete.

After edits, read the affected paragraphs/tables and report Track Changes status.

After cross-reference changes, run xref audit.

Before delivery, run `lex_gate_check` and provide remaining risks.
