#!/usr/bin/env bash
set -euo pipefail

# ── lex-hermes entrypoint ──
# Starts hermes gateway (background) then dashboard (foreground).
# Both must run for the Chat tab and sessions to work.

# Ensure runtime directories
mkdir -p /root/.hermes /workspace /project

# ── Harness bootstrap: ensure native Lex tools are visible ──────────────
# The coordinator must see native lex_* tools directly. Multi-agent
# orchestration is for complex workflows, not for hiding basic read/OCR/edit
# operations behind shell commands or profile-only workers.
HARNESS_CONFIG="/root/.hermes/.harness_config.yaml"
echo "[lex-hermes] Bootstrapping Lex harness config..."
cat > "$HARNESS_CONFIG" <<'HARNESS_EOF'
# lex-hermes harness - AUTO-GENERATED
# Native Lex tools are available to the coordinator. Use legal_orchestrate
# or delegate_task for complex multi-agent legal workflows.
platform_toolsets:
  cli:
    - hermes-cli
delegation:
  default_background: true
  max_spawn_depth: 1
  orchestrator_enabled: true
  max_concurrent_children: 5
  max_iterations: 50
  child_timeout_seconds: 600
HARNESS_EOF

# Merge harness config into main config if config.yaml exists and is not a dir
if [ -f /root/.hermes/config.yaml ] && [ ! -d /root/.hermes/config.yaml ]; then
  # Apply when native Lex tools are missing or stale coordinator-only toolsets remain.
  if ! grep -q "hermes-cli" /root/.hermes/config.yaml 2>/dev/null || grep -Eq "lex-docx-(coordinator|worker)" /root/.hermes/config.yaml 2>/dev/null; then
    echo "[lex-hermes] Applying Lex harness to config..."
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
    print('[lex-hermes] Lex harness config merged successfully')
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

  python3 -c "
import os, sys, shutil
sys.path.insert(0, '/opt/lex-hermes')

from hermes_cli import profiles as prof_mod

specs = [
    ('lex-coordinator',
     'Legal document orchestration: analyze requirements, decompose tasks, '
     'route to specialists, aggregate results. Can use native Lex tools directly.', True),
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

# Profile (identity soul) → role SOP (full operating procedure) map.
# Bootstrap appends the role SOP onto the identity soul so workers boot with
# their complete procedure baked into SOUL.md. Soul = identity; role = procedure;
# on conflict the role wins.
NAME_TO_ROLE = {
    'lex-coordinator': 'hpswarm-coordinator',
    'lex-drafter': 'hpswarm-drafter',
    'lex-reviewer-content': 'hpswarm-reviewer-content',
    'lex-reviewer-format': 'hpswarm-reviewer-format',
    'lex-reviewer-ts': 'hpswarm-reviewer-ts-consistency',
    'lex-reviewer-xref': 'hpswarm-reviewer-cross-ref',
    'lex-reviewer-translation': 'hpswarm-reviewer-translation',
}
ROLE_SEPARATOR = '\n\n---\n# Operating Procedure (authoritative — overrides identity on conflict)\n\n'

for name, desc, is_coord in specs:
    try:
        profile_dir = prof_mod.create_profile(name, description=desc)
    except FileExistsError:
        profile_dir = prof_mod.get_profile_dir(name)
        print(f'[lex-hermes] Profile already exists: {name}')
    except Exception as e:
        print(f'[lex-hermes] WARNING: Failed to create profile {name}: {e}', file=sys.stderr)
        continue

    # Copy role-specific SOUL.md (identity layer)
    soul_src = f'/opt/lex-hermes/profiles/{name}.soul.md'
    soul_dst = profile_dir / 'SOUL.md'
    if os.path.isfile(soul_src):
        shutil.copy2(soul_src, soul_dst)

    # Bake the role SOP (full procedure) onto the identity soul.
    # copy2 above resets SOUL.md to identity-only first, so this append is idempotent
    # across re-runs (no accumulation). On conflict, the appended procedure wins.
    role_slug = NAME_TO_ROLE.get(name)
    if role_slug:
        role_src = f'/opt/lex-hermes/.hermes-project/roles/{role_slug}.md'
        if os.path.isfile(role_src) and os.path.isfile(soul_dst):
            with open(role_src, encoding='utf-8') as rf:
                role_body = rf.read()
            with open(soul_dst, 'a', encoding='utf-8') as sf:
                sf.write(ROLE_SEPARATOR + role_body)
            print(f'[lex-hermes] Baked role SOP into {name} SOUL.md ({role_slug}, {len(role_body)} chars)')
        else:
            print(f'[lex-hermes] WARNING: role SOP missing for {name} ({role_src}) — identity-only soul', file=sys.stderr)

    # Write config.yaml with native Lex-capable toolsets.
    toolsets = ['hermes-cli'] if is_coord else ['lexitool', 'file', 'browser', 'delegation', 'skills', 'terminal', 'todo', 'web']
    config = {
        'platform_toolsets': {
            'cli': toolsets
        },
        'plugins': {
            'enabled': ['backoffice-issue-relay', 'legal-drafting-gate'],
        },
        'delegation': {
            'default_background': True,
            'max_spawn_depth': 1,
            'orchestrator_enabled': True,
            'max_concurrent_children': 5,
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
            'dispatch_in_gateway': True,
            'dispatch_interval_seconds': 5,
        }

    with open(profile_dir / 'config.yaml', 'w', encoding='utf-8') as f:
        yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False)
    toolsets_label = ', '.join(toolsets)
    print(f'[lex-hermes] Configured profile: {name} ({toolsets_label})')

print('[lex-hermes] Profile bootstrap complete')
" 2>/dev/null || echo "[lex-hermes] WARNING: Profile bootstrap failed (python3 modules unavailable?)"
}

bootstrap_profiles

# ── Profile symlinks: SOPs reference hpswarm-* while profiles are lex-* ──
# The kanban dispatcher spawns workers by profile name from the task assignee.
# Legal swarm SOPs use hpswarm-* names; these symlinks make both resolve.
create_profile_symlinks() {
  echo "[lex-hermes] Creating profile symlinks (hpswarm-* → lex-*)..."
  local prof_root="/root/.hermes/profiles"
  local pairs=(
    "hpswarm-coordinator:lex-coordinator"
    "hpswarm-drafter:lex-drafter"
    "hpswarm-reviewer-content:lex-reviewer-content"
    "hpswarm-reviewer-format:lex-reviewer-format"
    "hpswarm-reviewer-translation:lex-reviewer-translation"
    "hpswarm-reviewer-ts:lex-reviewer-ts"
    "hpswarm-reviewer-xref:lex-reviewer-xref"
  )
  for pair in "${pairs[@]}"; do
    local link="${prof_root}/${pair%%:*}"
    local target="${pair##*:}"
    if [ ! -e "$link" ] && [ -d "${prof_root}/${target}" ]; then
      ln -s "$target" "$link"
      echo "[lex-hermes]   ${pair%%:*} → $target"
    fi
  done
  echo "[lex-hermes] Profile symlinks ready"
}
create_profile_symlinks

# ── Lex Master bootstrap: API entrypoint + Weixin notification tools ─────
# The Outlook/API workflow enters through the lex-master api_server platform.
# Hermes deliberately keeps api_server's default toolset narrow, so make the
# profile-specific opt-in explicit here instead of changing core defaults.
ensure_lex_master_profile() {
  echo "[lex-hermes] Ensuring lex-master API + notification toolsets..."

  /opt/hermes/.venv/bin/python - <<'PY' 2>/dev/null || echo "[lex-hermes] WARNING: lex-master API bootstrap failed"
from pathlib import Path
import sys

try:
    import yaml
except Exception as exc:  # pragma: no cover - boot diagnostic
    print(f"[lex-hermes] WARNING: PyYAML unavailable: {exc}", file=sys.stderr)
    raise

profile_dir = Path("/root/.hermes/profiles/lex-master")
profile_dir.mkdir(parents=True, exist_ok=True)
config_path = profile_dir / "config.yaml"

try:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
except Exception:
    config = {}
if not isinstance(config, dict):
    config = {}

platform_toolsets = config.setdefault("platform_toolsets", {})
if not isinstance(platform_toolsets, dict):
    platform_toolsets = {}
    config["platform_toolsets"] = platform_toolsets

api_toolsets = platform_toolsets.setdefault("api_server", [])
if not isinstance(api_toolsets, list):
    api_toolsets = []
    platform_toolsets["api_server"] = api_toolsets

required_api_toolsets = [
    "messaging",
    "cronjob",
    "project_management",
    "kanban",
    "kanban_swarm",
    "lexitool",
    "legal_orchestration",
    "session_search",
    "memory",
    "skills",
    "todo",
    "terminal",
    "file",
    "web",
]
for toolset in required_api_toolsets:
    if toolset not in api_toolsets:
        api_toolsets.append(toolset)

cli_toolsets = platform_toolsets.setdefault("cli", [])
if isinstance(cli_toolsets, list) and "messaging" not in cli_toolsets:
    cli_toolsets.append("messaging")

platforms = config.setdefault("platforms", {})
if not isinstance(platforms, dict):
    platforms = {}
    config["platforms"] = platforms

api_server = platforms.setdefault("api_server", {})
if not isinstance(api_server, dict):
    api_server = {}
    platforms["api_server"] = api_server
api_server["enabled"] = True
extra = api_server.setdefault("extra", {})
if not isinstance(extra, dict):
    extra = {}
    api_server["extra"] = extra
extra.setdefault("host", "0.0.0.0")
extra.setdefault("port", 8642)
extra.setdefault("key", "123456")
extra.setdefault("allow_weak_key", True)
extra.setdefault("model_name", "lex-master")

config_path.write_text(
    yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
print("[lex-hermes] lex-master api_server toolsets include messaging/send_message")
PY
}
ensure_lex_master_profile

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

SERVE_HOST="${HERMES_SERVE_HOST:-0.0.0.0}"
SERVE_PORT="${HERMES_SERVE_PORT:-9119}"
SERVE_USERNAME="${HERMES_SERVE_USERNAME:-sebastian}"
SERVE_PASSWORD="${HERMES_SERVE_PASSWORD:-661225}"

echo "[lex-hermes] Starting official serve backend on ${SERVE_HOST}:${SERVE_PORT}..."
hermes serve --host "$SERVE_HOST" --port "$SERVE_PORT" --username "$SERVE_USERNAME" --password "$SERVE_PASSWORD" --tui &
DASH_PID=$!

wait "$DASH_PID"
