# ADR-0002: Backoffice Issue Relay for Lexitool and Harness Problems

## Status

Accepted

## Context

Lex-Hermes legal work runs inside the `lex-hermes` container, while repository maintenance and deeper code fixes are often performed from a separate development container or host checkout. The two environments share mapped paths such as `/workingfile` and the repository working tree, but they do not share the same process, Python import state, active session state, or container image lifecycle.

This creates a recurring failure mode:

- A legal agent encounters a lexitool, OCR, gateway, workflow, or environment problem during a real project.
- The agent tries ad hoc shell/Python workarounds inside the legal session.
- The issue is only visible in chat logs unless the user manually tells the maintenance agent.
- Fixes require code changes, hot-copying into the running container, commits, image builds, and sometimes database/session migration.

Prompt-only instructions are not enough. The harness needs a low-friction way for legal project agents to report "tooling/backoffice" problems to the maintainer side without interrupting the legal work or pretending the legal worker can repair the harness itself.

## Decision

Introduce a **Backoffice Issue Relay**: a code-level hook and shared-file protocol that lets lex-hermes sessions file structured maintenance issues for lexitool/harness problems. The maintainer agent can then poll, triage, fix, and close those issues from the development environment.

The relay should be implemented as a Hermes plugin/hook plus a small CLI/API surface, not as a new always-running bespoke service.

Initial trigger points:

- `post_tool_call` for native lexitool tools when the result is an error or warning likely caused by tool/runtime defects.
- Kanban task failure/blocked hooks when a worker reports tooling failure rather than legal-content failure.
- `transform_tool_result` to add a visible `backoffice_issue` reference to the tool result returned to the agent.

Future guardrail trigger points:

- `pre_tool_call` for blocked legal-harness guardrails when the agent attempts a prohibited workaround because a native tool failed.

Initial persistence:

- Write issue envelopes to a shared path visible to both containers.
- Preferred path: project-local `.hermes-project/backoffice/issues/*.json` when a project is active.
- Fallback path: global `/workingfile/.lex-hermes-backoffice/issues/*.json`.

Issue envelope fields:

- `id`
- `created_at`
- `project_id`
- `project_path`
- `session_id`
- `profile`
- `surface` (`cli`, `tui`, `web`, `gateway`, `kanban-worker`)
- `category` (`lexitool`, `ocr`, `workflow`, `gateway`, `db`, `image`, `permission`, `other`)
- `severity`
- `summary`
- `tool_name`
- `tool_args_redacted`
- `error`
- `repro_steps`
- `artifact_paths`
- `status` (`open`, `triaged`, `fixed`, `wont_fix`, `needs_user`)
- `fix_commit`
- `fixed_image_digest`

The legal agent should continue the legal task when safe, but must not hide the issue. Final responses should mention any open backoffice issue that affected delivery quality.

Issue creation policy:

- Automatically create backoffice issues for low-sensitive technical failures: native `lex_*` tool crashes, OCR/MinerU authentication or connectivity failures, kanban dispatcher/worker lock failures, import/runtime errors, and container permission/environment defects.
- Redact tool arguments by default. Do not persist full document text, replacement text, prompts, OCR output, or model messages in the issue envelope.
- Persist artifact paths and short technical error summaries, not file contents.
- If a useful repro artifact would require attaching legal content, identity information, or long document excerpts, create the issue with `status: needs_user` and record only the path plus a note that explicit user approval is required before attachment.
- Deduplicate repeated identical failures by fingerprint and increment an occurrence counter instead of spamming the shared issue directory.

## Consequences

- Lexitool and harness problems become durable artifacts instead of disappearing in chat.
- The maintainer side can fix real project failures with context, repro data, and artifact paths.
- Legal workers stop improvising low-quality workarounds for tool defects.
- Shared file paths are simple and robust across separate containers, but require cleanup/retention policy.
- The relay is not a replacement for Kanban execution facts. It records harness/tooling problems, not legal work progress.

## Open Questions

- Should maintainer pickup be pull-based (`lex_backoffice list/claim`) or push-based via gateway/webhook notification?
- Which errors should be auto-classified as "tool defect" instead of ordinary user/project error?
