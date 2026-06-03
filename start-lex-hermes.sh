#!/usr/bin/env bash
set -euo pipefail

# ── lex-hermes entrypoint ──
# Starts hermes gateway (background) then dashboard (foreground).
# Both must run for the Chat tab and sessions to work.

# Ensure runtime directories
mkdir -p /root/.hermes /workspace /project

# ── Harness bootstrap: ensure coordinator toolset ───────────────────────
# The coordinator MUST use lex-docx-coordinator (no lex_edit/lex_format).
# This is enforced at the config level — the tools simply don't exist for
# the parent agent, physically preventing direct document editing.
HARNESS_CONFIG="/root/.hermes/.harness_config.yaml"
if [ ! -f "$HARNESS_CONFIG" ]; then
  echo "[lex-hermes] Bootstrapping coordinator harness config..."
  cat > "$HARNESS_CONFIG" <<'HARNESS_EOF'
# lex-hermes coordinator harness — AUTO-GENERATED
# The coordinator (parent agent) uses lex-docx-coordinator: read-only tools.
# Document editing requires delegate_task → Drafter sub-agent.
platform_toolsets:
  cli:
    - browser
    - clarify
    - computer_use
    - cronjob
    - delegation
    - file
    - image_gen
    - kanban
    - lex-docx-coordinator
    - memory
    - messaging
    - project_management
    - session_search
    - skills
    - terminal
    - todo
    - tts
    - vision
    - web
delegation:
  max_spawn_depth: 1
  orchestrator_enabled: true
  max_concurrent_children: 3
  max_iterations: 50
  child_timeout_seconds: 600
HARNESS_EOF
fi

# Merge harness config into main config if config.yaml exists and is not a dir
if [ -f /root/.hermes/config.yaml ] && [ ! -d /root/.hermes/config.yaml ]; then
  # Only apply harness if config doesn't already have lex-docx-coordinator
  if ! grep -q "lex-docx-coordinator" /root/.hermes/config.yaml 2>/dev/null; then
    echo "[lex-hermes] Applying coordinator harness to config..."
    # Use Python to merge YAML safely
    python3 -c "
import yaml, sys
try:
    with open('/root/.hermes/config.yaml') as f:
        config = yaml.safe_load(f) or {}
    with open('$HARNESS_CONFIG') as f:
        harness = yaml.safe_load(f) or {}
    # Deep merge harness into config (harness wins for conflicts)
    for key, value in harness.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    with open('/root/.hermes/config.yaml', 'w') as f:
        yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False)
    print('[lex-hermes] Harness config merged successfully')
except Exception as e:
    print(f'[lex-hermes] WARNING: Failed to merge harness config: {e}', file=sys.stderr)
" 2>/dev/null || echo "[lex-hermes] WARNING: Could not merge harness config (python3/yaml unavailable?)"
  fi
fi

# Self-heal: if config.yaml is a directory (bind mount race), replace it
if [ -d /root/.hermes/config.yaml ]; then
  echo "[lex-hermes] Fixing config.yaml (was a directory, creating file)"
  rmdir /root/.hermes/config.yaml 2>/dev/null || rm -rf /root/.hermes/config.yaml
  cat > /root/.hermes/config.yaml <<'CEOF'
# lex-hermes auto-generated config
model:
  provider: auto
  default: anthropic/claude-opus-4.6
CEOF
fi

# ── Profile bootstrap: create legal worker profiles ─────────────────────
# Hermes Profiles are fully isolated HERMES_HOME directories. Each worker
# role gets its own profile with the correct toolset, SOUL.md, and description
# so the Kanban auto-decomposer can route tasks to the right specialist.
bootstrap_profiles() {
  echo "[lex-hermes] Bootstrapping worker profiles..."

  # Idempotent guard — skip if profiles already exist
  if [ -d "/root/.hermes/profiles/lex-drafter" ]; then
    echo "[lex-hermes] Profiles already exist, skipping bootstrap"
    return 0
  fi

  python3 -c "
import os, sys, shutil
sys.path.insert(0, '/opt/lex-hermes')

from hermes_cli import profiles as prof_mod

specs = [
    ('lex-coordinator',
     'Legal document orchestration: analyze requirements, decompose tasks, '
     'route to specialists, aggregate results. Does NOT edit documents directly.', True),
    ('lex-drafter',
     'Document drafting and editing: create new .docx contracts, modify existing '
     'documents, apply formatting revisions with Track Changes. Handles all content '
     'and structural modifications.', False),
    ('lex-reviewer-content',
     'Legal content review: verify legal substance, clause completeness, internal '
     'consistency, obligations accuracy, risk identification. Reads and reports.', False),
    ('lex-reviewer-format',
     'Document format review: font consistency, paragraph numbering, table formatting, '
     'header/footer, page layout, list indentation. Reads and reports.', False),
    ('lex-reviewer-ts',
     'Term sheet consistency review: cross-check contract terms against the governing '
     'Term Sheet (TS). Verify amounts, dates, party names, commercial terms match TS.', False),
    ('lex-reviewer-xref',
     'Cross-reference audit: verify all internal references (clause numbers, bookmark '
     'targets, section links), inter-document references, defined terms usage.', False),
    ('lex-reviewer-translation',
     'Bilingual translation quality review (CN↔EN): verify translation accuracy, '
     'legal terminology consistency, no omissions or mistranslations.', False),
]

import yaml

for name, desc, is_coord in specs:
    try:
        profile_dir = prof_mod.create_profile(name, description=desc)
    except FileExistsError:
        profile_dir = prof_mod.get_profile_dir(name)
        print(f'[lex-hermes] Profile already exists: {name}')
    except Exception as e:
        print(f'[lex-hermes] WARNING: Failed to create profile {name}: {e}', file=sys.stderr)
        continue

    # Copy role-specific SOUL.md
    soul_src = f'/opt/lex-hermes/profiles/{name}.soul.md'
    if os.path.isfile(soul_src):
        shutil.copy2(soul_src, profile_dir / 'SOUL.md')

    # Write config.yaml with correct toolset
    toolset = 'lex-docx-coordinator' if is_coord else 'lex-docx-worker'
    config = {
        'platform_toolsets': {
            'cli': [toolset, 'file', 'browser', 'delegation', 'skills', 'terminal', 'todo', 'web']
        },
        'delegation': {
            'max_spawn_depth': 1,
            'orchestrator_enabled': True,
            'max_concurrent_children': 3,
            'max_iterations': 50,
            'child_timeout_seconds': 600,
        },
    }
    # Coordinator also gets kanban orchestrator settings
    if is_coord:
        config['kanban'] = {
            'orchestrator_profile': 'lex-coordinator',
            'default_assignee': 'lex-drafter',
            'auto_promote_children': True,
        }

    with open(profile_dir / 'config.yaml', 'w', encoding='utf-8') as f:
        yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False)
    print(f'[lex-hermes] Configured profile: {name} ({toolset})')

print('[lex-hermes] Profile bootstrap complete')
" 2>/dev/null || echo "[lex-hermes] WARNING: Profile bootstrap failed (python3 modules unavailable?)"
}

bootstrap_profiles

echo "[lex-hermes] Starting gateway..."
hermes gateway run &
GW_PID=$!

# Wait for gateway to be ready (gateway uses Unix sockets, not HTTP health)
sleep 2
if ! kill -0 "$GW_PID" 2>/dev/null; then
  echo "[lex-hermes] Gateway failed to start"
  exit 1
fi
echo "[lex-hermes] Gateway ready (PID $GW_PID)"

# Forward signals to child processes
cleanup() {
  echo "[lex-hermes] Shutting down..."
  kill "$DASH_PID" 2>/dev/null || true
  kill "$GW_PID" 2>/dev/null || true
  wait "$DASH_PID" 2>/dev/null || true
  wait "$GW_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[lex-hermes] Starting dashboard on port 9119..."
hermes dashboard --host 0.0.0.0 --insecure --tui &
DASH_PID=$!

wait "$DASH_PID"
