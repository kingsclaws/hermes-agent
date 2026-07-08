---
name: hermes-upstream
description: >
  Maintain the lex-hermes-plugin branch by merging upstream NousResearch/hermes-agent.
  Handles the 3-layer separation: pure additions (no conflict), minimal source patches
  (154 lines), and plugin layer. Use when user says "sync upstream", "merge upstream",
  "update from hermes-agent", or "hermesupstream".
---

# Hermes Upstream Sync Skill

## When to Use

- User says "sync upstream" / "merge upstream" / "update from hermes-agent"
- User says "hermesupstream"
- Periodic maintenance to stay current with official releases

## Prerequisites

```bash
cd /root/.hermes/hermes-agent
git checkout lex-hermes-plugin
git remote -v  # must have 'upstream' pointing to NousResearch/hermes-agent
```

## The 3-Layer Architecture

Our fork separates changes into 3 layers to minimize merge conflicts:

```
Layer 1: upstream/main (NousResearch/hermes-agent)
  └── Layer 2: Minimal source patches (3 files, ~154 lines)
       └── Layer 3: Pure additions (plugin, tools, skills, vendor)
```

| Layer | Files | Conflict Risk | Merge Strategy |
|-------|-------|---------------|----------------|
| Layer 3 | tools/lex_*.py, skills/, vendor/lexitool/ | **Zero** — upstream doesn't have these | `git checkout lex-hermes -- <file>` |
| Layer 2 | hermes_state.py, conversation_compression.py, kanban_watchers.py | **Low** — small diffs | Manual merge with conflict resolution |
| Layer 1 | Everything else | **None** — we track upstream | `git merge upstream/main` |

## Merge Procedure

### Step 1: Fetch upstream

```bash
cd /root/.hermes/hermes-agent
git checkout lex-hermes-plugin
git fetch upstream main
```

### Step 2: Check divergence

```bash
git rev-list --count HEAD..upstream/main  # commits behind
git rev-list --count upstream/main..HEAD  # commits ahead
```

### Step 3: Merge upstream

```bash
git merge upstream/main --no-edit
```

If there are conflicts, they will only be in Layer 2 files. Resolve them:

```bash
# Check what conflicted
git diff --name-only --diff-filter=U

# For each conflicted file, edit and resolve:
# - hermes_state.py: keep our coordinator_for column
# - agent/conversation_compression.py: keep our coordinator_for propagation
# - gateway/kanban_watchers.py: keep our session wake functions
```

### Step 4: Verify Layer 3 files are intact

```bash
# These should ALL be unchanged after merge
git status -- tools/lex_*.py tools/legal_*.py tools/research_tool.py \
  tools/project_management_tool.py tools/kanban_toolset.py \
  skills/ vendor/lexitool/ plugins/lex-legal-tools/ \
  plugins/backoffice-issue-relay/ plugins/legal-drafting-gate/
```

### Step 5: Verify plugin still works

```bash
# Build check
python3 -m py_compile plugins/lex-legal-tools/__init__.py
python3 -m py_compile tools/research_tool.py
python3 -m py_compile tools/lex_preview_tool.py

# Tool registration check
python3 -c "
from tools.registry import discover_builtin_tools, registry
discover_builtin_tools()
lex = [t for t in registry._tools if t.startswith('lex_')]
print(f'Lex tools: {len(lex)}')
"
```

### Step 6: Commit and push

```bash
git add -A
git commit -m "chore: merge upstream/main into lex-hermes-plugin"
git push fork lex-hermes-plugin
```

### Step 7: Sync to container and test

```bash
CONTAINER=$(docker ps --filter name=lex-hermes --format '{{.Names}}' | head -1)

# Sync key files
for f in tools/project_management_tool.py tools/kanban_toolset.py \
  tools/research_tool.py tools/lex_preview_tool.py \
  hermes_state.py agent/conversation_compression.py gateway/kanban_watchers.py \
  plugins/lex-legal-tools/__init__.py plugins/lex-legal-tools/plugin.yaml; do
  docker cp "$f" $CONTAINER:/opt/hermes/$f 2>/dev/null
  docker cp "$f" $CONTAINER:/opt/lex-hermes/$f 2>/dev/null
done

# Restart gateways
docker exec $CONTAINER sh -c '
S6_SVC=/package/admin/s6-2.15.0.0/command/s6-svc
for svc in /run/service/gateway-*; do $S6_SVC -k "$svc" 2>/dev/null; $S6_SVC -u "$svc" 2>/dev/null; done
'

# Verify
docker exec $CONTAINER python3 -c "
from tools.registry import discover_builtin_tools, registry
discover_builtin_tools()
lex = [t for t in registry._tools if t.startswith('lex_')]
print(f'Lex tools: {len(lex)}')
"
```

## Conflict Resolution Guide

### hermes_state.py

Keep our `coordinator_for` column. If upstream adds new columns, keep both:

```python
# Our addition
    coordinator_for TEXT,
# Upstream addition (if any)
    new_upstream_column TEXT,
```

### agent/conversation_compression.py

Keep our `coordinator_for` propagation block. If upstream changes the compression flow, insert our block after the `set_session_title` call.

### gateway/kanban_watchers.py

Keep our `_resolve_coordinator_wake_source` and `_inject_kanban_session_wake` methods. They go at the end of the class, before the final closing brace.

## Upstream Release Checklist

When upstream releases a new version:

1. `git fetch upstream main`
2. `git merge upstream/main --no-edit`
3. Resolve conflicts (only Layer 2)
4. Verify Layer 3 files intact
5. `python3 -m py_compile` all modified files
6. `npm run build` WebUI (if web/ changed)
7. Sync to container, restart gateways
8. Verify `lex_preview`, `research_*`, `project_bind_session` tools work
9. Commit, push, tag: `git tag v0.18.x-lex-N && git push fork lex-hermes-plugin --tags`

## Current Layer 2 Files (Modified Upstream)

| File | Change | Lines |
|------|--------|-------|
| `hermes_state.py` | +`coordinator_for TEXT` column | +1 |
| `agent/conversation_compression.py` | +propagate coordinator_for on compression | +16 |
| `gateway/kanban_watchers.py` | +session wake functions | +137 |

## Current Layer 3 Files (Pure Additions)

| Category | Count | Examples |
|----------|-------|---------|
| Tools | 17 | lex_*.py, legal_*.py, research_tool.py, project_management_tool.py |
| Skills | 2 | deep-research, lex-master-handoff |
| Plugins | 3 | lex-legal-tools, backoffice-issue-relay, legal-drafting-gate |
| Vendor | 1 | vendor/lexitool/ (pip package) |
| Config | 2 | profiles/lex-*.soul.md (7 files) |
