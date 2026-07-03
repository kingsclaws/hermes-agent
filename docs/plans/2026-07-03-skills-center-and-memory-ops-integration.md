# Lex-Hermes Skills Center and Memory Operations Integration Plan

Date: 2026-07-03

## Goal

Integrate the useful official Hermes v0.18 improvements for:

1. Dashboard Skills Center: searchable marketplace, vendor/provider browsing, install preview, and pre-install security scan.
2. Memory tool batch operations: true atomic multi-operation memory updates in one tool call.

The integration must preserve Lex-Hermes-specific legal harness behavior, especially project-scoped memory and profile-aware runtime behavior.

## Current Findings

### Skills Center

Official Hermes v0.18 includes a substantially newer Dashboard skills hub:

- Backend endpoints in `hermes_cli/web_server.py`:
  - `POST /api/skills/hub/install`
  - `POST /api/skills/hub/uninstall`
  - `POST /api/skills/hub/update`
  - `GET /api/skills/hub/sources`
  - `GET /api/skills/hub/search`
  - `GET /api/skills/hub/preview`
  - `GET /api/skills/hub/scan`
- CLI/runtime support in `hermes_cli/skills_hub.py`.
- Hub source implementation in `tools/skills_hub.py`.
- Frontend API client in `web/src/lib/api.ts`.
- New Dashboard page in `web/src/pages/SkillsPage.tsx`.

Lex-Hermes currently has:

- `tools/skills_hub.py`, but older than official v0.18.
- `hermes_cli/skills_hub.py`, but older than official v0.18.
- Old `web/src/pages/SkillsPage.tsx`, focused on local skills/toolsets only.
- No source-code-level `/api/skills/hub/*` endpoints in `hermes_cli/web_server.py`.
- Some built bundle traces in `hermes_cli/web_dist`, but those are generated artifacts and are not a maintainable source of truth.

Conclusion: Skills Center should be ported as source code, not treated as already integrated.

### Memory Operations

Official Hermes v0.18 adds true atomic memory batch operations:

- The model can send an `operations` array.
- All operations are validated first.
- The final memory char budget is checked after applying the full batch in memory.
- If any operation fails, no file is written.
- This solves the old problem where the agent needed one call to delete stale memory and another call to add new memory, often failing due to quota.

Lex-Hermes currently has a custom `action="batch"` path:

- It supports `operations`.
- It supports `scope="global"` and `scope="project"`.
- It does not appear to be truly all-or-nothing, because it calls `store.add/replace/remove` per operation and can persist earlier operations before a later failure.

Conclusion: Memory should not be overwritten with official code directly. We should merge official atomic semantics into Lex's project-scoped memory implementation.

## Design Principles

- Preserve Lex project-scoped memory:
  - `scope="global"` for user preferences, cross-project tool facts, stable environment conventions.
  - `scope="project"` for client/project-specific document conventions and legal work context.
- Preserve prompt-cache safety:
  - Memory writes do not mutate the current system prompt.
  - New memory affects future sessions/turn initialization as currently designed.
- Prefer source-level integration:
  - Do not rely on copied `web_dist` bundles.
  - Rebuild Dashboard assets from `web/src`.
- Preserve profile isolation:
  - Skills hub paths must resolve under the active profile's `HERMES_HOME`.
  - Avoid import-time frozen path constants where official v0.18 already fixed them.
- Keep model-tool footprint stable:
  - `memory` remains the existing tool.
  - No new core tool for skills hub; skills hub is Dashboard/CLI surface.

## Implementation Plan

### Phase 1: Memory Tool Atomic Batch

Files:

- `tools/memory_tool.py`
- Relevant tests under `tests/` or `tests/tools/`

Tasks:

1. Add an internal atomic batch method that supports both target and scope.
   - Candidate shape: `MemoryStore.apply_batch(target, operations, scope="global")`.
   - Also support per-operation `target` and `scope` for Lex compatibility.
2. Validate every operation before writing:
   - Operation must be an object.
   - `action` must be `add`, `replace`, or `remove`.
   - `target` must be `memory` or `user`.
   - `scope` must be `global` or `project`.
   - `project` scope requires active project memory directory.
   - `add` and `replace` content must pass memory injection/security scan.
3. Group operations by `(target, scope)` and lock the relevant memory file(s).
   - Safe minimal implementation: acquire file locks in deterministic path order to avoid deadlocks.
   - Apply all operations to in-memory copies.
   - Check final budget per `(target, scope)`.
   - Commit all changed files only after all groups validate.
4. Change `_handle_batch` to call the atomic implementation instead of per-operation write methods.
5. Update schema guidance:
   - Prefer `operations` for multi-change or quota-consolidation cases.
   - Clarify all-or-nothing semantics.
   - Keep Lex scope guidance.
6. Add regression tests:
   - Batch remove stale + add new succeeds when add alone would exceed budget.
   - Later failing operation leaves earlier operations unwritten.
   - Project-scoped batch writes `.hermes-project/memories`.
   - Mixed global/project batch is all-or-nothing.
   - Duplicate add remains idempotent.
   - Ambiguous replace/remove fails without writing.

Validation:

```bash
python -m pytest tests/tools/test_memory_tool.py tests/agent/test_memory_write_bridge.py -q
python -m pytest tests/agent/test_prompt_builder.py -q
```

If exact test files differ, use `rg "memory_tool|memory\\(" tests` to locate the active memory tests.

### Phase 2: Skills Hub Runtime and Backend

Files:

- `tools/skills_hub.py`
- `hermes_cli/skills_hub.py`
- `hermes_cli/web_server.py`
- `hermes_cli/config.py`
- Tests under `tests/hermes_cli/`, `tests/tools/`, or existing skills hub tests

Tasks:

1. Port official v0.18 dynamic path resolution in `tools/skills_hub.py`.
   - Avoid import-time frozen `SKILLS_DIR`, `HUB_DIR`, `LOCK_FILE`, etc.
   - Keep legacy exported names via `__getattr__` where needed.
   - This matters for multi-profile and Lex Master/profile usage.
2. Port provider metadata:
   - OpenAI
   - Anthropic
   - HuggingFace
   - NVIDIA
   - Other official taps where low risk.
3. Port provider filtering helpers:
   - `github_provider_for`
   - `_PROVIDER_FILTER_VALUES`
   - `_filter_results_by_provider`
4. Port official source router/search improvements:
   - `create_source_router`
   - `parallel_search_sources`
   - featured/source metadata expected by Dashboard.
5. Port security scan/preview backend endpoints to `hermes_cli/web_server.py`.
6. Ensure profile parameter support:
   - Dashboard install/search/source endpoints should be able to resolve the target profile's skill state.
   - Do not leak skills across profiles.
7. Add or update tests for:
   - `/api/skills/hub/sources`
   - `/api/skills/hub/search`
   - `/api/skills/hub/preview`
   - `/api/skills/hub/scan`
   - profile-scoped installed state.

Validation:

```bash
python -m pytest tests/hermes_cli/test_web_server.py tests/tools/test_skills_hub.py -q
```

Adjust exact paths after locating current test names.

### Phase 3: Dashboard Skills Center Frontend

Files:

- `web/src/pages/SkillsPage.tsx`
- `web/src/lib/api.ts`
- Possibly `web/src/i18n` files if official page depends on new labels.
- Possibly shared UI components if official page references components not present in Lex.

Tasks:

1. Port official v0.18 `SkillHub*` TypeScript interfaces into `web/src/lib/api.ts`.
2. Add API methods:
   - `installSkillFromHub`
   - `uninstallSkillFromHub`
   - `updateSkillFromHub`
   - `searchSkillsHub`
   - `getSkillHubSources`
   - `previewSkillFromHub`
   - `scanSkillFromHub`
3. Replace or merge `SkillsPage.tsx`:
   - Keep Lex local skills/toolsets management.
   - Add official hub browser as a first-class tab/section.
   - Preserve existing Dashboard styling where Lex has customized layout.
4. Add install detail dialog:
   - SKILL.md preview.
   - File list.
   - Tags/provider/trust badges.
   - On-demand security scan.
5. Add source/vendor landing:
   - Featured skills.
   - Provider/source cards.
   - Counts and timeout display.
6. Ensure installed-state badges reflect the selected profile.

Validation:

```bash
cd web
npm run typecheck
npm run build
```

If the repo uses root-level frontend commands instead, use the existing package scripts discovered from `package.json`.

### Phase 4: Container and Existing Sessions

Tasks:

1. Rebuild the Dashboard frontend assets if Lex image uses checked-in `web_dist`.
2. Ensure Docker image includes any Python dependencies already expected by official skills hub/security scanner.
3. Rebuild Lex image.
4. Restart Lex container.
5. Verify active profiles:
   - `default`
   - `lex-master`
   - swarm reviewer/drafter profiles, if relevant.
6. Confirm existing sessions:
   - Existing persisted session data does not need DB migration.
   - Running in-memory agent processes need restart to see updated tool schema.
   - New turns in newly constructed agents should receive updated `memory` schema and skills hub backend.

Smoke tests inside container:

```bash
hermes tools | grep -i memory
hermes profile list
curl -s http://127.0.0.1:<dashboard-port>/api/skills/hub/sources
```

For memory:

```bash
python3 - <<'PY'
from tools.memory_tool import memory_tool
print(memory_tool(action="batch", target="memory", operations=[
    {"action": "add", "content": "Lex memory batch smoke test."},
    {"action": "remove", "old_text": "Lex memory batch smoke test."},
]))
PY
```

## Risks

- Directly replacing `tools/memory_tool.py` with official code would lose Lex project memory. Avoid full overwrite.
- Directly replacing `web/src/pages/SkillsPage.tsx` may drop Lex-specific local toolset UI. Prefer a merged page or verify official still includes equivalent local controls.
- `tools/skills_hub.py` has path-resolution changes that can break tests that monkeypatch constants. Preserve official `__getattr__` compatibility.
- Dashboard API endpoints may need auth/session token compatibility with Lex's current web server modifications.
- Skills hub calls external GitHub APIs; tests should mock network where possible.

## Recommended Order

1. Implement Phase 1 first because it is lower risk and immediately improves agent self-improvement.
2. Implement Phase 2 backend next.
3. Implement Phase 3 frontend after backend API passes smoke tests.
4. Rebuild and restart container only after tests pass.

## Acceptance Criteria

- `memory` tool supports one-call atomic updates with `operations`.
- A failed batch leaves no partial writes.
- Lex project-scoped memory still works.
- Dashboard Skills page can browse/search official skill providers.
- Dashboard can preview `SKILL.md` before install.
- Dashboard can run pre-install security scan.
- Dashboard install/update/uninstall operates against the intended profile.
- Existing sessions continue to load, and newly constructed agents see the upgraded memory schema.
