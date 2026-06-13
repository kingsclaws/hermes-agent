#!/command/with-contenv sh
# shellcheck shell=sh
# /opt/hermes/docker/main-wrapper.sh — wraps the container's CMD with
# the same argument-routing logic the pre-s6 entrypoint.sh used. Runs
# as /init's "main program" (Docker CMD) so it inherits stdin/stdout/
# stderr from the container.
#
# Shebang note: /init scrubs env before invoking CMD, so a plain
# `#!/bin/sh` wrapper sees an empty environ and `ENV HERMES_HOME=/opt/data`
# from the Dockerfile never reaches `hermes`. with-contenv repopulates
# the env from /run/s6/container_environment before exec'ing, which is
# what s6-supervised services use too (see main-hermes/run).
#
# Routing:
#   no args + TTY                 → exec `hermes` (interactive default)
#   no args + no TTY/dashboard    → exec `sleep infinity` (s6 services stay up)
#   first arg is an executable    → exec it directly (sleep, bash, sh, …)
#   first arg is anything else    → exec `hermes <args>` (subcommand passthrough)
#
# By default this image runs Hermes as root for document-workspace containers
# that mount user-managed legal files. Set HERMES_RUN_AS_ROOT=0 to restore the
# upstream unprivileged hermes user behavior.
set -e

_truthy() {
    case "${1:-}" in
        1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
        *) return 1 ;;
    esac
}

if _truthy "${HERMES_RUN_AS_ROOT:-}"; then
    export HOME="${HOME:-/root}"
    runner=""
else
    # HOME comes through with-contenv as /root (the /init context). Override
    # to the hermes user's home before dropping privileges so libraries that
    # resolve paths via $HOME (e.g. discord lockfile under XDG_STATE_HOME)
    # don't try to write to /root.
    export HOME=/opt/data
    runner="s6-setuidgid hermes"
fi

cd /opt/data
# shellcheck disable=SC1091
. /opt/hermes/.venv/bin/activate

if [ $# -eq 0 ]; then
    # Detached compose/dashboard deployments have no interactive stdin. Running
    # the CLI there exits immediately ("Input is not a terminal"), which makes
    # /init shut the whole container down and kills supervised services. Keep
    # the CMD alive; explicit commands like `hermes`, `bash`, or `sleep` still
    # pass through below.
    if _truthy "${HERMES_DASHBOARD:-}" || [ ! -t 0 ]; then
        exec sleep infinity
    fi
    # shellcheck disable=SC2086
    exec $runner hermes
fi

if command -v "$1" >/dev/null 2>&1; then
    # Bare executable — pass through directly.
    # shellcheck disable=SC2086
    exec $runner "$@"
fi

# Hermes subcommand pass-through.
# shellcheck disable=SC2086
exec $runner hermes "$@"
