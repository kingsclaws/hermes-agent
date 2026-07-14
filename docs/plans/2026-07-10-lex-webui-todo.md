# Lex WebUI TODO

Date: 2026-07-10

## Goal

Fork `nesquena/hermes-webui` and adapt it for `lex-hermes` legal workflows without
rebuilding generic Hermes chat UX from scratch.

Primary requirement:

- show project context
- show kanban flow and current task stage
- show evidence/source-digest coverage
- show research runs, sources, and findings

## Principles

- Keep `lex-hermes-plugin` as the backend/runtime repo.
- Keep the WebUI in a separate forked repo.
- Prefer stable read-oriented HTTP APIs over direct frontend reads of SQLite or
  `.hermes-project/*` files.
- Reuse existing Kanban backend APIs where possible.

## Phase 1: Backend Read APIs

### Done

- [x] Added dedicated `lex` API surface under `/api/lex`.
- [x] Exposed read-only project list/detail endpoints.
- [x] Exposed evidence/source-digest endpoints.
- [x] Exposed research run listing/detail endpoints.
- [x] Exposed kanban summary data per project.
- [x] Added unified matter cockpit endpoint: `/api/lex/projects/{id}/cockpit`.

### Notes

- Existing reusable backend surface:
  - `plugins/kanban/dashboard/plugin_api.py`
- Existing data sources:
  - `hermes_cli/projects_db.py`
  - `.hermes-project/project-meta.json`
  - `.hermes-project/project-state.json`
  - `.hermes-project/source-digests/*.source-digest.md`
  - `.hermes-project/research.db`
  - `<project>/kanban/kanban.db`

## Phase 2: WebUI Fork Integration

- [ ] Fork `nesquena/hermes-webui`.
- [ ] Verify base connection/auth against `lex-hermes`.
- [ ] Add `Projects` navigation entry.
- [ ] Add `Project Detail` page.
- [ ] Add `Evidence` page/panel.
- [ ] Add `Research` page/panel.
- [ ] Add `Kanban` page or integrate with existing kanban flow surface.

## Phase 3: Lex Workflow Cockpit

- [ ] Merge project status, kanban status, evidence coverage, and research runs
      into a single matter cockpit.
- [ ] Add drill-down from task -> evidence -> source digest -> research finding.
- [ ] Add live refresh or websocket/event-based updates for workflow movement.

## Open Risks

- Confirmed registry split:
  - desktop/TUI project tree uses upstream `hermes_cli/projects_db.py`
  - `tools/project_management_tool.py` expects a second `SessionDB`-backed
    project registry API (`get_project`, `list_projects`, `bind_project_coordinator`,
    `set_session_project`, `upsert_project_sources`, etc.)
  - those methods are not implemented on the current `hermes_state.SessionDB`
    in this checkout
- Confirmed kanban split:
  - shared Hermes kanban dashboard API uses `hermes_cli.kanban_db`
  - legal matter folders also carry local `<project>/kanban/kanban.db`
- Immediate WebUI integration should target the stable read model:
  - `projects_db`
  - `.hermes-project/*`
  - local project `kanban.db`
  - local project `research.db`
- A later normalization pass is still likely needed so the UI does not have to
  reason about two registries forever.

## Immediate Execution Order

1. Fork and boot `hermes-webui` against `lex-hermes`.
2. Add a `Projects` navigation entry and matter list view.
3. Use `/api/lex/projects/{id}/cockpit` as the first-pass matter detail payload.
4. Add drill-down calls to `/evidence`, `/research/runs/{run_id}`, `/kanban`.
5. Decide whether to normalize the dual project registry before writing back from UI.
