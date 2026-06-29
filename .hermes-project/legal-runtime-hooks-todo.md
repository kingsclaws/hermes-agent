# Legal Runtime Hooks TODO

Purpose: move legal-harness guardrails from prompt-only SOP into enforceable runtime checks, similar in spirit to Claude Code hooks but scoped to Hermes tool execution, workflow gates, and Kanban dispatch.

## Priority 1

- `lex_ocr_required_for_pdf`: before generic shell/Python OCR commands, require native `lex_ocr` unless the user explicitly asks for diagnostics. Existing `legal_ocr_guard` covers common patterns; extend it to post-failure recovery so an agent does not start trying PyPDF/PyMuPDF after `lex_ocr` fails.
- `lex_edit_requires_recent_read`: before `lex_edit`, require a recent `lex_read`/`lex_scan`/`lex_diff` evidence record for the same document and target region. This prevents blind bulk replacements in legal documents.
- `lex_edit_requires_verify_readback`: after `lex_edit`, require a readback verification or `lex_diff` before final response. The final answer must cite verified paragraphs/sections, not just tool success.
- `swarm_task_requires_dispatch_health`: after creating Kanban tasks, check dispatcher/gateway health and stale PID lock state. If workers do not claim within a short window, surface actionable status instead of silently leaving tasks in `ready`.

## Priority 2

- `final_claim_requires_evidence_coverage`: before final response for review/proofread tasks, require an evidence coverage table showing source files reviewed, page/paragraph ranges covered, OCR/read failures, and unresolved gaps.
- `coordinator_no_direct_edit`: coordinator roles may plan, allocate, and verify, but should not directly edit documents unless explicitly instructed. Editing belongs to drafter/editor roles and must be represented on the Kanban board.
- `project_fact_update_on_material_change`: when agent identifies a reliable project fact, issue, party, amount, deadline, or drafting decision, require update/confirm flow into `project_facts` rather than leaving it only in chat memory.
- `legal_gate_before_delivery`: before marking document work complete, run applicable gates: content review, format review, cross-reference review, defined-term review, TS/term-sheet consistency, and redline precision check.

## Implementation Notes

- Prefer code-level orchestration around `handle_function_call`, tool handlers, and Kanban task lifecycle over system-prompt wording.
- Keep hooks config-driven in `config.yaml` or project metadata. Do not add user-facing non-secret `HERMES_*` env vars.
- Preserve prompt caching: hooks should validate tool calls/results and append normal tool-result messages, not rebuild the system prompt mid-session.
- Existing sessions must inherit these checks through tool/runtime code, not by requiring session recreation.
