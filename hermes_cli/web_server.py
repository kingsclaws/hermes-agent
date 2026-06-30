"""
Hermes Agent — Web UI server.

Provides a FastAPI backend serving the Vite/React frontend and REST API
endpoints for managing configuration, environment variables, and sessions.

Usage:
    python -m hermes_cli.main web          # Start on http://127.0.0.1:9119
    python -m hermes_cli.main web --port 8080
"""

import asyncio
import hmac
import importlib.util
import json
import logging
import os
import secrets
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from hermes_cli import __version__, __release_date__
from hermes_cli.config import (
    cfg_get,
    DEFAULT_CONFIG,
    OPTIONAL_ENV_VARS,
    get_config_path,
    get_env_path,
    get_hermes_home,
    load_config,
    load_env,
    save_config,
    save_env_value,
    remove_env_value,
    check_config_version,
    redact_key,
)
from gateway.status import get_running_pid, read_runtime_status
from utils import env_var_enabled

try:
    from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ImportError:
    # First try lazy-installing the dashboard extras. Only the user actually
    # running `hermes dashboard` needs fastapi+uvicorn; lazy install keeps
    # them out of every other install path. After install, re-import.
    try:
        from tools.lazy_deps import ensure as _lazy_ensure
        _lazy_ensure("tool.dashboard", prompt=False)
        from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel
    except Exception:
        raise SystemExit(
            "Web UI requires fastapi and uvicorn.\n"
            f"Install with: {sys.executable} -m pip install 'fastapi' 'uvicorn[standard]'"
        )

WEB_DIST = Path(os.environ["HERMES_WEB_DIST"]) if "HERMES_WEB_DIST" in os.environ else Path(__file__).parent / "web_dist"
_log = logging.getLogger(__name__)

app = FastAPI(title="Hermes Agent", version=__version__)

# ---------------------------------------------------------------------------
# Session token for protecting sensitive endpoints (reveal).
# Generated fresh on every server start — dies when the process exits.
# Injected into the SPA HTML so only the legitimate web UI can use it.
# ---------------------------------------------------------------------------
_SESSION_TOKEN = secrets.token_urlsafe(32)
_SESSION_HEADER_NAME = "X-Hermes-Session-Token"

# Legacy in-browser PTY/TUI bridge (/api/pty, /api/pub, /api/events). Off
# unless ``hermes dashboard --tui`` or HERMES_DASHBOARD_TUI=1. Native web chat
# uses /api/ws and remains available behind the normal dashboard auth gate.
_DASHBOARD_EMBEDDED_CHAT_ENABLED = False

# Simple rate limiter for the reveal endpoint
_reveal_timestamps: List[float] = []
_REVEAL_MAX_PER_WINDOW = 5
_REVEAL_WINDOW_SECONDS = 30

# CORS: restrict to localhost origins only.  The web UI is intended to run
# locally; binding to 0.0.0.0 with allow_origins=["*"] would let any website
# read/modify config and secrets.

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Endpoints that do NOT require the session token.  Everything else under
# /api/ is gated by the auth middleware below.  Keep this list minimal —
# only truly non-sensitive, read-only endpoints belong here.
# ---------------------------------------------------------------------------
_PUBLIC_API_PATHS: frozenset = frozenset({
    "/api/status",
    "/api/config/defaults",
    "/api/config/schema",
    "/api/model/info",
    "/api/dashboard/themes",
    "/api/dashboard/plugins",
    "/api/kanban/broadcast",  # loopback-only kanban event relay from gateway notifier
})


def _has_valid_session_token(request: Request) -> bool:
    """True if the request carries a valid dashboard session token.

    The dedicated session header avoids collisions with reverse proxies that
    already use ``Authorization`` (for example Caddy ``basic_auth``). We still
    accept the legacy Bearer path for backward compatibility with older
    dashboard bundles.
    """
    session_header = request.headers.get(_SESSION_HEADER_NAME, "")
    if session_header and hmac.compare_digest(
        session_header.encode(),
        _SESSION_TOKEN.encode(),
    ):
        return True

    auth = request.headers.get("authorization", "")
    expected = f"Bearer {_SESSION_TOKEN}"
    return hmac.compare_digest(auth.encode(), expected.encode())


def _require_token(request: Request) -> None:
    """Validate the ephemeral session token.  Raises 401 on mismatch."""
    if not _has_valid_session_token(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


# Accepted Host header values for loopback binds. DNS rebinding attacks
# point a victim browser at an attacker-controlled hostname (evil.test)
# which resolves to 127.0.0.1 after a TTL flip — bypassing same-origin
# checks because the browser now considers evil.test and our dashboard
# "same origin". Validating the Host header at the app layer rejects any
# request whose Host isn't one we bound for. See GHSA-ppp5-vxwm-4cf7.
_LOOPBACK_HOST_VALUES: frozenset = frozenset({
    "localhost", "127.0.0.1", "::1",
})


def should_require_auth(host: str, allow_public: bool) -> bool:
    """Return True iff the dashboard OAuth auth gate must be active.

    Truth table:
      host == loopback                              → False (no auth)
      host != loopback AND allow_public (--insecure)→ False (legacy escape hatch)
      host != loopback AND NOT allow_public         → True  (gate engages)

    "Loopback" matches the same set used by ``--insecure`` enforcement in
    ``start_server``: 127.0.0.1, localhost, ::1. RFC1918 / CGNAT / link-local
    are deliberately treated as PUBLIC — a hostile device on the same LAN is
    exactly the threat model the gate is designed for.
    """
    return (host not in _LOOPBACK_HOST_VALUES) and (not allow_public)


def _is_accepted_host(host_header: str, bound_host: str) -> bool:
    """True if the Host header targets the interface we bound to.

    Accepts:
    - Exact bound host (with or without port suffix)
    - Loopback aliases when bound to loopback
    - Any host when bound to 0.0.0.0 (explicit opt-in to non-loopback,
      no protection possible at this layer)
    """
    if not host_header:
        return False
    # Strip port suffix. IPv6 addresses use bracket notation:
    #   [::1]         — no port
    #   [::1]:9119    — with port
    # Plain hosts/v4:
    #   localhost:9119
    #   127.0.0.1:9119
    h = host_header.strip()
    if h.startswith("["):
        # IPv6 bracketed — port (if any) follows "]:"
        close = h.find("]")
        if close != -1:
            host_only = h[1:close]  # strip brackets
        else:
            host_only = h.strip("[]")
    else:
        host_only = h.rsplit(":", 1)[0] if ":" in h else h
    host_only = host_only.lower()

    # 0.0.0.0 bind means operator explicitly opted into all-interfaces
    # (requires --insecure per web_server.start_server). No Host-layer
    # defence can protect that mode; rely on operator network controls.
    if bound_host in {"0.0.0.0", "::"}:
        return True

    # Loopback bind: accept the loopback names
    bound_lc = bound_host.lower()
    if bound_lc in _LOOPBACK_HOST_VALUES:
        return host_only in _LOOPBACK_HOST_VALUES

    # Explicit non-loopback bind: require exact host match
    return host_only == bound_lc


@app.middleware("http")
async def host_header_middleware(request: Request, call_next):
    """Reject requests whose Host header doesn't match the bound interface.

    Defends against DNS rebinding: a victim browser on a localhost
    dashboard is tricked into fetching from an attacker hostname that
    TTL-flips to 127.0.0.1. CORS and same-origin checks don't help —
    the browser now treats the attacker origin as same-origin with the
    dashboard. Host-header validation at the app layer catches it.

    See GHSA-ppp5-vxwm-4cf7.
    """
    # Store the bound host on app.state so this middleware can read it —
    # set by start_server() at listen time.
    bound_host = getattr(app.state, "bound_host", None)
    if bound_host:
        host_header = request.headers.get("host", "")
        if not _is_accepted_host(host_header, bound_host):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        "Invalid Host header. Dashboard requests must use "
                        "the hostname the server was bound to."
                    ),
                },
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Dashboard OAuth auth gate — engaged only when start_server flags the
# bind as non-loopback-without-insecure.  No-op pass-through in loopback
# mode so the legacy auth_middleware (below) handles those binds via
# the injected ``_SESSION_TOKEN``.  Registered between host_header and
# auth_middleware so the order is: host check → cookie auth → token auth.
# ---------------------------------------------------------------------------


@app.middleware("http")
async def _dashboard_auth_gate(request: Request, call_next):
    from hermes_cli.dashboard_auth.middleware import gated_auth_middleware
    return await gated_auth_middleware(request, call_next)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require the session token on all /api/ routes except the public list."""
    # When the OAuth gate is active, cookie-based auth (gated_auth_middleware
    # above) is authoritative.  The legacy _SESSION_TOKEN path is loopback-only
    # and is skipped here so the gate's session attachment isn't overridden.
    if getattr(request.app.state, "auth_required", False):
        return await call_next(request)
    path = request.url.path
    if path.startswith("/api/") and path not in _PUBLIC_API_PATHS:
        if not _has_valid_session_token(request):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Config schema — auto-generated from DEFAULT_CONFIG
# ---------------------------------------------------------------------------

# Manual overrides for fields that need select options or custom types
_SCHEMA_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "model": {
        "type": "string",
        "description": "Default model (e.g. anthropic/claude-sonnet-4.6)",
        "category": "general",
    },
    "model_context_length": {
        "type": "number",
        "description": "Context window override (0 = auto-detect from model metadata)",
        "category": "general",
    },
    "terminal.backend": {
        "type": "select",
        "description": "Terminal execution backend",
        "options": ["local", "docker", "ssh", "modal", "daytona", "singularity"],
    },
    "terminal.modal_mode": {
        "type": "select",
        "description": "Modal sandbox mode",
        "options": ["sandbox", "function"],
    },
    "tts.provider": {
        "type": "select",
        "description": "Text-to-speech provider",
        "options": ["edge", "elevenlabs", "openai", "neutts"],
    },
    "stt.provider": {
        "type": "select",
        "description": "Speech-to-text provider",
        # "mistral" temporarily removed — mistralai PyPI package quarantined
        # (malicious 2.4.6 release on 2026-05-12). Restore once available.
        "options": ["local", "openai"],
    },
    "display.skin": {
        "type": "select",
        "description": "CLI visual theme",
        "options": ["default", "ares", "mono", "slate"],
    },
    "dashboard.theme": {
        "type": "select",
        "description": "Web dashboard visual theme",
        "options": ["default", "midnight", "ember", "mono", "cyberpunk", "rose"],
    },
    "display.resume_display": {
        "type": "select",
        "description": "How resumed sessions display history",
        "options": ["minimal", "full", "off"],
    },
    "display.busy_input_mode": {
        "type": "select",
        "description": "Input behavior while agent is running",
        "options": ["interrupt", "queue", "steer"],
    },
    "memory.provider": {
        "type": "select",
        "description": "Memory provider plugin",
        "options": ["builtin", "honcho"],
    },
    "approvals.mode": {
        "type": "select",
        "description": "Dangerous command approval mode",
        "options": ["ask", "yolo", "deny"],
    },
    "context.engine": {
        "type": "select",
        "description": "Context management engine",
        "options": ["default", "custom"],
    },
    "human_delay.mode": {
        "type": "select",
        "description": "Simulated typing delay mode",
        "options": ["off", "typing", "fixed"],
    },
    "logging.level": {
        "type": "select",
        "description": "Log level for agent.log",
        "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
    },
    "agent.service_tier": {
        "type": "select",
        "description": "API service tier (OpenAI/Anthropic)",
        "options": ["", "auto", "default", "flex"],
    },
    "delegation.reasoning_effort": {
        "type": "select",
        "description": "Reasoning effort for delegated subagents",
        "options": ["", "low", "medium", "high"],
    },
}

# Categories with fewer fields get merged into "general" to avoid tab sprawl.
_CATEGORY_MERGE: Dict[str, str] = {
    "privacy": "security",
    "context": "agent",
    "skills": "agent",
    "cron": "agent",
    "network": "agent",
    "checkpoints": "agent",
    "approvals": "security",
    "human_delay": "display",
    "dashboard": "display",
    "code_execution": "agent",
    "prompt_caching": "agent",
    "goals": "agent",
    # Only `telegram.reactions` currently lives under telegram — fold it in
    # with the other messaging-platform config (discord) so it isn't an
    # orphan tab of one field.
    "telegram": "discord",
}

# Display order for tabs — unlisted categories sort alphabetically after these.
_CATEGORY_ORDER = [
    "general", "agent", "terminal", "display", "delegation",
    "memory", "compression", "security", "browser", "voice",
    "tts", "stt", "logging", "discord", "auxiliary",
]


def _infer_type(value: Any) -> str:
    """Infer a UI field type from a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "string"


def _build_schema_from_config(
    config: Dict[str, Any],
    prefix: str = "",
) -> Dict[str, Dict[str, Any]]:
    """Walk DEFAULT_CONFIG and produce a flat dot-path → field schema dict."""
    schema: Dict[str, Dict[str, Any]] = {}
    for key, value in config.items():
        full_key = f"{prefix}.{key}" if prefix else key

        # Skip internal / version keys
        if full_key in {"_config_version",}:
            continue

        # Category is the first path component for nested keys, or "general"
        # for top-level scalar fields (model, toolsets, timezone, etc.).
        if prefix:
            category = prefix.split(".")[0]
        elif isinstance(value, dict):
            category = key
        else:
            category = "general"

        if isinstance(value, dict):
            # Recurse into nested dicts
            schema.update(_build_schema_from_config(value, full_key))
        else:
            entry: Dict[str, Any] = {
                "type": _infer_type(value),
                "description": full_key.replace(".", " → ").replace("_", " ").title(),
                "category": category,
            }
            # Apply manual overrides
            if full_key in _SCHEMA_OVERRIDES:
                entry.update(_SCHEMA_OVERRIDES[full_key])
            # Merge small categories
            entry["category"] = _CATEGORY_MERGE.get(entry["category"], entry["category"])
            schema[full_key] = entry
    return schema


CONFIG_SCHEMA = _build_schema_from_config(DEFAULT_CONFIG)

# Inject virtual fields that don't live in DEFAULT_CONFIG but are surfaced
# by the normalize/denormalize cycle.  Insert model_context_length right after
# the "model" key so it renders adjacent in the frontend.
_mcl_entry = _SCHEMA_OVERRIDES["model_context_length"]
_ordered_schema: Dict[str, Dict[str, Any]] = {}
for _k, _v in CONFIG_SCHEMA.items():
    _ordered_schema[_k] = _v
    if _k == "model":
        _ordered_schema["model_context_length"] = _mcl_entry
CONFIG_SCHEMA = _ordered_schema


class ConfigUpdate(BaseModel):
    config: dict


class EnvVarUpdate(BaseModel):
    key: str
    value: str


class EnvVarDelete(BaseModel):
    key: str


class EnvVarReveal(BaseModel):
    key: str


class ModelAssignment(BaseModel):
    """Payload for POST /api/model/set — assign a provider/model to a slot.

    scope="main"        → writes model.provider + model.default
    scope="auxiliary"   → writes auxiliary.<task>.provider + auxiliary.<task>.model
    scope="auxiliary" with task=""  → applied to every auxiliary.* slot
    scope="auxiliary" with task="__reset__"  → resets every slot to provider="auto"
    """
    scope: str
    provider: str
    model: str
    task: str = ""


_GATEWAY_HEALTH_URL = os.getenv("GATEWAY_HEALTH_URL")
try:
    _GATEWAY_HEALTH_TIMEOUT = float(os.getenv("GATEWAY_HEALTH_TIMEOUT", "3"))
except (ValueError, TypeError):
    _log.warning(
        "Invalid GATEWAY_HEALTH_TIMEOUT value %r — using default 3.0s",
        os.getenv("GATEWAY_HEALTH_TIMEOUT"),
    )
    _GATEWAY_HEALTH_TIMEOUT = 3.0

# DEPRECATED (scheduled for removal): GATEWAY_HEALTH_URL / GATEWAY_HEALTH_TIMEOUT.
# Cross-container / cross-host gateway liveness detection will be folded into a
# first-class dashboard config key so it's no longer Docker-adjacent lore buried
# in env vars.  The env vars still work for now so existing Compose deployments
# don't break.  Do not add new callers — wire new uses through the planned
# config surface.


def _probe_gateway_health() -> tuple[bool, dict | None]:
    """Probe the gateway via its HTTP health endpoint (cross-container).

    .. deprecated::
        Driven by the deprecated ``GATEWAY_HEALTH_URL`` /
        ``GATEWAY_HEALTH_TIMEOUT`` env vars.  Scheduled for removal alongside
        a move to a first-class dashboard config key.  See
        :data:`_GATEWAY_HEALTH_URL` for context.

    Uses ``/health/detailed`` first (returns full state), falling back to
    the simpler ``/health`` endpoint.  Returns ``(is_alive, body_dict)``.

    Accepts any of these as ``GATEWAY_HEALTH_URL``:
    - ``http://gateway:8642``                (base URL — recommended)
    - ``http://gateway:8642/health``         (explicit health path)
    - ``http://gateway:8642/health/detailed`` (explicit detailed path)

    This is a **blocking** call — run via ``run_in_executor`` from async code.
    """
    if not _GATEWAY_HEALTH_URL:
        return False, None

    # Normalise to base URL so we always probe the right paths regardless of
    # whether the user included /health or /health/detailed in the env var.
    base = _GATEWAY_HEALTH_URL.rstrip("/")
    if base.endswith("/health/detailed"):
        base = base[: -len("/health/detailed")]
    elif base.endswith("/health"):
        base = base[: -len("/health")]

    for path in (f"{base}/health/detailed", f"{base}/health"):
        try:
            req = urllib.request.Request(path, method="GET")
            with urllib.request.urlopen(req, timeout=_GATEWAY_HEALTH_TIMEOUT) as resp:
                if resp.status == 200:
                    body = json.loads(resp.read())
                    return True, body
        except Exception:
            continue
    return False, None


@app.get("/api/status")
async def get_status():
    current_ver, latest_ver = check_config_version()

    # --- Gateway liveness detection ---
    # Try local PID check first (same-host).  If that fails and a remote
    # GATEWAY_HEALTH_URL is configured, probe the gateway over HTTP so the
    # dashboard works when the gateway runs in a separate container.
    gateway_pid = get_running_pid()
    gateway_running = gateway_pid is not None
    remote_health_body: dict | None = None

    if not gateway_running and _GATEWAY_HEALTH_URL:
        loop = asyncio.get_running_loop()
        alive, remote_health_body = await loop.run_in_executor(
            None, _probe_gateway_health
        )
        if alive:
            gateway_running = True
            # PID from the remote container (display only — not locally valid)
            if remote_health_body:
                gateway_pid = remote_health_body.get("pid")

    gateway_state = None
    gateway_platforms: dict = {}
    gateway_exit_reason = None
    gateway_updated_at = None
    configured_gateway_platforms: set[str] | None = None
    try:
        from gateway.config import load_gateway_config

        gateway_config = load_gateway_config()
        configured_gateway_platforms = {
            platform.value for platform in gateway_config.get_connected_platforms()
        }
    except Exception:
        configured_gateway_platforms = None

    # Prefer the detailed health endpoint response (has full state) when the
    # local runtime status file is absent or stale (cross-container).
    runtime = read_runtime_status()
    if runtime is None and remote_health_body and remote_health_body.get("gateway_state"):
        runtime = remote_health_body

    if runtime:
        gateway_state = runtime.get("gateway_state")
        gateway_platforms = runtime.get("platforms") or {}
        if configured_gateway_platforms is not None:
            gateway_platforms = {
                key: value
                for key, value in gateway_platforms.items()
                if key in configured_gateway_platforms
            }
        gateway_exit_reason = runtime.get("exit_reason")
        gateway_updated_at = runtime.get("updated_at")
        if not gateway_running:
            gateway_state = gateway_state if gateway_state in {"stopped", "startup_failed"} else "stopped"
            gateway_platforms = {}
        elif gateway_running and remote_health_body is not None:
            # The health probe confirmed the gateway is alive, but the local
            # runtime status file may be stale (cross-container).  Override
            # stopped/None state so the dashboard shows the correct badge.
            if gateway_state in {None, "stopped"}:
                gateway_state = "running"

    # If there was no runtime info at all but the health probe confirmed alive,
    # ensure we still report the gateway as running (no shared volume scenario).
    if gateway_running and gateway_state is None and remote_health_body is not None:
        gateway_state = "running"

    active_sessions = 0
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        try:
            sessions = db.list_sessions_rich(limit=50)
            now = time.time()
            active_sessions = sum(
                1 for s in sessions
                if s.get("ended_at") is None
                and (now - s.get("last_active", s.get("started_at", 0))) < 300
            )
        finally:
            db.close()
    except Exception:
        pass

    # Dashboard auth gate (Phase 7): surface whether the gate is engaged
    # and which providers are registered so ``hermes status`` and the
    # SPA's StatusPage can show "OAuth gate ON via Nous Research" or
    # "loopback only — no auth gate" with no extra round trips.
    auth_required = bool(getattr(app.state, "auth_required", False))
    auth_providers: list[str] = []
    try:
        from hermes_cli.dashboard_auth import list_providers as _list_providers
        auth_providers = [p.name for p in _list_providers()]
    except Exception:
        # Module not importable yet (early startup) — leave as [].
        pass

    return {
        "version": __version__,
        "release_date": __release_date__,
        "hermes_home": str(get_hermes_home()),
        "config_path": str(get_config_path()),
        "env_path": str(get_env_path()),
        "config_version": current_ver,
        "latest_config_version": latest_ver,
        "gateway_running": gateway_running,
        "gateway_pid": gateway_pid,
        "gateway_health_url": _GATEWAY_HEALTH_URL,
        "gateway_state": gateway_state,
        "gateway_platforms": gateway_platforms,
        "gateway_exit_reason": gateway_exit_reason,
        "gateway_updated_at": gateway_updated_at,
        "active_sessions": active_sessions,
        "auth_required": auth_required,
        "auth_providers": auth_providers,
    }


# ---------------------------------------------------------------------------
# Gateway + update actions (invoked from the Status page).
#
# Both commands are spawned as detached subprocesses so the HTTP request
# returns immediately.  stdin is closed (``DEVNULL``) so any stray ``input()``
# calls fail fast with EOF rather than hanging forever.  stdout/stderr are
# streamed to a per-action log file under ``~/.hermes/logs/<action>.log`` so
# the dashboard can tail them back to the user.
# ---------------------------------------------------------------------------

_ACTION_LOG_DIR: Path = get_hermes_home() / "logs"

# Short ``name`` (from the URL) → absolute log file path.
_ACTION_LOG_FILES: Dict[str, str] = {
    "gateway-restart": "gateway-restart.log",
    "hermes-update": "hermes-update.log",
}

# ``name`` → most recently spawned Popen handle.  Used so ``status`` can
# report liveness and exit code without shelling out to ``ps``.
_ACTION_PROCS: Dict[str, subprocess.Popen] = {}


def _spawn_hermes_action(subcommand: List[str], name: str) -> subprocess.Popen:
    """Spawn ``hermes <subcommand>`` detached and record the Popen handle.

    Uses the running interpreter's ``hermes_cli.main`` module so the action
    inherits the same venv/PYTHONPATH the web server is using.
    """
    log_file_name = _ACTION_LOG_FILES[name]
    _ACTION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _ACTION_LOG_DIR / log_file_name
    log_file = open(log_path, "ab", buffering=0)
    log_file.write(
        f"\n=== {name} started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode()
    )

    cmd = [sys.executable, "-m", "hermes_cli.main", *subcommand]

    popen_kwargs: Dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "env": {**os.environ, "HERMES_NONINTERACTIVE": "1"},
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    _ACTION_PROCS[name] = proc
    return proc


def _tail_lines(path: Path, n: int) -> List[str]:
    """Return the last ``n`` lines of ``path``.  Reads the whole file — fine
    for our small per-action logs.  Binary-decoded with ``errors='replace'``
    so log corruption doesn't 500 the endpoint."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    return lines[-n:] if n > 0 else lines


@app.post("/api/gateway/restart")
async def restart_gateway():
    """Kick off a ``hermes gateway restart`` in the background."""
    try:
        proc = _spawn_hermes_action(["gateway", "restart"], "gateway-restart")
    except Exception as exc:
        _log.exception("Failed to spawn gateway restart")
        raise HTTPException(status_code=500, detail=f"Failed to restart gateway: {exc}")
    return {
        "ok": True,
        "pid": proc.pid,
        "name": "gateway-restart",
    }


@app.post("/api/hermes/update")
async def update_hermes():
    """Kick off ``hermes update`` in the background."""
    try:
        proc = _spawn_hermes_action(["update"], "hermes-update")
    except Exception as exc:
        _log.exception("Failed to spawn hermes update")
        raise HTTPException(status_code=500, detail=f"Failed to start update: {exc}")
    return {
        "ok": True,
        "pid": proc.pid,
        "name": "hermes-update",
    }


@app.get("/api/actions/{name}/status")
async def get_action_status(name: str, lines: int = 200):
    """Tail an action log and report whether the process is still running."""
    log_file_name = _ACTION_LOG_FILES.get(name)
    if log_file_name is None:
        raise HTTPException(status_code=404, detail=f"Unknown action: {name}")

    log_path = _ACTION_LOG_DIR / log_file_name
    tail = _tail_lines(log_path, min(max(lines, 1), 2000))

    proc = _ACTION_PROCS.get(name)
    if proc is None:
        running = False
        exit_code: Optional[int] = None
        pid: Optional[int] = None
    else:
        exit_code = proc.poll()
        running = exit_code is None
        pid = proc.pid

    return {
        "name": name,
        "running": running,
        "exit_code": exit_code,
        "pid": pid,
        "lines": tail,
    }


@app.get("/api/sessions")
async def get_sessions(limit: int = 20, offset: int = 0):
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        try:
            sessions = db.list_sessions_rich(limit=limit, offset=offset)
            total = db.session_count()
            now = time.time()
            for s in sessions:
                s["is_active"] = (
                    s.get("ended_at") is None
                    and (now - s.get("last_active", s.get("started_at", 0))) < 300
                )
            return {"sessions": sessions, "total": total, "limit": limit, "offset": offset}
        finally:
            db.close()
    except Exception:
        _log.exception("GET /api/sessions failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/sessions/search")
async def search_sessions(q: str = "", limit: int = 20):
    """Full-text search across session message content using FTS5."""
    if not q or not q.strip():
        return {"results": []}
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        try:
            # Auto-add prefix wildcards so partial words match
            # e.g. "nimb" → "nimb*" matches "nimby"
            # Preserve quoted phrases and existing wildcards as-is
            import re
            terms = []
            for token in re.findall(r'"[^"]*"|\S+', q.strip()):
                if token.startswith('"') or token.endswith("*"):
                    terms.append(token)
                else:
                    terms.append(token + "*")
            prefix_query = " ".join(terms)
            matches = db.search_messages(query=prefix_query, limit=limit)
            # Group by session_id — return unique sessions with their best snippet
            seen: dict = {}
            for m in matches:
                sid = m["session_id"]
                if sid not in seen:
                    seen[sid] = {
                        "session_id": sid,
                        "snippet": m.get("snippet", ""),
                        "role": m.get("role"),
                        "source": m.get("source"),
                        "model": m.get("model"),
                        "session_started": m.get("session_started"),
                    }
            return {"results": list(seen.values())}
        finally:
            db.close()
    except Exception:
        _log.exception("GET /api/sessions/search failed")
        raise HTTPException(status_code=500, detail="Search failed")


def _normalize_config_for_web(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize config for the web UI.

    Hermes supports ``model`` as either a bare string (``"anthropic/claude-sonnet-4"``)
    or a dict (``{default: ..., provider: ..., base_url: ...}``).  The schema is built
    from DEFAULT_CONFIG where ``model`` is a string, but user configs often have the
    dict form.  Normalize to the string form so the frontend schema matches.

    Also surfaces ``model_context_length`` as a top-level field so the web UI can
    display and edit it.  A value of 0 means "auto-detect".
    """
    config = dict(config)  # shallow copy
    model_val = config.get("model")
    if isinstance(model_val, dict):
        # Extract context_length before flattening the dict
        ctx_len = model_val.get("context_length", 0)
        config["model"] = model_val.get("default", model_val.get("name", ""))
        config["model_context_length"] = ctx_len if isinstance(ctx_len, int) else 0
    else:
        config["model_context_length"] = 0
    return config


@app.get("/api/config")
async def get_config():
    config = _normalize_config_for_web(load_config())
    # Strip internal keys that the frontend shouldn't see or send back
    return {k: v for k, v in config.items() if not k.startswith("_")}


@app.get("/api/config/defaults")
async def get_defaults():
    return DEFAULT_CONFIG


@app.get("/api/config/schema")
async def get_schema():
    return {"fields": CONFIG_SCHEMA, "category_order": _CATEGORY_ORDER}


_EMPTY_MODEL_INFO: dict = {
    "model": "",
    "provider": "",
    "auto_context_length": 0,
    "config_context_length": 0,
    "effective_context_length": 0,
    "capabilities": {},
}


@app.get("/api/model/info")
def get_model_info():
    """Return resolved model metadata for the currently configured model.

    Calls the same context-length resolution chain the agent uses, so the
    frontend can display "Auto-detected: 200K" alongside the override field.
    Also returns model capabilities (vision, reasoning, tools) when available.
    """
    try:
        cfg = load_config()
        model_cfg = cfg.get("model", "")

        # Extract model name and provider from the config
        if isinstance(model_cfg, dict):
            model_name = model_cfg.get("default", model_cfg.get("name", ""))
            provider = model_cfg.get("provider", "")
            base_url = model_cfg.get("base_url", "")
            config_ctx = model_cfg.get("context_length")
        else:
            model_name = str(model_cfg) if model_cfg else ""
            provider = ""
            base_url = ""
            config_ctx = None

        if not model_name:
            return dict(_EMPTY_MODEL_INFO, provider=provider)

        # Resolve auto-detected context length (pass config_ctx=None to get
        # purely auto-detected value, then separately report the override)
        try:
            from agent.model_metadata import get_model_context_length
            auto_ctx = get_model_context_length(
                model=model_name,
                base_url=base_url,
                provider=provider,
                config_context_length=None,  # ignore override — we want auto value
            )
        except Exception:
            auto_ctx = 0

        config_ctx_int = 0
        if isinstance(config_ctx, int) and config_ctx > 0:
            config_ctx_int = config_ctx

        # Effective is what the agent actually uses
        effective_ctx = config_ctx_int if config_ctx_int > 0 else auto_ctx

        # Try to get model capabilities from models.dev
        caps = {}
        try:
            from agent.models_dev import get_model_capabilities
            mc = get_model_capabilities(provider=provider, model=model_name)
            if mc is not None:
                caps = {
                    "supports_tools": mc.supports_tools,
                    "supports_vision": mc.supports_vision,
                    "supports_reasoning": mc.supports_reasoning,
                    "context_window": mc.context_window,
                    "max_output_tokens": mc.max_output_tokens,
                    "model_family": mc.model_family,
                }
        except Exception:
            pass

        return {
            "model": model_name,
            "provider": provider,
            "auto_context_length": auto_ctx,
            "config_context_length": config_ctx_int,
            "effective_context_length": effective_ctx,
            "capabilities": caps,
        }
    except Exception:
        _log.exception("GET /api/model/info failed")
        return dict(_EMPTY_MODEL_INFO)


# ---------------------------------------------------------------------------
# Model assignment — pick provider+model for main slot or auxiliary slots.
# Mirrors the model.options JSON-RPC from tui_gateway but uses REST so the
# Models page (which has no chat PTY open) can drive it.
# ---------------------------------------------------------------------------

# Canonical auxiliary task slots. Keep in sync with DEFAULT_CONFIG["auxiliary"]
# in hermes_cli/config.py — listed here for deterministic ordering in the UI.
_AUX_TASK_SLOTS: Tuple[str, ...] = (
    "vision",
    "web_extract",
    "compression",
    "skills_hub",
    "approval",
    "mcp",
    "title_generation",
    "triage_specifier",
    "kanban_decomposer",
    "profile_describer",
    "curator",
)


@app.get("/api/model/options")
def get_model_options():
    """Return authenticated providers + their curated model lists.

    REST equivalent of the ``model.options`` JSON-RPC on tui_gateway, so the
    dashboard Models page can render the picker without a live chat session.
    The response shape matches ``model.options`` 1:1 so ``ModelPickerDialog``
    can share the same types.
    """
    try:
        from hermes_cli.inventory import build_models_payload, load_picker_context

        return build_models_payload(load_picker_context(), max_models=50)
    except Exception:
        _log.exception("GET /api/model/options failed")
        raise HTTPException(status_code=500, detail="Failed to list model options")


@app.get("/api/model/auxiliary")
def get_auxiliary_models():
    """Return current auxiliary task assignments.

    Shape:
      {
        "tasks": [
          {"task": "vision", "provider": "auto", "model": "", "base_url": ""},
          ...
        ],
        "main": {"provider": "openrouter", "model": "anthropic/claude-opus-4.7"},
      }
    """
    try:
        cfg = load_config()
        aux_cfg = cfg.get("auxiliary", {})
        if not isinstance(aux_cfg, dict):
            aux_cfg = {}

        tasks = []
        for slot in _AUX_TASK_SLOTS:
            slot_cfg = aux_cfg.get(slot, {}) if isinstance(aux_cfg.get(slot), dict) else {}
            tasks.append({
                "task": slot,
                "provider": str(slot_cfg.get("provider", "auto") or "auto"),
                "model": str(slot_cfg.get("model", "") or ""),
                "base_url": str(slot_cfg.get("base_url", "") or ""),
            })

        model_cfg = cfg.get("model", {})
        if isinstance(model_cfg, dict):
            main = {
                "provider": str(model_cfg.get("provider", "") or ""),
                "model": str(model_cfg.get("default", model_cfg.get("name", "")) or ""),
            }
        else:
            main = {"provider": "", "model": str(model_cfg) if model_cfg else ""}

        return {"tasks": tasks, "main": main}
    except Exception:
        _log.exception("GET /api/model/auxiliary failed")
        raise HTTPException(status_code=500, detail="Failed to read auxiliary config")


@app.post("/api/model/set")
async def set_model_assignment(body: ModelAssignment):
    """Assign a model to the main slot or an auxiliary task slot.

    Writes to ``~/.hermes/config.yaml`` — applies to **new** sessions only.
    The currently running chat PTY (if any) is not affected; use the
    ``/model`` slash command inside a chat to hot-swap that specific session.
    """
    scope = (body.scope or "").strip().lower()
    provider = (body.provider or "").strip()
    model = (body.model or "").strip()
    task = (body.task or "").strip().lower()

    if scope not in {"main", "auxiliary"}:
        raise HTTPException(status_code=400, detail="scope must be 'main' or 'auxiliary'")

    try:
        cfg = load_config()

        if scope == "main":
            if not provider or not model:
                raise HTTPException(status_code=400, detail="provider and model required for main")
            model_cfg = cfg.get("model", {})
            if not isinstance(model_cfg, dict):
                model_cfg = {}
            model_cfg["provider"] = provider
            model_cfg["default"] = model
            # Clear stale base_url so the resolver picks the provider's own default.
            if "base_url" in model_cfg and model_cfg.get("base_url"):
                model_cfg["base_url"] = ""
            # Also clear hardcoded context_length override — new model may have
            # a different context window.
            if "context_length" in model_cfg:
                model_cfg.pop("context_length", None)
            cfg["model"] = model_cfg
            save_config(cfg)
            return {"ok": True, "scope": "main", "provider": provider, "model": model}

        # scope == "auxiliary"
        aux = cfg.get("auxiliary")
        if not isinstance(aux, dict):
            aux = {}

        if task == "__reset__":
            # Reset every slot to provider="auto", model="" — keeps other fields intact.
            for slot in _AUX_TASK_SLOTS:
                slot_cfg = aux.get(slot)
                if not isinstance(slot_cfg, dict):
                    slot_cfg = {}
                slot_cfg["provider"] = "auto"
                slot_cfg["model"] = ""
                aux[slot] = slot_cfg
            cfg["auxiliary"] = aux
            save_config(cfg)
            return {"ok": True, "scope": "auxiliary", "reset": True}

        if not provider:
            raise HTTPException(status_code=400, detail="provider required for auxiliary")

        targets = [task] if task else list(_AUX_TASK_SLOTS)
        for slot in targets:
            if slot not in _AUX_TASK_SLOTS:
                raise HTTPException(status_code=400, detail=f"unknown auxiliary task: {slot}")
            slot_cfg = aux.get(slot)
            if not isinstance(slot_cfg, dict):
                slot_cfg = {}
            slot_cfg["provider"] = provider
            slot_cfg["model"] = model
            aux[slot] = slot_cfg

        cfg["auxiliary"] = aux
        save_config(cfg)
        return {
            "ok": True,
            "scope": "auxiliary",
            "tasks": targets,
            "provider": provider,
            "model": model,
        }
    except HTTPException:
        raise
    except Exception:
        _log.exception("POST /api/model/set failed")
        raise HTTPException(status_code=500, detail="Failed to save model assignment")




def _denormalize_config_from_web(config: Dict[str, Any]) -> Dict[str, Any]:
    """Reverse _normalize_config_for_web before saving.

    Reconstructs ``model`` as a dict by reading the current on-disk config
    to recover model subkeys (provider, base_url, api_mode, etc.) that were
    stripped from the GET response.  The frontend only sees model as a flat
    string; the rest is preserved transparently.

    Also handles ``model_context_length`` — writes it back into the model dict
    as ``context_length``.  A value of 0 or absent means "auto-detect" (omitted
    from the dict so get_model_context_length() uses its normal resolution).
    """
    config = dict(config)
    # Remove any _model_meta that might have leaked in (shouldn't happen
    # with the stripped GET response, but be defensive)
    config.pop("_model_meta", None)

    # Extract and remove model_context_length before processing model
    ctx_override = config.pop("model_context_length", 0)
    if not isinstance(ctx_override, int):
        try:
            ctx_override = int(ctx_override)
        except (TypeError, ValueError):
            ctx_override = 0

    model_val = config.get("model")
    if isinstance(model_val, str) and model_val:
        # Read the current disk config to recover model subkeys
        try:
            disk_config = load_config()
            disk_model = disk_config.get("model")
            if isinstance(disk_model, dict):
                # Preserve all subkeys, update default with the new value
                disk_model["default"] = model_val
                # Write context_length into the model dict (0 = remove/auto)
                if ctx_override > 0:
                    disk_model["context_length"] = ctx_override
                else:
                    disk_model.pop("context_length", None)
                config["model"] = disk_model
            # Model was previously a bare string — upgrade to dict if
            # user is setting a context_length override
            elif ctx_override > 0:
                config["model"] = {
                    "default": model_val,
                    "context_length": ctx_override,
                }
        except Exception:
            pass  # can't read disk config — just use the string form
    return config


@app.put("/api/config")
async def update_config(body: ConfigUpdate):
    try:
        save_config(_denormalize_config_from_web(body.config))
        return {"ok": True}
    except Exception:
        _log.exception("PUT /api/config failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/env")
async def get_env_vars():
    env_on_disk = load_env()
    result = {}
    for var_name, info in OPTIONAL_ENV_VARS.items():
        value = env_on_disk.get(var_name)
        result[var_name] = {
            "is_set": bool(value),
            "redacted_value": redact_key(value) if value else None,
            "description": info.get("description", ""),
            "url": info.get("url"),
            "category": info.get("category", ""),
            "is_password": info.get("password", False),
            "tools": info.get("tools", []),
            "advanced": info.get("advanced", False),
        }
    return result


@app.put("/api/env")
async def set_env_var(body: EnvVarUpdate):
    try:
        save_env_value(body.key, body.value)
        return {"ok": True, "key": body.key}
    except ValueError as exc:
        # save_env_value raises ValueError for invalid names and for keys
        # on the denylist (LD_PRELOAD, PATH, PYTHONPATH, …). Surface the
        # message to the SPA so the user understands why the write was
        # refused instead of seeing an opaque 500.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        _log.exception("PUT /api/env failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/api/env")
async def remove_env_var(body: EnvVarDelete):
    try:
        removed = remove_env_value(body.key)
        if not removed:
            raise HTTPException(status_code=404, detail=f"{body.key} not found in .env")
        return {"ok": True, "key": body.key}
    except HTTPException:
        raise
    except Exception:
        _log.exception("DELETE /api/env failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/env/reveal")
async def reveal_env_var(body: EnvVarReveal, request: Request):
    """Return the real (unredacted) value of a single env var.

    Protected by:
    - Ephemeral session token (generated per server start, injected into SPA)
    - Rate limiting (max 5 reveals per 30s window)
    - Audit logging
    """
    # --- Token check ---
    _require_token(request)

    # --- Rate limit ---
    now = time.time()
    cutoff = now - _REVEAL_WINDOW_SECONDS
    _reveal_timestamps[:] = [t for t in _reveal_timestamps if t > cutoff]
    if len(_reveal_timestamps) >= _REVEAL_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="Too many reveal requests. Try again shortly.")
    _reveal_timestamps.append(now)

    # --- Reveal ---
    env_on_disk = load_env()
    value = env_on_disk.get(body.key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"{body.key} not found in .env")

    _log.info("env/reveal: %s", body.key)
    return {"key": body.key, "value": value}


# ---------------------------------------------------------------------------
# OAuth provider endpoints — status + disconnect (Phase 1)
# ---------------------------------------------------------------------------
#
# Phase 1 surfaces *which OAuth providers exist* and whether each is
# connected, plus a disconnect button. The actual login flow (PKCE for
# Anthropic, device-code for Nous/Codex) still runs in the CLI for now;
# Phase 2 will add in-browser flows. For unconnected providers we return
# the canonical ``hermes auth add <provider>`` command so the dashboard
# can surface a one-click copy.


def _truncate_token(value: Optional[str], visible: int = 6) -> str:
    """Return ``...XXXXXX`` (last N chars) for safe display in the UI.

    We never expose more than the trailing ``visible`` characters of an
    OAuth access token. JWT prefixes (the part before the first dot) are
    stripped first when present so the visible suffix is always part of
    the signing region rather than a meaningless header chunk.

    Returns the Entra-ID placeholder when handed a callable (Azure Foundry
    bearer provider) — the callable is NEVER invoked here.
    """
    if not value:
        return ""
    if callable(value) and not isinstance(value, str):
        # Entra ID bearer provider — never reveal a minted token in the UI.
        return "<entra-id-bearer>"
    s = str(value)
    if "." in s and s.count(".") >= 2:
        # Looks like a JWT — show the trailing piece of the signature only.
        s = s.rsplit(".", 1)[-1]
    if len(s) <= visible:
        return s
    return f"…{s[-visible:]}"


def _anthropic_oauth_status() -> Dict[str, Any]:
    """Combined status across the three Anthropic credential sources we read.

    Hermes resolves Anthropic creds in this order at runtime:
    1. ``~/.hermes/.anthropic_oauth.json`` — Hermes-managed PKCE flow
    2. ``~/.claude/.credentials.json`` — Claude Code CLI credentials (auto)
    3. ``ANTHROPIC_TOKEN`` / ``ANTHROPIC_API_KEY`` env vars
    The dashboard reports the highest-priority source that's actually present.
    """
    try:
        from agent.anthropic_adapter import (
            read_hermes_oauth_credentials,
            read_claude_code_credentials,
            _HERMES_OAUTH_FILE,
        )
    except ImportError:
        read_claude_code_credentials = None  # type: ignore
        read_hermes_oauth_credentials = None  # type: ignore
        _HERMES_OAUTH_FILE = None  # type: ignore

    hermes_creds = None
    if read_hermes_oauth_credentials:
        try:
            hermes_creds = read_hermes_oauth_credentials()
        except Exception:
            hermes_creds = None
    if hermes_creds and hermes_creds.get("accessToken"):
        return {
            "logged_in": True,
            "source": "hermes_pkce",
            "source_label": f"Hermes PKCE ({_HERMES_OAUTH_FILE})",
            "token_preview": _truncate_token(hermes_creds.get("accessToken")),
            "expires_at": hermes_creds.get("expiresAt"),
            "has_refresh_token": bool(hermes_creds.get("refreshToken")),
        }

    cc_creds = None
    if read_claude_code_credentials:
        try:
            cc_creds = read_claude_code_credentials()
        except Exception:
            cc_creds = None
    if cc_creds and cc_creds.get("accessToken"):
        return {
            "logged_in": True,
            "source": "claude_code",
            "source_label": "Claude Code (~/.claude/.credentials.json)",
            "token_preview": _truncate_token(cc_creds.get("accessToken")),
            "expires_at": cc_creds.get("expiresAt"),
            "has_refresh_token": bool(cc_creds.get("refreshToken")),
        }

    env_token = os.getenv("ANTHROPIC_TOKEN") or os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        return {
            "logged_in": True,
            "source": "env_var",
            "source_label": "ANTHROPIC_TOKEN environment variable",
            "token_preview": _truncate_token(env_token),
            "expires_at": None,
            "has_refresh_token": False,
        }
    return {"logged_in": False, "source": None}


def _claude_code_only_status() -> Dict[str, Any]:
    """Surface Claude Code CLI credentials as their own provider entry.

    Independent of the Anthropic entry above so users can see whether their
    Claude Code subscription tokens are actively flowing into Hermes even
    when they also have a separate Hermes-managed PKCE login.
    """
    try:
        from agent.anthropic_adapter import read_claude_code_credentials
        creds = read_claude_code_credentials()
    except Exception:
        creds = None
    if creds and creds.get("accessToken"):
        return {
            "logged_in": True,
            "source": "claude_code_cli",
            "source_label": "~/.claude/.credentials.json",
            "token_preview": _truncate_token(creds.get("accessToken")),
            "expires_at": creds.get("expiresAt"),
            "has_refresh_token": bool(creds.get("refreshToken")),
        }
    return {"logged_in": False, "source": None}


# Provider catalog. The order matters — it's how we render the UI list.
# ``cli_command`` is what the dashboard surfaces as the copy-to-clipboard
# fallback while Phase 2 (in-browser flows) isn't built yet.
# ``flow`` describes the OAuth shape so the future modal can pick the
# right UI: ``pkce`` = open URL + paste callback code, ``device_code`` =
# show code + verification URL + poll, ``external`` = read-only (delegated
# to a third-party CLI like Claude Code or Qwen).
_OAUTH_PROVIDER_CATALOG: tuple[Dict[str, Any], ...] = (
    {
        "id": "anthropic",
        "name": "Anthropic (Claude API)",
        "flow": "pkce",
        "cli_command": "hermes auth add anthropic",
        "docs_url": "https://docs.claude.com/en/api/getting-started",
        "status_fn": _anthropic_oauth_status,
    },
    {
        "id": "claude-code",
        "name": "Claude Code (subscription)",
        "flow": "external",
        "cli_command": "claude setup-token",
        "docs_url": "https://docs.claude.com/en/docs/claude-code",
        "status_fn": _claude_code_only_status,
    },
    {
        "id": "nous",
        "name": "Nous Portal",
        "flow": "device_code",
        "cli_command": "hermes auth add nous",
        "docs_url": "https://portal.nousresearch.com",
        "status_fn": None,  # dispatched via auth.get_nous_auth_status
    },
    {
        "id": "openai-codex",
        "name": "OpenAI Codex (ChatGPT)",
        "flow": "device_code",
        "cli_command": "hermes auth add openai-codex",
        "docs_url": "https://platform.openai.com/docs",
        "status_fn": None,  # dispatched via auth.get_codex_auth_status
    },
    {
        "id": "qwen-oauth",
        "name": "Qwen (via Qwen CLI)",
        "flow": "external",
        "cli_command": "hermes auth add qwen-oauth",
        "docs_url": "https://github.com/QwenLM/qwen-code",
        "status_fn": None,  # dispatched via auth.get_qwen_auth_status
    },
    {
        "id": "minimax-oauth",
        "name": "MiniMax (OAuth)",
        # MiniMax's flow is structurally device-code (verification URI +
        # user code, backend polls the token endpoint) with a PKCE
        # extension for code-binding. The dashboard renders the same UX
        # as Nous's device-code flow; the PKCE bit is a security
        # extension that doesn't change the operator experience.
        "flow": "device_code",
        "cli_command": "hermes auth add minimax-oauth",
        "docs_url": "https://www.minimax.io",
        "status_fn": None,  # dispatched via auth.get_minimax_oauth_auth_status
    },
)


def _resolve_provider_status(provider_id: str, status_fn) -> Dict[str, Any]:
    """Dispatch to the right status helper for an OAuth provider entry."""
    if status_fn is not None:
        try:
            return status_fn()
        except Exception as e:
            return {"logged_in": False, "error": str(e)}
    try:
        from hermes_cli import auth as hauth
        if provider_id == "nous":
            raw = hauth.get_nous_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": "nous_portal",
                "source_label": raw.get("portal_base_url") or "Nous Portal",
                "token_preview": _truncate_token(raw.get("access_token")),
                "expires_at": raw.get("access_expires_at"),
                "has_refresh_token": bool(raw.get("has_refresh_token")),
            }
        if provider_id == "openai-codex":
            raw = hauth.get_codex_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": raw.get("source") or "openai_codex",
                "source_label": raw.get("auth_mode") or "OpenAI Codex",
                "token_preview": _truncate_token(raw.get("api_key")),
                "expires_at": None,
                "has_refresh_token": False,
                "last_refresh": raw.get("last_refresh"),
            }
        if provider_id == "qwen-oauth":
            raw = hauth.get_qwen_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": "qwen_cli",
                "source_label": raw.get("auth_store_path") or "Qwen CLI",
                "token_preview": _truncate_token(raw.get("access_token")),
                "expires_at": raw.get("expires_at"),
                "has_refresh_token": bool(raw.get("has_refresh_token")),
            }
        if provider_id == "minimax-oauth":
            raw = hauth.get_minimax_oauth_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": "minimax_oauth",
                "source_label": f"MiniMax ({raw.get('region', 'global')})",
                "token_preview": None,
                "expires_at": raw.get("expires_at"),
                "has_refresh_token": True,
            }
    except Exception as e:
        return {"logged_in": False, "error": str(e)}
    return {"logged_in": False}


@app.get("/api/providers/oauth")
async def list_oauth_providers():
    """Enumerate every OAuth-capable LLM provider with current status.

    Response shape (per provider):
        id              stable identifier (used in DELETE path)
        name            human label
        flow            "pkce" | "device_code" | "external"
        cli_command     fallback CLI command for users to run manually
        docs_url        external docs/portal link for the "Learn more" link
        status:
          logged_in        bool — currently has usable creds
          source           short slug ("hermes_pkce", "claude_code", ...)
          source_label     human-readable origin (file path, env var name)
          token_preview    last N chars of the token, never the full token
          expires_at       ISO timestamp string or null
          has_refresh_token bool
    """
    providers = []
    for p in _OAUTH_PROVIDER_CATALOG:
        status = _resolve_provider_status(p["id"], p.get("status_fn"))
        providers.append({
            "id": p["id"],
            "name": p["name"],
            "flow": p["flow"],
            "cli_command": p["cli_command"],
            "docs_url": p["docs_url"],
            "status": status,
        })
    return {"providers": providers}


@app.delete("/api/providers/oauth/{provider_id}")
async def disconnect_oauth_provider(provider_id: str, request: Request):
    """Disconnect an OAuth provider. Token-protected (matches /env/reveal)."""
    _require_token(request)

    valid_ids = {p["id"] for p in _OAUTH_PROVIDER_CATALOG}
    if provider_id not in valid_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider_id}. "
                   f"Available: {', '.join(sorted(valid_ids))}",
        )

    # Anthropic and claude-code clear the same Hermes-managed PKCE file
    # AND forget the Claude Code import. We don't touch ~/.claude/* directly
    # — that's owned by the Claude Code CLI; users can re-auth there if they
    # want to undo a disconnect.
    if provider_id in {"anthropic", "claude-code"}:
        try:
            from agent.anthropic_adapter import _HERMES_OAUTH_FILE
            if _HERMES_OAUTH_FILE.exists():
                _HERMES_OAUTH_FILE.unlink()
        except Exception:
            pass
        # Also clear the credential pool entry if present.
        try:
            from hermes_cli.auth import clear_provider_auth
            clear_provider_auth("anthropic")
        except Exception:
            pass
        _log.info("oauth/disconnect: %s", provider_id)
        return {"ok": True, "provider": provider_id}

    try:
        from hermes_cli.auth import clear_provider_auth
        cleared = clear_provider_auth(provider_id)
        _log.info("oauth/disconnect: %s (cleared=%s)", provider_id, cleared)
        return {"ok": bool(cleared), "provider": provider_id}
    except Exception as e:
        _log.exception("disconnect %s failed", provider_id)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# OAuth Phase 2 — in-browser PKCE & device-code flows
# ---------------------------------------------------------------------------
#
# Two flow shapes are supported:
#
#   PKCE (Anthropic):
#     1. POST /api/providers/oauth/anthropic/start
#          → server generates code_verifier + challenge, builds claude.ai
#            authorize URL, stashes verifier in _oauth_sessions[session_id]
#          → returns { session_id, flow: "pkce", auth_url }
#     2. UI opens auth_url in a new tab. User authorizes, copies code.
#     3. POST /api/providers/oauth/anthropic/submit { session_id, code }
#          → server exchanges (code + verifier) → tokens at console.anthropic.com
#          → persists to ~/.hermes/.anthropic_oauth.json AND credential pool
#          → returns { ok: true, status: "approved" }
#
#   Device code (Nous, OpenAI Codex):
#     1. POST /api/providers/oauth/{nous|openai-codex}/start
#          → server hits provider's device-auth endpoint
#          → gets { user_code, verification_url, device_code, interval, expires_in }
#          → spawns background poller thread that polls the token endpoint
#            every `interval` seconds until approved/expired
#          → stores poll status in _oauth_sessions[session_id]
#          → returns { session_id, flow: "device_code", user_code,
#                      verification_url, expires_in, poll_interval }
#     2. UI opens verification_url in a new tab and shows user_code.
#     3. UI polls GET /api/providers/oauth/{provider}/poll/{session_id}
#          every 2s until status != "pending".
#     4. On "approved" the background thread has already saved creds; UI
#        refreshes the providers list.
#
# Sessions are kept in-memory only (single-process FastAPI) and time out
# after 15 minutes. A periodic cleanup runs on each /start call to GC
# expired sessions so the dict doesn't grow without bound.

_OAUTH_SESSION_TTL_SECONDS = 15 * 60
_oauth_sessions: Dict[str, Dict[str, Any]] = {}
_oauth_sessions_lock = threading.Lock()

# Import OAuth constants from canonical source instead of duplicating.
# Guarded so hermes web still starts if anthropic_adapter is unavailable;
# Phase 2 endpoints will return 501 in that case.
try:
    from agent.anthropic_adapter import (
        _OAUTH_CLIENT_ID as _ANTHROPIC_OAUTH_CLIENT_ID,
        _OAUTH_TOKEN_URL as _ANTHROPIC_OAUTH_TOKEN_URL,
        _OAUTH_REDIRECT_URI as _ANTHROPIC_OAUTH_REDIRECT_URI,
        _OAUTH_SCOPES as _ANTHROPIC_OAUTH_SCOPES,
        _generate_pkce as _generate_pkce_pair,
    )
    _ANTHROPIC_OAUTH_AVAILABLE = True
except ImportError:
    _ANTHROPIC_OAUTH_AVAILABLE = False
_ANTHROPIC_OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"


def _gc_oauth_sessions() -> None:
    """Drop expired sessions. Called opportunistically on /start."""
    cutoff = time.time() - _OAUTH_SESSION_TTL_SECONDS
    with _oauth_sessions_lock:
        stale = [sid for sid, sess in _oauth_sessions.items() if sess["created_at"] < cutoff]
        for sid in stale:
            _oauth_sessions.pop(sid, None)


def _new_oauth_session(provider_id: str, flow: str) -> tuple[str, Dict[str, Any]]:
    """Create + register a new OAuth session, return (session_id, session_dict)."""
    sid = secrets.token_urlsafe(16)
    sess = {
        "session_id": sid,
        "provider": provider_id,
        "flow": flow,
        "created_at": time.time(),
        "status": "pending",  # pending | approved | denied | expired | error
        "error_message": None,
    }
    with _oauth_sessions_lock:
        _oauth_sessions[sid] = sess
    return sid, sess


def _save_anthropic_oauth_creds(access_token: str, refresh_token: str, expires_at_ms: int) -> None:
    """Persist Anthropic PKCE creds to both Hermes file AND credential pool.

    Mirrors what auth_commands.add_command does so the dashboard flow leaves
    the system in the same state as ``hermes auth add anthropic``.
    """
    from agent.anthropic_adapter import _HERMES_OAUTH_FILE
    payload = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at_ms,
    }
    _HERMES_OAUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _HERMES_OAUTH_FILE.with_name(
        f"{_HERMES_OAUTH_FILE.name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
    )
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, _HERMES_OAUTH_FILE)
        try:
            _HERMES_OAUTH_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
    # Best-effort credential-pool insert. Failure here doesn't invalidate
    # the file write — pool registration only matters for the rotation
    # strategy, not for runtime credential resolution.
    try:
        from agent.credential_pool import (
            PooledCredential,
            load_pool,
            AUTH_TYPE_OAUTH,
            SOURCE_MANUAL,
        )
        import uuid
        pool = load_pool("anthropic")
        # Avoid duplicate entries: delete any prior dashboard-issued OAuth entry
        existing = [e for e in pool.entries() if getattr(e, "source", "").startswith(f"{SOURCE_MANUAL}:dashboard_pkce")]
        for e in existing:
            try:
                pool.remove_entry(getattr(e, "id", ""))
            except Exception:
                pass
        entry = PooledCredential(
            provider="anthropic",
            id=uuid.uuid4().hex[:6],
            label="dashboard PKCE",
            auth_type=AUTH_TYPE_OAUTH,
            priority=0,
            source=f"{SOURCE_MANUAL}:dashboard_pkce",
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at_ms=expires_at_ms,
        )
        pool.add_entry(entry)
    except Exception as e:
        _log.warning("anthropic pool add (dashboard) failed: %s", e)


def _start_anthropic_pkce() -> Dict[str, Any]:
    """Begin PKCE flow. Returns the auth URL the UI should open."""
    if not _ANTHROPIC_OAUTH_AVAILABLE:
        raise HTTPException(status_code=501, detail="Anthropic OAuth not available (missing adapter)")
    verifier, challenge = _generate_pkce_pair()
    sid, sess = _new_oauth_session("anthropic", "pkce")
    sess["verifier"] = verifier
    sess["state"] = verifier  # Anthropic round-trips verifier as state
    params = {
        "code": "true",
        "client_id": _ANTHROPIC_OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _ANTHROPIC_OAUTH_REDIRECT_URI,
        "scope": _ANTHROPIC_OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    auth_url = f"{_ANTHROPIC_OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    return {
        "session_id": sid,
        "flow": "pkce",
        "auth_url": auth_url,
        "expires_in": _OAUTH_SESSION_TTL_SECONDS,
    }


def _submit_anthropic_pkce(session_id: str, code_input: str) -> Dict[str, Any]:
    """Exchange authorization code for tokens. Persists on success."""
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess or sess["provider"] != "anthropic" or sess["flow"] != "pkce":
        raise HTTPException(status_code=404, detail="Unknown or expired session")
    if sess["status"] != "pending":
        return {"ok": False, "status": sess["status"], "message": sess.get("error_message")}

    # Anthropic's redirect callback page formats the code as `<code>#<state>`.
    # Strip the state suffix if present (we already have the verifier server-side).
    parts = code_input.strip().split("#", 1)
    code = parts[0].strip()
    if not code:
        return {"ok": False, "status": "error", "message": "No code provided"}
    state_from_callback = parts[1] if len(parts) > 1 else ""

    exchange_data = json.dumps({
        "grant_type": "authorization_code",
        "client_id": _ANTHROPIC_OAUTH_CLIENT_ID,
        "code": code,
        "state": state_from_callback or sess["state"],
        "redirect_uri": _ANTHROPIC_OAUTH_REDIRECT_URI,
        "code_verifier": sess["verifier"],
    }).encode()
    req = urllib.request.Request(
        _ANTHROPIC_OAUTH_TOKEN_URL,
        data=exchange_data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "hermes-dashboard/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = f"Token exchange failed: {e}"
        return {"ok": False, "status": "error", "message": sess["error_message"]}

    access_token = result.get("access_token", "")
    refresh_token = result.get("refresh_token", "")
    expires_in = int(result.get("expires_in") or 3600)
    if not access_token:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = "No access token returned"
        return {"ok": False, "status": "error", "message": sess["error_message"]}

    expires_at_ms = int(time.time() * 1000) + (expires_in * 1000)
    try:
        _save_anthropic_oauth_creds(access_token, refresh_token, expires_at_ms)
    except Exception as e:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = f"Save failed: {e}"
        return {"ok": False, "status": "error", "message": sess["error_message"]}
    with _oauth_sessions_lock:
        sess["status"] = "approved"
    _log.info("oauth/pkce: anthropic login completed (session=%s)", session_id)
    return {"ok": True, "status": "approved"}


async def _start_device_code_flow(provider_id: str) -> Dict[str, Any]:
    """Initiate a device-code flow (Nous, OpenAI Codex, or MiniMax).

    Calls the provider's device-auth endpoint via the existing CLI helpers,
    then spawns a background poller. Returns the user-facing display fields
    so the UI can render the verification page link + user code.
    """
    if provider_id == "nous":
        from hermes_cli.auth import (
            _nous_device_scope_with_env_override,
            _request_nous_device_code_with_scope_fallback,
            PROVIDER_REGISTRY,
        )
        import httpx
        pconfig = PROVIDER_REGISTRY["nous"]
        portal_base_url = (
            os.getenv("HERMES_PORTAL_BASE_URL")
            or os.getenv("NOUS_PORTAL_BASE_URL")
            or pconfig.portal_base_url
        ).rstrip("/")
        client_id = pconfig.client_id
        scope, explicit_scope = _nous_device_scope_with_env_override(
            None,
            default_scope=pconfig.scope,
        )

        def _do_nous_device_request():
            with httpx.Client(
                timeout=httpx.Timeout(15.0),
                headers={"Accept": "application/json"},
            ) as client:
                return _request_nous_device_code_with_scope_fallback(
                    client=client,
                    portal_base_url=portal_base_url,
                    client_id=client_id,
                    scope=scope,
                    allow_legacy_fallback=not explicit_scope,
                )

        device_data, effective_scope = await asyncio.get_running_loop().run_in_executor(
            None, _do_nous_device_request
        )
        sid, sess = _new_oauth_session("nous", "device_code")
        sess["device_code"] = str(device_data["device_code"])
        sess["interval"] = int(device_data["interval"])
        sess["expires_at"] = time.time() + int(device_data["expires_in"])
        sess["portal_base_url"] = portal_base_url
        sess["client_id"] = client_id
        sess["scope"] = effective_scope
        threading.Thread(
            target=_nous_poller, args=(sid,), daemon=True, name=f"oauth-poll-{sid[:6]}"
        ).start()
        return {
            "session_id": sid,
            "flow": "device_code",
            "user_code": str(device_data["user_code"]),
            "verification_url": str(device_data["verification_uri_complete"]),
            "expires_in": int(device_data["expires_in"]),
            "poll_interval": int(device_data["interval"]),
        }

    if provider_id == "openai-codex":
        # Codex uses fixed OpenAI device-auth endpoints; reuse the helper.
        sid, _ = _new_oauth_session("openai-codex", "device_code")
        # Use the helper but in a thread because it polls inline.
        # We can't extract just the start step without refactoring auth.py,
        # so we run the full helper in a worker and proxy the user_code +
        # verification_url back via the session dict. The helper prints
        # to stdout — we capture nothing here, just status.
        threading.Thread(
            target=_codex_full_login_worker, args=(sid,), daemon=True,
            name=f"oauth-codex-{sid[:6]}",
        ).start()
        # Block briefly until the worker has populated the user_code, OR error.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with _oauth_sessions_lock:
                s = _oauth_sessions.get(sid)
            if s and (s.get("user_code") or s["status"] != "pending"):
                break
            await asyncio.sleep(0.1)
        with _oauth_sessions_lock:
            s = _oauth_sessions.get(sid, {})
        if s.get("status") == "error":
            raise HTTPException(status_code=500, detail=s.get("error_message") or "device-auth failed")
        if not s.get("user_code"):
            raise HTTPException(status_code=504, detail="device-auth timed out before returning a user code")
        return {
            "session_id": sid,
            "flow": "device_code",
            "user_code": s["user_code"],
            "verification_url": s["verification_url"],
            "expires_in": int(s.get("expires_in") or 900),
            "poll_interval": int(s.get("interval") or 5),
        }

    if provider_id == "minimax-oauth":
        # MiniMax uses a device-code-style flow (verification URI + user
        # code + background poll) with a PKCE extension on top. From the
        # operator's perspective it's identical to Nous's device-code
        # flow; the PKCE bit (verifier + challenge from
        # _minimax_pkce_pair) is a security extension that binds the
        # token exchange to the original session.
        from hermes_cli.auth import (
            _minimax_pkce_pair,
            _minimax_request_user_code,
            MINIMAX_OAUTH_CLIENT_ID,
            MINIMAX_OAUTH_GLOBAL_BASE,
        )
        import httpx
        verifier, challenge, state = _minimax_pkce_pair()
        portal_base_url = (
            os.getenv("MINIMAX_PORTAL_BASE_URL") or MINIMAX_OAUTH_GLOBAL_BASE
        ).rstrip("/")
        def _do_minimax_request():
            with httpx.Client(
                timeout=httpx.Timeout(15.0),
                headers={"Accept": "application/json"},
                follow_redirects=True,
            ) as client:
                return _minimax_request_user_code(
                    client=client,
                    portal_base_url=portal_base_url,
                    client_id=MINIMAX_OAUTH_CLIENT_ID,
                    code_challenge=challenge,
                    state=state,
                )
        device_data = await asyncio.get_event_loop().run_in_executor(
            None, _do_minimax_request
        )
        sid, sess = _new_oauth_session("minimax-oauth", "device_code")
        # The CLI flow names this `interval_ms` because MiniMax's
        # `interval` field is in milliseconds (defensive default 2000ms
        # in _minimax_poll_token).
        interval_raw = device_data.get("interval")
        sess["interval_ms"] = (
            int(interval_raw) if interval_raw is not None else None
        )
        sess["user_code"] = str(device_data["user_code"])
        sess["code_verifier"] = verifier
        sess["state"] = state
        sess["portal_base_url"] = portal_base_url
        sess["client_id"] = MINIMAX_OAUTH_CLIENT_ID
        sess["region"] = "global"
        # `expired_in` from MiniMax is overloaded — could be a unix-ms
        # timestamp OR a seconds-from-now duration. Mirror the heuristic
        # in _minimax_poll_token. Stash the raw value for the poller;
        # compute a derived expires_at + UI-friendly expires_in seconds.
        expired_in_raw = int(device_data["expired_in"])
        sess["expired_in_raw"] = expired_in_raw
        if expired_in_raw > 1_000_000_000_000:  # likely unix-ms
            expires_at_ts = expired_in_raw / 1000.0
            expires_in_seconds = max(0, int(expires_at_ts - time.time()))
        else:
            expires_at_ts = time.time() + expired_in_raw
            expires_in_seconds = expired_in_raw
        sess["expires_at"] = expires_at_ts
        threading.Thread(
            target=_minimax_poller,
            args=(sid,),
            daemon=True,
            name=f"oauth-poll-{sid[:6]}",
        ).start()
        return {
            "session_id": sid,
            "flow": "device_code",
            "user_code": str(device_data["user_code"]),
            "verification_url": str(device_data["verification_uri"]),
            "expires_in": expires_in_seconds,
            "poll_interval": max(2, (sess["interval_ms"] or 2000) // 1000),
        }

    raise HTTPException(status_code=400, detail=f"Provider {provider_id} does not support device-code flow")


def _nous_poller(session_id: str) -> None:
    """Background poller that drives a Nous device-code flow to completion."""
    from hermes_cli.auth import (
        NOUS_INFERENCE_AUTH_MODE_FRESH,
        _poll_for_token,
        refresh_nous_oauth_from_state,
    )
    from datetime import datetime, timezone
    import httpx
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess:
        return
    portal_base_url = sess["portal_base_url"]
    client_id = sess["client_id"]
    device_code = sess["device_code"]
    interval = sess["interval"]
    scope = sess.get("scope")
    expires_in = max(60, int(sess["expires_at"] - time.time()))
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0), headers={"Accept": "application/json"}) as client:
            token_data = _poll_for_token(
                client=client,
                portal_base_url=portal_base_url,
                client_id=client_id,
                device_code=device_code,
                expires_in=expires_in,
                poll_interval=interval,
            )
        # Same post-processing as _nous_device_code_login (mint agent key)
        now = datetime.now(timezone.utc)
        token_ttl = int(token_data.get("expires_in") or 0)
        auth_state = {
            "portal_base_url": portal_base_url,
            "inference_base_url": token_data.get("inference_base_url"),
            "client_id": client_id,
            "scope": token_data.get("scope") or scope,
            "token_type": token_data.get("token_type", "Bearer"),
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "obtained_at": now.isoformat(),
            "expires_at": (
                datetime.fromtimestamp(now.timestamp() + token_ttl, tz=timezone.utc).isoformat()
                if token_ttl else None
            ),
            "expires_in": token_ttl,
        }
        full_state = refresh_nous_oauth_from_state(
            auth_state,
            min_key_ttl_seconds=300,
            timeout_seconds=15.0,
            force_refresh=False,
            inference_auth_mode=NOUS_INFERENCE_AUTH_MODE_FRESH,
        )
        from hermes_cli.auth import persist_nous_credentials
        persist_nous_credentials(full_state)
        with _oauth_sessions_lock:
            sess["status"] = "approved"
        _log.info("oauth/device: nous login completed (session=%s)", session_id)
    except Exception as e:
        _log.warning("nous device-code poll failed (session=%s): %s", session_id, e)
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = str(e)


def _minimax_poller(session_id: str) -> None:
    """Background poller that drives a MiniMax OAuth flow to completion.

    Mirrors `_nous_poller` but calls the MiniMax-specific token endpoint,
    which uses a PKCE-style ``code_verifier`` + ``user_code`` rather than
    the ``device_code`` field used by Nous. On success, builds the same
    auth_state dict that ``_minimax_oauth_login`` (the CLI flow) builds
    and persists via ``_minimax_save_auth_state`` — so the dashboard
    path leaves the system in the same state as
    ``hermes auth add minimax-oauth``.
    """
    from hermes_cli.auth import (
        _minimax_poll_token,
        _minimax_resolve_token_expiry_unix,
        _minimax_save_auth_state,
        MINIMAX_OAUTH_GLOBAL_INFERENCE,
        MINIMAX_OAUTH_SCOPE,
    )
    from datetime import datetime, timezone
    import httpx
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess:
        return
    portal_base_url = sess["portal_base_url"]
    client_id = sess["client_id"]
    user_code = sess["user_code"]
    code_verifier = sess["code_verifier"]
    interval_ms = sess.get("interval_ms")
    expired_in_raw = sess["expired_in_raw"]
    try:
        with httpx.Client(
            timeout=httpx.Timeout(15.0),
            headers={"Accept": "application/json"},
            follow_redirects=True,
        ) as client:
            token_data = _minimax_poll_token(
                client=client,
                portal_base_url=portal_base_url,
                client_id=client_id,
                user_code=user_code,
                code_verifier=code_verifier,
                expired_in=expired_in_raw,
                interval_ms=interval_ms,
            )
        # Build the auth_state dict in the same shape as the CLI flow's
        # `_minimax_oauth_login` so `_minimax_save_auth_state` writes
        # the canonical record. Region is fixed to "global" for the
        # dashboard path; cn-region operators can still use the CLI
        # flow which supports `--region cn`.
        now = datetime.now(timezone.utc)
        expires_at_ts = _minimax_resolve_token_expiry_unix(
            int(token_data["expired_in"]), now=now,
        )
        expires_in_s = max(0, int(expires_at_ts - now.timestamp()))
        auth_state = {
            "provider": "minimax-oauth",
            "region": sess.get("region", "global"),
            "portal_base_url": portal_base_url,
            "inference_base_url": MINIMAX_OAUTH_GLOBAL_INFERENCE,
            "client_id": client_id,
            "scope": MINIMAX_OAUTH_SCOPE,
            "token_type": token_data.get("token_type", "Bearer"),
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "resource_url": token_data.get("resource_url"),
            "obtained_at": now.isoformat(),
            "expires_at": datetime.fromtimestamp(
                expires_at_ts, tz=timezone.utc
            ).isoformat(),
            "expires_in": expires_in_s,
        }
        _minimax_save_auth_state(auth_state)
        with _oauth_sessions_lock:
            sess["status"] = "approved"
        _log.info("oauth/device: minimax login completed (session=%s)", session_id)
    except Exception as e:
        _log.warning("minimax device-code poll failed (session=%s): %s", session_id, e)
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = str(e)


def _codex_full_login_worker(session_id: str) -> None:
    """Run the complete OpenAI Codex device-code flow.

    Codex doesn't use the standard OAuth device-code endpoints; it has its
    own ``/api/accounts/deviceauth/usercode`` (JSON body, returns
    ``device_auth_id``) and ``/api/accounts/deviceauth/token`` (JSON body
    polled until 200). On success the response carries an
    ``authorization_code`` + ``code_verifier`` that get exchanged at
    CODEX_OAUTH_TOKEN_URL with grant_type=authorization_code.

    The flow is replicated inline (rather than calling
    _codex_device_code_login) because that helper prints/blocks/polls in a
    single function — we need to surface the user_code to the dashboard the
    moment we receive it, well before polling completes.
    """
    try:
        import httpx
        from hermes_cli.auth import (
            CODEX_OAUTH_CLIENT_ID,
            CODEX_OAUTH_TOKEN_URL,
            DEFAULT_CODEX_BASE_URL,
        )
        issuer = "https://auth.openai.com"

        # Step 1: request device code
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            resp = client.post(
                f"{issuer}/api/accounts/deviceauth/usercode",
                json={"client_id": CODEX_OAUTH_CLIENT_ID},
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"deviceauth/usercode returned {resp.status_code}")
        device_data = resp.json()
        user_code = device_data.get("user_code", "")
        device_auth_id = device_data.get("device_auth_id", "")
        poll_interval = max(3, int(device_data.get("interval", "5")))
        if not user_code or not device_auth_id:
            raise RuntimeError("device-code response missing user_code or device_auth_id")
        verification_url = f"{issuer}/codex/device"
        with _oauth_sessions_lock:
            sess = _oauth_sessions.get(session_id)
            if not sess:
                return
            sess["user_code"] = user_code
            sess["verification_url"] = verification_url
            sess["device_auth_id"] = device_auth_id
            sess["interval"] = poll_interval
            sess["expires_in"] = 15 * 60  # OpenAI's effective limit
            sess["expires_at"] = time.time() + sess["expires_in"]

        # Step 2: poll until authorized
        deadline = time.monotonic() + sess["expires_in"]
        code_resp = None
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            while time.monotonic() < deadline:
                time.sleep(poll_interval)
                poll = client.post(
                    f"{issuer}/api/accounts/deviceauth/token",
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                    headers={"Content-Type": "application/json"},
                )
                if poll.status_code == 200:
                    code_resp = poll.json()
                    break
                if poll.status_code in {403, 404}:
                    continue  # user hasn't authorized yet
                raise RuntimeError(f"deviceauth/token poll returned {poll.status_code}")

        if code_resp is None:
            with _oauth_sessions_lock:
                sess["status"] = "expired"
                sess["error_message"] = "Device code expired before approval"
            return

        # Step 3: exchange authorization_code for tokens
        authorization_code = code_resp.get("authorization_code", "")
        code_verifier = code_resp.get("code_verifier", "")
        if not authorization_code or not code_verifier:
            raise RuntimeError("device-auth response missing authorization_code/code_verifier")
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            token_resp = client.post(
                CODEX_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": authorization_code,
                    "redirect_uri": f"{issuer}/deviceauth/callback",
                    "client_id": CODEX_OAUTH_CLIENT_ID,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if token_resp.status_code != 200:
            raise RuntimeError(f"token exchange returned {token_resp.status_code}")
        tokens = token_resp.json()
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        if not access_token:
            raise RuntimeError("token exchange did not return access_token")

        # Persist via credential pool — same shape as auth_commands.add_command
        from agent.credential_pool import (
            PooledCredential,
            load_pool,
            AUTH_TYPE_OAUTH,
            SOURCE_MANUAL,
        )
        import uuid as _uuid
        pool = load_pool("openai-codex")
        base_url = (
            os.getenv("HERMES_CODEX_BASE_URL", "").strip().rstrip("/")
            or DEFAULT_CODEX_BASE_URL
        )
        entry = PooledCredential(
            provider="openai-codex",
            id=_uuid.uuid4().hex[:6],
            label="dashboard device_code",
            auth_type=AUTH_TYPE_OAUTH,
            priority=0,
            source=f"{SOURCE_MANUAL}:dashboard_device_code",
            access_token=access_token,
            refresh_token=refresh_token,
            base_url=base_url,
        )
        pool.add_entry(entry)
        with _oauth_sessions_lock:
            sess["status"] = "approved"
        _log.info("oauth/device: openai-codex login completed (session=%s)", session_id)
    except Exception as e:
        _log.warning("codex device-code worker failed (session=%s): %s", session_id, e)
        with _oauth_sessions_lock:
            s = _oauth_sessions.get(session_id)
            if s:
                s["status"] = "error"
                s["error_message"] = str(e)


@app.post("/api/providers/oauth/{provider_id}/start")
async def start_oauth_login(provider_id: str, request: Request):
    """Initiate an OAuth login flow. Token-protected."""
    _require_token(request)
    _gc_oauth_sessions()
    valid = {p["id"] for p in _OAUTH_PROVIDER_CATALOG}
    if provider_id not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown provider {provider_id}")
    catalog_entry = next(p for p in _OAUTH_PROVIDER_CATALOG if p["id"] == provider_id)
    if catalog_entry["flow"] == "external":
        raise HTTPException(
            status_code=400,
            detail=f"{provider_id} uses an external CLI; run `{catalog_entry['cli_command']}` manually",
        )
    try:
        # The pkce branch is gated on provider_id == "anthropic" because
        # `_start_anthropic_pkce()` is hardcoded to the Anthropic flow.
        # Routing any other future pkce-flagged provider through it would
        # silently launch the Anthropic OAuth flow (the bug fixed in this
        # change for MiniMax). New PKCE providers must add their own
        # start function and an explicit branch here.
        if catalog_entry["flow"] == "pkce" and provider_id == "anthropic":
            return _start_anthropic_pkce()
        if catalog_entry["flow"] == "device_code":
            return await _start_device_code_flow(provider_id)
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("oauth/start %s failed", provider_id)
        raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=400, detail="Unsupported flow")


class OAuthSubmitBody(BaseModel):
    session_id: str
    code: str


@app.post("/api/providers/oauth/{provider_id}/submit")
async def submit_oauth_code(provider_id: str, body: OAuthSubmitBody, request: Request):
    """Submit the auth code for PKCE flows. Token-protected."""
    _require_token(request)
    if provider_id == "anthropic":
        return await asyncio.get_running_loop().run_in_executor(
            None, _submit_anthropic_pkce, body.session_id, body.code,
        )
    raise HTTPException(status_code=400, detail=f"submit not supported for {provider_id}")


@app.get("/api/providers/oauth/{provider_id}/poll/{session_id}")
async def poll_oauth_session(provider_id: str, session_id: str):
    """Poll a device-code session's status (no auth — read-only state)."""
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    if sess["provider"] != provider_id:
        raise HTTPException(status_code=400, detail="Provider mismatch for session")
    return {
        "session_id": session_id,
        "status": sess["status"],
        "error_message": sess.get("error_message"),
        "expires_at": sess.get("expires_at"),
    }


@app.delete("/api/providers/oauth/sessions/{session_id}")
async def cancel_oauth_session(session_id: str, request: Request):
    """Cancel a pending OAuth session. Token-protected."""
    _require_token(request)
    with _oauth_sessions_lock:
        sess = _oauth_sessions.pop(session_id, None)
    if sess is None:
        return {"ok": False, "message": "session not found"}
    return {"ok": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# Session detail endpoints
# ---------------------------------------------------------------------------



def _session_latest_descendant(session_id: str):
    """Resolve a session id to the newest child leaf session.

    /model may create child sessions. Dashboard refresh should continue the
    newest child instead of reopening the old parent.
    """
    from hermes_state import SessionDB

    def row_get(row, key, index):
        if isinstance(row, dict):
            return row.get(key)
        try:
            return row[key]
        except Exception:
            try:
                return row[index]
            except Exception:
                return None

    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid or not db.get_session(sid):
            return None, []

        conn = (
            getattr(db, "conn", None)
            or getattr(db, "_conn", None)
            or getattr(db, "connection", None)
            or getattr(db, "_connection", None)
        )

        rows = []
        if conn is not None:
            raw_rows = conn.execute(
                "SELECT id, parent_session_id, started_at FROM sessions"
            ).fetchall()
            for row in raw_rows:
                rows.append({
                    "id": row_get(row, "id", 0),
                    "parent_session_id": row_get(row, "parent_session_id", 1),
                    "started_at": row_get(row, "started_at", 2),
                })
        else:
            rows = db.list_sessions_rich(limit=10000, offset=0)

        children = {}
        for row in rows:
            rid = row.get("id")
            parent = row.get("parent_session_id")
            if rid and parent:
                children.setdefault(parent, []).append(row)

        def started(row):
            try:
                return float(row.get("started_at") or 0)
            except Exception:
                return 0.0

        current = sid
        path = [sid]
        seen = {sid}

        while children.get(current):
            candidates = [r for r in children[current] if r.get("id") not in seen]
            if not candidates:
                break
            candidates.sort(key=started, reverse=True)
            current = candidates[0]["id"]
            path.append(current)
            seen.add(current)

        return current, path
    finally:
        db.close()

@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str):
    from hermes_state import SessionDB
    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        session = db.get_session(sid) if sid else None
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session
    finally:
        db.close()



@app.get("/api/sessions/{session_id}/latest-descendant")
async def get_session_latest_descendant(session_id: str):
    latest, path = _session_latest_descendant(session_id)
    if not latest:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "requested_session_id": path[0] if path else session_id,
        "session_id": latest,
        "path": path,
        "changed": bool(path and latest != path[0]),
    }

@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    from hermes_state import SessionDB
    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            raise HTTPException(status_code=404, detail="Session not found")
        messages = db.get_messages(sid)
        return {"session_id": sid, "messages": messages}
    finally:
        db.close()


@app.get("/api/sessions/{session_id}/subagents")
async def get_session_subagents(
    session_id: str,
    status: Optional[str] = None,
    limit: int = 100,
):
    from hermes_state import SessionDB

    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            raise HTTPException(status_code=404, detail="Session not found")
        runs = db.list_subagent_runs(
            parent_session_id=sid,
            status=status,
            limit=limit,
        )
        return {"runs": runs, "session_id": sid}
    finally:
        db.close()


@app.get("/api/subagents/{run_id}")
async def get_subagent_run_detail(run_id: str, events_limit: int = 200):
    from hermes_state import SessionDB

    db = SessionDB()
    try:
        run = db.get_subagent_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Subagent run not found")
        events = db.list_subagent_events(run_id, limit=events_limit)
        return {"events": events, "run": run}
    finally:
        db.close()


@app.delete("/api/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    from hermes_state import SessionDB
    db = SessionDB()
    try:
        if not db.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"ok": True}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Project management endpoints
# ---------------------------------------------------------------------------

import shutil as _shutil


def _project_doc_count(cwd_path: Path) -> int:
    """Count common legal project documents under a project's working dir."""
    if not cwd_path.is_dir():
        return 0
    doc_count = 0
    for pattern in ("*.docx", "*.DOCX", "*.pdf", "*.PDF"):
        try:
            doc_count += len(list(cwd_path.rglob(pattern)))
        except OSError:
            continue
    return doc_count


def _project_created_iso(value) -> str:
    """Normalize DB timestamps (float seconds) and metadata strings for the UI."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(value)))
        except Exception:
            return ""
    return str(value)


def _project_identity_candidates(project: dict) -> set[str]:
    values = {
        str(project.get("id") or ""),
        str(project.get("name") or ""),
        str(project.get("directory") or ""),
        str(project.get("cwd") or ""),
        str(project.get("management_dir") or ""),
    }
    return {v for v in values if v}


def _scan_projects_dir(base: str = "/data/projects") -> list[dict]:
    """Scan for .hermes-project/project-meta.json files across multiple roots.

    Returns projects with both 'cwd' (document working directory) and
    'management_dir' (sidecar/bootstrap directory).  'directory' is kept as
    a backwards-compatible alias for cwd because the WebUI file browser uses
    it as the document root.
    """
    projects = []
    for base_dir in ("/data/projects", "/workingfile", "/workspace"):
        try:
            root = Path(base_dir)
        except Exception:
            continue
        if not root.is_dir():
            continue
        for meta_path in sorted(root.rglob(".hermes-project/project-meta.json")):
            try:
                meta = json.loads(meta_path.read_text())
                mgmt_dir = str(meta_path.parent.parent)
                cwd = meta.get("cwd", mgmt_dir)
                cwd_path = Path(cwd) if cwd else Path(mgmt_dir)
                projects.append({
                    "id": mgmt_dir.replace("/", "_").lstrip("_"),
                    "name": meta.get("name", "Unnamed"),
                    "client": meta.get("client", ""),
                    "goal": meta.get("goal", ""),
                    "directory": str(cwd_path),
                    "cwd": str(cwd_path),
                    "management_dir": mgmt_dir,
                    "source": "scan",
                    "created": meta.get("created", ""),
                    "doc_count": _project_doc_count(cwd_path),
                })
            except Exception:
                continue
    # Deduplicate by project name — merge CWD info, prefer mgmt_dir from /workspace/
    seen = {}
    for p in projects:
        name = p["name"]
        if name in seen:
            existing = seen[name]
            # Keep the one that has separate cwd != directory (better data)
            if p.get("cwd") and p["cwd"] != p["directory"]:
                seen[name] = p
            elif existing.get("cwd") and existing["cwd"] != existing["directory"]:
                pass  # keep existing
            elif "/workingfile/" in p.get("cwd", "") and "/workingfile/" not in existing.get("cwd", ""):
                seen[name] = p
        else:
            seen[name] = p
    return list(seen.values())


def _list_projects() -> list[dict]:
    """Return projects from state.db first, with filesystem scan as fallback."""
    projects: list[dict] = []
    seen: set[str] = set()

    try:
        from hermes_state import SessionDB
        db = SessionDB()
        try:
            for row in db.list_projects():
                mgmt_dir = str(row.get("path") or "")
                cwd = str(row.get("cwd") or mgmt_dir)
                cwd_path = Path(cwd) if cwd else Path(mgmt_dir)
                project = {
                    "id": row.get("id"),
                    "name": row.get("name") or "Unnamed",
                    "client": row.get("client") or "",
                    "goal": row.get("goal") or "",
                    "directory": str(cwd_path),
                    "cwd": str(cwd_path),
                    "management_dir": mgmt_dir or str(cwd_path),
                    "status": row.get("status") or "",
                    "notes": row.get("notes") or "",
                    "source": "db",
                    "created": _project_created_iso(row.get("created_at")),
                    "updated": _project_created_iso(row.get("updated_at")),
                    "doc_count": _project_doc_count(cwd_path),
                }
                projects.append(project)
                seen.update(_project_identity_candidates(project))
        finally:
            db.close()
    except Exception:
        _log.debug("state.db project list unavailable; falling back to scan", exc_info=True)

    for project in _scan_projects_dir():
        candidates = _project_identity_candidates(project)
        if candidates & seen:
            continue
        projects.append(project)
        seen.update(candidates)

    return projects


def _session_text_matches(session: dict, tokens: set[str], paths: tuple[str, ...]) -> bool:
    """Best-effort project match for legacy sessions created before project binding.

    Older sessions may have no ``project_id`` or ``project_cwd``. In practice,
    their title or first user-message preview often contains either the project
    name or the working directory path, so include those fields when populating
    project session pickers.
    """
    haystack = " ".join(
        str(session.get(key) or "")
        for key in ("title", "preview", "project_cwd", "id")
    ).lower()
    if not haystack:
        return False
    if any(path and path.lower() in haystack for path in paths):
        return True
    return any(tok in haystack for tok in tokens if len(tok) >= 2)



def _resolve_project(project_id: str) -> dict | None:
    for project in _list_projects():
        if project_id in _project_identity_candidates(project):
            return project
    return None


@app.get("/api/projects")
async def get_projects():
    """List all projects discovered under /data/projects."""
    try:
        projects = _list_projects()
        total = len(projects)
        return {"projects": projects, "total": total}
    except Exception:
        _log.exception("GET /api/projects failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/projects")
async def create_project(request: Request):
    """Create a new legal project by scanning a source directory."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    project_name = body.get("project_name", "").strip()
    client_name = body.get("client_name", "").strip()
    goal = body.get("goal", "").strip()
    dir_path = body.get("dir_path", "").strip()
    language = body.get("language", "ch")
    recursive = body.get("recursive", False)
    in_place = body.get("in_place", False)

    if not project_name or not dir_path:
        raise HTTPException(status_code=400, detail="project_name and dir_path are required")

    try:
        from lexitool.project_init import scan_and_init_project
        from hermes_cli.project_commands import _register_in_db
        src = Path(dir_path)
        # Auto-detect: if source dir already has .hermes-project scaffolding, use in_place
        if not in_place and (src / ".hermes-project" / "project-meta.json").is_file():
            in_place = True
        management_dir = body.get("management_dir", "").strip() or None
        result = scan_and_init_project(
            dir_path=dir_path,
            project_name=project_name,
            client_name=client_name,
            goal=goal,
            language=language,
            recursive=recursive,
            management_dir=management_dir,
            in_place=in_place,
        )
        if result.get("ok"):
            # Register in DB with proper cwd/management_dir separation
            cwd = result.get("cwd", dir_path)
            mgmt_dir = result.get("project_dir", "")
            _register_in_db(project_name, mgmt_dir, client_name, goal, cwd)
            return result
        raise HTTPException(status_code=400, detail=result.get("error", "Project init failed"))
    except ImportError:
        raise HTTPException(status_code=500, detail="lexitool not installed")
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("POST /api/projects failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_id}")
async def get_project_detail(project_id: str):
    """Get project details including linked sessions."""
    try:
        from hermes_state import SessionDB
    except ImportError:
        raise HTTPException(status_code=500, detail="SessionDB unavailable")

    project = _resolve_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Find sessions linked to this project directory
    try:
        db = SessionDB()
        try:
            explicit_sessions = db.list_project_sessions(str(project.get("id") or project_id), limit=1000)
            all_sessions = db.list_sessions_rich(
                limit=10000,
                offset=0,
                include_children=False,
                order_by_last_active=True,
            )
            cwd = str(project.get("cwd") or project.get("directory") or "")
            mgmt_dir = str(project.get("management_dir") or "")
            project_paths = tuple(p for p in (cwd, mgmt_dir) if p)
            project_name = str(project.get("name") or "")
            # Tokenise project name for heuristic title matching (e.g. "五冶邯郸纾困项目" → ["五冶","邯郸","纾困","项目"])
            name_tokens: set[str] = set()
            for chunk in project_name.replace("（", "(").replace("）", ")").split():
                name_tokens.add(chunk.lower())
            # Also split on common delimiters for CJK-only names
            if len(name_tokens) <= 1 and project_name:
                import re
                # Split CJK names into bigrams for partial matching
                cjk_chars = re.findall(r'[一-鿿]{2,}', project_name)
                name_tokens.update(t.lower() for t in cjk_chars)

            linked_by_id: dict[str, dict] = {}

            def add_session(session: dict) -> None:
                sid = str(session.get("id") or "")
                if sid and sid not in linked_by_id:
                    linked_by_id[sid] = session

            for session in explicit_sessions:
                add_session(session)

            for session in all_sessions:
                session_project_cwd = str(session.get("project_cwd") or "")
                if (
                    session.get("project_id") == project.get("id")
                    or (cwd and session_project_cwd.startswith(cwd))
                    or (mgmt_dir and session_project_cwd.startswith(mgmt_dir))
                    # Heuristic: legacy sessions often have only title/preview.
                    or _session_text_matches(session, name_tokens, project_paths)
                ):
                    add_session(session)

            def sort_key(session: dict) -> float:
                try:
                    return float(session.get("last_active") or session.get("started_at") or 0)
                except Exception:
                    return 0.0

            linked = sorted(linked_by_id.values(), key=sort_key, reverse=True)
            project["sessions"] = linked
            project["session_count"] = len(linked)
        finally:
            db.close()
    except Exception:
        project["sessions"] = []
        project["session_count"] = 0

    return project


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project directory and all its contents."""
    project = _resolve_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_dir = Path(project.get("management_dir") or project["directory"])
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail="Project directory not found")

    # Safety: only delete if it contains a .hermes-project marker
    if not (project_dir / ".hermes-project").is_dir():
        raise HTTPException(status_code=400, detail="Not a valid hermes project (no .hermes-project/)")

    try:
        _shutil.rmtree(str(project_dir))
        try:
            from hermes_state import SessionDB
            db = SessionDB()
            try:
                db.delete_project(str(project.get("id")))
            finally:
                db.close()
        except Exception:
            _log.debug("Failed to delete project DB row", exc_info=True)
        return {"ok": True, "deleted": str(project_dir)}
    except Exception as e:
        _log.exception("DELETE /api/projects failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/projects/{project_id}/sessions")
async def create_project_session(project_id: str, request: Request):
    """Create a new chat session pre-associated with a project.

    The session is created in the project's management directory so that
    the agent starts with the project context available.  The caller can
    optionally pass a ``cwd`` override in the JSON body.
    """
    try:
        from hermes_state import SessionDB
    except ImportError:
        raise HTTPException(status_code=500, detail="SessionDB unavailable")

    project = _resolve_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    body: dict = {}
    try:
        body = await request.json() or {}
    except Exception:
        pass

    mgmt_dir = str(project.get("management_dir") or project.get("directory") or "")
    cwd = body.get("cwd") or mgmt_dir
    source = body.get("source") or "web-launchpad"
    session_id = f"session-{secrets.token_hex(6)}"

    try:
        db = SessionDB()
        try:
            db.create_session(session_id, source)
            db.set_session_project(session_id, project.get("id") or project_id,
                                   project_cwd=mgmt_dir)
        finally:
            db.close()
    except Exception as e:
        _log.exception("Failed to create session for project %s", project_id)
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "ok": True,
        "session_id": session_id,
        "project_id": project_id,
        "management_dir": mgmt_dir,
    }


# ---------------------------------------------------------------------------
# Legal Swarm dashboard endpoints
# ---------------------------------------------------------------------------

@app.get("/api/kanban/swarm/runs")
async def list_swarm_runs(board: str = ""):
    """List legal-swarm runs on a kanban board."""
    if not board:
        raise HTTPException(status_code=400, detail="Query parameter 'board' is required")

    try:
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_legal_swarm import list_runs
        conn = kb.connect(board=board)
        try:
            runs = list_runs(conn)
            return {"ok": True, "board": board, "runs": runs}
        finally:
            conn.close()
    except Exception as e:
        _log.exception("GET /api/kanban/swarm/runs failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/kanban/swarm/runs/all")
async def list_all_swarm_runs():
    """List legal-swarm runs across ALL kanban boards (no board param needed)."""
    try:
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_legal_swarm import list_runs

        all_runs: list[dict] = []
        boards = kb.list_boards()

        for bm in boards:
            slug = bm.get("slug", "")
            if not slug:
                continue
            try:
                conn = kb.connect(board=slug)
                try:
                    runs = list_runs(conn)
                    for r in runs:
                        r["board"] = r.get("board") or slug
                    all_runs.extend(runs)
                finally:
                    conn.close()
            except Exception:
                _log.warning("Failed to list runs for board %s", slug, exc_info=True)

        all_runs.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
        return {"ok": True, "runs": all_runs}
    except Exception as e:
        _log.exception("GET /api/kanban/swarm/runs/all failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/kanban/swarm/runs/{run_id}")
async def get_swarm_run_status(run_id: str, board: str = ""):
    """Get per-node status for a single swarm run on a kanban board."""
    if not board:
        raise HTTPException(status_code=400, detail="Query parameter 'board' is required")

    try:
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_legal_swarm import run_status, list_runs, get_run
        conn = kb.connect(board=board)
        try:
            # Find the root task for this run_id
            all_runs = list_runs(conn)
            root_id = ""
            workflow_id = ""
            for r in all_runs:
                if r.get("run_id") == run_id:
                    root_id = r["root_task_id"]
                    workflow_id = r.get("workflow_id", "")
                    break

            if not root_id:
                raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found on board '{board}'")

            # Get full status
            status = run_status(conn, root_id)

            # Get run metadata
            run = get_run(conn, workflow_id, run_id)
            if run:
                status["run"] = {
                    "workflow_id": run.workflow_id,
                    "run_id": run.run_id,
                    "board": run.board,
                    "root_task_id": run.root_task_id,
                    "node_count": len(run.node_mappings),
                }

            return status
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("GET /api/kanban/swarm/runs/%s failed", run_id)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Kanban event broadcast endpoint — gateway notifier → dashboard bridge.
# The gateway's kanban notifier POSTs here to fan out task status events to
# browser subscribers on the originating session's /api/events channel.
# ---------------------------------------------------------------------------

KANBAN_EVENT_TYPES = frozenset({
    "kanban.task.created",
    "kanban.task.claimed",
    "kanban.task.started",
    "kanban.task.progress",
    "kanban.task.handoff",
    "kanban.task.completed",
    "kanban.task.approved",
    "kanban.task.rejected",
    "kanban.task.blocked",
    "kanban.task.crashed",
    "kanban.task.timed_out",
    "kanban.batch.completed",
})


@app.post("/api/kanban/broadcast")
async def kanban_broadcast(request: Request):
    """Gateway kanban notifier calls this to broadcast kanban events.

    Body::
        {
            "channel": "<session_channel>",
            "event_type": "kanban.task.completed",
            "payload": { "task_id": "...", "title": "...", ... }
        }

    Security: only accepts from localhost (127.0.0.1 or ::1).
    """
    # --- Security: loopback only -----------------------------------------
    client_host = request.client.host if request.client else None
    if client_host not in ("127.0.0.1", "::1"):
        _log.warning("kanban broadcast rejected from non-localhost: %s", client_host)
        raise HTTPException(status_code=403, detail="loopback only")

    # --- Parse body ------------------------------------------------------
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    channel = body.get("channel")
    event_type = body.get("event_type")
    payload = body.get("payload")

    if not channel or not isinstance(channel, str):
        raise HTTPException(status_code=400, detail="missing or invalid 'channel'")
    if not event_type or event_type not in KANBAN_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="missing or unknown 'event_type'")
    if not payload or not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="missing or invalid 'payload'")

    # --- Broadcast to all subscribers on the channel ---------------------
    formatted = json.dumps({
        "method": "event",
        "params": {"type": event_type, "payload": payload},
    })

    await _broadcast_event(channel, formatted)

    # --- Count how many subscribers received it --------------------------
    async with _event_lock:
        subs = list(_event_channels.get(channel, ()))
    subscriber_count = len(subs)

    _log.debug(
        "kanban broadcast: %s on channel %s → %d subscriber(s)",
        event_type, channel, subscriber_count,
    )
    return {"ok": True, "subscribers": subscriber_count}


@app.post("/api/projects/{project_id}/workflows/{workflow_id}/compile")
async def compile_swarm_workflow(project_id: str, workflow_id: str, request: Request):
    """Compile a YAML workflow definition into a kanban swarm run."""
    project = _resolve_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    body: dict = {}
    try:
        body = await request.json() or {}
    except Exception:
        pass

    project_dir = str(project.get("cwd") or project.get("directory") or "")

    try:
        from hermes_cli.kanban_legal_swarm import compile_workflow
        run = compile_workflow(
            project_dir=project_dir,
            workflow_id=workflow_id,
            params=body.get("params") or {},
        )
        return {
            "ok": True,
            "board": run.board,
            "run_id": run.run_id,
            "workflow_id": run.workflow_id,
            "root_task_id": run.root_task_id,
            "node_count": len(run.node_mappings),
            "project_id": project_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("POST /api/projects/.../compile failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Project file browsing / upload / download endpoints
# ---------------------------------------------------------------------------

import mimetypes as _mimetypes


def _resolve_project_path(project_id: str) -> Path | None:
    """Resolve a project_id to its document working directory on disk."""
    project = _resolve_project(project_id)
    if not project:
        return None
    return Path(project.get("cwd") or project["directory"])


def _project_sidecar_dir(project_dir: Path) -> Path:
    """Resolve the .hermes-project sidecar for a document working directory."""
    local = project_dir / ".hermes-project"
    if local.is_dir():
        return local
    try:
        resolved = project_dir.resolve()
        for project in _list_projects():
            cwd = Path(project.get("cwd") or project.get("directory") or "")
            if cwd.resolve() == resolved:
                return Path(project.get("management_dir") or cwd) / ".hermes-project"
    except Exception:
        pass
    return local


def _safe_project_rel(project_dir: Path, rel: str) -> Path | None:
    """Resolve a relative path within the project directory safely.

    Returns None if the result escapes the project directory (path traversal).
    """
    sanitised = rel.lstrip("/").replace("\\", "/")
    resolved = (project_dir / sanitised).resolve()
    if not resolved.is_relative_to(project_dir.resolve()):
        return None
    return resolved


def _scan_project_files(base_dir: Path, rel_path: str = "") -> list[dict]:
    """List files and directories at the given relative path within base_dir."""
    target = base_dir.resolve()
    if rel_path:
        safe = _safe_project_rel(base_dir, rel_path)
        if safe is None:
            return []
        target = safe

    if not target.is_dir():
        return []

    entries: list[dict] = []
    try:
        for child in sorted(target.iterdir()):
            try:
                st = child.stat()
                entries.append({
                    "name": child.name,
                    "path": str(child.relative_to(base_dir)),
                    "size": st.st_size if child.is_file() else 0,
                    "modified": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime),
                    ),
                    "is_dir": child.is_dir(),
                })
            except OSError:
                continue
    except OSError:
        pass

    # Directories first, then files, both alphabetically
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


@app.get("/api/projects/{project_id}/files")
async def list_project_files(project_id: str, path: str = ""):
    """List files and directories within a project."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Reject path traversal attempts
    if path:
        safe = _safe_project_rel(project_dir, path)
        if safe is None:
            raise HTTPException(status_code=403, detail="Path traversal rejected")

    files = _scan_project_files(project_dir, path)
    current_path = path or ""

    return {
        "files": files,
        "current_path": current_path,
        "project_id": project_id,
        "project_name": project_dir.name,
    }


@app.post("/api/projects/{project_id}/files")
async def upload_project_files(
    project_id: str,
    request: Request,
    path: str = "",
):
    """Upload one or more files into a project directory."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Resolve target directory
    target_dir = project_dir
    if path:
        safe = _safe_project_rel(project_dir, path)
        if safe is None:
            raise HTTPException(status_code=403, detail="Path traversal rejected")
        target_dir = safe

    # Ensure target directory exists
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        form = await request.form()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid multipart form data")

    uploaded: list[dict] = []
    errors: list[dict] = []

    MAX_SIZE = 50 * 1024 * 1024  # 50 MB
    MAX_FILES = 10

    upload_fields = form.getlist("files")
    if len(upload_fields) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files — maximum {MAX_FILES} per request",
        )

    for field in upload_fields:
        if not hasattr(field, "filename"):
            errors.append({"filename": "(unknown)", "error": "Not a file"})
            continue

        raw_filename = field.filename or "unnamed"
        filename = Path(raw_filename).name
        if (
            not filename
            or filename in {".", ".."}
            or "/" in raw_filename
            or "\\" in raw_filename
            or "\x00" in raw_filename
        ):
            errors.append({"filename": raw_filename, "error": "Invalid filename"})
            continue
        # Read into memory to check size (FastAPI/Starlette streams to temp files
        # for large uploads, but we enforce a hard cap)
        content = await field.read()
        if len(content) > MAX_SIZE:
            errors.append({
                "filename": raw_filename,
                "error": f"File exceeds {MAX_SIZE // (1024*1024)} MB limit",
            })
            continue

        dest = (target_dir / filename).resolve()
        if not dest.is_relative_to(project_dir.resolve()):
            errors.append({"filename": raw_filename, "error": "Path traversal rejected"})
            continue
        try:
            dest.write_bytes(content)
            uploaded.append({"name": filename, "size": len(content)})
        except OSError as e:
            errors.append({"filename": raw_filename, "error": str(e)})

    return {"uploaded": uploaded, "errors": errors}


@app.get("/api/projects/{project_id}/files/download")
async def download_project_file(project_id: str, path: str = ""):
    """Download a file from a project directory."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if not path:
        raise HTTPException(status_code=400, detail="path query parameter required")

    safe = _safe_project_rel(project_dir, path)
    if safe is None:
        raise HTTPException(status_code=403, detail="Path traversal rejected")

    if not safe.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    mime_type, _ = _mimetypes.guess_type(str(safe))
    if not mime_type:
        mime_type = "application/octet-stream"

    return FileResponse(
        safe,
        media_type=mime_type,
        filename=safe.name,
        headers={"Content-Disposition": f'attachment; filename="{safe.name}"'},
    )


@app.delete("/api/projects/{project_id}/files")
async def delete_project_file(project_id: str, path: str = ""):
    """Delete a file or empty directory from a project."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if not path:
        raise HTTPException(status_code=400, detail="path query parameter required")

    safe = _safe_project_rel(project_dir, path)
    if safe is None:
        raise HTTPException(status_code=403, detail="Path traversal rejected")

    if not safe.exists():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        if safe.is_dir():
            if any(safe.iterdir()):
                raise HTTPException(
                    status_code=400,
                    detail="Directory is not empty — remove contents first",
                )
            safe.rmdir()
        else:
            safe.unlink()
    except HTTPException:
        raise
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "path": path}


# ---------------------------------------------------------------------------
# Project version management endpoints (git)
# — reuse lexitool.git_ops functions directly
# ---------------------------------------------------------------------------

import shlex as _shlex


@app.get("/api/projects/{project_id}/versions")
async def list_project_versions(project_id: str, n: int = 20):
    """List recent git commits for a project."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        from lexitool import git_ops
    except ImportError:
        raise HTTPException(status_code=500, detail="lexitool.git_ops unavailable")

    result = git_ops.log(str(project_dir), n=n)
    status_result = git_ops.status(str(project_dir))

    return {
        "project_id": project_id,
        "branch": result.branch,
        "head": result.commit_hash,
        "dirty": status_result.data.get("dirty", False),
        "changed_files": status_result.data.get("changed_files", []),
        "commits": [
            {
                "hash": c.split()[0],
                "date": c.split()[1] if len(c.split()) > 1 else "",
                "message": " ".join(c.split()[2:]) if len(c.split()) > 2 else "",
                "full": c,
            }
            for c in result.data.get("commits", [])
        ],
    }


@app.post("/api/projects/{project_id}/versions")
async def create_project_snapshot(project_id: str, request: Request):
    """Create a git snapshot (commit) of the current project state."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    message = body.get("message", "").strip()
    author = body.get("author", "hermes-agent")
    tag = body.get("tag")
    allow_empty = body.get("allow_empty", False)

    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    try:
        from lexitool import git_ops
    except ImportError:
        raise HTTPException(status_code=500, detail="lexitool.git_ops unavailable")

    result = git_ops.snapshot(
        str(project_dir), message, author=author, tag=tag, allow_empty=allow_empty,
    )

    if not result.ok:
        raise HTTPException(status_code=400, detail=result.message)

    return {
        "ok": True,
        "message": result.message,
        "commit_hash": result.commit_hash,
        "tag": result.tag,
        "branch": result.branch,
        "no_changes": result.data.get("no_changes", False),
    }


@app.get("/api/projects/{project_id}/versions/{commit_sha}")
async def get_project_version_detail(project_id: str, commit_sha: str):
    """Get details for a specific commit."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        import subprocess as _sp
    except ImportError:
        pass

    # git show for the commit
    try:
        p = _sp.run(
            ["git", "-C", str(project_dir), "show", "--stat", "--format=%H%n%h%n%an%n%ad%n%s%n%b", commit_sha],
            capture_output=True, text=True, timeout=10,
        )
        if p.returncode != 0:
            raise HTTPException(status_code=404, detail=f"Commit not found: {commit_sha}")
    except _sp.TimeoutExpired:
        raise HTTPException(status_code=500, detail="git show timed out")

    lines = p.stdout.strip().split("\n")
    # parse the show output
    hash_full = lines[0] if len(lines) > 0 else ""
    hash_short = lines[1] if len(lines) > 1 else ""
    author = lines[2] if len(lines) > 2 else ""
    date = lines[3] if len(lines) > 3 else ""
    subject = lines[4] if len(lines) > 4 else ""

    # files changed (after the blank line)
    body_start = 6  # skip the empty line after subject
    changed_files = []
    for line in lines[body_start:]:
        if not line.strip():
            break
        # stat lines look like: " path/to/file | 12 +++"
        if "|" in line:
            changed_files.append(line.strip())

    return {
        "project_id": project_id,
        "hash_full": hash_full,
        "hash_short": hash_short,
        "author": author,
        "date": date,
        "subject": subject,
        "changed_files": changed_files,
    }


@app.get("/api/projects/{project_id}/branches")
async def list_project_branches(project_id: str):
    """List git branches for a project."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    import subprocess as _sp

    try:
        p = _sp.run(
            ["git", "-C", str(project_dir), "branch", "--format=%(refname:short)%(HEAD)%(objectname:short)"],
            capture_output=True, text=True, timeout=10,
        )
        if p.returncode != 0:
            raise HTTPException(status_code=400, detail="git branch failed")
    except _sp.TimeoutExpired:
        raise HTTPException(status_code=500, detail="git branch timed out")

    branches = []
    for line in p.stdout.strip().split("\n"):
        if not line.strip():
            continue
        # format: branchname*<hash> or branchname <hash>
        current = line.startswith("*")
        name = line.lstrip("*").strip()
        # split hash off the end (7 hex chars)
        parts = name.rsplit(None, 1)
        branch_name = parts[0] if len(parts) == 2 else name
        branch_hash = parts[1] if len(parts) == 2 else ""
        branches.append({
            "name": branch_name,
            "current": line.startswith("*"),
            "hash": branch_hash,
        })

    return {
        "project_id": project_id,
        "branches": branches,
    }


@app.post("/api/projects/{project_id}/branches")
async def create_project_branch(project_id: str, request: Request):
    """Create a new git branch (and optionally switch to it)."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    name = body.get("name", "").strip()
    switch = body.get("switch", True)

    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    try:
        from lexitool import git_ops
    except ImportError:
        raise HTTPException(status_code=500, detail="lexitool.git_ops unavailable")

    result = git_ops.revision_branch(str(project_dir), name, create=True, switch=switch)

    if not result.ok:
        raise HTTPException(status_code=400, detail=result.message)

    return {
        "ok": True,
        "branch": result.branch,
        "commit_hash": result.commit_hash,
        "message": result.message,
    }


@app.get("/api/projects/{project_id}/worktrees")
async def list_project_worktrees(project_id: str):
    """List all git worktrees for a project."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        from lexitool import git_ops
    except ImportError:
        raise HTTPException(status_code=500, detail="lexitool.git_ops unavailable")

    result = git_ops.list_worktrees(str(project_dir))

    if not result.ok:
        raise HTTPException(status_code=400, detail=result.message)

    return {
        "project_id": project_id,
        "worktrees": result.data.get("worktrees", []),
        "current_branch": result.branch,
    }


@app.post("/api/projects/{project_id}/worktrees")
async def create_project_worktree(project_id: str, request: Request):
    """Create a new git worktree for parallel work."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    name = body.get("name", "").strip()
    base_branch = body.get("base_branch") or None

    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    try:
        from lexitool import git_ops
    except ImportError:
        raise HTTPException(status_code=500, detail="lexitool.git_ops unavailable")

    result = git_ops.create_worktree(str(project_dir), name, base_branch=base_branch)

    if not result.ok:
        raise HTTPException(status_code=400, detail=result.message)

    return {
        "ok": True,
        "worktree_path": result.data.get("worktree_path", ""),
        "branch": result.branch,
        "message": result.message,
    }


@app.delete("/api/projects/{project_id}/worktrees/{name}")
async def remove_project_worktree(project_id: str, name: str, force: bool = False):
    """Remove a git worktree."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        from lexitool import git_ops
    except ImportError:
        raise HTTPException(status_code=500, detail="lexitool.git_ops unavailable")

    result = git_ops.remove_worktree(str(project_dir), name, force=force)

    if not result.ok:
        raise HTTPException(status_code=400, detail=result.message)

    return {"ok": True, "message": result.message}


@app.post("/api/projects/{project_id}/diff")
async def diff_project_files(project_id: str, request: Request):
    """Compare two document versions (paragraph-level summary or side-by-side diff)."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    original = body.get("original", "").strip()
    revised = body.get("revised", "").strip()
    mode = body.get("mode", "summary")

    if not original or not revised:
        raise HTTPException(status_code=400, detail="original and revised paths are required")

    # Resolve paths securely
    orig_safe = _safe_project_rel(project_dir, original)
    rev_safe = _safe_project_rel(project_dir, revised)

    if orig_safe is None or rev_safe is None:
        raise HTTPException(status_code=403, detail="Path traversal rejected")

    if not orig_safe.is_file():
        raise HTTPException(status_code=404, detail=f"Original file not found: {original}")
    if not rev_safe.is_file():
        raise HTTPException(status_code=404, detail=f"Revised file not found: {revised}")

    try:
        from lexitool import diff
    except ImportError:
        raise HTTPException(status_code=500, detail="lexitool.diff unavailable")

    try:
        result = diff.summary(str(orig_safe), str(rev_safe))
        result["original"] = original
        result["revised"] = revised

        if mode == "sidebyside":
            result["pairs"] = _build_diff_pairs(str(orig_safe), str(rev_safe))

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Diff failed: {e}")


def _build_diff_pairs(orig_path: str, rev_path: str) -> list[dict]:
    """Build side-by-side paragraph pairs for diff view."""
    import difflib
    from lexitool.diff import _read_final_paragraphs

    old_paras = _read_final_paragraphs(orig_path)
    new_paras = _read_final_paragraphs(rev_path)
    old_texts = [p["text"] for p in old_paras]
    new_texts = [p["text"] for p in new_paras]
    matcher = difflib.SequenceMatcher(None, old_texts, new_texts, autojunk=False)

    pairs: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                pairs.append({
                    "side": "both",
                    "type": "unchanged",
                    "old_para": old_paras[k]["para"],
                    "new_para": new_paras[j1 + (k - i1)]["para"],
                    "old_text": old_texts[k],
                    "new_text": new_texts[j1 + (k - i1)],
                })
        elif tag == "delete":
            for k in range(i1, i2):
                pairs.append({
                    "side": "left",
                    "type": "removed",
                    "old_para": old_paras[k]["para"],
                    "new_para": None,
                    "old_text": old_texts[k],
                    "new_text": "",
                })
        elif tag == "insert":
            for k in range(j1, j2):
                pairs.append({
                    "side": "right",
                    "type": "added",
                    "old_para": None,
                    "new_para": new_paras[k]["para"],
                    "old_text": "",
                    "new_text": new_texts[k],
                })
        elif tag == "replace":
            max_len = max(i2 - i1, j2 - j1)
            for k in range(max_len):
                o_idx = i1 + k if k < (i2 - i1) else None
                n_idx = j1 + k if k < (j2 - j1) else None
                pairs.append({
                    "side": "both",
                    "type": "modified",
                    "old_para": old_paras[o_idx]["para"] if o_idx is not None else None,
                    "new_para": new_paras[n_idx]["para"] if n_idx is not None else None,
                    "old_text": old_texts[o_idx] if o_idx is not None else "",
                    "new_text": new_texts[n_idx] if n_idx is not None else "",
                })

    return pairs


# ---------------------------------------------------------------------------
# Document-aware file endpoints (Phase B)
# — reuses lexitool.doc_stats, python-docx for preview, inventory JSON
# ---------------------------------------------------------------------------

_INVENTORY_FILE = ".hermes-project/inventory.json"

import subprocess as _subprocess


def _read_inventory(project_dir: Path) -> dict:
    """Read or initialise the project document inventory."""
    inv_path = _project_sidecar_dir(project_dir) / "inventory.json"
    if inv_path.is_file():
        try:
            return json.loads(inv_path.read_text())
        except Exception:
            pass
    return {"documents": {}}


def _write_inventory(project_dir: Path, inventory: dict):
    """Persist the project document inventory."""
    inv_path = _project_sidecar_dir(project_dir) / "inventory.json"
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False))


def _extract_docx_text(docx_path: Path, max_paras: int = 20) -> list[str]:
    """Extract first N paragraphs of text from a .docx file."""
    try:
        from docx import Document
    except ImportError:
        return []
    try:
        doc = Document(str(docx_path))
        paras = []
        for p in doc.paragraphs:
            text = p.text.strip()
            if text:
                paras.append(text)
            if len(paras) >= max_paras:
                break
        return paras
    except Exception:
        return []


def _search_docx_text(project_dir: Path, query: str, max_results: int = 30) -> list[dict]:
    """Full-text search across .docx files in a project directory."""
    results: list[dict] = []
    qlower = query.lower()
    try:
        from docx import Document
    except ImportError:
        return results

    for docx_path in project_dir.rglob("*.docx"):
        if results.__len__() >= max_results:
            break
        # Skip worktrees to avoid duplicates
        if ".worktrees" in str(docx_path):
            continue
        try:
            rel = str(docx_path.relative_to(project_dir))
        except Exception:
            rel = docx_path.name
        try:
            doc = Document(str(docx_path))
            matched_paras: list[str] = []
            for p in doc.paragraphs:
                text = p.text.strip()
                if text and qlower in text.lower():
                    matched_paras.append(text[:200])
                if matched_paras.__len__() >= 3:
                    break
            if matched_paras:
                results.append({
                    "path": rel,
                    "name": docx_path.name,
                    "matches": matched_paras,
                    "size": docx_path.stat().st_size,
                })
        except Exception:
            continue

    # Also search .txt and .md files
    for txt_path in project_dir.rglob("*"):
        if results.__len__() >= max_results:
            break
        if ".worktrees" in str(txt_path):
            continue
        if txt_path.suffix.lower() not in (".txt", ".md"):
            continue
        try:
            text = txt_path.read_text(errors="ignore")
            if qlower in text.lower():
                lines = text.split("\n")
                matched_lines = [l[:200] for l in lines if qlower in l.lower()][:3]
                rel = str(txt_path.relative_to(project_dir))
                results.append({
                    "path": rel,
                    "name": txt_path.name,
                    "matches": matched_lines,
                    "size": txt_path.stat().st_size,
                })
        except Exception:
            continue

    return results


@app.get("/api/projects/{project_id}/files/analyze")
async def analyze_project_file(project_id: str, path: str):
    """Analyze a document (.docx) — paragraph counts, fonts, tables, TC stats."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    safe = _safe_project_rel(project_dir, path)
    if safe is None:
        raise HTTPException(status_code=403, detail="Path traversal rejected")
    if not safe.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    ext = safe.suffix.lower()
    result: dict = {"path": path, "name": safe.name, "ext": ext}

    if ext == ".docx":
        try:
            from lexitool import doc_stats as _ds
        except ImportError:
            raise HTTPException(status_code=500, detail="lexitool unavailable")
        try:
            stats = _ds.doc_stats(str(safe))
            result["stats"] = stats
        except Exception as e:
            result["error"] = str(e)
    elif ext in (".pdf",):
        result["stats"] = {"paragraphs": "N/A (PDF)", "message": "PDF analysis not yet available"}
    else:
        # Generic file stats
        try:
            text = safe.read_text(errors="ignore")
            lines = text.split("\n")
            result["stats"] = {
                "lines": len(lines),
                "chars": len(text),
                "size": safe.stat().st_size,
            }
        except Exception as e:
            result["error"] = str(e)

    return result


@app.get("/api/projects/{project_id}/files/preview")
async def preview_project_file(project_id: str, path: str, paras: int = 20):
    """Preview the first N paragraphs of a document."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    safe = _safe_project_rel(project_dir, path)
    if safe is None:
        raise HTTPException(status_code=403, detail="Path traversal rejected")
    if not safe.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    ext = safe.suffix.lower()
    result: dict = {"path": path, "name": safe.name, "ext": ext, "preview": []}

    if ext == ".docx":
        result["preview"] = _extract_docx_text(safe, max_paras=paras)
    elif ext in (".txt", ".md"):
        try:
            lines = safe.read_text(errors="ignore").split("\n")
            result["preview"] = [l for l in lines[:paras] if l.strip()]
        except Exception as e:
            result["error"] = str(e)

    return result


@app.patch("/api/projects/{project_id}/files/meta")
async def update_file_metadata(project_id: str, request: Request):
    """Update document metadata (status, tags, notes)."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    path = body.get("path", "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="path is required")

    safe = _safe_project_rel(project_dir, path)
    if safe is None:
        raise HTTPException(status_code=403, detail="Path traversal rejected")

    inventory = _read_inventory(project_dir)
    entry = inventory.setdefault("documents", {}).setdefault(path, {})
    entry.setdefault("name", safe.name)

    for key in ("status", "tags", "notes", "version", "signing_status"):
        if key in body:
            entry[key] = body[key]

    from datetime import datetime as _dt, timezone as _tz

    entry["updated"] = _dt.now(_tz.utc).isoformat()
    _write_inventory(project_dir, inventory)

    return {"ok": True, "path": path, "meta": entry}


@app.post("/api/projects/{project_id}/files/audit")
async def add_file_audit_event(project_id: str, request: Request):
    """Log an audit event for a file (user action)."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    path = body.get("path", "").strip()
    action = body.get("action", "").strip()
    user = body.get("user", "system").strip()
    detail = body.get("detail", "").strip()

    if not path or not action:
        raise HTTPException(status_code=400, detail="path and action are required")

    safe = _safe_project_rel(project_dir, path)
    if safe is None:
        raise HTTPException(status_code=403, detail="Path traversal rejected")

    from datetime import datetime as _dt, timezone as _tz

    inventory = _read_inventory(project_dir)
    entry = inventory.setdefault("documents", {}).setdefault(path, {"name": safe.name})

    audit_log = entry.setdefault("audit_log", [])
    event = {
        "timestamp": _dt.now(_tz.utc).isoformat(),
        "user": user,
        "action": action,
        "detail": detail,
    }
    audit_log.append(event)
    entry["updated"] = event["timestamp"]
    _write_inventory(project_dir, inventory)

    return {"ok": True, "path": path, "event": event}


@app.get("/api/projects/{project_id}/files/audit")
async def get_file_audit_trail(project_id: str, path: str = ""):
    """Get the audit trail for a file."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    rel = _safe_project_rel(project_dir, path)
    if rel is None:
        raise HTTPException(status_code=403, detail="Path traversal rejected")

    inventory = _read_inventory(project_dir)
    entry = inventory.get("documents", {}).get(path, {})

    return {
        "project_id": project_id,
        "path": path,
        "signing_status": entry.get("signing_status", "unsigned"),
        "audit_log": entry.get("audit_log", []),
    }


@app.get("/api/projects/{project_id}/inventory")
async def get_project_inventory(project_id: str):
    """Get the structured document inventory for a project."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    inventory = _read_inventory(project_dir)

    # Enrich with file-system info (size, modified) for each document
    for rel_path, entry in inventory.get("documents", {}).items():
        doc_path = project_dir / rel_path
        if doc_path.is_file():
            entry["size"] = doc_path.stat().st_size
            from datetime import datetime as _dt, timezone as _tz

            entry["modified"] = _dt.fromtimestamp(
                doc_path.stat().st_mtime, tz=_tz.utc
            ).isoformat()
            if "name" not in entry:
                entry["name"] = doc_path.name

    return {"project_id": project_id, "inventory": inventory}


@app.get("/api/projects/{project_id}/documents/search")
async def search_project_documents(
    project_id: str,
    q: str = "",
    max_results: int = 30,
    status: str = "",
    file_type: str = "",
    date_from: str = "",
    date_to: str = "",
):
    """Full-text search across project documents with optional filters."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if not q.strip():
        return {"project_id": project_id, "query": q, "results": [], "filters": {
            "status": status, "file_type": file_type, "date_from": date_from, "date_to": date_to,
        }}

    results = _search_docx_text(project_dir, q.strip(), max_results=max_results)

    # Apply metadata filters
    inventory = _read_inventory(project_dir).get("documents", {})
    if status or file_type:
        results = [
            r for r in results
            if (not status or inventory.get(r.get("path", ""), {}).get("status", "").lower() == status.lower())
            and (not file_type or r.get("path", "").lower().endswith(f".{file_type.lower()}"))
        ]

    if date_from or date_to:
        def _parse_ts(ts_str: str):
            try:
                from datetime import datetime as _dt
                return _dt.fromisoformat(ts_str)
            except Exception:
                return None

        dt_from = _parse_ts(f"{date_from}T00:00:00") if date_from else None
        dt_to = _parse_ts(f"{date_to}T23:59:59") if date_to else None

        filtered: list[dict] = []
        for r in results:
            meta = inventory.get(r.get("path", ""), {})
            updated = meta.get("updated", "")
            if updated:
                try:
                    from datetime import datetime as _dt
                    ts = _dt.fromisoformat(updated)
                    if dt_from and ts < dt_from:
                        continue
                    if dt_to and ts > dt_to:
                        continue
                except Exception:
                    pass
            filtered.append(r)
        results = filtered

    return {
        "project_id": project_id,
        "query": q,
        "results": results,
        "total": results.__len__(),
        "filters": {
            "status": status, "file_type": file_type,
            "date_from": date_from, "date_to": date_to,
        },
    }


@app.get("/api/projects/{project_id}/files/refs")
async def get_file_references(project_id: str, path: str = ""):
    """Reverse lookup: find all checklist items and CPs that reference a given file."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    rel = _safe_project_rel(project_dir, path)
    refs: list[dict] = []

    for cl in _read_project_json(project_dir, "checklists.json", []):
        for it in cl.get("items", []):
            if rel in (it.get("document_refs") or []):
                refs.append({
                    "type": "checklist_item",
                    "checklist_id": cl.get("id"),
                    "checklist_name": cl.get("name"),
                    "item_id": it.get("id"),
                    "item_text": it.get("text"),
                })

    for cp in _read_project_json(project_dir, "cps.json", []):
        if rel in (cp.get("document_refs") or []):
            refs.append({
                "type": "cp",
                "cp_id": cp.get("id"),
                "cp_description": cp.get("description"),
                "cp_status": cp.get("status"),
            })

    return {"project_id": project_id, "path": rel, "refs": refs}


@app.post("/api/projects/{project_id}/binder")
async def create_project_binder(project_id: str, request: Request):
    """Merge multiple DOCX files into a single binder document."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    files = body.get("files") or []
    if not files or len(files) < 1:
        raise HTTPException(status_code=400, detail="files array with at least 1 path is required")

    title = body.get("title", "Binder").strip()
    output_name = body.get("output_name", f"binder_{_uuid.uuid4().hex[:8]}.docx").strip()

    # Resolve all paths
    resolved: list[Path] = []
    for f in files:
        safe = _safe_project_rel(project_dir, str(f))
        if safe is None:
            raise HTTPException(status_code=403, detail=f"Path traversal rejected: {f}")
        if not safe.is_file():
            raise HTTPException(status_code=404, detail=f"File not found: {f}")
        resolved.append(safe)

    output_path = project_dir / ".hermes-project" / "binders"
    output_path.mkdir(parents=True, exist_ok=True)
    output_file = output_path / output_name

    try:
        from docx import Document
    except ImportError:
        raise HTTPException(status_code=500, detail="python-docx unavailable")

    try:
        merged = Document()

        # Copy styles from first document as base
        first = Document(str(resolved[0]))

        # Copy sections (page layout) from first doc
        for section in first.sections:
            pass  # new doc already has one section

        # Set title
        if merged.paragraphs:
            merged.paragraphs[0].text = title
            merged.paragraphs[0].style = merged.styles["Heading 1"]
        else:
            p = merged.add_paragraph(title, style="Heading 1")

        merged.add_paragraph("")  # spacing

        page_count = 0
        for idx, file_path in enumerate(resolved):
            doc = Document(str(file_path))

            if idx > 0:
                merged.add_page_break()

            # Add file section header
            h = merged.add_paragraph()
            h.style = merged.styles["Heading 2"]
            h.text = f"Document {idx + 1}: {file_path.name}"

            for para in doc.paragraphs:
                p = merged.add_paragraph()
                p.style = para.style
                for run in para.runs:
                    r = p.add_run(run.text)
                    if run.bold: r.bold = True
                    if run.italic: r.italic = True
                    if run.underline: r.underline = True
                    if run.font.name: r.font.name = run.font.name
                    if run.font.size: r.font.size = run.font.size

            # Copy tables
            for table in doc.tables:
                t = merged.add_table(rows=len(table.rows), cols=len(table.columns))
                t.style = table.style
                for ri, row in enumerate(table.rows):
                    for ci, cell in enumerate(row.cells):
                        t.cell(ri, ci).text = cell.text

            page_count += 1

        merged.save(str(output_file))

        return {
            "ok": True,
            "output": str(output_file.relative_to(project_dir)),
            "page_count": page_count,
            "files_merged": len(resolved),
            "size": output_file.stat().st_size,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Binder creation failed: {e}")


# ---------------------------------------------------------------------------
# Project lifecycle dashboard endpoints (Phase C)
# — phase tracking, checklists, CPs, tasks stored in .hermes-project/ JSON
# ---------------------------------------------------------------------------

import uuid as _uuid

_PHASES = [
    "init", "drafting", "review", "execution", "cp", "closing", "registration",
]


def _read_project_json(project_dir: Path, filename: str, default=None):
    """Read a JSON file from .hermes-project/, returning *default* on failure."""
    path = _project_sidecar_dir(project_dir) / filename
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def _write_project_json(project_dir: Path, filename: str, data):
    """Atomically write a JSON file to .hermes-project/."""
    path = _project_sidecar_dir(project_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


# ── Native legal workflows ─────────────────────────────────────────────────


def _workflow_project_filters(project_id: str) -> tuple[str | None, str | None]:
    project = _resolve_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return (
        str(project.get("id") or "") or None,
        str(project.get("cwd") or project.get("directory") or "") or None,
    )


def _normalise_workflow_steps(raw_steps: list | None) -> list[dict]:
    steps: list[dict] = []
    for index, raw in enumerate(raw_steps or []):
        if not isinstance(raw, dict):
            continue
        step_id = str(raw.get("id") or raw.get("step_key") or f"step-{index + 1}").strip()
        if not step_id:
            step_id = f"step-{index + 1}"
        input_payload = raw.get("input") if isinstance(raw.get("input"), dict) else {}
        for key in ("instructions", "x", "y"):
            if key in raw and key not in input_payload:
                input_payload[key] = raw.get(key)
        steps.append({
            "id": step_id,
            "step_index": int(raw.get("step_index", index)),
            "title": str(raw.get("title") or step_id),
            "type": str(raw.get("type") or "manual"),
            "role": str(raw.get("role") or "coordinator"),
            "depends_on": raw.get("depends_on") if isinstance(raw.get("depends_on"), list) else [],
            "input": input_payload,
            "requires_approval": bool(raw.get("requires_approval")),
        })
    return steps


def _workflow_step_prompt(workflow: dict, step: dict) -> str:
    step_input = step.get("input") if isinstance(step.get("input"), dict) else {}
    instructions = str(step_input.get("instructions") or workflow.get("instructions") or "").strip()
    payload = {
        "workflow_run_id": workflow.get("id"),
        "step_id": step.get("id"),
        "step_key": step.get("step_key"),
        "title": step.get("title"),
        "type": step.get("type"),
        "role": step.get("role"),
        "requires_approval": bool(step.get("requires_approval")),
        "project_dir": workflow.get("project_dir"),
        "document_path": workflow.get("document_path"),
        "term_sheet_path": workflow.get("term_sheet_path"),
        "input": step_input,
    }
    return f"""
请执行以下原生法律 workflow step，并在完成后调用 legal_workflow(action="update_step") 回写状态和结构化结果。

执行约束：
1. 如果 requires_approval=true，先提交计划并等待确认，不得修改文档。
2. 如果 type 是读取/分析类，只读取和产出结构化结果，不修改文档。
3. 如果需要修改 Word，必须使用 lex_read 定位、lex_edit 修改、lex_read 读回验证。
4. 发现或更正项目事实时，必须调用 project_facts 更新。
5. 完成后把 status/result 写回 step_id，不要只在正文里总结。

workflow step:
{json.dumps(payload, ensure_ascii=False, indent=2)}

用户补充要求：
{instructions or "无"}
""".strip()


@app.get("/api/projects/{project_id}/workflows")
async def list_project_workflows(project_id: str, limit: int = 20):
    """List persistent legal workflows for a project."""
    project_db_id, project_dir = _workflow_project_filters(project_id)
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        try:
            workflows = []
            seen = set()
            for kwargs in (
                {"project_id": project_db_id, "project_dir": None},
                {"project_id": None, "project_dir": project_dir},
            ):
                if not kwargs["project_id"] and not kwargs["project_dir"]:
                    continue
                for workflow in db.list_legal_workflows(limit=limit, **kwargs):
                    wid = workflow.get("id")
                    if wid in seen:
                        continue
                    seen.add(wid)
                    workflows.append(workflow)
            workflows.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
            workflows = workflows[: max(1, int(limit or 20))]
        finally:
            db.close()
        return {"project_id": project_id, "workflows": workflows}
    except Exception as e:
        _log.exception("GET project workflows failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/projects/{project_id}/workflows")
async def create_project_workflow(project_id: str, request: Request):
    """Create a persistent legal workflow plan from WebUI canvas nodes."""
    project_db_id, project_dir = _workflow_project_filters(project_id)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    name = str(body.get("name") or "法律文书 workflow").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    steps = _normalise_workflow_steps(body.get("steps") if isinstance(body.get("steps"), list) else [])
    if not steps:
        raise HTTPException(status_code=400, detail="steps are required")

    try:
        from hermes_state import SessionDB
        db = SessionDB()
        try:
            run_id = db.create_legal_workflow(
                name=name,
                project_id=project_db_id,
                project_dir=project_dir,
                document_path=str(body.get("document_path") or "").strip() or None,
                term_sheet_path=str(body.get("term_sheet_path") or "").strip() or None,
                instructions=str(body.get("instructions") or "").strip() or None,
                steps=steps,
            )
            workflow = db.get_legal_workflow(run_id)
        finally:
            db.close()
        return {"ok": True, "workflow": workflow}
    except Exception as e:
        _log.exception("POST project workflow failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/workflows/definitions")
async def list_workflow_definitions():
    """Return available YAML workflow definitions (the 4 swarm templates)."""
    try:
        from hermes_cli.project_commands import _LEGAL_WORKFLOW_TEMPLATES
        defs = []
        for wid, spec in _LEGAL_WORKFLOW_TEMPLATES.items():
            nodes = spec.get("nodes", [])
            pipeline = " → ".join(n["id"] for n in nodes)
            defs.append({
                "id": wid,
                "version": spec.get("version", 1),
                "description": spec.get("description", ""),
                "node_count": len(nodes),
                "pipeline": pipeline,
                "input_schema": spec.get("input_schema", []),
                "timeout_minutes": spec.get("timeout_minutes"),
            })
        return {"ok": True, "definitions": defs}
    except Exception as e:
        _log.exception("GET /api/workflows/definitions failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Chat Room REST endpoints
# ---------------------------------------------------------------------------


@app.post("/api/rooms")
async def create_room(request: Request):
    """Create a legal swarm chat room by compiling a workflow.

    Body: ``{project_id, workflow_id, params?}``
    Returns: ``{ok, board, run_id, workflow_id, node_count}``
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    project_id = str(body.get("project_id", "")).strip()
    workflow_id = str(body.get("workflow_id", "")).strip()
    params = body.get("params") or {}

    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    if not workflow_id:
        raise HTTPException(status_code=400, detail="workflow_id is required")

    project = _resolve_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_dir = project.get("cwd") or project.get("directory") or ""

    try:
        from hermes_cli.kanban_legal_swarm import compile_workflow

        run = compile_workflow(
            project_dir=project_dir,
            workflow_id=workflow_id,
            params=params,
        )
        return {
            "ok": True,
            "board": run.board,
            "run_id": run.run_id,
            "workflow_id": run.workflow_id,
            "root_task_id": run.root_task_id,
            "node_count": len(run.node_mappings),
            "project_id": project_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("create_room failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/rooms/{board}/{run_id}/messages")
async def get_room_messages(board: str, run_id: str, since: str = ""):
    """Return historical chat room messages from kanban DB."""
    room_id = f"{board}/{run_id}"
    if not _ROOM_ID_RE.match(room_id):
        raise HTTPException(status_code=400, detail="Invalid board/run_id")

    import sqlite3 as _sql
    from hermes_cli.kanban_db import (
        kanban_db_path,
        list_comments_for_tasks,
        list_events_for_tasks,
    )
    from hermes_cli.kanban_legal_swarm import get_run_task_ids

    db_path = kanban_db_path(board=board)
    if not db_path.exists():
        return {"ok": True, "messages": []}

    conn = _sql.connect(str(db_path))
    conn.row_factory = _sql.Row
    try:
        # Find root task for this run
        row = conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key LIKE ? "
            "AND status != 'archived' ORDER BY created_at DESC LIMIT 1",
            (f"%:{run_id}:%",),
        ).fetchone()
        if not row:
            return {"ok": True, "messages": []}
        task_ids = get_run_task_ids(conn, row["id"])

        since_id = 0
        if since and since.isdigit():
            since_id = int(since)

        messages: list[dict] = []
        for c in list_comments_for_tasks(conn, task_ids, since_id=since_id):
            body = c.body or ""
            if body.startswith("[swarm:blackboard] "):
                continue
            messages.append(
                _format_room_message(
                    f"comment-{c.id}", "bot", body,
                    sender=c.author, task_id=c.task_id,
                    timestamp=c.created_at,
                )
            )

        for ev in list_events_for_tasks(conn, task_ids, since_id=since_id):
            kind = ev.kind or ""
            if kind == "commented":
                continue
            messages.append(
                _format_room_message(
                    f"event-{ev.id}", "status",
                    f"Task {ev.task_id}: {kind}",
                    task_id=ev.task_id,
                    timestamp=ev.created_at,
                )
            )

        messages.sort(key=lambda m: m["timestamp"])
        return {"ok": True, "messages": messages}
    finally:
        conn.close()


@app.get("/api/rooms/{board}/{run_id}/status")
async def get_room_status(board: str, run_id: str):
    """Return combined room status: run progress, bot list, topology."""
    room_id = f"{board}/{run_id}"
    if not _ROOM_ID_RE.match(room_id):
        raise HTTPException(status_code=400, detail="Invalid board/run_id")

    import sqlite3 as _sql
    from hermes_cli.kanban_db import kanban_db_path
    from hermes_cli.kanban_legal_swarm import get_run_task_ids, run_status

    db_path = kanban_db_path(board=board)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Board not found")

    conn = _sql.connect(str(db_path))
    conn.row_factory = _sql.Row
    try:
        row = conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key LIKE ? "
            "AND status != 'archived' ORDER BY created_at DESC LIMIT 1",
            (f"%:{run_id}:%",),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Run not found")

        root_id = row["id"]
        status = run_status(conn, root_id)

        # Build bot list from node topology
        bots: list[dict] = []
        for nid, nstatus in status.get("nodes", {}).items():
            for t in nstatus.get("tasks", []):
                bots.append({
                    "id": nid,
                    "name": t.get("assignee", nid) or nid,
                    "profile": t.get("assignee", ""),
                    "status": t.get("status", "pending"),
                    "taskId": t.get("task_id", ""),
                    "kind": nstatus.get("kind", "worker"),
                })

        return {
            "ok": True,
            "run_status": status,
            "bots": bots,
        }
    finally:
        conn.close()


@app.get("/api/bots")
async def list_bot_profiles():
    """Return available legal bot profiles for the chat room."""
    bots = [
        {"id": "lex-coordinator", "name": "Lex Coordinator", "description": "Orchestrates workflows and runs scorecard gates.", "profile": "lex-coordinator"},
        {"id": "lex-drafter", "name": "Lex Drafter", "description": "Drafts and revises legal documents with Track Changes.", "profile": "lex-drafter"},
        {"id": "lex-reviewer-content", "name": "Lex Reviewer (Content)", "description": "Reviews legal substance, completeness, and consistency.", "profile": "lex-reviewer-content"},
        {"id": "lex-reviewer-format", "name": "Lex Reviewer (Format)", "description": "Reviews fonts, spacing, numbering, and page layout.", "profile": "lex-reviewer-format"},
        {"id": "lex-reviewer-xref", "name": "Lex Reviewer (XRef)", "description": "Reviews internal citations, bookmarks, and defined terms.", "profile": "lex-reviewer-xref"},
        {"id": "lex-reviewer-ts", "name": "Lex Reviewer (TS)", "description": "Checks contract vs term sheet consistency.", "profile": "lex-reviewer-ts"},
        {"id": "lex-reviewer-translation", "name": "Lex Reviewer (Translation)", "description": "Reviews bilingual accuracy and terminology.", "profile": "lex-reviewer-translation"},
    ]
    return {"ok": True, "bots": bots}


@app.get("/api/workflows/{run_id}")
async def get_workflow(run_id: str):
    """Get a persistent legal workflow run and ordered steps."""
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        try:
            workflow = db.get_legal_workflow(run_id)
        finally:
            db.close()
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return {"workflow": workflow}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("GET workflow failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/workflows/{run_id}")
async def update_workflow_run(run_id: str, request: Request):
    """Update workflow run metadata."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    fields = {
        key: body[key]
        for key in ("name", "status", "document_path", "term_sheet_path", "instructions")
        if key in body
    }
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        try:
            ok = db.update_legal_workflow(run_id, **fields)
            workflow = db.get_legal_workflow(run_id)
        finally:
            db.close()
        if not ok or not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return {"ok": True, "workflow": workflow}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("PATCH workflow failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/workflows/{run_id}/steps/{step_id}")
async def update_workflow_step(run_id: str, step_id: str, request: Request):
    """Update workflow step status/result/input from WebUI."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    allowed = {
        "title", "type", "role", "status", "depends_on", "input", "result",
        "requires_approval", "started_at", "ended_at",
    }
    step = {key: body[key] for key in allowed if key in body}
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        try:
            workflow = db.get_legal_workflow(run_id)
            if not workflow:
                raise HTTPException(status_code=404, detail="Workflow not found")
            if not any(str(item.get("id")) == step_id for item in workflow.get("steps", [])):
                raise HTTPException(status_code=404, detail="Workflow step not found")
            db.update_legal_workflow_step(step_id, **step)
            workflow = db.get_legal_workflow(run_id)
        finally:
            db.close()
        return {"ok": True, "workflow": workflow}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("PATCH workflow step failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/workflows/{run_id}/steps/{step_id}/run")
async def prepare_workflow_step_run(run_id: str, step_id: str):
    """Prepare a selected workflow step for execution in the active Native Chat."""
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        try:
            workflow = db.get_legal_workflow(run_id)
            if not workflow:
                raise HTTPException(status_code=404, detail="Workflow not found")
            step = next(
                (item for item in workflow.get("steps", []) if str(item.get("id")) == step_id),
                None,
            )
            if not step:
                raise HTTPException(status_code=404, detail="Workflow step not found")
            db.update_legal_workflow_step(step_id, status="running", started_at=time.time())
            workflow = db.get_legal_workflow(run_id)
            step = next(item for item in workflow.get("steps", []) if str(item.get("id")) == step_id)
        finally:
            db.close()
        return {
            "ok": True,
            "workflow": workflow,
            "step": step,
            "prompt": _workflow_step_prompt(workflow, step),
        }
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("POST workflow step run failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── Dashboard (aggregated summary) ─────────────────────────────────────────


@app.get("/api/projects/{project_id}/dashboard")
async def get_project_dashboard(project_id: str):
    """Aggregated project summary — phase, docs, checklists, CPs, tasks, git status."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    from datetime import datetime as _dt, timezone as _tz

    # Phase state
    phase_state = _read_project_json(project_dir, "phase-state.json", {
        "phase": "init", "phase_index": 0, "phase_history": [],
    })

    # Checklists
    checklists = _read_project_json(project_dir, "checklists.json", [])

    # CPs
    cps = _read_project_json(project_dir, "cps.json", [])

    # Tasks
    tasks = _read_project_json(project_dir, "tasks.json", [])

    # Inventory / doc counts
    inventory = _read_inventory(project_dir)
    doc_count = len(inventory.get("documents", {}))
    docs_by_status: dict[str, int] = {}
    for entry in inventory.get("documents", {}).values():
        s = entry.get("status", "unknown")
        docs_by_status[s] = docs_by_status.get(s, 0) + 1

    # Git status (if available)
    git_info: dict = {}
    try:
        from lexitool import git_ops
        gs = git_ops.status(str(project_dir))
        gl = git_ops.log(str(project_dir), n=5)
        git_info = {
            "dirty": gs.data.get("dirty", False),
            "changed_files": gs.data.get("changed_files", []),
            "branch": gl.branch,
            "head": gl.commit_hash,
            "recent_commits": [
                {"hash": c.split()[0], "message": " ".join(c.split()[2:])}
                for c in gl.data.get("commits", [])[:5]
            ],
        }
    except Exception:
        pass

    # Checklist progress
    total_items = sum(len(cl.get("items", [])) for cl in checklists)
    checked_items = sum(
        sum(1 for it in cl.get("items", []) if it.get("checked"))
        for cl in checklists
    )

    # CP progress
    cp_done = sum(1 for cp in cps if cp.get("status") in ("met", "waived"))
    cp_total = len(cps)

    # Task progress
    task_done = sum(1 for t in tasks if t.get("status") == "done")
    task_total = len(tasks)

    return {
        "project_id": project_id,
        "phase": phase_state,
        "phases": _PHASES,
        "documents": {
            "total": doc_count,
            "by_status": docs_by_status,
        },
        "checklists": {
            "items_checked": checked_items,
            "items_total": total_items,
            "lists": len(checklists),
        },
        "cps": {
            "done": cp_done,
            "total": cp_total,
        },
        "tasks": {
            "done": task_done,
            "total": task_total,
        },
        "git": git_info,
    }


# ── Phases ─────────────────────────────────────────────────────────────────


@app.get("/api/projects/{project_id}/phases")
async def get_project_phases(project_id: str):
    """Get current phase and phase history."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    phase_state = _read_project_json(project_dir, "phase-state.json", {
        "phase": "init", "phase_index": 0, "phase_history": [],
    })

    return {
        "project_id": project_id,
        "phases": _PHASES,
        "current_phase": phase_state.get("phase", "init"),
        "current_index": phase_state.get("phase_index", 0),
        "phase_history": phase_state.get("phase_history", []),
    }


@app.patch("/api/projects/{project_id}/phases")
async def advance_project_phase(project_id: str, request: Request):
    """Advance to the next project phase."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    from datetime import datetime as _dt, timezone as _tz

    phase_state = _read_project_json(project_dir, "phase-state.json", {
        "phase": "init", "phase_index": 0, "phase_history": [],
    })

    current_idx = phase_state.get("phase_index", 0)
    if current_idx + 1 >= len(_PHASES):
        raise HTTPException(status_code=400, detail="Already at final phase")

    # Record completion of current phase
    phase_history = phase_state.get("phase_history", [])
    phase_history.append({
        "phase": _PHASES[current_idx],
        "completed": _dt.now(_tz.utc).isoformat(),
    })

    new_idx = current_idx + 1
    new_state = {
        "phase": _PHASES[new_idx],
        "phase_index": new_idx,
        "phase_history": phase_history,
    }
    _write_project_json(project_dir, "phase-state.json", new_state)

    return {
        "ok": True,
        "previous_phase": _PHASES[current_idx],
        "current_phase": _PHASES[new_idx],
        "phase_index": new_idx,
    }


# ── Checklists ─────────────────────────────────────────────────────────────


@app.get("/api/projects/{project_id}/checklists")
async def get_project_checklists(project_id: str):
    """List all checklists for a project."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    checklists = _read_project_json(project_dir, "checklists.json", [])
    return {"project_id": project_id, "checklists": checklists}


@app.post("/api/projects/{project_id}/checklists")
async def create_project_checklist(project_id: str, request: Request):
    """Create a new checklist."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    items_raw = body.get("items", [])
    items = [
        {"id": str(_uuid.uuid4())[:8], "text": (it.get("text", "") if isinstance(it, dict) else str(it)).strip(), "checked": False, "document_refs": (it.get("document_refs", []) if isinstance(it, dict) else [])}
        for it in items_raw
    ]

    checklists = _read_project_json(project_dir, "checklists.json", [])
    new_cl = {
        "id": str(_uuid.uuid4())[:8],
        "name": name,
        "items": items,
    }
    checklists.append(new_cl)
    _write_project_json(project_dir, "checklists.json", checklists)

    return {"ok": True, "checklist": new_cl}


@app.patch("/api/projects/{project_id}/checklists/{checklist_id}/items/{item_id}")
async def toggle_checklist_item(project_id: str, checklist_id: str, item_id: str, request: Request):
    """Toggle a checklist item's checked state or update document references."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    checklists = _read_project_json(project_dir, "checklists.json", [])
    for cl in checklists:
        if cl.get("id") == checklist_id:
            for it in cl.get("items", []):
                if it.get("id") == item_id:
                    if "checked" in body:
                        it["checked"] = body["checked"]
                    if "document_refs" in body:
                        it["document_refs"] = body["document_refs"]
                    _write_project_json(project_dir, "checklists.json", checklists)
                    return {"ok": True, "item": it}
            raise HTTPException(status_code=404, detail="Checklist item not found")
    raise HTTPException(status_code=404, detail="Checklist not found")


# ── Conditions Precedent ────────────────────────────────────────────────────


@app.get("/api/projects/{project_id}/cps")
async def get_project_cps(project_id: str):
    """List conditions precedent for a project."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    cps = _read_project_json(project_dir, "cps.json", [])
    return {"project_id": project_id, "cps": cps}


@app.post("/api/projects/{project_id}/cps")
async def create_project_cp(project_id: str, request: Request):
    """Create a new condition precedent."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    description = body.get("description", "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="description is required")

    cps = _read_project_json(project_dir, "cps.json", [])
    new_cp = {
        "id": str(_uuid.uuid4())[:8],
        "description": description,
        "status": body.get("status", "pending"),
        "due_date": body.get("due_date", ""),
        "notes": body.get("notes", ""),
        "document_refs": body.get("document_refs", []),
    }
    cps.append(new_cp)
    _write_project_json(project_dir, "cps.json", cps)

    return {"ok": True, "cp": new_cp}


@app.patch("/api/projects/{project_id}/cps/{cp_id}")
async def update_project_cp(project_id: str, cp_id: str, request: Request):
    """Update a condition precedent's status or notes."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    cps = _read_project_json(project_dir, "cps.json", [])
    for cp in cps:
        if cp.get("id") == cp_id:
            for key in ("status", "notes", "due_date", "description", "document_refs"):
                if key in body:
                    cp[key] = body[key]
            _write_project_json(project_dir, "cps.json", cps)
            return {"ok": True, "cp": cp}
    raise HTTPException(status_code=404, detail="CP not found")


# ── Tasks ───────────────────────────────────────────────────────────────────


@app.get("/api/projects/{project_id}/tasks")
async def get_project_tasks(project_id: str):
    """List project tasks."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    tasks = _read_project_json(project_dir, "tasks.json", [])
    return {"project_id": project_id, "tasks": tasks}


@app.post("/api/projects/{project_id}/tasks")
async def create_project_task(project_id: str, request: Request):
    """Create a new project task."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    tasks = _read_project_json(project_dir, "tasks.json", [])
    new_task = {
        "id": str(_uuid.uuid4())[:8],
        "title": title,
        "status": body.get("status", "todo"),
        "assignee": body.get("assignee", ""),
        "due_date": body.get("due_date", ""),
        "notes": body.get("notes", ""),
    }
    tasks.append(new_task)
    _write_project_json(project_dir, "tasks.json", tasks)

    return {"ok": True, "task": new_task}


@app.patch("/api/projects/{project_id}/tasks/{task_id}")
async def update_project_task(project_id: str, task_id: str, request: Request):
    """Update a project task."""
    project_dir = _resolve_project_path(project_id)
    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    tasks = _read_project_json(project_dir, "tasks.json", [])
    for task in tasks:
        if task.get("id") == task_id:
            for key in ("title", "status", "assignee", "due_date", "notes"):
                if key in body:
                    task[key] = body[key]
            _write_project_json(project_dir, "tasks.json", tasks)
            return {"ok": True, "task": task}
    raise HTTPException(status_code=404, detail="Task not found")


# ---------------------------------------------------------------------------
# Log viewer endpoint
# ---------------------------------------------------------------------------


@app.get("/api/logs")
async def get_logs(
    file: str = "agent",
    lines: int = 100,
    level: Optional[str] = None,
    component: Optional[str] = None,
    search: Optional[str] = None,
):
    from hermes_cli.logs import _read_tail, LOG_FILES

    log_name = LOG_FILES.get(file)
    if not log_name:
        raise HTTPException(status_code=400, detail=f"Unknown log file: {file}")
    log_path = get_hermes_home() / "logs" / log_name
    if not log_path.exists():
        return {"file": file, "lines": []}

    try:
        from hermes_logging import COMPONENT_PREFIXES
    except ImportError:
        COMPONENT_PREFIXES = {}

    # Normalize "ALL" / "all" / empty → no filter. _matches_filters treats an
    # empty tuple as "must match a prefix" (startswith(()) is always False),
    # so passing () instead of None silently drops every line.
    min_level = level if level and level.upper() != "ALL" else None
    if component and component.lower() != "all":
        comp_prefixes = COMPONENT_PREFIXES.get(component)
        if comp_prefixes is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown component: {component}. "
                       f"Available: {', '.join(sorted(COMPONENT_PREFIXES))}",
            )
    else:
        comp_prefixes = None

    has_filters = bool(min_level or comp_prefixes or search)
    result = _read_tail(
        log_path, min(lines, 500) if not search else 2000,
        has_filters=has_filters,
        min_level=min_level,
        component_prefixes=comp_prefixes,
    )
    # Post-filter by search term (case-insensitive substring match).
    # _read_tail doesn't support free-text search, so we filter here and
    # trim to the requested line count afterward.
    if search:
        needle = search.lower()
        result = [l for l in result if needle in l.lower()][-min(lines, 500):]
    return {"file": file, "lines": result}


# ---------------------------------------------------------------------------
# Cron job management endpoints
# ---------------------------------------------------------------------------


class CronJobCreate(BaseModel):
    prompt: str
    schedule: str
    name: str = ""
    deliver: str = "local"


class CronJobUpdate(BaseModel):
    updates: dict


_CRON_PROFILE_LOCK = threading.RLock()


def _cron_profile_dicts() -> List[Dict[str, Any]]:
    """Return dashboard profile records, falling back to a directory scan."""
    from hermes_cli import profiles as profiles_mod
    try:
        return [_profile_to_dict(p) for p in profiles_mod.list_profiles()]
    except Exception:
        _log.exception("Failed to list profiles for cron dashboard; falling back to directory scan")
        return _fallback_profile_dicts(profiles_mod)


def _cron_profile_home(profile: Optional[str]) -> Tuple[str, Path]:
    """Resolve a profile query value to (profile_name, HERMES_HOME)."""
    from hermes_cli import profiles as profiles_mod

    raw = (profile or "default").strip() or "default"
    try:
        canon = profiles_mod.normalize_profile_name(raw)
        profiles_mod.validate_profile_name(canon)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not profiles_mod.profile_exists(canon):
        raise HTTPException(status_code=404, detail=f"Profile '{canon}' does not exist.")
    return canon, profiles_mod.get_profile_dir(canon)


def _annotate_cron_job(job: Dict[str, Any], profile: str, home: Path) -> Dict[str, Any]:
    annotated = dict(job)
    annotated["profile"] = profile
    annotated["profile_name"] = profile
    annotated["hermes_home"] = str(home)
    annotated["is_default_profile"] = profile == "default"
    return annotated


def _call_cron_for_profile(profile: Optional[str], func_name: str, *args, **kwargs):
    """Run cron.jobs helpers against the selected profile's cron directory.

    cron.jobs keeps CRON_DIR/JOBS_FILE/OUTPUT_DIR as module globals resolved
    from the process HERMES_HOME at import time. The dashboard is a single
    process that can inspect many profiles, so temporarily retarget those
    globals while holding a lock and restore them immediately after the call.
    """
    profile_name, home = _cron_profile_home(profile)
    with _CRON_PROFILE_LOCK:
        from cron import jobs as cron_jobs

        old_cron_dir = cron_jobs.CRON_DIR
        old_jobs_file = cron_jobs.JOBS_FILE
        old_output_dir = cron_jobs.OUTPUT_DIR
        cron_jobs.CRON_DIR = home / "cron"
        cron_jobs.JOBS_FILE = cron_jobs.CRON_DIR / "jobs.json"
        cron_jobs.OUTPUT_DIR = cron_jobs.CRON_DIR / "output"
        try:
            result = getattr(cron_jobs, func_name)(*args, **kwargs)
        finally:
            cron_jobs.CRON_DIR = old_cron_dir
            cron_jobs.JOBS_FILE = old_jobs_file
            cron_jobs.OUTPUT_DIR = old_output_dir

    if isinstance(result, list):
        return [_annotate_cron_job(j, profile_name, home) for j in result]
    if isinstance(result, dict):
        return _annotate_cron_job(result, profile_name, home)
    return result


def _find_cron_job_profile(job_id: str) -> Optional[str]:
    for profile in _cron_profile_dicts():
        name = str(profile.get("name") or "")
        if not name:
            continue
        jobs = _call_cron_for_profile(name, "list_jobs", True)
        if any(j.get("id") == job_id or j.get("name") == job_id for j in jobs):
            return name
    return None


@app.get("/api/cron/jobs")
async def list_cron_jobs(profile: str = "all"):
    requested = (profile or "all").strip()
    if requested.lower() != "all":
        return _call_cron_for_profile(requested, "list_jobs", True)

    jobs: List[Dict[str, Any]] = []
    for item in _cron_profile_dicts():
        name = str(item.get("name") or "")
        if not name:
            continue
        try:
            jobs.extend(_call_cron_for_profile(name, "list_jobs", True))
        except Exception:
            _log.exception("Failed to list cron jobs for profile %s", name)
    return jobs


@app.get("/api/cron/jobs/{job_id}")
async def get_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "get_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/cron/jobs")
async def create_cron_job(body: CronJobCreate, profile: str = "default"):
    try:
        return _call_cron_for_profile(
            profile,
            "create_job",
            prompt=body.prompt,
            schedule=body.schedule,
            name=body.name,
            deliver=body.deliver,
        )
    except Exception as e:
        _log.exception("POST /api/cron/jobs failed")
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/cron/jobs/{job_id}")
async def update_cron_job(job_id: str, body: CronJobUpdate, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        job = _call_cron_for_profile(selected, "update_job", job_id, body.updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/cron/jobs/{job_id}/pause")
async def pause_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "pause_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/cron/jobs/{job_id}/resume")
async def resume_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "resume_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/cron/jobs/{job_id}/trigger")
async def trigger_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "trigger_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.delete("/api/cron/jobs/{job_id}")
async def delete_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        removed = _call_cron_for_profile(selected, "remove_job", job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Profile management endpoints (minimal — list/create/rename/delete + SOUL.md)
# ---------------------------------------------------------------------------


class ProfileCreate(BaseModel):
    name: str
    clone_from_default: bool = False
    no_skills: bool = False


class ProfileRename(BaseModel):
    new_name: str


class ProfileSoulUpdate(BaseModel):
    content: str


def _profile_attr(info, name: str, default: Any = None) -> Any:
    try:
        return getattr(info, name)
    except Exception:
        return default


def _profile_to_dict(info) -> Dict[str, Any]:
    return {
        "name": _profile_attr(info, "name", ""),
        "path": str(_profile_attr(info, "path", "")),
        "is_default": bool(_profile_attr(info, "is_default", False)),
        "model": _profile_attr(info, "model"),
        "provider": _profile_attr(info, "provider"),
        "has_env": bool(_profile_attr(info, "has_env", False)),
        "skill_count": int(_profile_attr(info, "skill_count", 0) or 0),
    }


def _fallback_profile_dicts(profiles_mod) -> List[Dict[str, Any]]:
    def _safe(callable_, default):
        try:
            return callable_()
        except Exception:
            return default

    profiles: List[Dict[str, Any]] = []
    default_home = profiles_mod._get_default_hermes_home()
    if default_home.is_dir():
        model, provider = _safe(lambda: profiles_mod._read_config_model(default_home), (None, None))
        profiles.append({
            "name": "default",
            "path": str(default_home),
            "is_default": True,
            "model": model,
            "provider": provider,
            "has_env": (default_home / ".env").exists(),
            "skill_count": _safe(lambda: profiles_mod._count_skills(default_home), 0),
        })

    profiles_root = profiles_mod._get_profiles_root()
    if profiles_root.is_dir():
        for entry in sorted(profiles_root.iterdir()):
            if not entry.is_dir() or not profiles_mod._PROFILE_ID_RE.match(entry.name):
                continue
            model, provider = _safe(lambda entry=entry: profiles_mod._read_config_model(entry), (None, None))
            profiles.append({
                "name": entry.name,
                "path": str(entry),
                "is_default": False,
                "model": model,
                "provider": provider,
                "has_env": (entry / ".env").exists(),
                "skill_count": _safe(lambda entry=entry: profiles_mod._count_skills(entry), 0),
            })

    return profiles


def _resolve_profile_dir(name: str) -> Path:
    """Validate ``name`` and resolve to its directory or raise an HTTPException."""
    from hermes_cli import profiles as profiles_mod
    try:
        profiles_mod.validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not profiles_mod.profile_exists(name):
        raise HTTPException(status_code=404, detail=f"Profile '{name}' does not exist.")
    return profiles_mod.get_profile_dir(name)


def _profile_setup_command(name: str) -> str:
    """Return the shell command used to configure a profile in the CLI."""
    _resolve_profile_dir(name)
    return "hermes setup" if name == "default" else f"{name} setup"


@app.get("/api/profiles")
async def list_profiles_endpoint():
    from hermes_cli import profiles as profiles_mod
    try:
        return {"profiles": [_profile_to_dict(p) for p in profiles_mod.list_profiles()]}
    except Exception:
        _log.exception("GET /api/profiles failed; falling back to profile directory scan")
        return {"profiles": _fallback_profile_dicts(profiles_mod)}


@app.post("/api/profiles")
async def create_profile_endpoint(body: ProfileCreate):
    from hermes_cli import profiles as profiles_mod
    try:
        path = profiles_mod.create_profile(
            name=body.name,
            clone_from="default" if body.clone_from_default else None,
            clone_config=body.clone_from_default,
            no_skills=body.no_skills,
        )
        # Match the CLI's profile-create flow: fresh named profiles get the
        # bundled skills installed. When cloning from default, create_profile()
        # has already copied the source profile's skills, including any
        # user-installed skills. When no_skills=True, create_profile() wrote
        # the opt-out marker and seed_profile_skills() will no-op.
        if not body.clone_from_default:
            profiles_mod.seed_profile_skills(path, quiet=True)

        # Match the CLI's profile-create flow: named profiles should get a
        # wrapper in ~/.local/bin when the alias is safe to create.
        collision = profiles_mod.check_alias_collision(body.name)
        if not collision:
            profiles_mod.create_wrapper_script(body.name)
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("POST /api/profiles failed")
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "name": body.name, "path": str(path)}


@app.get("/api/profiles/{name}/setup-command")
async def get_profile_setup_command(name: str):
    return {"command": _profile_setup_command(name)}


@app.post("/api/profiles/{name}/open-terminal")
async def open_profile_terminal_endpoint(name: str):
    try:
        command = _profile_setup_command(name)

        if sys.platform.startswith("win"):
            subprocess.Popen(["cmd.exe", "/c", "start", "", command])
        elif sys.platform == "darwin":
            escaped = command.replace("\\", "\\\\").replace('"', '\\"')
            applescript = (
                'tell application "Terminal"\n'
                "activate\n"
                f'do script "{escaped}"\n'
                "end tell"
            )
            subprocess.Popen(["osascript", "-e", applescript])
        else:
            terminal_commands = [
                ("x-terminal-emulator", ["x-terminal-emulator", "-e", "sh", "-lc", command]),
                ("gnome-terminal", ["gnome-terminal", "--", "sh", "-lc", command]),
                ("konsole", ["konsole", "-e", "sh", "-lc", command]),
                ("xfce4-terminal", ["xfce4-terminal", "-e", f"sh -lc '{command}'"]),
                ("mate-terminal", ["mate-terminal", "-e", f"sh -lc '{command}'"]),
                ("lxterminal", ["lxterminal", "-e", f"sh -lc '{command}'"]),
                ("tilix", ["tilix", "-e", "sh", "-lc", command]),
                ("alacritty", ["alacritty", "-e", "sh", "-lc", command]),
                ("kitty", ["kitty", "sh", "-lc", command]),
                ("xterm", ["xterm", "-e", "sh", "-lc", command]),
            ]
            for executable, popen_args in terminal_commands:
                if subprocess.call(
                    ["which", executable],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ) == 0:
                    subprocess.Popen(popen_args)
                    break
            else:
                raise HTTPException(
                    status_code=400,
                    detail="No supported terminal emulator found",
                )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("POST /api/profiles/%s/open-terminal failed", name)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "command": command}


@app.patch("/api/profiles/{name}")
async def rename_profile_endpoint(name: str, body: ProfileRename):
    from hermes_cli import profiles as profiles_mod
    try:
        path = profiles_mod.rename_profile(name, body.new_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, FileExistsError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("PATCH /api/profiles/%s failed", name)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "name": body.new_name, "path": str(path)}


@app.delete("/api/profiles/{name}")
async def delete_profile_endpoint(name: str):
    """Delete a profile. The dashboard collects the user's confirmation in
    its own dialog before this request, so we always pass ``yes=True`` to
    skip the CLI's interactive prompt."""
    from hermes_cli import profiles as profiles_mod
    try:
        path = profiles_mod.delete_profile(name, yes=True)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("DELETE /api/profiles/%s failed", name)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "path": str(path)}


@app.get("/api/profiles/{name}/soul")
async def get_profile_soul(name: str):
    soul_path = _resolve_profile_dir(name) / "SOUL.md"
    if soul_path.exists():
        try:
            return {"content": soul_path.read_text(encoding="utf-8"), "exists": True}
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Could not read SOUL.md: {e}")
    return {"content": "", "exists": False}


@app.put("/api/profiles/{name}/soul")
async def update_profile_soul(name: str, body: ProfileSoulUpdate):
    soul_path = _resolve_profile_dir(name) / "SOUL.md"
    try:
        soul_path.write_text(body.content, encoding="utf-8")
    except OSError as e:
        _log.exception("PUT /api/profiles/%s/soul failed", name)
        raise HTTPException(status_code=500, detail=f"Could not write SOUL.md: {e}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Skills & Tools endpoints
# ---------------------------------------------------------------------------


class SkillToggle(BaseModel):
    name: str
    enabled: bool


@app.get("/api/skills")
async def get_skills():
    from tools.skills_tool import _find_all_skills
    from hermes_cli.skills_config import get_disabled_skills
    config = load_config()
    disabled = get_disabled_skills(config)
    skills = _find_all_skills(skip_disabled=True)
    for s in skills:
        s["enabled"] = s["name"] not in disabled
    return skills


@app.put("/api/skills/toggle")
async def toggle_skill(body: SkillToggle):
    from hermes_cli.skills_config import get_disabled_skills, save_disabled_skills
    config = load_config()
    disabled = get_disabled_skills(config)
    if body.enabled:
        disabled.discard(body.name)
    else:
        disabled.add(body.name)
    save_disabled_skills(config, disabled)
    return {"ok": True, "name": body.name, "enabled": body.enabled}


@app.get("/api/tools/toolsets")
async def get_toolsets():
    from hermes_cli.tools_config import (
        _get_effective_configurable_toolsets,
        _get_platform_tools,
        _toolset_has_keys,
    )
    from toolsets import resolve_toolset

    config = load_config()
    enabled_toolsets = _get_platform_tools(
        config,
        "cli",
        include_default_mcp_servers=False,
    )
    result = []
    for name, label, desc in _get_effective_configurable_toolsets():
        try:
            tools = sorted(set(resolve_toolset(name)))
        except Exception:
            tools = []
        is_enabled = name in enabled_toolsets
        result.append({
            "name": name, "label": label, "description": desc,
            "enabled": is_enabled,
            "available": is_enabled,
            "configured": _toolset_has_keys(name, config),
            "tools": tools,
        })
    return result


# ---------------------------------------------------------------------------
# Raw YAML config endpoint
# ---------------------------------------------------------------------------


class RawConfigUpdate(BaseModel):
    yaml_text: str


@app.get("/api/config/raw")
async def get_config_raw():
    path = get_config_path()
    if not path.exists():
        return {"yaml": ""}
    return {"yaml": path.read_text(encoding="utf-8")}


@app.put("/api/config/raw")
async def update_config_raw(body: RawConfigUpdate):
    try:
        parsed = yaml.safe_load(body.yaml_text)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="YAML must be a mapping")
        save_config(parsed)
        return {"ok": True}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")


# ---------------------------------------------------------------------------
# Token / cost analytics endpoint
# ---------------------------------------------------------------------------


@app.get("/api/analytics/usage")
async def get_usage_analytics(days: int = 30):
    from hermes_state import SessionDB
    from agent.insights import InsightsEngine

    db = SessionDB()
    try:
        cutoff = time.time() - (days * 86400)
        cur = db._conn.execute("""
            SELECT date(started_at, 'unixepoch') as day,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(reasoning_tokens) as reasoning_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as actual_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls
            FROM sessions WHERE started_at > ?
            GROUP BY day ORDER BY day
        """, (cutoff,))
        daily = [dict(r) for r in cur.fetchall()]

        cur2 = db._conn.execute("""
            SELECT model,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls
            FROM sessions WHERE started_at > ? AND model IS NOT NULL
            GROUP BY model ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
        """, (cutoff,))
        by_model = [dict(r) for r in cur2.fetchall()]

        cur3 = db._conn.execute("""
            SELECT SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cache_read_tokens) as total_cache_read,
                   SUM(reasoning_tokens) as total_reasoning,
                   COALESCE(SUM(estimated_cost_usd), 0) as total_estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as total_actual_cost,
                   COUNT(*) as total_sessions,
                   SUM(COALESCE(api_call_count, 0)) as total_api_calls
            FROM sessions WHERE started_at > ?
        """, (cutoff,))
        totals = dict(cur3.fetchone())
        insights_report = InsightsEngine(db).generate(days=days)
        skills = insights_report.get("skills", {
            "summary": {
                "total_skill_loads": 0,
                "total_skill_edits": 0,
                "total_skill_actions": 0,
                "distinct_skills_used": 0,
            },
            "top_skills": [],
        })

        return {
            "daily": daily,
            "by_model": by_model,
            "totals": totals,
            "period_days": days,
            "skills": skills,
        }
    finally:
        db.close()


@app.get("/api/analytics/models")
async def get_models_analytics(days: int = 30):
    """Rich per-model analytics for the Models dashboard page.

    Returns token/cost/session breakdown per model plus capability metadata
    from models.dev (context window, vision, tools, reasoning, etc.).
    """
    from hermes_state import SessionDB

    db = SessionDB()
    try:
        cutoff = time.time() - (days * 86400)

        cur = db._conn.execute("""
            SELECT model,
                   billing_provider,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(reasoning_tokens) as reasoning_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as actual_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls,
                   SUM(tool_call_count) as tool_calls,
                   MAX(started_at) as last_used_at,
                   AVG(input_tokens + output_tokens) as avg_tokens_per_session
            FROM sessions WHERE started_at > ? AND model IS NOT NULL AND model != ''
            GROUP BY model, billing_provider
            ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
        """, (cutoff,))
        rows = [dict(r) for r in cur.fetchall()]

        models = []
        for row in rows:
            provider = row.get("billing_provider") or ""
            model_name = row["model"]
            caps = {}
            try:
                from agent.models_dev import get_model_capabilities
                mc = get_model_capabilities(provider=provider, model=model_name)
                if mc is not None:
                    caps = {
                        "supports_tools": mc.supports_tools,
                        "supports_vision": mc.supports_vision,
                        "supports_reasoning": mc.supports_reasoning,
                        "context_window": mc.context_window,
                        "max_output_tokens": mc.max_output_tokens,
                        "model_family": mc.model_family,
                    }
            except Exception:
                pass

            models.append({
                "model": model_name,
                "provider": provider,
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cache_read_tokens": row["cache_read_tokens"],
                "reasoning_tokens": row["reasoning_tokens"],
                "estimated_cost": row["estimated_cost"],
                "actual_cost": row["actual_cost"],
                "sessions": row["sessions"],
                "api_calls": row["api_calls"],
                "tool_calls": row["tool_calls"],
                "last_used_at": row["last_used_at"],
                "avg_tokens_per_session": row["avg_tokens_per_session"],
                "capabilities": caps,
            })

        totals_cur = db._conn.execute("""
            SELECT COUNT(DISTINCT model) as distinct_models,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cache_read_tokens) as total_cache_read,
                   SUM(reasoning_tokens) as total_reasoning,
                   COALESCE(SUM(estimated_cost_usd), 0) as total_estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as total_actual_cost,
                   COUNT(*) as total_sessions,
                   SUM(COALESCE(api_call_count, 0)) as total_api_calls
            FROM sessions WHERE started_at > ? AND model IS NOT NULL AND model != ''
        """, (cutoff,))
        totals = dict(totals_cur.fetchone())

        return {
            "models": models,
            "totals": totals,
            "period_days": days,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# /api/pty — PTY-over-WebSocket bridge for the dashboard "Chat" tab.
#
# The endpoint spawns the same ``hermes --tui`` binary the CLI uses, behind
# a POSIX pseudo-terminal, and forwards bytes + resize escapes across a
# WebSocket.  The browser renders the ANSI through xterm.js (see
# web/src/pages/ChatPage.tsx).
#
# Auth: ``?token=<session_token>`` query param (browsers can't set
# Authorization on the WS upgrade).  Same ephemeral ``_SESSION_TOKEN`` as
# REST.  Localhost-only — we defensively reject non-loopback clients even
# though uvicorn binds to 127.0.0.1.
# ---------------------------------------------------------------------------

import re
import asyncio

# PTY bridge is POSIX-only (depends on fcntl/termios/ptyprocess).  On native
# Windows the import raises; catch and leave PtyBridge=None so the rest of
# the dashboard (sessions, jobs, metrics, config editor) still loads and the
# /api/pty endpoint cleanly refuses with a WSL-suggested message.
try:
    from hermes_cli.pty_bridge import PtyBridge, PtyUnavailableError
    _PTY_BRIDGE_AVAILABLE = True
except ImportError as _pty_import_err:  # pragma: no cover - Windows-only path
    PtyBridge = None  # type: ignore[assignment]
    _PTY_BRIDGE_AVAILABLE = False

    class PtyUnavailableError(RuntimeError):  # type: ignore[no-redef]
        """Stub on platforms where pty_bridge can't be imported."""
        pass

_RESIZE_RE = re.compile(rb"\x1b\[RESIZE:(\d+);(\d+)\]")
_PTY_READ_CHUNK_TIMEOUT = 0.2
_VALID_CHANNEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Starlette's TestClient reports the peer as "testclient"; treat it as
# loopback so tests don't need to rewrite request scope.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _ws_client_is_allowed(ws: "WebSocket") -> bool:
    """Check if the WebSocket client IP is acceptable.

    Loopback mode: only loopback clients allowed — the legacy
    ``?token=<_SESSION_TOKEN>`` path is the only auth we have, so we
    don't want LAN hosts guessing tokens.

    Gated mode: any peer is allowed — uvicorn's ``proxy_headers=True``
    (enabled when the OAuth gate is active so cookies can pick up
    ``X-Forwarded-Proto``) rewrites ``ws.client.host`` to the
    X-Forwarded-For value, which is the real internet client IP. The
    OAuth gate + single-use ``?ticket=`` is the auth at that point; the
    Host/Origin guard in :func:`_ws_host_origin_is_allowed` is what
    blocks DNS-rebinding here, not the peer IP.
    """
    if getattr(app.state, "auth_required", False):
        return True
    # 0.0.0.0 / :: bind means the operator explicitly opted into all-interfaces
    # (requires --insecure). No IP-level defence possible; rely on token auth
    # and network controls — same trade-off as _is_accepted_host.
    bound_host = getattr(app.state, "bound_host", None)
    if bound_host in {"0.0.0.0", "::"}:
        return True
    client_host = ws.client.host if ws.client else ""
    if not client_host:
        return True
    return client_host in _LOOPBACK_HOSTS


def _ws_host_origin_is_allowed(ws: "WebSocket") -> bool:
    """Apply the dashboard Host/Origin guard to WebSocket upgrades.

    FastAPI HTTP middleware does not run for WebSocket routes, so the
    DNS-rebinding Host check used for normal dashboard HTTP requests must be
    repeated here before accepting the upgrade.  Browsers also send an Origin
    header on WebSocket handshakes; when present, require it to target the
    same bound dashboard host.
    """
    bound_host = getattr(app.state, "bound_host", None)
    if not bound_host:
        return True

    host_header = ws.headers.get("host", "")
    if not _is_accepted_host(host_header, bound_host):
        return False

    origin = ws.headers.get("origin", "")
    if not origin:
        return True

    parsed = urllib.parse.urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    return _is_accepted_host(parsed.netloc, bound_host)


def _ws_request_is_allowed(ws: "WebSocket") -> bool:
    """Return True when the WebSocket upgrade matches dashboard boundaries."""
    return _ws_host_origin_is_allowed(ws) and _ws_client_is_allowed(ws)


def _ws_auth_ok(ws: "WebSocket") -> bool:
    """Validate WS-upgrade auth in either loopback or gated mode.

    Loopback / ``--insecure``: legacy ``?token=<_SESSION_TOKEN>`` query
    parameter, constant-time compared.

    Gated (public bind, no ``--insecure``): ``?ticket=<single-use>`` query
    parameter consumed against the dashboard-auth ticket store. The legacy
    token path is unconditionally rejected in this mode (the SPA bundle
    isn't carrying the token any longer).

    Returns True if the WS should be accepted; callers close with the
    appropriate WS code (4401) on False. Audit-logs the rejection so
    operators can debug "WS keeps closing" issues from the log.
    """
    auth_required = bool(getattr(app.state, "auth_required", False))
    if auth_required:
        ticket = ws.query_params.get("ticket", "")
        if not ticket:
            return False
        # Lazy import — keeps this function importable in test harnesses
        # that don't bring in the dashboard_auth layer.
        from hermes_cli.dashboard_auth.audit import AuditEvent, audit_log
        from hermes_cli.dashboard_auth.ws_tickets import (
            TicketInvalid,
            consume_ticket,
        )

        try:
            consume_ticket(ticket)
            return True
        except TicketInvalid as exc:
            audit_log(
                AuditEvent.WS_TICKET_REJECTED,
                reason=str(exc),
                ip=(ws.client.host if ws.client else ""),
                path=ws.url.path,
            )
            return False

    token = ws.query_params.get("token", "")
    ok = hmac.compare_digest(token.encode(), _SESSION_TOKEN.encode())
    if not ok:
        _log.warning("WS auth failed: path=%s token_len=%d session_len=%d token_head=%s",
                     ws.url.path, len(token), len(_SESSION_TOKEN),
                     token[:8] if len(token) >= 8 else token)
    return ok

# Per-channel subscriber registry used by /api/pub (PTY-side gateway → dashboard)
# and /api/events (dashboard → browser sidebar).  Keyed by an opaque channel id
# the chat tab generates on mount; entries auto-evict when the last subscriber
# drops AND the publisher has disconnected.
_event_channels: dict[str, set] = {}
_event_lock = asyncio.Lock()


# Per-room subscriber registry for /ws/room/{board}/{run_id}.  Each room has a
# background poller that bridges kanban comments/events to chat.  Rooms
# garbage-collect 5 min after the last subscriber disconnects.
@dataclass
class RoomState:
    board: str
    run_id: str
    subscribers: set[WebSocket] = field(default_factory=set)
    poller_task: "asyncio.Task | None" = None
    last_comment_id: int = 0
    last_event_id: int = 0
    task_ids: list[str] = field(default_factory=list)
    gc_timer: "asyncio.Task | None" = None
    created_at: float = field(default_factory=time.time)


_rooms: dict[str, RoomState] = {}
_rooms_lock = asyncio.Lock()

_ROOM_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
_ROOM_GC_SECONDS = 300  # 5 min
_ROOM_POLL_SECONDS = 3


def _resolve_chat_argv(
    resume: Optional[str] = None,
    sidecar_url: Optional[str] = None,
) -> tuple[list[str], Optional[str], Optional[dict]]:
    """Resolve the argv + cwd + env for the chat PTY.

    Default: whatever ``hermes --tui`` would run.  Tests monkeypatch this
    function to inject a tiny fake command (``cat``, ``sh -c 'printf …'``)
    so nothing has to build Node or the TUI bundle.

    Session resume is propagated via the ``HERMES_TUI_RESUME`` env var —
    matching what ``hermes_cli.main._launch_tui`` does for the CLI path.
    Appending ``--resume <id>`` to argv doesn't work because ``ui-tui`` does
    not parse its argv.

    `sidecar_url` (when set) is forwarded as ``HERMES_TUI_SIDECAR_URL`` so
    the spawned ``tui_gateway.entry`` can mirror dispatcher emits to the
    dashboard's ``/api/pub`` endpoint (see :func:`pub_ws`).
    """
    from hermes_cli.main import PROJECT_ROOT, _make_tui_argv

    argv, cwd = _make_tui_argv(PROJECT_ROOT / "ui-tui", tui_dev=False)
    env = os.environ.copy()
    env.setdefault("NODE_ENV", "production")
    # Browser-embedded chat should prefer stable wheel-based scrollback over
    # native terminal mouse tracking. When mouse tracking is enabled, wheel
    # events are consumed by the TUI and forwarded as terminal input, which
    # makes browser-side transcript scrolling feel broken. Keep the terminal
    # build unchanged for native CLI usage; only disable mouse tracking for
    # the dashboard PTY path.
    env.setdefault("HERMES_TUI_DISABLE_MOUSE", "1")
    env.setdefault("HERMES_TUI_INLINE", "1")

    if resume:
        latest_resume, _latest_path = _session_latest_descendant(resume)
        if latest_resume:
            resume = latest_resume
        env["HERMES_TUI_RESUME"] = resume

    if sidecar_url:
        env["HERMES_TUI_SIDECAR_URL"] = sidecar_url

    return list(argv), str(cwd) if cwd else None, env


def _build_sidecar_url(channel: str) -> Optional[str]:
    """ws:// URL the PTY child should publish events to, or None when unbound.

    Loopback / ``--insecure``: uses ``?token=<_SESSION_TOKEN>``.

    Gated mode: mints a single-use ticket via the dashboard-auth ticket
    store (server-side mint, no HTTP round trip — the PTY child is a
    server-spawned process and we trust it). The ticket binds to the
    pseudo-user ``"pty-sidecar"`` so audit logs can distinguish these from
    browser-initiated tickets.

    The single-use lifetime means the PTY child cannot reconnect without a
    new sidecar URL. PTY children open ``/api/pub`` once at startup; if
    reconnect semantics ever become important, this should be upgraded to
    a long-lived process-scoped token.
    """
    host = getattr(app.state, "bound_host", None)
    port = getattr(app.state, "bound_port", None)

    if not host or not port:
        return None

    netloc = f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"

    if getattr(app.state, "auth_required", False):
        # Gated mode — mint a ticket so the WS upgrade survives _ws_auth_ok.
        from hermes_cli.dashboard_auth.ws_tickets import mint_ticket

        ticket = mint_ticket(user_id="pty-sidecar", provider="server-internal")
        qs = urllib.parse.urlencode({"ticket": ticket, "channel": channel})
    else:
        qs = urllib.parse.urlencode({"token": _SESSION_TOKEN, "channel": channel})

    return f"ws://{netloc}/api/pub?{qs}"


async def _broadcast_event(channel: str, payload: str) -> None:
    """Fan out one publisher frame to every subscriber on `channel`."""
    async with _event_lock:
        subs = list(_event_channels.get(channel, ()))

    for sub in subs:
        try:
            await sub.send_text(payload)
        except Exception:
            # Subscriber went away mid-send; the /api/events finally clause
            # will remove it from the registry on its next iteration.
            _log.warning("broadcast send failed for subscriber on %s", channel, exc_info=True)


def _channel_or_close_code(ws: WebSocket) -> Optional[str]:
    """Return the channel id from the query string or None if invalid."""
    channel = ws.query_params.get("channel", "")

    return channel if _VALID_CHANNEL_RE.match(channel) else None


@app.websocket("/api/pty")
async def pty_ws(ws: WebSocket) -> None:
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await ws.close(code=4403)
        return

    # --- auth + loopback check (before accept so we can close cleanly) ---
    if not _ws_auth_ok(ws):
        await ws.close(code=4401)
        return

    if not _ws_request_is_allowed(ws):
        await ws.close(code=4403)
        return

    await ws.accept()

    # On native Windows, the POSIX PTY bridge can't be imported.  Tell the
    # client and close cleanly rather than pretending the feature works.
    if not _PTY_BRIDGE_AVAILABLE:
        await ws.send_text(
            "\r\n\x1b[31mChat unavailable: the embedded terminal requires a "
            "POSIX PTY, which native Windows Python doesn't provide.\x1b[0m\r\n"
            "\x1b[33mInstall Hermes inside WSL2 to use the dashboard's /chat "
            "tab — the rest of the dashboard works here.\x1b[0m\r\n"
        )
        await ws.close(code=1011)
        return

    # --- spawn PTY ------------------------------------------------------
    resume = ws.query_params.get("resume") or None
    channel = _channel_or_close_code(ws)
    sidecar_url = _build_sidecar_url(channel) if channel else None

    try:
        argv, cwd, env = _resolve_chat_argv(resume=resume, sidecar_url=sidecar_url)
    except SystemExit as exc:
        # _make_tui_argv calls sys.exit(1) when node/npm is missing.
        await ws.send_text(f"\r\n\x1b[31mChat unavailable: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return


    try:
        bridge = PtyBridge.spawn(argv, cwd=cwd, env=env)
    except PtyUnavailableError as exc:
        await ws.send_text(f"\r\n\x1b[31mChat unavailable: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return
    except (FileNotFoundError, OSError) as exc:
        await ws.send_text(f"\r\n\x1b[31mChat failed to start: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return

    loop = asyncio.get_running_loop()

    # --- reader task: PTY master → WebSocket ----------------------------
    async def pump_pty_to_ws() -> None:
        while True:
            chunk = await loop.run_in_executor(
                None, bridge.read, _PTY_READ_CHUNK_TIMEOUT
            )
            if chunk is None:  # EOF
                return
            if not chunk:  # no data this tick; yield control and retry
                await asyncio.sleep(0)
                continue
            try:
                await ws.send_bytes(chunk)
            except Exception:
                return

    reader_task = asyncio.create_task(pump_pty_to_ws())

    # --- writer loop: WebSocket → PTY master ----------------------------
    try:
        while True:
            msg = await ws.receive()
            msg_type = msg.get("type")
            if msg_type == "websocket.disconnect":
                break
            raw = msg.get("bytes")
            if raw is None:
                text = msg.get("text")
                raw = text.encode("utf-8") if isinstance(text, str) else b""
            if not raw:
                continue

            # Resize escape is consumed locally, never written to the PTY.
            match = _RESIZE_RE.match(raw)
            if match and match.end() == len(raw):
                cols = int(match.group(1))
                rows = int(match.group(2))
                bridge.resize(cols=cols, rows=rows)
                continue

            bridge.write(raw)
    except WebSocketDisconnect:
        pass
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):
            pass
        bridge.close()


# ---------------------------------------------------------------------------
# /api/ws — JSON-RPC WebSocket for the dashboard native "Chat" tab.
#
# Drives the same `tui_gateway.dispatch` surface Ink uses over stdio, while
# React owns the transcript/composer and renders structured tool/workflow
# metadata directly.
# ---------------------------------------------------------------------------


@app.websocket("/api/ws")
async def gateway_ws(ws: WebSocket) -> None:
    if not _ws_auth_ok(ws):
        await ws.close(code=4401)
        return

    if not _ws_request_is_allowed(ws):
        await ws.close(code=4403)
        return

    from tui_gateway.ws import handle_ws

    await handle_ws(ws)


# ---------------------------------------------------------------------------
# /api/pub + /api/events — chat-tab event broadcast.
#
# The PTY-side ``tui_gateway.entry`` opens /api/pub at startup (driven by
# HERMES_TUI_SIDECAR_URL set in /api/pty's PTY env) and writes every
# dispatcher emit through it.  The dashboard fans those frames out to any
# subscriber that opened /api/events on the same channel id.  This is what
# gives the React sidebar its tool-call feed without breaking the PTY
# child's stdio handshake with Ink.
# ---------------------------------------------------------------------------


@app.websocket("/api/pub")
async def pub_ws(ws: WebSocket) -> None:
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await ws.close(code=4403)
        return

    if not _ws_auth_ok(ws):
        await ws.close(code=4401)
        return

    if not _ws_request_is_allowed(ws):
        await ws.close(code=4403)
        return

    channel = _channel_or_close_code(ws)
    if not channel:
        await ws.close(code=4400)
        return

    await ws.accept()

    try:
        while True:
            await _broadcast_event(channel, await ws.receive_text())
    except WebSocketDisconnect:
        pass


@app.websocket("/api/events")
async def events_ws(ws: WebSocket) -> None:
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await ws.close(code=4403)
        return

    if not _ws_auth_ok(ws):
        await ws.close(code=4401)
        return

    if not _ws_request_is_allowed(ws):
        await ws.close(code=4403)
        return

    channel = _channel_or_close_code(ws)
    if not channel:
        await ws.close(code=4400)
        return

    await ws.accept()

    async with _event_lock:
        _event_channels.setdefault(channel, set()).add(ws)

    try:
        while True:
            # Subscribers don't speak — the receive() just blocks until
            # disconnect so the connection stays open as long as the
            # browser holds it.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _event_lock:
            subs = _event_channels.get(channel)

            if subs is not None:
                subs.discard(ws)

                if not subs:
                    _event_channels.pop(channel, None)


# ---------------------------------------------------------------------------
# Chat Room WebSocket — multi-participant legal swarm chat
# ---------------------------------------------------------------------------


def _format_room_message(
    msg_id: str,
    kind: str,
    text: str,
    *,
    sender: str = "",
    sender_profile: str = "",
    task_id: str = "",
    timestamp: float | None = None,
) -> dict:
    """Build a RoomMessage-compatible JSON dict for WS broadcast."""
    return {
        "id": msg_id,
        "kind": kind,
        "text": text,
        "sender": sender or "",
        "senderProfile": sender_profile or "",
        "taskId": task_id or "",
        "timestamp": timestamp or time.time(),
    }


async def _room_poller(room_id: str) -> None:
    """Background task: poll kanban DB for new events/comments, push to room subscribers."""
    while True:
        await asyncio.sleep(_ROOM_POLL_SECONDS)
        async with _rooms_lock:
            room = _rooms.get(room_id)
            if room is None or not room.subscribers:
                return

        try:
            from hermes_cli.kanban_db import (
                kanban_db_path,
                list_comments_for_tasks,
                list_events_for_tasks,
            )
            db_path = kanban_db_path(board=room.board)
            if not db_path.exists():
                continue
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            try:
                # Fetch new comments and events since last cursor
                new_comments = list_comments_for_tasks(
                    conn, room.task_ids, since_id=room.last_comment_id,
                )
                new_events = list_events_for_tasks(
                    conn, room.task_ids, since_id=room.last_event_id,
                )

                if not new_comments and not new_events:
                    continue

                # Build room messages
                messages: list[dict] = []
                for c in new_comments:
                    # Determine sender from comment metadata
                    sender = c.author or "bot"
                    sender_profile = ""
                    # Try to extract profile from blackboard-style comments
                    body = c.body or ""
                    if body.startswith("[swarm:blackboard] "):
                        continue  # skip blackboard entries, they're internal
                    messages.append(
                        _format_room_message(
                            f"comment-{c.id}",
                            "bot",
                            body,
                            sender=sender,
                            sender_profile=sender_profile,
                            task_id=c.task_id,
                            timestamp=c.created_at,
                        )
                    )
                    if c.id > room.last_comment_id:
                        room.last_comment_id = c.id

                for ev in new_events:
                    kind = ev.kind or ""
                    # Map kanban event kinds to room message text
                    text_map: dict[str, str] = {
                        "completed": f"Task {ev.task_id} completed",
                        "blocked": f"Task {ev.task_id} blocked",
                        "commented": f"Task {ev.task_id} has a new comment",
                        "heartbeat": f"Task {ev.task_id} heartbeat received",
                        "claimed": f"Task {ev.task_id} claimed",
                        "gave_up": f"Task {ev.task_id} gave up",
                        "crashed": f"Task {ev.task_id} worker crashed",
                        "timed_out": f"Task {ev.task_id} timed out",
                    }
                    text = text_map.get(kind, f"Task {ev.task_id}: {kind}")
                    # Include payload details if available
                    if ev.payload and isinstance(ev.payload, dict):
                        if "summary" in ev.payload:
                            text += f" — {ev.payload['summary'][:200]}"
                        elif "comment" in ev.payload:
                            text += f" — {ev.payload['comment'][:200]}"
                    messages.append(
                        _format_room_message(
                            f"event-{ev.id}",
                            "status",
                            text,
                            task_id=ev.task_id,
                            timestamp=ev.created_at,
                        )
                    )
                    if ev.id > room.last_event_id:
                        room.last_event_id = ev.id

            finally:
                conn.close()

            # Broadcast to all subscribers
            if messages:
                dead: list[WebSocket] = []
                for ws in list(room.subscribers):
                    try:
                        for msg in messages:
                            await ws.send_json(msg)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    room.subscribers.discard(ws)

        except Exception:
            _log.exception("room poller error for %s", room_id)


def _ensure_room_poller(room: RoomState, room_id: str) -> None:
    """Start the poller for *room* if not already running."""
    if room.poller_task is None or room.poller_task.done():
        room.poller_task = asyncio.create_task(_room_poller(room_id))


async def _cancel_room_gc(room: RoomState) -> None:
    """Cancel a pending GC timer for *room*."""
    if room.gc_timer is not None and not room.gc_timer.done():
        room.gc_timer.cancel()
    room.gc_timer = None


def _schedule_room_gc(room_id: str) -> None:
    """Schedule garbage collection for a room after a delay of inactivity.

    Caller must hold ``_rooms_lock``.
    """
    async def _gc_after_delay():
        await asyncio.sleep(_ROOM_GC_SECONDS)
        async with _rooms_lock:
            room = _rooms.get(room_id)
            if room is None:
                return
            if room.subscribers:
                return  # someone reconnected
            if room.poller_task and not room.poller_task.done():
                room.poller_task.cancel()
            _rooms.pop(room_id, None)

    room = _rooms.get(room_id)
    if room is not None:
        room.gc_timer = asyncio.create_task(_gc_after_delay())


@app.websocket("/ws/room/{board}/{run_id}")
async def room_ws(ws: WebSocket, board: str, run_id: str) -> None:
    """Chat room WebSocket — multi-participant legal swarm conversation.

    Clients send ``{type: "user_msg", text: "..."}`` to broadcast to all
    subscribers. The server pushes ``RoomMessage`` objects as JSON for every
    new kanban comment/event. On first connect the client should send
    ``{type: "reply"}`` to receive a full replay.
    """
    room_id = f"{board}/{run_id}"

    if not _ROOM_ID_RE.match(room_id):
        await ws.close(code=4400)
        return

    if not _ws_auth_ok(ws):
        await ws.close(code=4401)
        return

    await ws.accept()

    # Register subscriber
    async with _rooms_lock:
        if room_id not in _rooms:
            # Try to populate task_ids from the kanban DB
            import sqlite3
            from hermes_cli.kanban_db import kanban_db_path
            from hermes_cli.kanban_legal_swarm import get_run_task_ids

            db_path = kanban_db_path(board=board)
            task_ids: list[str] = []
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                try:
                    # Find root task — it has idempotency_key matching the run
                    row = conn.execute(
                        "SELECT id FROM tasks WHERE idempotency_key LIKE ? "
                        "AND status != 'archived' ORDER BY created_at DESC LIMIT 1",
                        (f"%:{run_id}:%",),
                    ).fetchone()
                    if row:
                        task_ids = get_run_task_ids(conn, row["id"])
                finally:
                    conn.close()

            _rooms[room_id] = RoomState(
                board=board,
                run_id=run_id,
                task_ids=task_ids,
            )

        room = _rooms[room_id]
        room.subscribers.add(ws)
        await _cancel_room_gc(room)
        _ensure_room_poller(room, room_id)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "user_msg":
                text = str(msg.get("text", "")).strip()
                if not text:
                    continue
                payload = _format_room_message(
                    f"user-{int(time.time() * 1000)}",
                    "user",
                    text,
                )
                # Broadcast to all subscribers including sender
                dead: list[WebSocket] = []
                async with _rooms_lock:
                    subs = list(room.subscribers) if room_id in _rooms else []
                for sub in subs:
                    try:
                        await sub.send_json(payload)
                    except Exception:
                        dead.append(sub)
                if dead:
                    async with _rooms_lock:
                        if room_id in _rooms:
                            for d in dead:
                                _rooms[room_id].subscribers.discard(d)

            elif msg_type == "replay":
                # Send full message history from kanban DB
                from hermes_cli.kanban_db import (
                    kanban_db_path,
                    list_comments_for_tasks,
                    list_events_for_tasks,
                )
                import sqlite3 as _sql

                db_path = kanban_db_path(board=board)
                msgs: list[dict] = []
                if db_path.exists():
                    conn = _sql.connect(str(db_path))
                    try:
                        async with _rooms_lock:
                            tids = list(_rooms[room_id].task_ids) if room_id in _rooms else []
                        for c in list_comments_for_tasks(conn, tids):
                            body = c.body or ""
                            if body.startswith("[swarm:blackboard] "):
                                continue
                            msgs.append(
                                _format_room_message(
                                    f"comment-{c.id}", "bot", body,
                                    sender=c.author, task_id=c.task_id,
                                    timestamp=c.created_at,
                                )
                            )
                        for ev in list_events_for_tasks(conn, tids):
                            kind = ev.kind or ""
                            if kind == "commented":
                                continue  # comment already captured above
                            msgs.append(
                                _format_room_message(
                                    f"event-{ev.id}", "status",
                                    f"Task {ev.task_id}: {kind}",
                                    task_id=ev.task_id,
                                    timestamp=ev.created_at,
                                )
                            )
                    finally:
                        conn.close()

                # Send as individual messages
                for m in sorted(msgs, key=lambda x: x["timestamp"]):
                    await ws.send_json(m)

    except WebSocketDisconnect:
        pass
    finally:
        # Unregister subscriber
        async with _rooms_lock:
            if room_id in _rooms:
                _rooms[room_id].subscribers.discard(ws)
                if not _rooms[room_id].subscribers:
                    _schedule_room_gc(room_id)


# ═══════════════════════════════════════════════════════════════════════════
# Standalone chatroom WebSocket — real-time multi-bot chat, no kanban dep.
# ═══════════════════════════════════════════════════════════════════════════

# In-memory chatroom state — simpler than RoomState since no kanban polling.
_CHATROOMS: dict[str, "ChatRoom"] = {}
_CHATROOMS_LOCK = asyncio.Lock()
_CHATROOM_GC_SECONDS = 600  # 10 min idle before GC


@dataclass
class ChatRoom:
    room_id: str
    subscribers: set[WebSocket] = field(default_factory=set)
    # Track which subscribers are bots so we can GC rooms that only have bots
    bot_subscribers: set[WebSocket] = field(default_factory=set)
    # In-memory message log for replay on join
    messages: list[dict] = field(default_factory=list)
    max_messages: int = 200
    # Subprocesses for bot workers
    bot_procs: dict[str, "asyncio.subprocess.Process"] = field(default_factory=dict)
    gc_timer: "asyncio.Task | None" = None
    created_at: float = field(default_factory=time.time)


def _chatroom_gc_after_delay(room_id: str) -> None:
    """Caller must hold _CHATROOMS_LOCK when calling but NOT when scheduling."""

    async def _gc():
        await asyncio.sleep(_CHATROOM_GC_SECONDS)
        async with _CHATROOMS_LOCK:
            room = _CHATROOMS.get(room_id)
            if room is None:
                return
            if room.subscribers:
                return
            # Kill any lingering bot processes
            for name, proc in list(room.bot_procs.items()):
                try:
                    proc.kill()
                except Exception:
                    pass
            _CHATROOMS.pop(room_id, None)

    room = _CHATROOMS.get(room_id)
    if room is not None:
        room.gc_timer = asyncio.create_task(_gc())


# Bot profiles that are auto-spawned when a chatroom comes alive
_CHATROOM_BOT_PROFILES = {
    "lex-coordinator": "Gavel",
    "lex-drafter": "PenTool",
    "lex-reviewer-content": "Search",
    "lex-reviewer-format": "Layout",
    "lex-reviewer-xref": "Link",
    "lex-reviewer-ts": "FileText",
}


@dataclass
class ChatRoomBot:
    id: str
    name: str
    icon: str
    profile: str
    kind: str


def _get_available_bots() -> list[ChatRoomBot]:
    """Return the list of available legal bot profiles for chatroom."""
    bots: list[ChatRoomBot] = []
    for profile, icon in _CHATROOM_BOT_PROFILES.items():
        name = profile.replace("lex-", "").replace("-", " ").title()
        kind = ""
        if "coordinator" in profile:
            kind = "coordinator"
        elif "drafter" in profile:
            kind = "worker"
        elif "reviewer" in profile:
            kind = "reviewer"
        bots.append(ChatRoomBot(
            id=profile,
            name=name,
            icon=icon,
            profile=profile,
            kind=kind,
        ))
    return bots


async def _spawn_bot_worker(
    room_id: str,
    bot_profile: str,
    bot_name: str,
) -> "asyncio.subprocess.Process | None":
    """Spawn a chatroom bot worker subprocess that connects back to the room WS."""
    try:
        from hermes_cli.main import PROJECT_ROOT

        python = sys.executable
        worker_path = Path(__file__).parent / "chatroom_bot.py"

        if not worker_path.exists():
            _log.warning("chatroom_bot.py not found at %s", worker_path)
            return None

        # Build the WS URL the bot connects back to
        host = getattr(app.state, "bound_host", "127.0.0.1")
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        port = getattr(app.state, "bound_port", 9119)
        ws_url = f"ws://{host}:{port}/ws/chatroom/{room_id}"

        env = os.environ.copy()
        env["BOT_PROFILE"] = bot_profile
        env["BOT_NAME"] = bot_name
        env["ROOM_ID"] = room_id
        env["RELAY_WS_URL"] = ws_url
        env["HERMES_SESSION_TOKEN"] = _SESSION_TOKEN
        env["SIMULATE"] = "1"  # FIXME: Remove after debugging

        proc = await asyncio.create_subprocess_exec(
            python,
            str(worker_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _log.info("Spawned chatroom bot %s (pid=%d) for room %s", bot_name, proc.pid, room_id)
        return proc
    except Exception:
        _log.exception("Failed to spawn bot %s for room %s", bot_name, room_id)
        return None


@dataclass
class _ChatroomMsg:
    id: str
    msg_type: str  # "message", "system", "thinking"
    username: str
    content: str
    timestamp: float
    role: str = "user"
    profile: str = ""


def _format_chatroom_msg(cm: _ChatroomMsg) -> dict:
    kind: str
    if cm.msg_type == "system":
        kind = "system"
    elif cm.msg_type in ("thinking", "thinking_end"):
        kind = "status"
    elif cm.role == "bot":
        kind = "bot"
    else:
        kind = "user"
    return {
        "id": cm.id,
        "kind": kind,
        "text": cm.content,
        "sender": cm.username,
        "senderProfile": cm.profile or cm.username,
        "timestamp": cm.timestamp,
    }


@app.websocket("/ws/chatroom/{room_id}")
async def chatroom_ws(ws: WebSocket, room_id: str) -> None:
    """Standalone multi-bot chatroom WebSocket.

    No kanban dependency — messages are relayed in real-time between
    connected clients. Bot workers connect back to this WS and respond
    to @mentions via Hermes CLIsubprocesses.
    """
    if not _ws_auth_ok(ws):
        await ws.accept()
        await ws.close(code=4401, reason="Unauthorized")
        return

    # Validate room_id
    if not re.match(r"^[a-zA-Z0-9_.-]+$", room_id):
        await ws.close(code=4400, reason="Invalid room_id")
        return

    my_username = ""
    my_role = "user"

    try:
        await ws.accept()

        # ── Read loop ───────────────────────────────────────────────────
        async for raw in ws.iter_json():
            msg_type = (raw.get("type") or "").strip()
            if not msg_type:
                continue

            if msg_type == "join":
                my_username = (raw.get("username") or f"user-{id(ws):x}").strip()
                my_role = (raw.get("role") or "user").strip()
                bot_profile = (raw.get("profile") or "").strip()

                async with _CHATROOMS_LOCK:
                    if room_id not in _CHATROOMS:
                        _CHATROOMS[room_id] = ChatRoom(room_id=room_id)
                        # Cancel any lingering GC timer
                        if _CHATROOMS[room_id].gc_timer:
                            _CHATROOMS[room_id].gc_timer.cancel()
                    room = _CHATROOMS[room_id]
                    room.subscribers.add(ws)
                    if my_role == "bot":
                        room.bot_subscribers.add(ws)

                # Send history replay to the joining client
                async with _CHATROOMS_LOCK:
                    history = list(room.messages[-100:])

                if history:
                    await ws.send_json({
                        "id": f"replay-{int(time.time())}",
                        "kind": "system",
                        "text": "",
                        "sender": "",
                        "timestamp": time.time(),
                        "_replay": history,
                    })

                # Broadcast join to others if it's a user (not a bot)
                if my_role != "bot":
                    join_msg = _ChatroomMsg(
                        id=f"join-{int(time.time())}",
                        msg_type="system",
                        username="system",
                        content=f"{my_username} joined the room",
                        timestamp=time.time(),
                    )
                    async with _CHATROOMS_LOCK:
                        room.messages.append({
                            "id": join_msg.id,
                            "type": "system",
                            "username": join_msg.username,
                            "content": join_msg.content,
                            "timestamp": join_msg.timestamp,
                        })
                        room.messages = room.messages[-room.max_messages:]
                    fmtd = _format_chatroom_msg(join_msg)
                    await _chatroom_broadcast(room_id, fmtd, exclude={ws})

                # Auto-spawn bot workers if this is the first user
                if my_role != "bot":
                    pass  # DISABLED: subprocess bots replaced by in-process @mention handling
                    # await _chatroom_ensure_bots(room_id)

            elif msg_type == "message":
                content = (raw.get("content") or "").strip()
                if not content:
                    continue

                msg = _ChatroomMsg(
                    id=f"msg-{int(time.time() * 1000)}-{id(ws):x}",
                    msg_type="message",
                    username=my_username,
                    content=content,
                    timestamp=time.time(),
                    role=my_role,
                )

                # Persist to in-memory log
                async with _CHATROOMS_LOCK:
                    room = _CHATROOMS.get(room_id)
                    if room:
                        room.messages.append({
                            "id": msg.id,
                            "type": "message",
                            "username": msg.username,
                            "content": msg.content,
                            "timestamp": msg.timestamp,
                            "role": msg.role,
                        })
                        room.messages = room.messages[-room.max_messages:]

                fmtd = _format_chatroom_msg(msg)
                await _chatroom_broadcast(room_id, fmtd)

                # Handle @mentions — spawn background tasks for bot responses
                mentions = _parse_mentions(content)
                if mentions:
                    for bot_name in mentions:
                        if bot_name in _CHATROOM_BOT_PROFILES:
                            asyncio.create_task(
                                _handle_chatroom_mention(room_id, bot_name, content, my_username)
                            )

            elif msg_type == "thinking" or msg_type == "thinking_end":
                # Relay thinking status from bots to all subscribers
                fmtd = _format_chatroom_msg(_ChatroomMsg(
                    id=f"think-{int(time.time() * 1000)}",
                    msg_type=msg_type,
                    username=raw.get("username", ""),
                    content=raw.get("status", raw.get("content", "")),
                    timestamp=time.time(),
                    role="bot",
                    profile=raw.get("profile", ""),
                ))
                await _chatroom_broadcast(room_id, fmtd)

            elif msg_type == "replay":
                # Client requests replay — resend history
                async with _CHATROOMS_LOCK:
                    room = _CHATROOMS.get(room_id)
                    history = list(room.messages[-100:]) if room else []
                if history:
                    await ws.send_json({
                        "id": f"replay-{int(time.time())}",
                        "kind": "system",
                        "text": "",
                        "sender": "",
                        "timestamp": time.time(),
                        "_replay": history,
                    })

    except WebSocketDisconnect:
        pass
    finally:
        # Remove from room and decide if bots should be evicted
        do_gc = False
        evict_bots: list[WebSocket] = []
        kill_procs: list["asyncio.subprocess.Process"] = []
        leave_msg_data: dict | None = None
        async with _CHATROOMS_LOCK:
            room = _CHATROOMS.get(room_id)
            if room:
                room.subscribers.discard(ws)
                room.bot_subscribers.discard(ws)
                if my_username and my_role != "bot":
                    leave_msg_data = _format_chatroom_msg(_ChatroomMsg(
                        id=f"leave-{int(time.time())}",
                        msg_type="system",
                        username="system",
                        content=f"{my_username} left the room",
                        timestamp=time.time(),
                    ))
                    # If no human subscribers remain, evict bots so room can GC
                    human_count = len(room.subscribers) - len(room.bot_subscribers)
                    if human_count <= 0:
                        evict_bots = list(room.bot_subscribers)
                        kill_procs = list(room.bot_procs.values())
                        room.bot_procs.clear()
                if not room.subscribers:
                    do_gc = True

        # Broadcast leave (outside lock to avoid deadlock with bot disconnects)
        if leave_msg_data is not None:
            await _chatroom_broadcast(room_id, leave_msg_data)

        # Kill bot processes so they don't reconnect
        for proc in kill_procs:
            try:
                proc.kill()
            except Exception:
                pass

        # Close bot connections (outside lock — their disconnect handlers need it)
        for bot_ws in evict_bots:
            try:
                await bot_ws.close(4001, "Room empty")
            except Exception:
                pass

        if do_gc:
            async with _CHATROOMS_LOCK:
                if room_id in _CHATROOMS and not _CHATROOMS[room_id].subscribers:
                    _chatroom_gc_after_delay(room_id)


def _parse_mentions(content: str) -> list[str]:
    """Extract @botname mentions from a message. Returns deduplicated list."""
    import re as _re
    names: list[str] = []
    seen: set[str] = set()
    for m in _re.finditer(r"@([a-zA-Z][a-zA-Z0-9_.-]*)", content):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


async def _handle_chatroom_mention(
    room_id: str,
    bot_name: str,
    user_message: str,
    sender: str,
) -> None:
    """Process an @mention for a bot and send its response to the room."""
    profile = bot_name  # Bot name IS the profile name (e.g. "lex-drafter")
    display_name = bot_name.replace("lex-", "").replace("-", " ").title()

    # Send thinking status
    thinking_msg = _format_chatroom_msg(_ChatroomMsg(
        id=f"think-{int(time.time() * 1000)}-{bot_name}",
        msg_type="thinking",
        username=bot_name,
        content="processing",
        timestamp=time.time(),
        role="bot",
        profile=profile,
    ))
    await _chatroom_broadcast(room_id, thinking_msg)

    # Build prompt and call Hermes CLI
    prompt = (
        f"The user @mentioned you in the chatroom. "
        f"Reply helpfully as {bot_name} ({profile}).\n"
        f"Sender: {sender}\n"
        f"Message: {user_message}\n"
        f"Reply directly (do NOT include @-name prefix):"
    )

    _log.info("CHATROOM MENTION bot=%s room=%s sender=%s msg_len=%d",
              bot_name, room_id, sender, len(user_message))
    try:
        # Use venv hermes directly — the shim may drop privs/chdir unexpectedly
        hermes_bin = os.environ.get("HERMES_VENV_BIN", "/opt/hermes/.venv/bin/hermes")
        proc = await asyncio.create_subprocess_exec(
            hermes_bin,
            "-p", profile,
            "chat",
            "-q", prompt,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _log.info("CHATROOM MENTION bot=%s pid=%d spawned, waiting...", bot_name, proc.pid)
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=120,
        )
        _log.info("CHATROOM MENTION bot=%s pid=%d rc=%d out_len=%d err_len=%d",
                  bot_name, proc.pid, proc.returncode,
                  len(stdout) if stdout else 0, len(stderr) if stderr else 0)
        if proc.returncode != 0:
            err = (stderr or b"").decode(errors="replace").strip()
            reply = f"[{display_name}] Error: Hermes exited {proc.returncode}: {err[:200]}"
        else:
            out = ((stdout or b"") + (stderr or b"")).decode(errors="replace")
            # Extract the agent response from between the box-drawing markers
            # Box openers: ╭─ ... ─╮ (U+256D ... U+256E)
            # Box closers: ╰─ ... ─╯ (U+2570 ... U+256F)
            import re as _re
            m = _re.search(r"╭─.+─╮\s*\n(.*?)\n\s*╰", out, _re.DOTALL)
            if m:
                reply = m.group(1).strip()
            else:
                # Fallback: take everything between the separator line and the resume hint
                lines = out.split("\n")
                cleaned: list[str] = []
                started = False
                for line in lines:
                    s = line.strip()
                    if not started:
                        if s.startswith("──") or s.startswith("╭"):
                            started = True
                        continue
                    if s.startswith("Resume this") or s.startswith("Session:") or s.startswith("Duration:"):
                        break
                    if s.startswith("╰"):  # box closer
                        continue
                    cleaned.append(line)
                reply = "\n".join(cleaned).strip()
            if not reply:
                reply = f"[{display_name}] (empty response)"
    except asyncio.TimeoutError:
        _log.warning("CHATROOM MENTION bot=%s TIMEOUT", bot_name)
        reply = f"[{display_name}] Timed out after 120s"
    except FileNotFoundError:
        _log.error("CHATROOM MENTION bot=%s hermes binary not found", bot_name)
        reply = f"[{display_name}] Hermes binary not found"
    except Exception as e:
        _log.exception("CHATROOM MENTION bot=%s ERROR", bot_name)
        reply = f"[{display_name}] Error: {e}"

    # Send thinking_end
    thinking_end_msg = _format_chatroom_msg(_ChatroomMsg(
        id=f"think-end-{int(time.time() * 1000)}-{bot_name}",
        msg_type="thinking_end",
        username=bot_name,
        content="",
        timestamp=time.time(),
        role="bot",
        profile=profile,
    ))
    await _chatroom_broadcast(room_id, thinking_end_msg)

    # Send reply
    reply_msg = _format_chatroom_msg(_ChatroomMsg(
        id=f"msg-{int(time.time() * 1000)}-{bot_name}",
        msg_type="message",
        username=bot_name,
        content=reply,
        timestamp=time.time(),
        role="bot",
        profile=profile,
    ))
    await _chatroom_broadcast(room_id, reply_msg)


async def _chatroom_broadcast(
    room_id: str,
    msg: dict,
    *,
    exclude: set[WebSocket] | None = None,
) -> None:
    """Send a JSON message to all subscribers of a chatroom."""
    ex = exclude or set()
    async with _CHATROOMS_LOCK:
        room = _CHATROOMS.get(room_id)
        if room is None:
            _log.warning("CHATROOM BROADCAST room=%s NOT FOUND", room_id)
            return
        subs = list(room.subscribers)
    _log.debug("CHATROOM BROADCAST room=%s subs=%d ex=%d", room_id, len(subs), len(ex))

    async def _send_one(ws: WebSocket) -> None:
        if ws in ex:
            return
        try:
            await asyncio.wait_for(ws.send_json(msg), timeout=1.0)
        except Exception as e:
            _log.debug("CHATROOM SEND FAIL ws=%s err=%s", id(ws), e)

    await asyncio.gather(*(_send_one(ws) for ws in subs), return_exceptions=True)


async def _chatroom_ensure_bots(room_id: str) -> None:
    """Ensure bot worker subprocesses are spawned for a chatroom."""
    _log.debug("CHATROOM ENSURE_BOTS room=%s START pid=%d", room_id, os.getpid())
    async with _CHATROOMS_LOCK:
        room = _CHATROOMS.get(room_id)
        if room is None:
            return
        # Purge dead processes before checking
        dead: list[str] = []
        for bid, proc in room.bot_procs.items():
            if proc.returncode is not None:
                dead.append(bid)
        for bid in dead:
            del room.bot_procs[bid]
        if room.bot_procs:
            _log.debug("CHATROOM ENSURE_BOTS room=%s bots already running", room_id)
            return

    bots = _get_available_bots()
    _log.debug("CHATROOM ENSURE_BOTS room=%s spawning %d bots", room_id, len(bots))
    for bot in bots:
        proc = await _spawn_bot_worker(room_id, bot.profile, bot.id)
        if proc:
            async with _CHATROOMS_LOCK:
                room = _CHATROOMS.get(room_id)
                if room:
                    room.bot_procs[bot.id] = proc

    async with _CHATROOMS_LOCK:
        room = _CHATROOMS.get(room_id)
        sub_cnt = len(room.subscribers) if room else -1
    _log.debug("CHATROOM ENSURE_BOTS room=%s DONE subs=%d", room_id, sub_cnt)


@app.get("/api/chatroom/bots")
async def list_chatroom_bots():
    """Return available legal chatroom bot profiles."""
    return {
        "ok": True,
        "bots": [
            {
                "id": b.id,
                "name": b.name,
                "kind": b.kind,
                "icon": b.icon,
                "profile": b.profile,
            }
            for b in _get_available_bots()
        ],
    }


@app.post("/api/chatroom/{room_id}")
async def create_chatroom(room_id: str, request: Request):
    """Create or get a chatroom. Returns room info.

    Body (optional): {project_id: str, workflow_id: str} — if both provided,
    also compiles a kanban workflow and links it to the room.
    """
    if not re.match(r"^[a-zA-Z0-9_.-]+$", room_id):
        raise HTTPException(status_code=400, detail="Invalid room_id")

    kanban_info: dict = {}
    body: dict = {}
    try:
        body = await request.json() or {}
    except Exception:
        pass

    project_id = (body.get("project_id") or "").strip()
    workflow_id = (body.get("workflow_id") or "").strip()

    if project_id and workflow_id:
        try:
            from hermes_cli.kanban_legal_swarm import compile_workflow

            project = _resolve_project(project_id)
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            project_dir = project.get("cwd") or project.get("directory") or ""
            run = compile_workflow(
                project_dir=project_dir,
                workflow_id=workflow_id,
                params=body.get("params") or {},
            )
            kanban_info = {
                "board": run.board,
                "run_id": run.run_id,
                "workflow_id": run.workflow_id,
                "root_task_id": run.root_task_id,
                "node_count": len(run.node_mappings),
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            _log.exception("compile_workflow failed for chatroom %s", room_id)
            raise HTTPException(status_code=500, detail=str(e))

    return {
        "ok": True,
        "room_id": room_id,
        "bots": [
            {"id": b.id, "name": b.name, "kind": b.kind}
            for b in _get_available_bots()
        ],
        "kanban": kanban_info or None,
    }


def _normalise_prefix(raw: Optional[str]) -> str:
    """Normalise an X-Forwarded-Prefix header value.

    Thin re-export of :func:`hermes_cli.dashboard_auth.prefix.normalise_prefix`
    — the single source of truth lives in the dashboard_auth package so
    the gate middleware, the OAuth routes, the cookie helpers, and the
    SPA mount all agree on validation rules.
    """
    from hermes_cli.dashboard_auth.prefix import normalise_prefix
    return normalise_prefix(raw)


def mount_spa(application: FastAPI):
    """Mount the built SPA. Falls back to index.html for client-side routing.

    The session token is injected into index.html via a ``<script>`` tag so
    the SPA can authenticate against protected API endpoints without a
    separate (unauthenticated) token-dispensing endpoint.

    When served behind a path-prefix reverse proxy (e.g.
    ``mission-control.tilos.com/hermes/*`` -> local Caddy -> :9119), the
    proxy injects ``X-Forwarded-Prefix: /hermes`` on every request. We
    rewrite the served ``index.html`` so absolute asset URLs (``/assets/...``)
    and the SPA's runtime ``__HERMES_BASE_PATH__`` honour that prefix
    without rebuilding the bundle.
    """
    if not WEB_DIST.exists():
        @application.get("/{full_path:path}")
        async def no_frontend(full_path: str):
            return JSONResponse(
                {"error": "Frontend not built. Run: cd web && npm run build"},
                status_code=404,
            )
        return

    _index_path = WEB_DIST / "index.html"

    def _serve_index(prefix: str = ""):
        """Return index.html with the session token + base-path injected.

        ``prefix`` is the normalised ``X-Forwarded-Prefix`` (e.g. ``/hermes``)
        or empty string when served at root.

        When the OAuth auth gate is active (``app.state.auth_required``),
        the legacy ``_SESSION_TOKEN`` is NOT injected — the SPA reads
        identity from ``/api/auth/me`` over cookie auth instead.  The
        ``__HERMES_AUTH_REQUIRED__`` flag lets the SPA pick the right
        auth scheme for /api/pty and /api/ws (ticket vs token).
        """
        html = _index_path.read_text()
        chat_js = "true"
        embedded_chat_js = "true" if _DASHBOARD_EMBEDDED_CHAT_ENABLED else "false"
        gated = bool(getattr(app.state, "auth_required", False))
        gated_js = "true" if gated else "false"
        if gated:
            bootstrap_script = (
                f"<script>"
                f"window.__HERMES_DASHBOARD_CHAT__={chat_js};"
                f"window.__HERMES_DASHBOARD_EMBEDDED_CHAT__={embedded_chat_js};"
                f'window.__HERMES_BASE_PATH__="{prefix}";'
                f"window.__HERMES_AUTH_REQUIRED__={gated_js};"
                f"</script>"
            )
        else:
            bootstrap_script = (
                f'<script>window.__HERMES_SESSION_TOKEN__="{_SESSION_TOKEN}";'
                f"window.__HERMES_DASHBOARD_CHAT__={chat_js};"
                f"window.__HERMES_DASHBOARD_EMBEDDED_CHAT__={embedded_chat_js};"
                f'window.__HERMES_BASE_PATH__="{prefix}";'
                f"window.__HERMES_AUTH_REQUIRED__={gated_js};"
                f"</script>"
            )
        if prefix:
            # Rewrite absolute asset URLs baked into the Vite build so the
            # browser fetches them through the same proxy prefix.
            html = html.replace('href="/assets/', f'href="{prefix}/assets/')
            html = html.replace('src="/assets/', f'src="{prefix}/assets/')
            html = html.replace('href="/favicon.ico"', f'href="{prefix}/favicon.ico"')
            html = html.replace('href="/fonts/', f'href="{prefix}/fonts/')
            html = html.replace('href="/ds-assets/', f'href="{prefix}/ds-assets/')
            html = html.replace('src="/ds-assets/', f'src="{prefix}/ds-assets/')
        html = html.replace("</head>", f"{bootstrap_script}</head>", 1)
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    # When served behind a path-prefix proxy, the built CSS contains
    # absolute ``url(/fonts/...)`` and ``url(/ds-assets/...)`` references.
    # Browsers resolve those against the document origin, which means
    # under ``/hermes`` they'd hit ``mission-control.tilos.com/fonts/...``
    # (the MC Pages app), not the Hermes backend. Intercept CSS asset
    # requests BEFORE the StaticFiles mount and rewrite the absolute paths
    # when a prefix is in play.
    @application.get("/assets/{filename}.css")
    async def serve_css(filename: str, request: Request):
        css_path = WEB_DIST / "assets" / f"{filename}.css"
        if not css_path.is_file() or not css_path.resolve().is_relative_to(
            WEB_DIST.resolve()
        ):
            return JSONResponse({"error": "not found"}, status_code=404)
        prefix = _normalise_prefix(request.headers.get("x-forwarded-prefix"))
        css = css_path.read_text()
        if prefix:
            for asset_dir in ("/fonts/", "/fonts-terminal/", "/ds-assets/", "/assets/"):
                css = css.replace(f"url({asset_dir}", f"url({prefix}{asset_dir}")
                css = css.replace(f"url(\"{asset_dir}", f"url(\"{prefix}{asset_dir}")
                css = css.replace(f"url('{asset_dir}", f"url('{prefix}{asset_dir}")
        return Response(content=css, media_type="text/css")

    application.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @application.get("/{full_path:path}")
    async def serve_spa(full_path: str, request: Request):
        prefix = _normalise_prefix(request.headers.get("x-forwarded-prefix"))
        file_path = WEB_DIST / full_path
        # Prevent path traversal via url-encoded sequences (%2e%2e/)
        if (
            full_path
            and file_path.resolve().is_relative_to(WEB_DIST.resolve())
            and file_path.exists()
            and file_path.is_file()
        ):
            return FileResponse(file_path)
        return _serve_index(prefix)


# ---------------------------------------------------------------------------
# Dashboard theme endpoints
# ---------------------------------------------------------------------------

# Built-in dashboard themes — label + description only.  The actual color
# definitions live in the frontend (web/src/themes/presets.ts).
_BUILTIN_DASHBOARD_THEMES = [
    {"name": "default",       "label": "Hermes Teal",         "description": "Classic dark teal — the canonical Hermes look"},
    {"name": "default-large", "label": "Hermes Teal (Large)", "description": "Hermes Teal with bigger fonts and roomier spacing"},
    {"name": "midnight",      "label": "Midnight",            "description": "Deep blue-violet with cool accents"},
    {"name": "ember",     "label": "Ember",          "description": "Warm crimson and bronze — forge vibes"},
    {"name": "mono",      "label": "Mono",           "description": "Clean grayscale — minimal and focused"},
    {"name": "cyberpunk", "label": "Cyberpunk",      "description": "Neon green on black — matrix terminal"},
    {"name": "rose",      "label": "Rosé",           "description": "Soft pink and warm ivory — easy on the eyes"},
]


def _parse_theme_layer(value: Any, default_hex: str, default_alpha: float = 1.0) -> Optional[Dict[str, Any]]:
    """Normalise a theme layer spec from YAML into `{hex, alpha}` form.

    Accepts shorthand (a bare hex string) or full dict form.  Returns
    ``None`` on garbage input so the caller can fall back to a built-in
    default rather than blowing up.
    """
    if value is None:
        return {"hex": default_hex, "alpha": default_alpha}
    if isinstance(value, str):
        return {"hex": value, "alpha": default_alpha}
    if isinstance(value, dict):
        hex_val = value.get("hex", default_hex)
        alpha_val = value.get("alpha", default_alpha)
        if not isinstance(hex_val, str):
            return None
        try:
            alpha_f = float(alpha_val)
        except (TypeError, ValueError):
            alpha_f = default_alpha
        return {"hex": hex_val, "alpha": max(0.0, min(1.0, alpha_f))}
    return None


_THEME_DEFAULT_TYPOGRAPHY: Dict[str, str] = {
    "fontSans": 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    "fontMono": 'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace',
    "baseSize": "15px",
    "lineHeight": "1.55",
    "letterSpacing": "0",
}

_THEME_DEFAULT_LAYOUT: Dict[str, str] = {
    "radius": "0.5rem",
    "density": "comfortable",
}

_THEME_OVERRIDE_KEYS = {
    "card", "cardForeground", "popover", "popoverForeground",
    "primary", "primaryForeground", "secondary", "secondaryForeground",
    "muted", "mutedForeground", "accent", "accentForeground",
    "destructive", "destructiveForeground", "success", "warning",
    "border", "input", "ring",
}

# Well-known named asset slots themes can populate.  Any other keys under
# ``assets.custom`` are exposed as ``--theme-asset-custom-<key>`` CSS vars
# for plugin/shell use.
_THEME_NAMED_ASSET_KEYS = {"bg", "hero", "logo", "crest", "sidebar", "header"}

# Component-style buckets themes can override.  The value under each bucket
# is a mapping from camelCase property name to CSS string; each pair emits
# ``--component-<bucket>-<kebab-property>`` on :root.  The frontend's shell
# components (Card, App header, Backdrop, etc.) consume these vars so themes
# can restyle chrome (clip-path, border-image, segmented progress, etc.)
# without shipping their own CSS.
_THEME_COMPONENT_BUCKETS = {
    "card", "header", "footer", "sidebar", "tab",
    "progress", "badge", "backdrop", "page",
}

_THEME_LAYOUT_VARIANTS = {"standard", "cockpit", "tiled"}

# Cap on customCSS length so a malformed/oversized theme YAML can't blow up
# the response payload or the <style> tag.  32 KiB is plenty for every
# practical reskin (the Strike Freedom demo is ~2 KiB).
_THEME_CUSTOM_CSS_MAX = 32 * 1024


def _normalise_theme_definition(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise a user theme YAML into the wire format `ThemeProvider`
    expects.  Returns ``None`` if the theme is unusable.

    Accepts both the full schema (palette/typography/layout) and a loose
    form with bare hex strings, so hand-written YAMLs stay friendly.
    """
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    # Palette
    palette_src = data.get("palette", {}) if isinstance(data.get("palette"), dict) else {}
    # Allow top-level `colors.background` as a shorthand too.
    colors_src = data.get("colors", {}) if isinstance(data.get("colors"), dict) else {}

    def _layer(key: str, default_hex: str, default_alpha: float = 1.0) -> Dict[str, Any]:
        spec = palette_src.get(key, colors_src.get(key))
        parsed = _parse_theme_layer(spec, default_hex, default_alpha)
        return parsed if parsed is not None else {"hex": default_hex, "alpha": default_alpha}

    palette = {
        "background": _layer("background", "#041c1c", 1.0),
        "midground": _layer("midground", "#ffe6cb", 1.0),
        "foreground": _layer("foreground", "#ffffff", 0.0),
        "warmGlow": palette_src.get("warmGlow") or data.get("warmGlow") or "rgba(255, 189, 56, 0.35)",
        "noiseOpacity": 1.0,
    }
    raw_noise = palette_src.get("noiseOpacity", data.get("noiseOpacity"))
    try:
        palette["noiseOpacity"] = float(raw_noise) if raw_noise is not None else 1.0
    except (TypeError, ValueError):
        palette["noiseOpacity"] = 1.0

    # Typography
    typo_src = data.get("typography", {}) if isinstance(data.get("typography"), dict) else {}
    typography = dict(_THEME_DEFAULT_TYPOGRAPHY)
    for key in ("fontSans", "fontMono", "fontDisplay", "fontUrl", "baseSize", "lineHeight", "letterSpacing"):
        val = typo_src.get(key)
        if isinstance(val, str) and val.strip():
            typography[key] = val

    # Layout
    layout_src = data.get("layout", {}) if isinstance(data.get("layout"), dict) else {}
    layout = dict(_THEME_DEFAULT_LAYOUT)
    radius = layout_src.get("radius")
    if isinstance(radius, str) and radius.strip():
        layout["radius"] = radius
    density = layout_src.get("density")
    if isinstance(density, str) and density in {"compact", "comfortable", "spacious"}:
        layout["density"] = density

    # Color overrides — keep only valid keys with string values.
    overrides_src = data.get("colorOverrides", {})
    color_overrides: Dict[str, str] = {}
    if isinstance(overrides_src, dict):
        for key, val in overrides_src.items():
            if key in _THEME_OVERRIDE_KEYS and isinstance(val, str) and val.strip():
                color_overrides[key] = val

    # Assets — named slots + arbitrary user-defined keys.  Values must be
    # strings (URLs or CSS ``url(...)``/``linear-gradient(...)`` expressions).
    # We don't fetch remote assets here; the frontend just injects them as
    # CSS vars.  Empty values are dropped so a theme can explicitly clear a
    # slot by setting ``hero: ""``.
    assets_out: Dict[str, Any] = {}
    assets_src = data.get("assets", {}) if isinstance(data.get("assets"), dict) else {}
    for key in _THEME_NAMED_ASSET_KEYS:
        val = assets_src.get(key)
        if isinstance(val, str) and val.strip():
            assets_out[key] = val
    custom_assets_src = assets_src.get("custom")
    if isinstance(custom_assets_src, dict):
        custom_assets: Dict[str, str] = {}
        for key, val in custom_assets_src.items():
            if (
                isinstance(key, str)
                and key.replace("-", "").replace("_", "").isalnum()
                and isinstance(val, str)
                and val.strip()
            ):
                custom_assets[key] = val
        if custom_assets:
            assets_out["custom"] = custom_assets

    # Custom CSS — raw CSS text the frontend injects as a scoped <style>
    # tag on theme apply.  Clipped to _THEME_CUSTOM_CSS_MAX to keep the
    # payload bounded.  We intentionally do NOT parse/sanitise the CSS
    # here — the dashboard is localhost-only and themes are user-authored
    # YAML in ~/.hermes/, same trust level as the config file itself.
    custom_css_val = data.get("customCSS")
    custom_css: Optional[str] = None
    if isinstance(custom_css_val, str) and custom_css_val.strip():
        custom_css = custom_css_val[:_THEME_CUSTOM_CSS_MAX]

    # Component style overrides — per-bucket dicts of camelCase CSS
    # property -> CSS string.  The frontend converts these into CSS vars
    # that shell components (Card, App header, Backdrop) consume.
    component_styles_src = data.get("componentStyles", {})
    component_styles: Dict[str, Dict[str, str]] = {}
    if isinstance(component_styles_src, dict):
        for bucket, props in component_styles_src.items():
            if bucket not in _THEME_COMPONENT_BUCKETS or not isinstance(props, dict):
                continue
            clean: Dict[str, str] = {}
            for prop, value in props.items():
                if (
                    isinstance(prop, str)
                    and prop.replace("-", "").replace("_", "").isalnum()
                    and isinstance(value, (str, int, float))
                    and str(value).strip()
                ):
                    clean[prop] = str(value)
            if clean:
                component_styles[bucket] = clean

    layout_variant_src = data.get("layoutVariant")
    layout_variant = (
        layout_variant_src
        if isinstance(layout_variant_src, str) and layout_variant_src in _THEME_LAYOUT_VARIANTS
        else "standard"
    )

    result: Dict[str, Any] = {
        "name": name,
        "label": data.get("label") or name,
        "description": data.get("description", ""),
        "palette": palette,
        "typography": typography,
        "layout": layout,
        "layoutVariant": layout_variant,
    }
    if color_overrides:
        result["colorOverrides"] = color_overrides
    if assets_out:
        result["assets"] = assets_out
    if custom_css is not None:
        result["customCSS"] = custom_css
    if component_styles:
        result["componentStyles"] = component_styles
    return result


def _discover_user_themes() -> list:
    """Scan ~/.hermes/dashboard-themes/*.yaml for user-created themes.

    Returns a list of fully-normalised theme definitions ready to ship
    to the frontend, so the client can apply them without a secondary
    round-trip or a built-in stub.
    """
    themes_dir = get_hermes_home() / "dashboard-themes"
    if not themes_dir.is_dir():
        return []
    result = []
    for f in sorted(themes_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        normalised = _normalise_theme_definition(data)
        if normalised is not None:
            result.append(normalised)
    return result


@app.get("/api/dashboard/themes")
async def get_dashboard_themes():
    """Return available themes and the currently active one.

    Built-in entries ship name/label/description only (the frontend owns
    their full definitions in `web/src/themes/presets.ts`).  User themes
    from `~/.hermes/dashboard-themes/*.yaml` ship with their full
    normalised definition under `definition`, so the client can apply
    them without a stub.
    """
    config = load_config()
    active = cfg_get(config, "dashboard", "theme", default="default")
    user_themes = _discover_user_themes()
    seen = set()
    themes = []
    for t in _BUILTIN_DASHBOARD_THEMES:
        seen.add(t["name"])
        themes.append(t)
    for t in user_themes:
        if t["name"] in seen:
            continue
        themes.append({
            "name": t["name"],
            "label": t["label"],
            "description": t["description"],
            "definition": t,
        })
        seen.add(t["name"])
    return {"themes": themes, "active": active}


class ThemeSetBody(BaseModel):
    name: str


@app.put("/api/dashboard/theme")
async def set_dashboard_theme(body: ThemeSetBody):
    """Set the active dashboard theme (persists to config.yaml)."""
    config = load_config()
    if "dashboard" not in config:
        config["dashboard"] = {}
    config["dashboard"]["theme"] = body.name
    save_config(config)
    return {"ok": True, "theme": body.name}


# ---------------------------------------------------------------------------
# Dashboard plugin system
# ---------------------------------------------------------------------------

def _safe_plugin_api_relpath(api_field: Any, *, dashboard_dir: Path) -> Optional[str]:
    """Validate the manifest's ``api`` field for the plugin loader.

    The web server later imports this file as a Python module via
    ``importlib.util.spec_from_file_location`` (arbitrary code
    execution by design — that's how plugins extend the backend).
    Pre-#29156 the field was used as-is, which meant:

    * An absolute path swallowed the plugin's dashboard directory
      entirely — ``Path('safe/dashboard') / '/tmp/evil.py'`` resolves
      to ``/tmp/evil.py``, so any attacker-controlled manifest could
      point the import at any Python file on disk (GHSA-5qr3-c538-wm9j).
    * A ``../..`` traversal could climb out of the plugin into
      neighbouring directories on the search path.

    Return the original string when the resolved path stays under
    ``dashboard_dir``; return ``None`` (with a warning logged at the
    call site) otherwise so the plugin still loads its static JS/CSS
    but its backend ``api`` is rejected.
    """
    if not isinstance(api_field, str) or not api_field.strip():
        return None
    candidate = Path(api_field)
    if candidate.is_absolute():
        return None
    try:
        resolved = (dashboard_dir / candidate).resolve()
        base = dashboard_dir.resolve()
    except (OSError, RuntimeError):
        return None
    try:
        resolved.relative_to(base)
    except ValueError:
        return None
    return api_field


def _discover_dashboard_plugins() -> list:
    """Scan plugins/*/dashboard/manifest.json for dashboard extensions.

    Checks three plugin sources (same as hermes_cli.plugins):
    1. User plugins:    ~/.hermes/plugins/<name>/dashboard/manifest.json
    2. Bundled plugins: <repo>/plugins/<name>/dashboard/manifest.json  (memory/, etc.)
    3. Project plugins: ./.hermes/plugins/  (only if HERMES_ENABLE_PROJECT_PLUGINS)
    """
    plugins = []
    seen_names: set = set()

    from hermes_cli.plugins import get_bundled_plugins_dir
    bundled_root = get_bundled_plugins_dir()
    search_dirs = [
        (get_hermes_home() / "plugins", "user"),
        (bundled_root / "memory", "bundled"),
        (bundled_root, "bundled"),
    ]
    # GHSA-5qr3-c538-wm9j (#29156): the previous ``os.environ.get(...)``
    # check treated *any* non-empty string as truthy, so ``=0``, ``=false``,
    # and ``=no`` — all of which the agent loader and operators correctly
    # read as "disabled" — silently *enabled* the untrusted project source
    # in the web server.  Combined with the absolute-path RCE primitive on
    # the manifest's ``api`` field (now patched below), this turned the
    # opt-in into a sticky always-on switch.  Use the shared truthy
    # semantics (``1`` / ``true`` / ``yes`` / ``on``) so the gate matches
    # ``hermes_cli/plugins.py`` and the documented user contract.
    if env_var_enabled("HERMES_ENABLE_PROJECT_PLUGINS"):
        search_dirs.append((Path.cwd() / ".hermes" / "plugins", "project"))

    for plugins_root, source in search_dirs:
        if not plugins_root.is_dir():
            continue
        for child in sorted(plugins_root.iterdir()):
            if not child.is_dir():
                continue
            manifest_file = child / "dashboard" / "manifest.json"
            if not manifest_file.exists():
                continue
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                name = data.get("name", child.name)
                if name in seen_names:
                    continue
                seen_names.add(name)
                # Tab options: ``path`` + ``position`` for a new tab, optional
                # ``override`` to replace a built-in route, and ``hidden`` to
                # register the plugin component/slots without adding a tab
                # (useful for slot-only plugins like a header-crest injector).
                raw_tab = data.get("tab", {}) if isinstance(data.get("tab"), dict) else {}
                tab_info = {
                    "path": raw_tab.get("path", f"/{name}"),
                    "position": raw_tab.get("position", "end"),
                }
                override_path = raw_tab.get("override")
                if isinstance(override_path, str) and override_path.startswith("/"):
                    tab_info["override"] = override_path
                if bool(raw_tab.get("hidden")):
                    tab_info["hidden"] = True
                # Slots: list of named slot locations this plugin populates.
                # The frontend exposes ``registerSlot(pluginName, slotName, Component)``
                # on window; plugins with non-empty slots call it from their JS bundle.
                slots_src = data.get("slots")
                slots: List[str] = []
                if isinstance(slots_src, list):
                    slots = [s for s in slots_src if isinstance(s, str) and s]
                entry = data.get("entry", "dist/index.js")
                css = data.get("css")
                if isinstance(entry, str) and entry:
                    entry_path = (child / "dashboard" / entry).resolve()
                    dashboard_root = (child / "dashboard").resolve()
                    try:
                        entry_path.relative_to(dashboard_root)
                    except ValueError:
                        _log.warning("Plugin %s: refusing entry outside dashboard dir: %r", name, entry)
                        continue
                    if not entry_path.is_file():
                        _log.warning("Plugin %s: dashboard entry missing: %s", name, entry_path)
                        continue
                if isinstance(css, str) and css:
                    css_path = (child / "dashboard" / css).resolve()
                    dashboard_root = (child / "dashboard").resolve()
                    try:
                        css_path.relative_to(dashboard_root)
                    except ValueError:
                        _log.warning("Plugin %s: refusing css outside dashboard dir: %r", name, css)
                        css = None
                    else:
                        if not css_path.is_file():
                            _log.warning("Plugin %s: dashboard css missing: %s", name, css_path)
                            css = None
                # Validate ``api`` at discovery time so the value cached
                # on the plugin entry is already safe to feed into the
                # importer.  An attacker-controlled manifest can name
                # any absolute path or ``..`` traversal here — the
                # web server then imports that file as a Python module
                # (RCE, GHSA-5qr3-c538-wm9j).
                raw_api = data.get("api")
                dashboard_dir = child / "dashboard"
                safe_api = _safe_plugin_api_relpath(raw_api, dashboard_dir=dashboard_dir)
                if raw_api and safe_api is None:
                    _log.warning(
                        "Plugin %s: refusing unsafe api path %r (must be a "
                        "relative file inside the plugin's dashboard/ "
                        "directory); backend routes from this plugin will "
                        "not be mounted",
                        name, raw_api,
                    )
                plugins.append({
                    "name": name,
                    "label": data.get("label", name),
                    "description": data.get("description", ""),
                    "icon": data.get("icon", "Puzzle"),
                    "version": data.get("version", "0.0.0"),
                    "tab": tab_info,
                    "slots": slots,
                    "entry": entry,
                    "css": css,
                    "has_api": bool(safe_api),
                    "source": source,
                    "_dir": str(dashboard_dir),
                    "_api_file": safe_api,
                })
            except Exception as exc:
                _log.warning("Bad dashboard plugin manifest %s: %s", manifest_file, exc)
                continue
    return plugins


# Cache discovered plugins per-process (refresh on explicit re-scan).
_dashboard_plugins_cache: Optional[list] = None


def _get_dashboard_plugins(force_rescan: bool = False) -> list:
    global _dashboard_plugins_cache
    if _dashboard_plugins_cache is None or force_rescan:
        _dashboard_plugins_cache = _discover_dashboard_plugins()
    elif _dashboard_plugins_cache:
        if any(not Path(p["_dir"]).is_dir() for p in _dashboard_plugins_cache):
            _dashboard_plugins_cache = _discover_dashboard_plugins()
    return _dashboard_plugins_cache


@app.get("/api/dashboard/plugins")
async def get_dashboard_plugins():
    """Return discovered dashboard plugins (excludes user-hidden ones)."""
    plugins = _get_dashboard_plugins()
    # Read user's hidden plugins list from config.
    config = load_config()
    hidden: list = cfg_get(config, "dashboard", "hidden_plugins", default=[]) or []
    # Strip internal fields before sending to frontend and filter out hidden.
    return [
        {k: v for k, v in p.items() if not k.startswith("_")}
        for p in plugins
        if p["name"] not in hidden
    ]


@app.get("/api/dashboard/plugins/rescan")
async def rescan_dashboard_plugins():
    """Force re-scan of dashboard plugins."""
    plugins = _get_dashboard_plugins(force_rescan=True)
    return {"ok": True, "count": len(plugins)}


class _AgentPluginInstallBody(BaseModel):
    identifier: str
    force: bool = False
    enable: bool = True


def _strip_dashboard_manifest(p: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in p.items() if not k.startswith("_")}


def _merged_plugins_hub() -> Dict[str, Any]:
    """Agent discovery + dashboard manifests + optional provider picker metadata."""
    from hermes_cli.plugins_cmd import (
        _discover_all_plugins,
        _get_current_context_engine,
        _get_current_memory_provider,
        _discover_context_engines,
        _discover_memory_providers,
        _get_disabled_set,
        _get_enabled_set,
        _read_manifest as _read_plugin_manifest_at,
    )

    dashboard_list = _get_dashboard_plugins()
    dash_by_name = {str(p["name"]): p for p in dashboard_list}

    disabled_set = _get_disabled_set()
    enabled_set = _get_enabled_set()

    # Read user-hidden plugins from config for the user_hidden field.
    config = load_config()
    hidden_plugins: list = cfg_get(config, "dashboard", "hidden_plugins", default=[]) or []

    plugins_root_resolved = (get_hermes_home() / "plugins").resolve()
    rows: List[Dict[str, Any]] = []

    for name, version, description, source, dir_str in _discover_all_plugins():
        if name in disabled_set:
            runtime_status = "disabled"
        elif name in enabled_set:
            runtime_status = "enabled"
        else:
            runtime_status = "inactive"

        dir_path = Path(dir_str)
        dm = dash_by_name.get(name)
        has_dash_manifest = dm is not None or (dir_path / "dashboard" / "manifest.json").exists()

        under_user_tree = False
        try:
            dir_path.resolve().relative_to(plugins_root_resolved)
            under_user_tree = True
        except ValueError:
            pass

        can_remove_update = (
            source in {"user", "git"} and under_user_tree and Path(dir_str).is_dir()
        )

        # Check if this plugin provides tools that require auth
        auth_required = False
        auth_command = ""
        manifest_data = _read_plugin_manifest_at(dir_path)
        provides_tools = manifest_data.get("provides_tools") or []
        if provides_tools:
            try:
                from tools.registry import registry
                for tname in provides_tools:
                    entry = registry.get_entry(tname)
                    if entry and entry.check_fn and not entry.check_fn():
                        auth_required = True
                        auth_command = f"hermes auth {name}"
                        break
            except Exception:
                pass

        rows.append({
            "name": name,
            "version": version or "",
            "description": description or "",
            "source": source,
            "runtime_status": runtime_status,
            "has_dashboard_manifest": has_dash_manifest,
            "dashboard_manifest": _strip_dashboard_manifest(dm) if dm else None,
            "path": dir_str,
            "can_remove": can_remove_update,
            "can_update_git": can_remove_update and (Path(dir_str) / ".git").exists(),
            "auth_required": auth_required,
            "auth_command": auth_command,
            "user_hidden": name in hidden_plugins,
        })

    agent_names = {r["name"] for r in rows}
    orphan_dashboard = [
        _strip_dashboard_manifest(p)
        for p in dashboard_list
        if str(p["name"]) not in agent_names
    ]

    memory_providers: List[Dict[str, str]] = []
    try:
        for n, desc in _discover_memory_providers():
            memory_providers.append({"name": n, "description": desc})
    except Exception:
        memory_providers = []

    context_engines: List[Dict[str, str]] = []
    try:
        for n, desc in _discover_context_engines():
            context_engines.append({"name": n, "description": desc})
    except Exception:
        context_engines = []

    return {
        "plugins": rows,
        "orphan_dashboard_plugins": orphan_dashboard,
        "providers": {
            "memory_provider": _get_current_memory_provider() or "",
            "memory_options": memory_providers,
            "context_engine": _get_current_context_engine(),
            "context_options": context_engines,
        },
    }


@app.get("/api/dashboard/plugins/hub")
async def get_plugins_hub(request: Request):
    """Unified agent plugins + dashboard extension metadata (session protected)."""
    _require_token(request)
    try:
        return _merged_plugins_hub()
    except Exception as exc:
        _log.warning("plugins/hub failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to build plugins hub.") from exc


@app.post("/api/dashboard/agent-plugins/install")
async def post_agent_plugin_install(request: Request, body: _AgentPluginInstallBody):
    _require_token(request)
    from hermes_cli.plugins_cmd import dashboard_install_plugin

    result = dashboard_install_plugin(
        body.identifier.strip(),
        force=body.force,
        enable=body.enable,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error") or "Install failed.",
        )
    _get_dashboard_plugins(force_rescan=True)
    # Strip internal paths from the response
    result.pop("after_install_path", None)
    return result


def _validate_plugin_name(name: str) -> str:
    """Reject path-traversal attempts in plugin name URL parameters."""
    name = name.strip("/")
    if not name or ".." in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid plugin name.")
    return name


@app.post("/api/dashboard/agent-plugins/{name:path}/enable")
async def post_agent_plugin_enable(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from hermes_cli.plugins_cmd import dashboard_set_agent_plugin_enabled

    result = dashboard_set_agent_plugin_enabled(name, enabled=True)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Enable failed.")
    return result


@app.post("/api/dashboard/agent-plugins/{name:path}/disable")
async def post_agent_plugin_disable(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from hermes_cli.plugins_cmd import dashboard_set_agent_plugin_enabled

    result = dashboard_set_agent_plugin_enabled(name, enabled=False)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Disable failed.")
    return result


@app.post("/api/dashboard/agent-plugins/{name:path}/update")
async def post_agent_plugin_update(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from hermes_cli.plugins_cmd import dashboard_update_user_plugin

    result = dashboard_update_user_plugin(name)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Update failed.")
    _get_dashboard_plugins(force_rescan=True)
    return result


@app.delete("/api/dashboard/agent-plugins/{name:path}")
async def delete_agent_plugin(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from hermes_cli.plugins_cmd import dashboard_remove_user_plugin

    result = dashboard_remove_user_plugin(name)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Remove failed.")
    _get_dashboard_plugins(force_rescan=True)
    return result


class _PluginProvidersPutBody(BaseModel):
    memory_provider: Optional[str] = None
    context_engine: Optional[str] = None


@app.put("/api/dashboard/plugin-providers")
async def put_plugin_providers(request: Request, body: _PluginProvidersPutBody):
    """Persist memory provider / context engine selection (writes config.yaml)."""
    _require_token(request)
    from hermes_cli.plugins_cmd import (
        _save_context_engine,
        _save_memory_provider,
    )

    if body.memory_provider is not None:
        _save_memory_provider(body.memory_provider)
    if body.context_engine is not None:
        _save_context_engine(body.context_engine)
    return {"ok": True}


class _PluginVisibilityBody(BaseModel):
    hidden: bool


@app.post("/api/dashboard/plugins/{name:path}/visibility")
async def post_plugin_visibility(request: Request, name: str, body: _PluginVisibilityBody):
    """Toggle a plugin's sidebar visibility (persists to config.yaml dashboard.hidden_plugins)."""
    _require_token(request)
    name = _validate_plugin_name(name)

    config = load_config()
    if "dashboard" not in config or not isinstance(config.get("dashboard"), dict):
        config["dashboard"] = {}
    hidden_list: list = config["dashboard"].get("hidden_plugins") or []
    if not isinstance(hidden_list, list):
        hidden_list = []

    if body.hidden and name not in hidden_list:
        hidden_list.append(name)
    elif not body.hidden and name in hidden_list:
        hidden_list.remove(name)

    config["dashboard"]["hidden_plugins"] = hidden_list
    save_config(config)
    return {"ok": True, "name": name, "hidden": body.hidden}


@app.get("/dashboard-plugins/{plugin_name}/{file_path:path}")
async def serve_plugin_asset(plugin_name: str, file_path: str):
    """Serve static assets from a dashboard plugin directory.

    Only serves files from the plugin's ``dashboard/`` subdirectory.
    Path traversal is blocked by checking ``resolve().is_relative_to()``.

    Restricted to a browser-fetchable suffix allowlist (JS/CSS/JSON/HTML/
    SVG/PNG/JPG/WOFF). The dashboard loads plugin JS via ``<script src>``
    and CSS via ``<link href>``, neither of which can attach a custom
    auth header — so this route stays unauthenticated to keep the SPA
    working. But user-installed plugins ship a ``plugin_api.py``
    backend module that the browser never fetches; it's only imported
    by :func:`_mount_plugin_api_routes` at startup. Without a suffix
    allowlist, anyone on the loopback port can curl the ``.py`` source
    of a private third-party plugin. Reject everything outside the
    browser-asset set.
    """
    plugins = _get_dashboard_plugins()
    plugin = next((p for p in plugins if p["name"] == plugin_name), None)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")

    base = Path(plugin["_dir"])
    target = (base / file_path).resolve()

    if not target.is_relative_to(base.resolve()):
        raise HTTPException(status_code=403, detail="Path traversal blocked")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Browser-asset suffix allowlist. Everything outside this set is
    # rejected with 404 so we don't leak ``.py`` backend sources, README
    # files, ``.env.example`` templates, etc. — none of which the SPA
    # actually fetches. Add to this set deliberately when a new asset
    # type comes up; do NOT change the default fallback.
    suffix = target.suffix.lower()
    content_types = {
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".html": "text/html",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".ico": "image/x-icon",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
        ".ttf": "font/ttf",
        ".otf": "font/otf",
        ".map": "application/json",
    }
    if suffix not in content_types:
        raise HTTPException(
            status_code=404,
            detail="File not found",
        )
    media_type = content_types[suffix]
    return FileResponse(
        target,
        media_type=media_type,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


def _mount_plugin_api_routes():
    """Import and mount backend API routes from plugins that declare them.

    Each plugin's ``api`` field points to a Python file that must expose
    a ``router`` (FastAPI APIRouter).  Routes are mounted under
    ``/api/plugins/<name>/``.

    Backend import is restricted to ``bundled`` and ``user`` sources.
    Project plugins (``./.hermes/plugins/``) ship with the CWD and are
    therefore attacker-controlled in any threat model where the user
    opens a malicious repo; they can extend the dashboard UI via
    static JS/CSS but their Python ``api`` file is never auto-imported
    by the web server.  See GHSA-5qr3-c538-wm9j (#29156).
    """
    for plugin in _get_dashboard_plugins():
        api_file_name = plugin.get("_api_file")
        if not api_file_name:
            continue
        if plugin.get("source") == "project":
            _log.warning(
                "Plugin %s: ignoring backend api=%s (project plugins may "
                "not auto-import Python code; move the plugin to "
                "~/.hermes/plugins/ if you trust it)",
                plugin["name"], api_file_name,
            )
            continue
        dashboard_dir = Path(plugin["_dir"])
        api_path = dashboard_dir / api_file_name
        try:
            resolved_api = api_path.resolve()
            resolved_base = dashboard_dir.resolve()
            resolved_api.relative_to(resolved_base)
        except (OSError, RuntimeError, ValueError):
            # Discovery already filters this, but re-check here in case
            # ``_dir`` was tampered with after caching or a future caller
            # bypasses the validator.  Defence in depth keeps the import
            # primitive contained even if the upstream check regresses.
            _log.warning(
                "Plugin %s: refusing to import api file outside its "
                "dashboard directory (%s)", plugin["name"], api_path,
            )
            continue
        if not api_path.exists():
            _log.warning("Plugin %s declares api=%s but file not found", plugin["name"], api_file_name)
            continue
        try:
            module_name = f"hermes_dashboard_plugin_{plugin['name']}"
            spec = importlib.util.spec_from_file_location(module_name, api_path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            # Register in sys.modules BEFORE exec_module so pydantic/FastAPI
            # can resolve forward references (e.g. models defined in a file
            # that uses `from __future__ import annotations`). Without this,
            # TypeAdapter lazy-build fails at first request with
            # "is not fully defined" because the module namespace isn't
            # reachable by name for string-annotation resolution.
            sys.modules[module_name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:
                sys.modules.pop(module_name, None)
                raise
            router = getattr(mod, "router", None)
            if router is None:
                _log.warning("Plugin %s api file has no 'router' attribute", plugin["name"])
                continue
            app.include_router(router, prefix=f"/api/plugins/{plugin['name']}")
            _log.info("Mounted plugin API routes: /api/plugins/%s/", plugin["name"])
        except Exception as exc:
            _log.warning("Failed to load plugin %s API routes: %s", plugin["name"], exc)


# Mount plugin API routes before the SPA catch-all.
_mount_plugin_api_routes()

# Mount the dashboard auth routes (/login, /auth/*, /api/auth/*) before the
# SPA catch-all so /{full_path:path} doesn't swallow them.  These are
# always mounted — the gate middleware decides whether to enforce auth,
# not whether the routes exist.
from hermes_cli.dashboard_auth.routes import router as _dashboard_auth_router  # noqa: E402
app.include_router(_dashboard_auth_router)

mount_spa(app)


def start_server(
    host: str = "127.0.0.1",
    port: int = 9119,
    open_browser: bool = True,
    allow_public: bool = False,
    *,
    embedded_chat: bool = False,
):
    """Start the web UI server."""
    import uvicorn

    global _DASHBOARD_EMBEDDED_CHAT_ENABLED
    _DASHBOARD_EMBEDDED_CHAT_ENABLED = embedded_chat

    # Phase 0: stash the auth-gate flag on app.state so middleware / SPA-token
    # injection / WS-auth paths can branch on it consistently.  Phase 3.5
    # uses this to decide whether to refuse the bind, log the gate-on
    # banner, and enable uvicorn proxy_headers.
    app.state.auth_required = should_require_auth(host, allow_public)

    if app.state.auth_required:
        # Phase 3.5: the gate engages on non-loopback binds.  The legacy
        # "refusing to bind" guard is replaced by "require at least one
        # provider to be registered, else fail closed".
        from hermes_cli.dashboard_auth import list_providers
        if not list_providers():
            # Surface the *specific* reason any bundled provider declined
            # to register (e.g. missing HERMES_DASHBOARD_OAUTH_CLIENT_ID).
            # Each provider plugin that ships with Hermes Agent exposes a
            # module-level ``LAST_SKIP_REASON`` string for this purpose;
            # without it the operator would only see "no providers" which
            # is misleading when the provider IS installed but unconfigured.
            skip_reasons: list[str] = []
            try:
                from plugins.dashboard_auth import nous as _nous_plugin

                if _nous_plugin.LAST_SKIP_REASON:
                    skip_reasons.append(
                        f"  • nous: {_nous_plugin.LAST_SKIP_REASON}"
                    )
            except Exception:
                pass

            if skip_reasons:
                raise SystemExit(
                    f"Refusing to bind dashboard to {host} — the OAuth auth "
                    f"gate engages on non-loopback binds, but no auth "
                    f"providers are registered.\n"
                    f"\n"
                    f"Bundled providers reported these issues:\n"
                    + "\n".join(skip_reasons)
                    + "\n"
                    f"\n"
                    f"Or pass --insecure to skip the auth gate (NOT "
                    f"recommended on untrusted networks)."
                )
            raise SystemExit(
                f"Refusing to bind dashboard to {host} — the OAuth auth "
                f"gate engages on non-loopback binds, but no auth providers "
                f"are registered and no bundled plugin reported a reason "
                f"(was the dashboard_auth/nous plugin removed?).\n"
                f"Install a DashboardAuthProvider plugin, or pass --insecure "
                f"to skip the auth gate (NOT recommended on untrusted "
                f"networks)."
            )
        _log.info(
            "Dashboard binding to %s with OAuth auth gate enabled. "
            "Providers: %s",
            host,
            ", ".join(p.name for p in list_providers()),
        )
    elif host not in _LOOPBACK_HOST_VALUES and allow_public:
        # --insecure path — no auth, loud warning.
        _log.warning(
            "Binding to %s with --insecure — the dashboard has no robust "
            "authentication. Only use on trusted networks.", host,
        )

    # Record the bound host so host_header_middleware can validate incoming
    # Host headers against it. Defends against DNS rebinding (GHSA-ppp5-vxwm-4cf7).
    # bound_port is also stashed so /api/pty can build the back-WS URL the
    # PTY child uses to publish events to the dashboard sidebar.
    app.state.bound_host = host
    app.state.bound_port = port

    if open_browser:
        import webbrowser

        # On headless Linux (no DISPLAY or WAYLAND_DISPLAY) some registered
        # browsers are TUI programs (links, lynx, www-browser) that try to
        # take over the terminal.  That can send SIGHUP to the server process
        # and cause an immediate exit even though uvicorn bound successfully.
        # Skip the auto-open attempt on headless systems and let the user
        # open the URL manually.  macOS and Windows are always considered
        # display-capable.
        _has_display = (
            sys.platform != "linux"
            or bool(os.environ.get("DISPLAY"))
            or bool(os.environ.get("WAYLAND_DISPLAY"))
        )

        if _has_display:
            def _open():
                try:
                    time.sleep(1.0)
                    webbrowser.open(f"http://{host}:{port}")
                except Exception:
                    pass

            threading.Thread(target=_open, daemon=True).start()
        else:
            _log.debug(
                "Skipping browser-open: no DISPLAY or WAYLAND_DISPLAY detected "
                "(headless Linux). Pass --no-open to suppress this detection."
            )

    print(f"  Hermes Web UI → http://{host}:{port}")
    # proxy_headers defaults to False so _ws_client_is_allowed sees the real
    # connection peer rather than X-Forwarded-For's rewritten value (which
    # would defeat the loopback gate when behind a reverse proxy).  When the
    # OAuth gate is active we are explicitly running behind a TLS terminator
    # (Fly.io) and need X-Forwarded-Proto to decide cookie Secure flags, so
    # we flip proxy_headers on for that mode.
    uvicorn.run(
        app, host=host, port=port, log_level="warning",
        proxy_headers=bool(app.state.auth_required),
    )
