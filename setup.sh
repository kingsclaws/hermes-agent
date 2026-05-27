#!/usr/bin/env bash
# setup.sh — Hermes Agent Legal Harness Setup
#
# Usage:
#   ./setup.sh install                          # Install HPSwarm profiles to ~/.hermes/profiles/
#   ./setup.sh new-project <path> [options]      # Initialize a new legal project via hp init
#
# After modifying tools/lex_docx_tool.py, hot-reload with:
#   hermes chat -q "/py from tools.lex_docx_tool import reload_lex_docx_tools; print(reload_lex_docx_tools())"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

cmd_install() {
    echo "==> Installing HPSwarm profiles to $HERMES_HOME/profiles/"
    for profile_dir in "$REPO_ROOT"/harness/legal/profiles/*/; do
        name=$(basename "$profile_dir")
        target="$HERMES_HOME/profiles/$name"
        if [ -L "$target" ] || [ -d "$target" ]; then
            echo "    skip: $name (already exists)"
        else
            cp -r "$profile_dir" "$target"
            echo "    done: $name"
        fi
    done

    echo ""
    echo "==> Profiles installed."
    echo ""
    echo "==> To hot-reload lex_docx tools after editing:"
    echo "    hermes chat -q \"/py from tools.lex_docx_tool import reload_lex_docx_tools; print(reload_lex_docx_tools())\""
}

# ── new-project: Thin wrapper around hp init ──
# hp (hermes-project) is the canonical project session manager.  We set legal
# defaults on top so every new legal project starts with the right profile,
# toolset, and context preamble.
#
# Positional args after <path> are forwarded to hp init, so you can override
# any default:
#
#   ./setup.sh new-project /path/to/project \
#       --name "弘郡二期" \
#       --client "中信金资" \
#       --goal "起草担保合同纠纷诉状"
#
cmd_new_project() {
    local project_path="${1:-}"
    if [ -z "$project_path" ]; then
        echo "Usage: $0 new-project <path> [--name NAME] [--client CLIENT] [--goal GOAL] [...]"
        echo ""
        echo "Creates a legal project via hp init with legal defaults:"
        echo "  --profile-name hpswarm-coordinator"
        echo "  --toolsets     lex-docx"
        echo ""
        echo "All extra args are forwarded to hp init.  See: hp init --help"
        exit 1
    fi
    shift  # pop project_path; rest ($@) forwarded to hp init

    local abs_path
    abs_path="$(cd "$(dirname "$project_path")" 2>/dev/null && pwd)/$(basename "$project_path")" || abs_path="$project_path"

    # Ensure the directory exists (hp init requires it)
    mkdir -p "$abs_path"

    echo "==> Creating legal project via hp init: $abs_path"

    # hp init creates the session, registers the project, and generates
    # project-context.md under .hermes-project/.  Session creation may fail
    # if the backend is unavailable — the project directory and context file
    # are still usable; the session can be created later with `hp open`.
    hp init \
        --cwd "$abs_path" \
        --profile-name "hpswarm-coordinator" \
        --toolsets "lex-docx" \
        --skip-session-check \
        --skip-initial-sync \
        "$@" 2>&1 || true

    # Inject legal harness context into the generated project-context.md.
    # hp init already wrote the base template — we append legal SOP linkage
    # and reference files.
    local context_file="$abs_path/.hermes-project/project-context.md"
    if [ -f "$context_file" ]; then
        _inject_legal_preamble "$context_file" "$abs_path"
    fi

    # Copy STANDARDS.md as a project-level reference
    cp "$REPO_ROOT"/harness/legal/STANDARDS.md "$abs_path"/STANDARDS.md

    echo ""
    echo "==> Project created. Next steps:"
    echo "    1. Edit $context_file — review and customize"
    echo "    2. Start coordinator:"
    echo "       cd $abs_path && hermes chat -p hpswarm-coordinator"
    echo "    3. Coordinator reads project-context.md automatically"
    echo ""
    echo "    hp commands for ongoing management:"
    echo "      hp list               — list all projects"
    echo "      hp context <slug>      — view project context"
    echo "      hp sync <slug>         — sync context into active session"
    echo "      hp goal <slug> <goal>  — update project goal"
    echo "      hp open <slug>         — open project session"
}

# Append legal-specific preamble to the hp-generated context file.
_inject_legal_preamble() {
    local context_file="$1"
    local project_dir="$2"

    local preamble
    preamble=$(cat <<'INJECT'
## Legal Harness Integration

This project uses the **lex-hermes** legal harness. The following are always available:

| Resource | Path |
|----------|------|
| Legal SOP | STANDARDS.md (project root) |
| HPSwarm Coordinator | Profile: hpswarm-coordinator |
| HPSwarm Drafter | Profile: hpswarm-drafter |
| HPSwarm Reviewer (Content) | Profile: hpswarm-reviewer-content |
| HPSwarm Reviewer (Format) | Profile: hpswarm-reviewer-format |
| lex_docx tools | 30 native tools under lex-docx toolset |

### Legal Workflow (from STANDARDS.md)

**Iron Rules:**
1. 逐字审阅 — every word must be reviewed
2. TC 全程 — all edits in Track Changes mode
3. 段落编号不变 — never renumber paragraphs during editing
4. 先结构后内容 — check structure before content

**Quality Gates:**
- Gate 1: Structure complete (TOC, heading levels, numbering)
- Gate 2: Content reviewed (legal substance, completeness, consistency)
- Gate 3: Format reviewed (fonts, spacing, indentation, tables, headers/footers)
- Gate 4: Final cleanup (accept TC, remove comments, clean headers)

### Delegation Flow
```
User → Coordinator → Drafter → Reviewer-Content → Reviewer-Format → Coordinator → User
```
Each handoff includes exact file paths, paragraph ranges, and acceptance criteria.

### Reference
Read STANDARDS.md for the complete legal document production SOP.
INJECT
)

    # Insert the preamble before "## Active Tasks" — a stable anchor in hp's template.
    if grep -q '^## Active Tasks' "$context_file"; then
        # Use a temp file; sed -i varies between GNU and BSD.
        local tmp
        tmp=$(mktemp)
        awk -v preamble="$preamble" '
            /^## Active Tasks/ && !injected {
                print preamble
                print ""
                injected=1
            }
            { print }
        ' "$context_file" > "$tmp"
        mv "$tmp" "$context_file"
    else
        # Fallback: append at end if anchor not found
        printf '\n%s\n' "$preamble" >> "$context_file"
    fi

    # Replace placeholder goal if one was set via --goal
    local goal
    goal=$(python3 -c "
import json, sys
try:
    meta = json.load(open('$project_dir/.hermes-project/project-meta.json'))
    g = meta.get('goal', '')
    sys.stdout.write(g)
except Exception:
    pass
" 2>/dev/null)
    if [ -n "$goal" ]; then
        # Update the current goal section in context
        python3 -c "
import re
ctx = open('$context_file').read()
# Replace '(待补充)' goal placeholder with actual goal
ctx = ctx.replace('- 当前目标：（待补充）', '- 当前目标：$goal')
open('$context_file', 'w').write(ctx)
" 2>/dev/null || true
    fi
}

cmd_reload() {
    echo "==> Hot-reloading lex_docx tools..."
    cd "$REPO_ROOT"
    python3 -c "
import sys
sys.path.insert(0, '.')
sys.path.insert(0, '/root/.hermes/tools/lex-docx-build')
from tools.lex_docx_tool import reload_lex_docx_tools
result = reload_lex_docx_tools()
print(f'Deregistered: {result[\"deregistered\"]}')
print(f'Re-registered: {result[\"reregistered\"]}')
"
}

case "${1:-}" in
    install)       cmd_install ;;
    new-project)   cmd_new_project "${2:-}" "${@:3}" ;;
    reload)        cmd_reload ;;
    *)
        echo "Usage: $0 {install|new-project <path> [options]|reload}"
        exit 1
        ;;
esac
