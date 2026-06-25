---
name: legal-xref
description: "Cross-reference audit, management, and repair for legal documents using lex-docx CLI. Covers single-doc audit, multi-doc cross-doc scan, static-to-dynamic ref conversion, term format audit, and xref repair workflows."
version: 1.0.0
author: KingsClaws; Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Legal, DOCX, Cross-Reference, XRef, Audit]
    category: legal
---

# Legal Cross-Reference Skill

Use the `lex-docx` CLI for all cross-reference operations on legal `.docx` documents.
This skill covers audit, repair, conversion, and multi-document cross-reference
management.

## Prerequisites

```bash
which lex-docx && lex-docx xref-audit --help && lex-docx term-format-audit --help
```

---

## Core Tools

| Command | Scope | Output |
|---------|-------|--------|
| `xref audit` | Single doc | Dead refs + unreferenced clauses |
| `cross-doc-scan` | Multiple docs | Broken cross-document refs |
| `audit-documents` | Project-wide | Per-doc audit + cross-doc scan |
| `convert-static-refs` | Single doc | Static text → clickable REF fields |
| `term-format-audit` | Single doc | Terms with formatting data |
| `xref convert` | Single doc | Static ref → REF field (family alias) |
| `xref scan` | Multiple docs | Cross-doc scan (family alias) |

---

## Single Document Audit

### Quick Audit

```bash
# JSON output (machine-readable)
lex-docx xref audit contract.docx --fmt json

# Human-readable text output
lex-docx xref audit contract.docx --fmt text
```

### Understanding the Output

```json
{
  "total_refs": 47,
  "dead_refs": [
    {
      "para": 23,
      "ref_text": "详见第15.3条",
      "target": "15.3",
      "reason": "clause not found in document"
    }
  ],
  "unreferenced_clauses": [
    "第23条 违约责任",
    "Schedule 4"
  ]
}
```

**Dead ref**: A cross-reference ("详见第X条") that points to a non-existent target.
**Unreferenced clause**: A heading/clause that exists but no other clause references it.

### What the Scanner Detects

The scanner builds a clause index from:
- Word heading styles (Heading 1, Heading 2, ...)
- AOHead custom styles
- Chinese numerals: 第一条, 第十二条, 第二十三条
- English headings: Clause X, Section X, Article X
- **Inferred headings**: bold + short + no-indent paragraphs (common in legal templates)

Reference patterns detected:
- Chinese: 第X条, 第X.X款, 详见第X条, 根据第X条
- English: Clause X, Section X, Article X, Clause X.X
- Cross-doc: 《DocumentName》第X条

### Verifying Machine Findings

Machine audit may produce false positives:
- References to attachment schedules (not in heading system) → mark as "verified: attachment ref"
- References in comments → check with `lex-docx comment-list`
- TC-related numbering shifts → check `review inspect` with original/final views

For each dead ref:
1. Read surrounding context: `lex-docx review inspect contract.docx --range <N-1>,<N+1> --rich`
2. Check if target exists in another document
3. Determine severity: MAJOR (legal effect) vs MINOR (auto-fixable)

---

## Multi-Document Audit (Project-Wide)

For transactions with multiple documents (loan + guarantee + security + schedules):

```bash
# Full project audit
lex-docx audit-documents Loan.docx \
  --docs Guarantee.docx Security.docx Schedule1.docx \
  --fmt json

# Cross-document scan only (no per-doc audit)
lex-docx cross-doc-scan Loan.docx \
  --docs Guarantee.docx Security.docx \
  --fmt text
```

### Cross-Document Reference Patterns

```
Source: Guarantee.docx §5
Text: "as defined in Clause 3.2 of the Loan Agreement"
Target: Loan.docx → check if §3.2 exists

Source: Security.docx §12
Text: "pursuant to Schedule 3 of the Facility Agreement"  
Target: Facility.docx → check if Schedule 3 exists
```

### Multi-Doc Audit Output

```json
{
  "Loan.docx": {
    "dead_refs": [...],
    "unreferenced_clauses": [...]
  },
  "Guarantee.docx": {
    "dead_refs": [...],
    "broken_cross_refs": [
      {"para": 5, "ref": "Loan.docx §22", "reason": "§22 not in Loan.docx"}
    ]
  }
}
```

---

## Static Reference Conversion

Convert hardcoded "第X条" text to clickable Word REF fields:

```bash
# Preview what would change (always do this first)
lex-docx convert-static-refs contract.docx --dry-run

# Convert specific clauses only
lex-docx convert-static-refs contract.docx --clauses 3.2 5.1 12 --dry-run

# Apply conversion (NOT dry-run)
lex-docx convert-static-refs contract.docx --no-dry-run --out contract_xref.docx
```

**When to convert:**
- Final delivery version — makes navigation easier for readers
- Internal review — helps reviewers jump between clauses

**When NOT to convert:**
- Mid-negotiation — clause numbers may shift
- Template documents — will be filled/edited later

---

## Term Format Audit

Audit how defined terms are formatted — essential for:
- Adding new clauses that integrate organically
- Verifying definition consistency across documents
- Cross-system drafting (APLMA vs NAFMII)

```bash
# JSON output with full format data
lex-docx term-format-audit contract.docx --fmt json

# Specific range
lex-docx term-format-audit contract.docx --range 0,30 --fmt json

# Human-readable
lex-docx term-format-audit contract.docx --fmt text
```

### Output Fields

```json
{
  "para_index": 3,
  "text_preview": "\"贷款\"指本协议项下...",
  "terms": [
    {
      "text": "贷款",
      "char_start": 1,
      "char_end": 3,
      "format": {
        "bold": true,
        "italic": false,
        "underline": true,
        "caps": false,
        "small_caps": false,
        "font_name": "宋体",
        "font_size": 11.5
      }
    }
  ]
}
```

### Using the Format Data

| Format Signature | System | Example |
|-----------------|--------|---------|
| `bold + underline` | NAFMII (Chinese) | `[b][u]贷款[/u][/b]` |
| `bold` only | APLMA/LMA | `[b]Facility Agent[/b]` |
| `bold + caps` | ISDA/US | `[b][caps]TRANSACTION[/caps][/b]` |
| `none` | General | Plain text terms |

---

## XRef Repair Workflow

When dead refs are found:

### 1. Diagnose the Cause

```bash
# Check if TC revisions shifted numbering
lex-docx review inspect contract.docx --range <para-2>,<para+2> --rich

# Check comments for numbering change notes
lex-docx comment-list contract.docx --range <para-2>,<para+2> --fmt text

# Check original vs final numbering
lex-docx review inspect contract.docx --range <para-2>,<para+2> --fmt text
```

### 2. Classify the Issue

| Cause | Fix |
|-------|-----|
| Numbering shifted during editing | Accept TC, re-run audit |
| Clause was deleted | Determine if reference should be removed |
| Clause was renumbered | Update the reference text |
| Attachment reference (false positive) | Mark as verified, no fix needed |
| Wrong clause number originally | Correct the reference |

### 3. Apply the Fix

```bash
# Fix a wrong clause number
lex-docx replace contract.docx --para 23 --old "第15.3条" --new "第14.3条" --tc

# Fix a wrong cross-document reference
lex-docx replace guarantee.docx --para 5 --old "Clause 22" --new "Clause 20" --tc

# Verify after fix
lex-docx xref audit contract.docx --fmt text
```

### 4. Re-Audit After Fix

```bash
lex-docx xref audit contract.docx --fmt text
# Should show 0 dead refs after all fixes applied
```

---

## Large Document Strategy

For documents with >200 paragraphs:

```bash
# 1. Get section boundaries
lex-docx structure contract.docx

# 2. Audit per section range
lex-docx xref audit contract.docx --fmt json | python3 -c "
import json, sys
data = json.load(sys.stdin)
# Filter dead refs by para range
section_refs = [r for r in data['dead_refs'] if 0 <= r['para'] <= 50]
print(f'Section 1: {len(section_refs)} dead refs')
"

# 3. Process section by section
```

---

## NAFMII / APLMA Specific Checks

### NAFMII (Chinese Loan Agreement)
- [ ] 条款编号体系: 第一条 → 1.1 → (1) → ①
- [ ] 第1条定义术语在全文使用一致
- [ ] 交叉引用格式: "详见第X条" / "根据第X.X款"
- [ ] 附件引用: "详见附件X" — 附件真实存在
- [ ] 可选条款残留: 选了[A]方案后[B]方案文字完全删除

### APLMA (English Syndicated Loan)
- [ ] Clause numbering: 1 → 1.1 → (a) → (i)
- [ ] Defined terms: bold + initial caps (NOT underline)
- [ ] Cross-refs: "Clause X" / "Schedule X"
- [ ] Schedule references point to existing schedules
- [ ] Definition clause (§1.1) covers all bold-capitalized terms

---

## Quick Reference

| Task | Command |
|------|---------|
| Single doc audit | `lex-docx xref audit doc.docx --fmt text` |
| Multi-doc audit | `lex-docx audit-documents main.docx --docs a.docx b.docx` |
| Cross-doc scan | `lex-docx cross-doc-scan main.docx --docs a.docx b.docx` |
| Term format audit | `lex-docx term-format-audit doc.docx --fmt json` |
| Preview conversion | `lex-docx convert-static-refs doc.docx --dry-run` |
| Apply conversion | `lex-docx convert-static-refs doc.docx --no-dry-run --out out.docx` |
| Fix dead ref | `lex-docx replace doc.docx --para N --old "§X" --new "§Y" --tc` |
| Verify fix | `lex-docx xref audit doc.docx --fmt text` |

## Pitfalls

- Always run `--dry-run` before `convert-static-refs` — it modifies the document
- Machine audit may flag attachment references as dead refs — verify manually
- TC revisions can shift numbering — accept TC before final xref audit
- Cross-doc refs need the target document to exist on disk — use absolute paths
- `term-format-audit` only detects terms in definition patterns — manually defined terms may be missed
- Don't convert static refs mid-negotiation — clause numbers will shift
