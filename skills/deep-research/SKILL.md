---
name: deep-research
description: >
  Deep research workflow for legal and project work. Orchestrates multi-source
  research with source ledger, evidence tracking, and structured deliverables.
  Use when user says "deep research", "deepresearch", "研究一下", "帮我查",
  or asks complex research questions that need multiple sources.
---

# Deep Research Skill

## When to Use

- User asks a complex research question requiring multiple sources
- User says "deep research" / "deepresearch" / "研究一下" / "帮我查一下"
- The question spans legal, regulatory, factual, or cross-reference domains
- A single web search won't suffice

## Workflow

### Phase 1: Clarify (1-2 questions max)

Only ask questions that materially change the research strategy:
- **Jurisdiction**: Which legal system / country?
- **Date scope**: Current law only, or historical evolution?
- **Depth**: Quick overview or exhaustive analysis?
- **Source preference**: Statutes only, or include academic commentary?

If the user's question is already specific, skip this phase.

### Phase 2: Plan

Decompose the main question into 3-7 subquestions. For each:
- What source type to search (web, project docs, OCR, prior facts)
- Priority order
- Stop condition (what constitutes "enough")

Present the plan as a numbered list. Proceed unless user objects.

### Phase 3: Collect

For each subquestion, in parallel where possible:
1. `web_search` for current/authoritative sources
2. `scrape` / `web_fetch` to extract full content
3. Check project documents via `lex_read` / `lex_scan`
4. Check prior project facts via `memory(action="read")`

Record every source in a structured ledger:
```
| # | Source | Type | Authority | URL/Path | Key Finding |
|---|--------|------|-----------|----------|-------------|
```

Authority tiers:
- **A**: Statutes, regulations, official guidance, court originals
- **B**: Project source documents, formal client materials
- **C**: Reputable secondary analysis, law firm publications
- **D**: Low-trust or unverified references

### Phase 4: Synthesize

For each subquestion:
- **Confirmed findings**: Supported by ≥2 Tier A/B sources
- **Disputed findings**: Conflicting sources — flag both sides
- **Unresolved gaps**: No authoritative source found
- **Recommended next checks**: What would resolve gaps

### Phase 5: Deliver

Always produce three files in the project directory:

1. **`research-{topic}.md`** — Working draft for future agents
2. **`research-{topic}.html`** — User-facing formatted report
3. **`research-{topic}.docx`** — Converted via pandoc

Chat reply: summary only + file paths.

### Phase 6: Update Project Facts

If findings are high-confidence (Tier A/B, ≥2 sources):
- Write to `project_facts` via `memory(action="add", scope="project")`
- Tag with source references

## Output Template

```markdown
# Research: {Topic}

## Question
{Original question}

## Executive Summary
{2-3 sentence answer}

## Findings

### Sub-question 1: {Question}
**Answer**: {Finding}
**Sources**: {List with authority tiers}
**Confidence**: High/Medium/Low

### Sub-question 2: {Question}
...

## Source Ledger
| # | Source | Authority | URL | Key Excerpt |
|---|--------|-----------|-----|-------------|

## Unresolved Questions
- {Gap 1}
- {Gap 2}

## Recommendations
- {Next step 1}
- {Next step 2}
```

## Legal-Specific Rules

1. **Prefer primary sources**: Statutes > regulations > commentary > blog posts
2. **Track jurisdiction**: Never assume a rule applies across borders
3. **Track dates**: Note effective dates, amendments, sunset clauses
4. **Preserve citation granularity**: Paragraph/page/section level when possible
5. **Distinguish project evidence from external reference**: Don't mix source types
6. **Flag contradictions**: If two sources disagree, present both with context

## Integration with Lex Master

When called from a project coordinator session:
- Research findings update that project's facts
- Deliverables go to the project directory
- Call `lex_master_route(action="report")` when done (if dispatched by lex-master)

When called standalone (no project context):
- Deliverables go to current working directory
- No project facts update
