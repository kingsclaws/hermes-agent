---
name: legal-drafting
description: "Content-first legal document drafting and editing using lex-docx CLI. Covers convention analysis, TC editing, format management, verification protocol, and English/APLMA contract drafting."
version: 1.0.0
author: KingsClaws; Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Legal, DOCX, Drafting, Track-Changes, APLMA, NAFMII]
    category: legal
---

# Legal Drafting Skill — Content-First Contract Drafting

Use the `lex-docx` CLI for legal document drafting and editing. This skill emphasizes
**content-first** drafting — organic integration into the host document's conventions,
not copy-paste from templates.

## Prerequisites

The `lex-docx` CLI must be available. Verify:
```bash
which lex-docx && lex-docx stats --help
```

If missing, install:
```bash
pip install -e /root/.hermes/tools/lexitool
```

## Core Principle: Convention-Before-Edit

Before editing an existing document, ALWAYS analyze its conventions first:

1. **Term format audit** — How are defined terms formatted?
2. **Xref convention** — How are cross-references written?
3. **Numbering scheme** — What numbering style?
4. **Drafting voice** — shall/may or 应当/可以?

### Convention Analysis Step-by-Step

```bash
# Step 1: Extract defined term formatting
lex-docx term-format-audit contract.docx --fmt json

# Step 2: Understand document structure
lex-docx structure contract.docx

# Step 3: Sample-read key clauses (read 3-4 same-type paragraphs)
# Use the Bash tool to run lex-docx, or read .docx directly

# Step 4: Summarize the conventions BEFORE editing
# - Term format: bold+underline / bold only / bold+caps
# - Xref style: 第X条 / Clause X / Section X
# - Numbering: chinese_article / legal / decimal
# - Voice: shall/may / 应当/可以
```

## Document Statistics

```bash
lex-docx stats contract.docx
# Returns: paragraphs, tables, fonts, Track Changes count
```

## Editing with Track Changes

Always use `--tc` for content changes. Author defaults to `agent`.

```bash
# Insert text at paragraph end
lex-docx insert contract.docx --para 15 --text "新条款内容" --tc

# Replace text within paragraph
lex-docx replace contract.docx --para 15 --old "旧文字" --new "新文字" --tc

# Delete text (TC DEL)
lex-docx delete contract.docx --para 15 --tc
```

### TC-aware editing

```bash
# Preview TC changes
lex-docx tc-list contract.docx --fmt text

# Accept all changes (finalize)
lex-docx tc-accept contract.docx

# Reject all changes
lex-docx tc-reject contract.docx

# Accept only a specific author
lex-docx tc-accept contract.docx --author "JT"
```

## Format Management

```bash
# Format brush: copy format from reference para to target
lex-docx format-brush contract.docx --ref 10 --target 15,16,17

# Set outline level (for heading hierarchy)
lex-docx set-outline-level contract.docx --para 5 --level 2

# Bold defined terms (auto-detect)
lex-docx bold-terms contract.docx --scan --range 0,50

# Highlight paragraphs
lex-docx highlight contract.docx --range 10,15 --color yellow

# Query paragraphs by format
lex-docx para-query contract.docx --font "宋体" --bold --range 0,100
```

## Mandatory Post-Edit Verification Protocol

After EVERY edit, execute these checks:

### 1. Context Readback (±2 paragraphs)
```bash
lex-docx review inspect contract.docx --range <N-2>,<N+2> --rich
```

### 2. Format Consistency Check
```bash
lex-docx doctor check contract.docx --font 宋体 --font-size 11.5
```

### 3. Content Integration Verification (editing existing contracts)
- [ ] Defined term format matches host convention (use `term-format-audit`)
- [ ] New terms exist in host's definition clause
- [ ] Cross-reference convention matches host
- [ ] Drafting voice and sentence structure consistent
- [ ] New clause reads as organically integrated

### 4. TC Verification
```bash
lex-docx tc-list contract.docx --range <N-2>,<N+2> --fmt text
```

## English / APLMA Contract Drafting

### Defined Term Formatting by Standard

| Standard | Format | CLI Check |
|----------|--------|-----------|
| APLMA | Bold + Initial Caps | `lex-docx term-format-audit` → `bold: true, caps: false` |
| LMA | Bold + Initial Caps | Same as APLMA |
| ISDA | ALL CAPS | `caps: true` |
| NAFMII | Bold + Underline | `bold: true, underline: true` |

### Cross-Reference Convention

| Standard | Internal Ref | Schedule Ref |
|----------|-------------|--------------|
| APLMA | Clause X | Schedule X |
| LMA | Clause X | Part II of Schedule 3 |
| US | Section X | Exhibit A |

### Drafting Conventions

- **shall** = obligation, **may** = right/discretion, **must** = rarely used
- One-sentence-per-clause with nested sub-clauses (a)(b)(i)(ii)
- Connective words capitalized: PROVIDED THAT, NOTWITHSTANDING, EXCEPT THAT
- Date format: "on the date falling 5 Business Days after the Utilisation Date"

### Never Cross the Streams

- APLMA contract → use bold+caps terms, Clause X refs, shall/may
- NAFMII contract → use bold+underline terms, 第X条 refs, 应当/可以
- Running `term-format-audit` tells you which camp you're in

## Table Operations

```bash
# Extract table data
lex-docx extract contract.docx --table 3 --fmt json

# Inspect table formatting
lex-docx table-inspect contract.docx --table 5 --fmt text

# Fill table from JSON
lex-docx fill-table contract.docx --table 3 --data values.json

# Format table
lex-docx format-table contract.docx --table 3 --shading D9E2F3 --borders single

# Copy table between documents
lex-docx copy-table src.docx --src-table 3 dst.docx --dst-pos after_para:10
```

## Document Cleanup (Pre-Delivery)

```bash
# Preview what will be cleaned
lex-docx clean contract.docx --dry-run

# Execute cleanup: accept TC + remove comments + clear headers
lex-docx clean contract.docx --accept --yes

# Only remove empty paragraphs
lex-docx cleanup contract.docx --mode fix
```

## Structured Template Workflow (NAFMII/APLMA)

For annotated templates with fill-in blanks and guidance notes:

1. **Mechanical fill** — Fill blanks, select checkboxes (use `fill-table`, `fill-kv`)
2. **Substantive draft** — Draft custom clauses based on guidance notes (use `insert`/`replace` with TC)
3. **Cleanup** — Remove guidance notes/highlights/annotations (use `clean`, `comment-clean`)
4. **Review** — Switch to `legal-proofread` skill

## Quick Reference

| Task | Command |
|------|---------|
| Convention analysis | `lex-docx term-format-audit doc.docx` |
| Structure overview | `lex-docx structure doc.docx` |
| Stats | `lex-docx stats doc.docx` |
| Insert with TC | `lex-docx insert doc.docx --para N --text "..." --tc` |
| Replace with TC | `lex-docx replace doc.docx --para N --old "A" --new "B" --tc` |
| Format brush | `lex-docx format-brush doc.docx --ref N --target M` |
| Format diagnose | `lex-docx doctor check doc.docx --font 宋体` |
| Format fix | `lex-docx doctor fix doc.docx --font 宋体` |
| Bold terms | `lex-docx bold-terms doc.docx --scan` |
| TC review | `lex-docx review inspect doc.docx --range 0,50 --rich` |
| Clean deliver | `lex-docx clean doc.docx --accept --yes` |

## Pitfalls

- Never edit without running convention analysis first on existing documents
- Don't mix NAFMII conventions into APLMA contracts and vice versa
- Always use `--tc` for content changes; format-only changes may skip TC
- Don't delete entire paragraphs during editing — use TC DEL instead
- After every edit, read back ±2 paragraphs to check for collateral damage
- `term-format-audit` tells you EXACT formatting — don't assume
