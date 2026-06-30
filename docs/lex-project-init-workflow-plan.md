# Lex Project Init Workflow Plan

## Objective

Build a legal-project initialization workflow that turns a project directory
into a maintained project state, source inventory, source digests, party facts,
external verification notes, and an init impression before drafting begins.

## State Model

- `CREATED`: project record exists; no material triage yet.
- `INIT_READING`: source inventory exists and core materials are being read.
- `PARTY_ENRICHMENT`: party facts extracted from client materials are checked
  through configured external sources such as QCC MCP.
- `INIT_SYNTHESIS`: coordinator promotes reliable facts and writes the init
  impression.
- `READY_TO_DRAFT`: init outputs are complete; drafting requires explicit user
  authorization.
- `DRAFTING`: user has explicitly requested drafting/revision.
- `REVIEWING`: content/format/xref/TS gates are running.
- `CLOSING`: final delivery, closing checklist, and report generation.

Init may advance automatically through `READY_TO_DRAFT`. Entering `DRAFTING`
requires user authorization unless a user-approved workflow already includes it.

## Init Completion Standard

Init is complete only when these outputs exist:

- `project_profile`: client, matter type, transaction structure, objectives,
  target documents, stage, key dates, and parties.
- `project_facts`: canonical source facts, external facts, candidate facts,
  conflicts, confidence, source refs, timestamps, and updater identity.
- `source_inventory`: every material file with priority, type, read status,
  read method, OCR/cache state, and whether it must be read before init closes.
- `init_impression`: coordinator synthesis covering structure, parties,
  commercial terms, document set, source evidence, drafting implications, risk
  flags, open questions, and recommended next workflow.

## Source Workflow

1. Scan project directory and create `source_inventory`.
2. Classify files by type:
   - core transaction documents
   - commercial terms / approvals
   - party materials
   - security / asset materials
   - prior versions / markups / feedback lists
   - project notes / issue reports
   - supporting / backlog / ignored
3. Dispatch one worker per core file.
4. Batch supporting files when safe.
5. Use up to 5 parallel agents and up to 2 parallel OCR jobs by default.
6. Require full read coverage for core materials, or record a user-facing issue.

## Source Digest Contract

Every reader worker must produce a structured `source_digest`:

```yaml
file_path:
file_type:
read_method: lex_read | lex_ocr | pdf_text | vision | manual
read_coverage: full | partial | failed
confidence: high | medium | low
one_paragraph_summary:
key_facts:
  - fact:
    source_quote_or_location:
    legal_relevance:
open_questions:
  - question:
    reason:
risk_flags:
  - issue:
    severity:
    source_location:
suggested_project_facts:
  - key:
    value:
    source:
```

If `read_coverage != full` for a core file, init may continue but must surface
the issue and may not silently treat the file as read.

## OCR Rules

- DOCX/DOC/HTML/MD/XLSX: use native lexitool/readers first.
- PDF: check text layer first; use `lex_ocr` for scanned files.
- Images/scans: use `lex_ocr` or configured vision fallback.
- OCR output is cached in DB and mirrored to `.hermes-project/ocr/`.
- OCR failures are recorded with concrete error text; workers must not install
  ad hoc PDF/OCR packages as a workaround.

## Party Enrichment

After party materials are digested:

1. Extract party profiles from client materials.
2. Query QCC MCP when configured.
3. Store QCC output in `external_facts`, never overwriting client material.
4. Generate `verification_issue` records for conflicts.
5. Set `queried_at` and `expires_at` for external facts.

Priority of facts:

1. client formal materials
2. project transaction documents
3. QCC/external verification
4. worker inference

QCC conflicts do not block init by default. High-risk conflicts must be
prominently included in `open_questions` and `risk_flags`.

## Fact Layers

- `source_facts`: coordinator-approved facts from project materials.
- `external_facts`: QCC/MCP/public-source facts with expiry.
- `candidate_facts`: worker-proposed facts and inferences.
- `fact_conflicts`: structured conflict records.

Workers may propose facts. Coordinators promote facts. All later drafting and
review agents must treat the project fact DB as live state and update it when
they discover material changes.

## User Interruption Rules

Ask the user only for:

- missing core materials
- failed core reads/OCR
- high-risk party conflicts
- ambiguous project type
- unclear scope
- authorization to enter drafting

Other issues go into `open_questions` and `missing_info_list`.

## Execution Layer

Kanban is the single execution layer. WebUI is a display/control surface.

High-level entrypoints:

- `project_create(name, path, client?, matter_type?)`: create + auto-init.
- `project_register(name, path, no_init=true)`: register only, no auto-read.
- `project_init_start(project_id)`: create the init kanban graph.

Init kanban nodes:

- `inventory.scan`
- `source.digest:<file>`
- `party.enrich:<party>`
- `init.synthesis`
- `project.fact.promote`
- `init.user_questions`

## Project Directory Mirror

DB is canonical. `.hermes-project` is the readable mirror:

```text
.hermes-project/
  project-profile.md
  init-impression.md
  missing-info-list.md
  source-inventory.md
  source-digests/
  ocr/
  facts/
    project-facts.md
    fact-conflicts.md
  workflows/
    init-run-<timestamp>.md
```

Manual markdown edits are not automatically imported into DB. Agents must use a
fact/project update tool to promote human edits.

## Init Completion Report

Coordinator reports briefly:

- status
- core files read / total
- OCR count and failures
- party verification count and conflicts
- key facts
- top risks
- top open questions
- recommended next workflow
- links to generated outputs

Full report is generated as HTML and DOCX. Markdown remains for agent-readable
working papers.

## Implementation Slices

1. Add project init schema/migrations for source inventory, digests, facts,
   external facts, conflicts, and workflow runs.
2. Implement `project_create`, `project_register`, and `project_init_start`.
3. Implement source scanner and file classifier.
4. Implement digest worker contract and hard completion validation.
5. Implement OCR cache and lex_ocr-first scan handling.
6. Implement QCC MCP party enrichment.
7. Implement coordinator synthesis and fact promotion.
8. Mirror DB outputs to `.hermes-project`.
9. Add kanban/webui visibility for init graph and status.
10. Add startup context injection so all existing and new sessions understand
    project state, init outputs, and fact-maintenance duties.
