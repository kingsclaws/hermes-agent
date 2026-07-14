# Hermes WebUI Integration Map

Date: 2026-07-10

## Conclusion

`nesquena/hermes-webui` is not just a frontend shell. It is a full Python +
vanilla-JS app with its own backend (`server.py`, `api/*`) and static UI
(`static/*`).

For Lex integration, the lowest-risk path is:

1. keep `lex-hermes-plugin` as the authoritative backend/runtime
2. keep the WebUI fork as a separate repo
3. add a small proxy layer in the WebUI backend for `/api/lex/*`
4. add a new `Projects` / matter cockpit panel in the WebUI frontend

This avoids direct browser cross-origin calls to `hermes-agent`, reuses the
WebUI's existing auth/session model, and keeps Lex-specific UI isolated from
Hermes core chat UX.

## Why this path

- `hermes-webui` already supports a gateway-backed chat bridge:
  - `HERMES_WEBUI_CHAT_BACKEND=gateway`
  - `HERMES_WEBUI_GATEWAY_BASE_URL=...`
  - documented in `docs/advanced-chat-setup.md`
- The app already has a panel-driven UI architecture:
  - navigation buttons in `static/index.html`
  - panel switching and panel state in `static/panels.js`
  - API calls and cold-load/live UI logic in `static/ui.js`
- Lex APIs are now available in `lex-hermes-plugin` under `/api/lex/*`

## Recommended backend work in the WebUI fork

Add a thin proxy namespace in the WebUI fork backend, for example:

- `api/lex.py`
- mount routes like:
  - `/api/lex/projects`
  - `/api/lex/projects/{id}`
  - `/api/lex/projects/{id}/cockpit`
  - `/api/lex/projects/{id}/evidence`
  - `/api/lex/projects/{id}/research/runs`
  - `/api/lex/projects/{id}/research/runs/{run_id}`
  - `/api/lex/projects/{id}/kanban`

Suggested implementation:

- use an env-configured upstream:
  - `HERMES_WEBUI_LEX_BASE_URL=http://127.0.0.1:PORT`
  - optional `HERMES_WEBUI_LEX_API_KEY=...`
- server-side HTTP proxy from WebUI backend to `lex-hermes-plugin`
- keep the browser on same-origin calls to the WebUI server only

This is preferable to direct browser fetches because it:

- reuses existing auth/session behavior
- avoids CORS work
- allows future response shaping/caching in the fork backend

## Recommended frontend work in the WebUI fork

Primary touchpoints:

- `static/index.html`
  - add a new nav tab, likely `projects`
  - add a new panel container, e.g. `#panelProjects`
- `static/panels.js`
  - register the new panel in panel switching
  - load list/detail state on panel entry
- `static/ui.js`
  - add Lex fetch helpers
  - add project list rendering
  - add cockpit detail rendering
- `static/style.css`
  - add matter cockpit styles

## UI shape for first pass

Use `/api/lex/projects/{id}/cockpit` as the main detail payload.

Panel structure:

- left list: projects / matters
- detail header: matter name, phase, status, primary path
- workflow strip:
  - current lane
  - active tasks
  - blocked tasks
  - review tasks
- evidence section:
  - digest count
  - facts excerpt
  - source digest list
- research section:
  - run count
  - latest run
  - status breakdown
- kanban section:
  - lane counts
  - latest tasks

## Concrete frontend anchors in `hermes-webui`

- navigation is defined inline in `static/index.html`
- active panel state is tracked by `_currentPanel` in `static/panels.js`
- major non-chat panels already exist:
  - `tasks`
  - `kanban`
  - `skills`
  - `memory`
  - `workspaces`
  - `profiles`
  - `todos`
  - `insights`
  - `logs`
  - `settings`

This means `projects` fits the existing architecture cleanly.

## Constraints to remember

- `tools/project_management_tool.py` in `lex-hermes-plugin` expects a second
  `SessionDB`-backed project registry API that is not implemented in the
  current checkout.
- Desktop/TUI project views already rely on `hermes_cli/projects_db.py`.
- Therefore the WebUI fork should initially treat `/api/lex/*` as the
  authoritative read model and avoid write features that assume the old Lex
  project registry works.

## Immediate next implementation in the fork

1. add backend proxy routes in the WebUI fork
2. add `Projects` nav tab and empty-state panel
3. render `/api/lex/projects`
4. render `/api/lex/projects/{id}/cockpit`
5. add drill-down to evidence/research/kanban detail routes
