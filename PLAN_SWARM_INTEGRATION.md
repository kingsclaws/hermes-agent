# PLAN: Swarm Integration Fix — Why Agents Don't Use Kanban Swarm

## Root Cause Analysis

The agent (coordinator) bypasses swarm entirely and uses `lex_edit`/`lex_read` directly.
Five concrete gaps cause this:

### Gap 1: Coordinator Soul Profile — Zero Kanban Awareness (CRITICAL)

**File:** `profiles/lex-coordinator.soul.md`

The soul profile is what the agent loads at startup. It defines the agent's identity, tools, and behavioral rules. The current soul says:

```
工具集: delegate_task, lex_read, execute_code
```

The profile's entire "派出规则" (dispatch rules) section references `delegate_task()` — the OLD synchronous delegation primitive. There is **zero mention** of:
- `swarm_task_create` — creating kanban tasks
- `swarm_task_poll` — non-blocking status check
- `swarm_task_collect` — collecting worker output
- `swarm_board_status` — viewing the board
- Any kanban concept whatsoever

The agent follows its soul profile faithfully. If the soul says "use delegate_task," the agent uses delegate_task. The kanban tools exist in the registry but the agent has no instruction to use them.

### Gap 2: Kanban Swarm Toolset Missing New Tools

**File:** `toolsets.py`

The `kanban_swarm` toolset definition lists 13 tools but **does not include** `swarm_task_poll` and `swarm_task_collect`. These were registered in `tools/kanban_toolset.py` (Track A) but never added to the toolset definition. The toolset is the gatekeeper — if a tool isn't in the toolset, the agent can't see it.

Current toolset tools:
```
swarm_board_create, swarm_board_info,
swarm_task_create, swarm_task_assign,
swarm_task_wait, swarm_workflow_compile,
swarm_board_status,
swarm_task_claim, swarm_task_read,
swarm_task_handoff, swarm_task_approve,
swarm_task_reject, swarm_task_revise
```

Missing: `swarm_task_poll`, `swarm_task_collect`

### Gap 3: Role File — Ambiguous "Recommended" Status

**File:** `.hermes-project/roles/hpswarm-coordinator.md`

The coordinator role file has extensive kanban documentation (lines 18-63) but presents it as:

```
### Kanban 协调模式（推荐 🆕）
```

Then separately keeps:

```
### 旧版委派（兼容保留）
```

This ambiguity is fatal. The agent sees TWO ways to dispatch work and defaults to the simpler `delegate_task` path since that's what the soul profile teaches. The role file's kanban section is detailed but reads as optional reference material rather than mandatory workflow.

### Gap 4: Gateway Dispatcher May Not Be Running

The kanban swarm architecture requires the gateway's `kanban_runner.py` dispatcher to:
1. Poll boards for pending tasks
2. Spawn worker agents via `hermes chat -q`
3. Monitor worker progress
4. Update task status on completion

If the dispatcher isn't running (or isn't configured for the coordinator's board), tasks sit in `todo` forever and the coordinator's `swarm_task_wait` blocks until timeout.

### Gap 5: No Delivery Spec Enforcement Awareness

The coordinator doesn't know about `DELIVERY_SPEC.yaml` gates. Even if it used kanban, it wouldn't know that `swarm_task_handoff` and `swarm_task_approve` now enforce hard gates (Track A). The error messages from failed gates are actionable, but the coordinator needs to know to EXPECT them and how to respond.

---

## Fix Plan

### Fix 1: Rewrite `lex-coordinator.soul.md` — Kanban-First Coordination

Replace the `delegate_task`-centric soul with a kanban-first approach:

1. **Toolset declaration** — Add `kanban_swarm` to the profile's working toolset
2. **Core workflow** — Replace "delegate_task → wait → collect" with "swarm_task_create → swarm_task_poll → swarm_task_collect"
3. **Dispatch rules** — Rewrite "派出规则" section to use kanban tools
4. **Gate awareness** — Add section about DELIVERY_SPEC gates and how to handle gate failures
5. **Remove delegate_task references** — Make kanban the ONLY documented path

### Fix 2: Add `swarm_task_poll` + `swarm_task_collect` to `toolsets.py`

Add the two new tools to the `kanban_swarm` toolset definition so agents can see them.

### Fix 3: Rewrite `hpswarm-coordinator.md` — Remove Ambiguity

1. **Delete** the "旧版委派（兼容保留）" section entirely
2. **Remove** all `delegate_task` workflow examples
3. **Replace** with kanban-only workflows
4. **Add** gate failure handling procedures
5. **Add** poll + collect patterns

### Fix 4: Verify Gateway Dispatcher Configuration

Check that the gateway has kanban dispatch enabled in its config and is actively monitoring boards.

### Fix 5: Update Worker Soul Profiles

The Drafter and Reviewer soul profiles (`lex-drafter.soul.md`, `lex-reviewer-content.soul.md`, etc.) also need kanban worker tool awareness (`swarm_task_claim`, `swarm_task_handoff`, `swarm_task_approve`, `swarm_task_reject`, `swarm_task_read`).

---

## Implementation Order

```
Fix 2 (toolsets.py — trivial, 2 lines)
  → Fix 1 (lex-coordinator.soul.md — core rewrite)
  → Fix 3 (hpswarm-coordinator.md — remove old path)
  → Fix 5 (worker souls — add kanban worker tools)
  → Fix 4 (verify dispatcher config)
```

Fixes 1-3 and 5 are documentation/profile changes that directly change agent behavior.
Fix 4 is operational verification.

---

## Success Criteria

After all fixes, when a user says "review this contract" the coordinator should:

1. Call `swarm_board_status()` to check the board
2. Call `swarm_task_create()` to create tasks for Drafter and Reviewer(s)
3. Call `swarm_task_poll()` to check progress (non-blocking)
4. Call `swarm_task_collect()` to retrieve results
5. Handle gate failures by reading the `failed_checks` and `fix_hint` fields
6. Present integrated results to the user

The coordinator should NEVER directly call `lex_edit` for document modifications — that's the worker's job.
