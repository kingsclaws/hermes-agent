---
name: legal-proofread
description: "Multi-stage legal document proofreading using lex-docx CLI. Covers content review, format audit, cross-reference validation, TS consistency, and pre-delivery quality gates."
version: 1.0.0
author: KingsClaws; Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Legal, DOCX, Proofread, Review, Quality]
    category: legal
---

# Legal Proofreading Skill — Multi-Stage Document Review

Use the `lex-docx` CLI for comprehensive legal document proofreading. This skill covers
four review dimensions: content, format, cross-reference, and deal-term consistency.

## Prerequisites

```bash
which lex-docx && lex-docx stats --help
```

## Proofreading Workflow

For documents of any size, follow this sequence:

```
1. Structure Assessment → Understand the document
2. Format Audit        → Mechanical checks (doctor + lint)
3. Content Review      → Legal substance review
4. XRef Audit          → Cross-reference validation
5. TS Consistency      → Deal term consistency (if Term Sheet exists)
6. Pre-Delivery Gate   → Final cleanup check
```

---

## Stage 1: Structure Assessment

```bash
# Get document overview
lex-docx stats contract.docx

# Export structure (headings, paragraphs, tables)
lex-docx structure contract.docx

# Query heading paragraphs
lex-docx para-query contract.docx --outline-level 1,2 --fmt text

# Check numbering integrity
lex-docx numbering inspect contract.docx --fmt text
```

**Questions to answer:**
- How many paragraphs/tables/sections?
- What heading structure? (Heading 1/2/3 or inferred?)
- Fonts used? Unified or mixed?
- Track Changes present? How many?
- Comments present? How many?

---

## Stage 2: Format Audit (Doctor + Lint)

### Doctor Check (structural issues)

```bash
# Full format diagnosis
lex-docx doctor check contract.docx --font 宋体 --font-size 11.5

# Check with custom standards
lex-docx doctor check contract.docx \
  --font 宋体 --ascii-font "Times New Roman" \
  --font-size 12 --toc-levels 1-3

# Auto-fix (with preview)
lex-docx doctor fix contract.docx --font 宋体 --rules D01,D02,D04 --dry-run

# Apply fixes
lex-docx doctor fix contract.docx --font 宋体 --rules D01,D02,D04
```

Doctor rules:
| Rule | Issue | Auto-Fix |
|------|-------|----------|
| D01 | Font mismatch | Yes |
| D02 | Font size mismatch | Yes |
| D03 | Double numbering | No (needs judgment) |
| D04 | Outline level leak | Yes |
| D05 | Invalid style reference | Yes |
| D06 | Sibling numPr gap | No |
| D07 | TOC u-switch missing | Yes |
| D08 | Heading font inconsistent | Yes |
| D09 | Footer stale entities | No (warn only) |

### Lint Check (content format rules)

```bash
# All rules
lex-docx lint contract.docx --fmt text

# Specific rules
lex-docx lint contract.docx --rules font_mismatch,double_numbering --fmt json
```

### Format Consistency Deep-Dive

```bash
# Query all non-standard font paragraphs
lex-docx para-query contract.docx --font "等线" --fmt text

# Check for inconsistent bolding
lex-docx para-query contract.docx --bold --range 0,100 --fmt text

# Find paragraphs with wrong alignment
lex-docx para-query contract.docx --jc left --fmt text
```

---

## Stage 3: Content Review

### Per-Range Deep Read

```bash
# Rich format-aware inspection
lex-docx review inspect contract.docx --range 0,50 --rich --fmt text

# Check comments (lawyer notes / negotiation decisions)
lex-docx comment-list contract.docx --fmt text

# Check Track Changes
lex-docx tc-list contract.docx --fmt text

# View range with comments inline
lex-docx review inspect contract.docx --range 0,50 --fmt text
```

### Content Review Checklist

For each paragraph range:
- [ ] Legal accuracy — facts, dates, amounts correct?
- [ ] Logical consistency — arguments supported by facts?
- [ ] Language quality — precise, unambiguous, professional?
- [ ] Completeness — all required elements present?
- [ ] **Organic integration** (modified docs) — format conventions consistent?

### Organic Integration Check (Modified Contracts)

For newly added or modified clauses:
- [ ] Defined term format matches host (`lex-docx term-format-audit`)
- [ ] Cross-reference convention matches host
- [ ] Drafting voice matches host (shall/may vs 应当/可以)
- [ ] Numbering scheme matches host
- [ ] Reads as organic, not copy-pasted

---

## Stage 4: Cross-Reference Audit

```bash
# Single document full audit
lex-docx xref audit contract.docx --fmt text

# Multi-document audit (main + schedules + guarantees)
lex-docx audit-documents main.docx \
  --docs schedule1.docx guarantee.docx security.docx \
  --fmt json

# Cross-document scan only
lex-docx cross-doc-scan main.docx \
  --docs guarantee.docx security.docx \
  --fmt text

# Convert static refs to clickable REF fields (preview)
lex-docx convert-static-refs contract.docx --dry-run
```

### XRef Audit Output Interpretation

```
Dead refs: "详见第15.3条" → 15.3 doesn't exist → MAJOR
Unreferenced clauses: 第23条 exists but never referenced → MINOR
Broken cross-doc: guarantee.docx §5 → main.docx §22 doesn't exist → MAJOR
```

**Severity levels:**
- **MAJOR**: Affects legal enforceability (dead refs, wrong clause numbers)
- **MINOR**: Can be auto-fixed (orphan clauses, formatting)
- **ADVISORY**: Style preference (xref convention inconsistency)

### Term Definition Audit

```bash
# Extract all defined terms with formatting
lex-docx term-format-audit contract.docx --fmt json

# Verify: every defined term used consistently throughout
# Check: formatting matches host convention
```

---

## Stage 5: Term Sheet Consistency (if applicable)

If a Term Sheet exists, verify:
- [ ] Party names match TS
- [ ] Amounts/dates match TS  
- [ ] Selected options match TS (A/B scheme choices)
- [ ] Deal structure matches TS (guarantee structure, security package)
- [ ] Conditions precedent match TS

---

## Stage 6: Pre-Delivery Gate

### Final Review Summary

```bash
# Comprehensive review stats
lex-docx review stats contract.docx --fmt text

# Check remaining TC
lex-docx tc-list contract.docx --fmt text

# Check remaining comments
lex-docx comment-list contract.docx --fmt text

# Footer audit
lex-docx footer-audit contract.docx --fmt text
```

### Cleanup (if approved)

```bash
# Preview
lex-docx clean contract.docx --dry-run

# Execute
lex-docx clean contract.docx --accept --yes --backup
```

### Pre-Delivery Checklist

- [ ] All TC accepted (or intentionally left for client review)
- [ ] All comments resolved and cleaned
- [ ] Headers/footers verified
- [ ] No orphan empty paragraphs
- [ ] All xref dead refs resolved
- [ ] Format consistent throughout
- [ ] Table of Contents refreshed: `lex-docx toc contract.docx refresh`
- [ ] Final stats look clean: `lex-docx stats contract.docx`

---

## Output Format

After proofreading, produce a structured report:

```markdown
## Proofreading Report: {document}

### Structure
- Paragraphs: {N}, Tables: {M}, Sections: {S}
- Fonts: {distribution}
- TC count: {N}, Comments: {M}

### Format Issues
- D01 font mismatch: {N} paragraphs
- D02 size mismatch: {N} paragraphs
- ...

### Content Issues
- Major (legal effect): ...
- Minor (wording): ...
- Advisory (style): ...

### Cross-Reference Issues
- Dead refs: {N} (verified {V}, confirmed broken {C})
- Broken cross-doc refs: {N}
- Unreferenced clauses: {N}

### Term Sheet Consistency
- Matches: ...
- Deviations: ...

### Pre-Delivery Status
- [ ] Ready for delivery
- [ ] Needs {N} fixes before delivery
```

## Working with Different Document Sizes

| Doc Size | Strategy |
|----------|----------|
| ≤ 50 paras | Read entire doc, manual review |
| 50-200 paras | Split into ~50-para chunks, review each |
| > 200 paras | Use structured range-based review per section |

For large documents (>200 paras), process section by section:
```bash
lex-docx structure contract.docx  # get section boundaries
lex-docx review inspect contract.docx --range 0,50 --rich
lex-docx review inspect contract.docx --range 51,100 --rich
# ... continue per section
```

## Pitfalls

- Don't skip the structure assessment — you need to know what you're dealing with
- Doctor check before manual review — let the machine find mechanical issues first
- Always run xref audit; never rely on manual cross-reference checking
- `term-format-audit` beats guessing — use it for every modified contract
- Don't clean TC/comments until review findings are confirmed
- For large docs, review by section, not by mechanical chunk
