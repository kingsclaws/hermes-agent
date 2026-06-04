---
name: legal-project-workflow
description: "Create legal projects from document directories."
version: 1.0.0
author: KingsClaws; Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Legal, Project, DOCX, OCR, Lexitool]
    category: productivity
---

# Legal Project Workflow Skill

This skill defines the Lex Hermes workflow for scanning legal document directories and creating project context. It uses native Hermes tools, not shell commands or direct `lexitool` Python imports.

## When to Use

Use this skill when Master asks to scan a legal project directory, read all base materials, create a project, summarize transaction documents, or internalize a syndicated loan or litigation matter.

## Prerequisites

The session should expose native tools:

`project_select`, `project_status`, `project_context`, `project_list`, `search_files`, `read_file`, `lex_project_init`, `lex_ocr`, `lex_read`, `lex_stats`.

If native Lex tools are unavailable, state that clearly. Do not silently fall back to `terminal`, `execute_code`, `lex-ocr`, or `from lexitool...`.

## How to Run

Call native tools directly:

```text
search_files(path="/workingfile", pattern="*颐保*", target="files")
lex_ocr(file_path="/workingfile/140. 颐保银团/基础资料/营业执照.pdf", language="ch")
lex_read(path="/workingfile/140. 颐保银团/1. Execution/D01.docx", mode="structure")
lex_project_init(project_path="/workingfile/140. 颐保银团", project_name="140. 颐保银团")
```

Never run Lex document operations through `terminal` or `execute_code`.

## Quick Reference

`search_files`: Find project files and directories.

`read_file`: Read existing `.md` project notes and term sheets.

`lex_ocr`: OCR local legal PDFs and scans.

`lex_read`: Read `.docx` documents.

`lex_project_init`: Create or refresh project index/context.

`project_select` / `project_status` / `project_context`: Work with registered project context.

## Procedure

1. Locate the project directory with `search_files`.
2. Read existing `.md` materials with `read_file`.
3. List and classify files: base documents, term sheet, approval, main agreements, security documents, legal opinions, CP materials.
4. Use `lex_ocr` for local PDFs and scans.
5. Use `lex_read` for DOCX files; use `mode="structure"` first, then targeted reads.
6. Use `lex_project_init` to register/index the directory.
7. Summarize parties, transaction structure, financing terms, security, approvals, missing documents, risk points, and next steps.

## Pitfalls

Do not use shell loops for OCR or DOCX reading.

Do not import `lexitool` in `execute_code`.

Do not treat all PDFs as generic `pymupdf` extraction targets. Legal scans first use `lex_ocr`.

Do not create project notes before reading the actual source documents.

## Verification

Before reporting completion, confirm:

1. Which files were read or OCR'd.
2. Which native tools were used.
3. Whether project context was created or refreshed.
4. Remaining unread files, failed OCR files, or uncertain facts.
