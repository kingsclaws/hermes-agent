# ADR-0001: Kanban-Backed Legal Harness

## Status

Accepted

## Context

Lex-Hermes is shifting from a coding-oriented harness to a legal document harness. Legal document work often requires whole-document reading, paragraph-by-paragraph review, structured handoff, project fact maintenance, and verification evidence. Prompt-only instructions have not been reliable enough for this: agents can still bypass the intended workflow with direct `lex_edit`, broad replacement, or ad hoc delegation.

The user primarily works through CLI/TUI today. WebUI is important as a viewer and intervention surface, but it is not stable enough to be the required execution path.

## Decision

Use `legal_workflow_runs` as the user-visible workflow state, and use kanban as the mandatory execution layer for non-trivial legal document work.

This must be enforced by code orchestration, not just prompting:

- CLI/TUI agents must autonomously create or resume a kanban-backed workflow run for document drafting, revision, review, translation QA, template fill/cleanup, or other multi-step legal document work.
- `legal_orchestrate` should route those tasks into kanban-backed workflow execution instead of directly editing or delegating outside the workflow.
- Direct `lex_read` remains allowed for inspection and orientation.
- Direct `lex_edit` remains allowed only for explicit, narrow, single-point edits.
- Kanban task events and handoff metadata are the canonical execution fact record.
- `project_facts` stores matter facts, not execution proof.
- `edit_verification_record` stores edit verification evidence, not general project facts.
- WebUI reads and controls workflow state, but is not the required launcher or executor.

## Consequences

- Legal document tasks become visible, resumable, and auditable from CLI/TUI and WebUI.
- Agent work must produce structured handoff and verification evidence before approval.
- Scorecards can evaluate actual execution records instead of trusting final prose.
- Some simple tasks must still bypass kanban to avoid excessive overhead; this requires a clear narrow-edit exception.
- The system needs a stable bridge between `legal_workflow_steps` and kanban task IDs.

