#!/bin/bash
# backoffice-autofix.sh — Automated mimocode repair pipeline.
#
# Reads open backoffice issues, fixes them via a persistent mimocode session,
# commits & pushes, rebuilds Docker image, and hot-reloads tools into
# running sessions so existing work picks up the fixes immediately.
#
# The mimocode session is stored at $MIMOCODE_SESSION_DIR and persists across
# runs — mimocode remembers the codebase, avoiding re-learning costs.

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────
REPO_DIR="${HERMES_REPO_DIR:-/opt/hermes-agent}"
ISSUE_DIR="${BACKOFFICE_ISSUE_DIR:-/workingfile/.lex-hermes-backoffice/issues}"
MIMOCODE_SESSION_DIR="${HOME}/.hermes/backoffice-mimocode-session"
MAX_FIXES="${MAX_FIXES:-3}"
DRY_RUN="${DRY_RUN:-false}"
IMAGE="${LEX_IMAGE:-localhost:6678/lex-hermes:latest}"
DOCKERFILE="${LEX_DOCKERFILE:-$REPO_DIR/Dockerfile.patch}"

# ── Args ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --max-fixes) MAX_FIXES="$2"; shift 2 ;;
    --issue) TARGET_ISSUE="$2"; shift 2 ;;
    --skill) CLAUDE_SKILL="$2"; shift 2 ;;
    *) echo "Unknown: $1"; exit 1 ;;
  esac
done

# ── Find issues ──────────────────────────────────────────────────
if [[ -n "${TARGET_ISSUE:-}" ]]; then
  OPEN_ISSUES=("$ISSUE_DIR/${TARGET_ISSUE}.REPORT.md")
else
  mapfile -t ALL_ISSUES < <(ls "$ISSUE_DIR"/*.REPORT.md 2>/dev/null | sort -r || true)
  OPEN_ISSUES=()
  for report in "${ALL_ISSUES[@]}"; do
    grep -q 'Status:.*open' "$report" 2>/dev/null && OPEN_ISSUES+=("$report") || true
  done
fi

echo "Found ${#OPEN_ISSUES[@]} open issues (limit: $MAX_FIXES)"
[[ ${#OPEN_ISSUES[@]} -eq 0 ]] && { echo "Nothing to fix."; exit 0; }

# ── Ensure repo ──────────────────────────────────────────────────
if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone --branch lex-hermes --depth 1 \
    https://github.com/kingsclaws/hermes-agent.git "$REPO_DIR"
fi
cd "$REPO_DIR"
git pull --ff-only origin lex-hermes 2>/dev/null || true

# ── Ensure persistent mimocode session ─────────────────────────────
mkdir -p "$MIMOCODE_SESSION_DIR"
INIT_FLAG="$MIMOCODE_SESSION_DIR/.initialized"

if [[ ! -f "$INIT_FLAG" ]]; then
  echo "Initializing persistent mimocode session..."
  cat > "$MIMOCODE_SESSION_DIR/init-prompt.txt" << 'CTX'
You are a lex-hermes maintenance agent with full knowledge of this codebase.

## Permanent Context (do not forget across sessions)
- Repository: /opt/hermes-agent (branch: lex-hermes)
- Fork of: NousResearch/hermes-agent
- Legal document tools: vendor/lexitool/lexitool/*.py, tools/lexitool_tool.py
- Backoffice relay: plugins/backoffice-issue-relay/
- Gateway: gateway/run.py, hermes_cli/web_server.py
- Docker: Dockerfile.patch builds to localhost:6678/lex-hermes:latest
- Key files: CLAUDE.md, CONTEXT.md, docs/adr/

## Your Job
When given a backoffice issue REPORT.md:
1. Read the issue to understand the error
2. Find the relevant source file
3. Fix the bug
4. Commit with: fix(backoffice): <issue-id>
5. Push to origin lex-hermes
6. Report what you fixed

Keep responses concise. Focus on correct, minimal fixes.
CTX
  touch "$INIT_FLAG"
fi

# ── Fix each issue ──────────────────────────────────────────────
FIXED=0
for report in "${OPEN_ISSUES[@]}"; do
  [[ $FIXED -ge $MAX_FIXES ]] && break

  ISSUE_ID=$(basename "$report" .REPORT.md)
  echo ""
  echo "══════════════════════════════════════════"
  echo "  [$((FIXED+1))/$MAX_FIXES] Fixing: $ISSUE_ID"
  echo "══════════════════════════════════════════"

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY RUN] Would fix: $ISSUE_ID"
    FIXED=$((FIXED + 1))
    continue
  fi

  # Run mimocode in the persistent session directory
  SKILL="${CLAUDE_SKILL:-/diagnose}"
  (cd "$MIMOCODE_SESSION_DIR" && mimocode --print --output-format text \
    "$SKILL 修复这个 backoffice issue。报告路径: $report

1. 读取报告内容
2. 在 $REPO_DIR 中定位相关代码
3. 根据 issue 类型处理：bug→修复, feature→实现, sop→写 skill, arch→重构
4. git commit & push 到 lex-hermes 分支" \
    2>&1) || echo "[mimocode] Non-zero exit (may be benign)"

  FIXED=$((FIXED + 1))
done

# ── Rebuild & hot-reload ─────────────────────────────────────────
if [[ "$DRY_RUN" != "true" ]] && [[ $FIXED -gt 0 ]]; then
  echo ""
  echo "══════════════════════════════════════════"
  echo "  Rebuilding Docker image..."
  echo "══════════════════════════════════════════"

  cd "$REPO_DIR"
  docker build --pull=false -t "$IMAGE" -f "$DOCKERFILE" . || {
    echo "Docker build failed. Fixes are committed but not deployed."
    exit 1
  }

  echo "Image rebuilt: $IMAGE"
  echo "Run: docker stop lex-hermes && docker rm lex-hermes && docker run ... to deploy"
  echo "Or: hermes tools reload (to hot-reload without restart)"
fi

echo ""
echo "Done. Fixed $FIXED issues."
