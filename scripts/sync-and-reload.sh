#!/bin/bash
# sync-and-reload.sh — Sync host changes to container + force live sessions to pick up new souls/tools
#
# Usage: ./scripts/sync-and-reload.sh [container-name]
#   Default container: lex-hermes
#
# This script:
#   1. Copies changed source files to the container
#   2. Updates SOUL.md at main home + all profile homes
#   3. Updates config.yaml platform_toolsets (adds kanban_swarm if missing)
#   4. Restarts gateway services so they pick up new souls
#   5. Verifies all gateways come back up
#
# After this script runs:
#   - NEW sessions get the updated soul + tools immediately
#   - EXISTING sessions: gateways were restarted, WebUI tabs reconnect
#     with new sessions that use the updated soul

set -euo pipefail
CONTAINER="${1:-lex-hermes}"
HOST_REPO="/opt/hermes-agent"
CONTAINER_REPO="/opt/lex-hermes"
PROFILES_DIR="$HOST_REPO/profiles"
GATEWAY_SERVICES=(
    gateway-lex-coordinator
    gateway-lex-drafter
    gateway-lex-reviewer-content
    gateway-lex-reviewer-format
    gateway-lex-reviewer-translation
    gateway-lex-reviewer-ts
    gateway-lex-reviewer-xref
)
S6_SVC="/package/admin/s6-2.15.0.0/command/s6-svc"
S6_SVSTAT="/package/admin/s6-2.15.0.0/command/s6-svstat"

echo "=== Sync & Reload — $(date) ==="

# ── Step 1: Copy critical source files ──────────────────────────────
echo "→ Syncing source files..."
docker cp "$HOST_REPO/toolsets.py"                 "$CONTAINER:$CONTAINER_REPO/toolsets.py"
docker cp "$HOST_REPO/gateway/run_helpers.py"      "$CONTAINER:$CONTAINER_REPO/gateway/run_helpers.py"
docker cp "$HOST_REPO/tools/kanban_toolset.py"     "$CONTAINER:$CONTAINER_REPO/tools/kanban_toolset.py"
docker cp "$HOST_REPO/gateway/kanban_runner.py"    "$CONTAINER:$CONTAINER_REPO/gateway/kanban_runner.py"
docker cp "$HOST_REPO/hermes_cli/kanban_db.py"     "$CONTAINER:$CONTAINER_REPO/hermes_cli/kanban_db.py"
docker cp "$HOST_REPO/hermes_cli/web_server.py"    "$CONTAINER:$CONTAINER_REPO/hermes_cli/web_server.py"

# ── Step 2: Update SOUL.md at ALL layers (identity soul + baked role SOP) ──
echo "→ Updating SOUL.md files (identity + role SOP)..."

ROLES_DIR="$HOST_REPO/.hermes-project/roles"
ROLE_SEPARATOR=$'\n\n---\n# Operating Procedure (authoritative — overrides identity on conflict)\n\n'

# Profile (identity soul) → role SOP (full procedure) map.
# MUST mirror NAME_TO_ROLE in start-lex-hermes.sh bootstrap_profiles().
declare -A NAME_TO_ROLE=(
    [lex-coordinator]=hpswarm-coordinator
    [lex-drafter]=hpswarm-drafter
    [lex-reviewer-content]=hpswarm-reviewer-content
    [lex-reviewer-format]=hpswarm-reviewer-format
    [lex-reviewer-ts]=hpswarm-reviewer-ts-consistency
    [lex-reviewer-xref]=hpswarm-reviewer-cross-ref
    [lex-reviewer-translation]=hpswarm-reviewer-translation
)

SOUL_FILES=(
    lex-coordinator
    lex-drafter
    lex-reviewer-content
    lex-reviewer-format
    lex-reviewer-translation
    lex-reviewer-ts
    lex-reviewer-xref
)

# build_soul <profile> <output-file> — identity soul + separator + role SOP.
# Falls back to identity-only if the role SOP is missing (graceful degradation).
build_soul() {
    local profile="$1" out="$2"
    local role_slug="${NAME_TO_ROLE[$profile]:-}"
    cat "$PROFILES_DIR/${profile}.soul.md" > "$out"
    if [ -n "$role_slug" ] && [ -f "$ROLES_DIR/${role_slug}.md" ]; then
        printf '%s' "$ROLE_SEPARATOR" >> "$out"
        cat "$ROLES_DIR/${role_slug}.md" >> "$out"
    else
        echo "  ⚠ role SOP missing for $profile ($role_slug) — identity-only soul"
    fi
}

TMP_SOUL_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_SOUL_DIR"' EXIT

# Profile homes (used by gateway-spawned workers + dispatch)
for profile in "${SOUL_FILES[@]}"; do
    combined="$TMP_SOUL_DIR/${profile}.SOUL.md"
    build_soul "$profile" "$combined"
    docker cp "$combined" "$CONTAINER:/root/.hermes/profiles/${profile}/SOUL.md"
done

# Main coordinator home (used by CLI + WebUI coordinator sessions)
docker cp "$TMP_SOUL_DIR/lex-coordinator.SOUL.md" "$CONTAINER:/root/.hermes/SOUL.md"

echo "  ✓ All SOUL.md files updated (identity + baked role SOP)"

# ── Step 3: Ensure kanban_swarm in configs ──────────────────────────
echo "→ Checking config.yaml platform_toolsets..."

# Main config
docker exec "$CONTAINER" python3 -c "
import yaml
p = '/root/.hermes/config.yaml'
with open(p) as f:
    cfg = yaml.safe_load(f)
toolsets = cfg.setdefault('platform_toolsets', {}).setdefault('cli', [])
if 'kanban_swarm' not in toolsets:
    toolsets.append('kanban_swarm')
    print('  + Added kanban_swarm to main config')
else:
    print('  ✓ Main config has kanban_swarm')
with open(p, 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
"

# Worker profile configs
for profile in lex-drafter lex-reviewer-content lex-reviewer-format \
               lex-reviewer-translation lex-reviewer-ts lex-reviewer-xref; do
    docker exec "$CONTAINER" python3 -c "
import yaml
p = '/root/.hermes/profiles/${profile}/config.yaml'
with open(p) as f:
    cfg = yaml.safe_load(f)
toolsets = cfg.setdefault('platform_toolsets', {}).setdefault('cli', [])
if 'kanban_swarm' not in toolsets:
    toolsets.append('kanban_swarm')
    print('  + Added kanban_swarm to ${profile} config')
with open(p, 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
"
done

# ── Step 4: Restart gateway services ────────────────────────────────
echo "→ Restarting gateway services..."
for svc in "${GATEWAY_SERVICES[@]}"; do
    docker exec "$CONTAINER" "$S6_SVC" -r "/run/service/$svc"
done
sleep 5

# ── Step 5: Verify all gateways are up ──────────────────────────────
echo "→ Verifying gateways..."
FAILED=0
for svc in "${GATEWAY_SERVICES[@]}"; do
    STATUS=$(docker exec "$CONTAINER" "$S6_SVSTAT" "/run/service/$svc" 2>&1 || true)
    if echo "$STATUS" | grep -q "up"; then
        echo "  ✓ $svc"
    else
        echo "  ✗ $svc — $STATUS"
        FAILED=1
    fi
done

echo ""
if [ $FAILED -eq 0 ]; then
    echo "=== Sync complete — all systems operational ==="
else
    echo "=== Sync complete with ERRORS — check failed gateways ==="
    exit 1
fi
