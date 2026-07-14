#!/bin/sh
set -eu

# This entrypoint configures deployment services only. Profile state, including
# lex-master's config.yaml and SOUL.md, is never created or rewritten here.
export HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
export HERMES_DASHBOARD="${HERMES_DASHBOARD:-1}"
export HERMES_DASHBOARD_HOST="${HERMES_DASHBOARD_HOST:-0.0.0.0}"
export HERMES_DASHBOARD_PORT="${HERMES_DASHBOARD_PORT:-9119}"
export HERMES_AUTO_GATEWAY="${HERMES_AUTO_GATEWAY:-1}"
export HERMES_AUTO_GATEWAY_PROFILES="${HERMES_AUTO_GATEWAY_PROFILES:-lex-master}"
export GATEWAY_ALLOW_ALL_USERS="${GATEWAY_ALLOW_ALL_USERS:-true}"
export HERMES_PROJECTS_DB_PATH="${HERMES_PROJECTS_DB_PATH:-$HERMES_HOME/state.db}"
export HERMES_RUN_AS_ROOT="${HERMES_RUN_AS_ROOT:-1}"
export HERMES_DOCKER_EXEC_AS_ROOT="${HERMES_DOCKER_EXEC_AS_ROOT:-1}"
export HERMES_ALLOW_ROOT_GATEWAY="${HERMES_ALLOW_ROOT_GATEWAY:-1}"

export HERMES_WEBUI_DIR="${HERMES_WEBUI_DIR:-/opt/hermes-webui-lex}"
export HERMES_WEBUI_HOST="${HERMES_WEBUI_HOST:-0.0.0.0}"
export HERMES_WEBUI_PORT="${HERMES_WEBUI_PORT:-8788}"
export HERMES_WEBUI_STATE_DIR="${HERMES_WEBUI_STATE_DIR:-$HERMES_HOME/webui/state}"
export HERMES_WEBUI_AGENT_DIR="${HERMES_WEBUI_AGENT_DIR:-/opt/lex-hermes}"
export HERMES_WEBUI_LEX_BASE_URL="${HERMES_WEBUI_LEX_BASE_URL:-http://127.0.0.1:9119}"
export HERMES_WEBUI_SKIP_ONBOARDING="${HERMES_WEBUI_SKIP_ONBOARDING:-1}"

if [ ! -f "$HERMES_WEBUI_DIR/server.py" ]; then
    echo "[lex] WebUI is missing from $HERMES_WEBUI_DIR" >&2
    exit 1
fi

# Register WebUI before s6 compiles the service graph.
service_dir=/etc/s6-overlay/s6-rc.d/lex-webui
mkdir -p "$service_dir/dependencies.d" /etc/s6-overlay/s6-rc.d/user/contents.d
printf '%s\n' longrun > "$service_dir/type"
: > "$service_dir/dependencies.d/base"
: > /etc/s6-overlay/s6-rc.d/user/contents.d/lex-webui
cat > "$service_dir/run" <<'EOF'
#!/command/with-contenv sh
set -e

export HOME=/root
export PYTHONPATH="${HERMES_WEBUI_AGENT_DIR:-/opt/lex-hermes}${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "${HERMES_WEBUI_STATE_DIR:-/root/.hermes/webui/state}"
cd "${HERMES_WEBUI_DIR:-/opt/hermes-webui-lex}"
exec /opt/hermes/.venv/bin/python server.py
EOF
chmod 0755 "$service_dir/run"

echo "[lex] source: /opt/lex-hermes -> $(readlink -f /opt/lex-hermes)"
echo "[lex] dashboard: ${HERMES_DASHBOARD_HOST}:${HERMES_DASHBOARD_PORT}"
echo "[lex] webui: ${HERMES_WEBUI_HOST}:${HERMES_WEBUI_PORT}"
echo "[lex] gateway profiles: ${HERMES_AUTO_GATEWAY_PROFILES}"

exec /init /opt/hermes/docker/main-wrapper.sh "$@"
