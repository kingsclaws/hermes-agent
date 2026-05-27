#!/usr/bin/env bash
# setup.sh — Hermes Agent Legal Harness Setup
#
# Usage:
#   ./setup.sh install            # Install HPSwarm profiles to ~/.hermes/profiles/
#   ./setup.sh new-project <path>  # Initialize a new project from template
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
    echo "==> Profiles installed. Enable in config.yaml:"
    echo "    toolsets: [..., lex-docx]"
    echo ""
    echo "==> To hot-reload lex_docx tools after editing:"
    echo "    hermes chat -q \"/py from tools.lex_docx_tool import reload_lex_docx_tools; print(reload_lex_docx_tools())\""
}

cmd_new_project() {
    local project_path="${1:-}"
    if [ -z "$project_path" ]; then
        echo "Usage: $0 new-project <path>"
        exit 1
    fi

    local abs_path
    abs_path="$(cd "$(dirname "$project_path")" 2>/dev/null && pwd)/$(basename "$project_path")" || abs_path="$project_path"

    echo "==> Creating project: $abs_path"
    mkdir -p "$abs_path"/workspace

    cp "$REPO_ROOT"/harness/projects/_template/BOOTSTRAP.md "$abs_path"/
    cp "$REPO_ROOT"/harness/projects/_template/CONTEXT.md "$abs_path"/

    # Copy standards as reference
    cp "$REPO_ROOT"/harness/legal/STANDARDS.md "$abs_path"/

    echo ""
    echo "==> Project created. Next steps:"
    echo "    1. Edit $abs_path/BOOTSTRAP.md — fill in project details"
    echo "    2. Start coordinator:"
    echo "       cd $abs_path && hermes chat -p hpswarm-coordinator"
    echo "    3. Coordinator will read BOOTSTRAP.md and initialize CONTEXT.md"
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
    new-project)   cmd_new_project "${2:-}" ;;
    reload)        cmd_reload ;;
    *)
        echo "Usage: $0 {install|new-project <path>|reload}"
        exit 1
        ;;
esac
