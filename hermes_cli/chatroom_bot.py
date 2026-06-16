"""Chatroom bot worker — connects to the chatroom WebSocket as a bot,
listens for @mentions, and generates AI responses via Hermes CLI.

Environment variables:
  BOT_PROFILE    — Hermes profile name (e.g. "lex-drafter")
  BOT_NAME       — Display name in chat
  ROOM_ID        — Chat room ID to join
  RELAY_WS_URL   — WebSocket URL of the relay (default: ws://127.0.0.1:9119/ws/chatroom/{ROOM_ID})
  HERMES_BIN     — Path to hermes CLI (default: hermes)
  SIMULATE       — If "1", run in simulation mode (no real AI)
"""

import asyncio
import json
import os
import signal
import sys
import time

# ── Config ────────────────────────────────────────────────────────────────

BOT_PROFILE = os.getenv("BOT_PROFILE", "")
BOT_NAME = os.getenv("BOT_NAME", BOT_PROFILE)
ROOM_ID = os.getenv("ROOM_ID", "default")
RELAY_WS_URL = os.getenv(
    "RELAY_WS_URL",
    f"ws://127.0.0.1:9119/ws/chatroom/{ROOM_ID}",
)
HERMES_BIN = os.getenv("HERMES_BIN", "hermes")
SIMULATE = os.getenv("SIMULATE", "0") == "1"

# ── State ─────────────────────────────────────────────────────────────────

_shutting_down = False
_recent_context: list[dict] = []  # [(username, content), ...]
MAX_CONTEXT = 20


def _build_prompt(user_msg: str) -> str:
    """Build a prompt with recent conversation context."""
    parts = []
    if _recent_context:
        parts.append("Recent conversation:")
        for who, what in _recent_context[-MAX_CONTEXT:]:
            parts.append(f"  [{who}]: {what}")
        parts.append("")
    parts.append(f"The user @mentioned you. Reply helpfully as {BOT_NAME} ({BOT_PROFILE}).")
    parts.append(f"User message: {user_msg}")
    parts.append("Reply directly (do NOT include @your-name prefix):")
    return "\n".join(parts)


async def _run_hermes(prompt: str) -> str:
    """Run Hermes CLI with the given profile and prompt. Returns response text."""
    try:
        proc = await asyncio.create_subprocess_exec(
            HERMES_BIN,
            "-p", BOT_PROFILE,
            "chat",
            "-q", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=120,
        )
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            return f"[error] Hermes exited {proc.returncode}: {err[:200]}"
        return stdout.decode(errors="replace").strip() or "(empty response)"
    except asyncio.TimeoutError:
        return "[error] Hermes timed out after 120s"
    except FileNotFoundError:
        return "[error] hermes binary not found"
    except Exception as e:
        return f"[error] {e}"


def _simulate_reply(prompt: str) -> str:
    """Return a simulated reply when Hermes is unavailable."""
    return (
        f"🤖 [{BOT_NAME}] Simulation mode — I would respond as {BOT_PROFILE} "
        f"to: {prompt[:100]}..."
    )


# ── Main loop ─────────────────────────────────────────────────────────────


async def run() -> None:
    global _shutting_down

    # Auth: use the dashboard session token
    token = os.getenv("HERMES_SESSION_TOKEN", "")
    ws_url = RELAY_WS_URL
    if token:
        sep = "&" if "?" in ws_url else "?"
        ws_url += f"{sep}token={token}"

    print(f"[chatroom_bot] Connecting to {ws_url} as {BOT_NAME} ({BOT_PROFILE})",
          flush=True)

    # Import websockets lazily
    try:
        from websockets.asyncio.client import connect
    except ImportError:
        print("[chatroom_bot] websockets not installed, cannot run", flush=True)
        return

    reconnect_delay = 1
    max_reconnect = 30

    while not _shutting_down:
        try:
            async with connect(ws_url) as ws:
                reconnect_delay = 1  # reset on successful connect

                # Register as bot
                await ws.send(json.dumps({
                    "type": "join",
                    "username": BOT_NAME,
                    "role": "bot",
                    "profile": BOT_PROFILE,
                }, ensure_ascii=False))

                print(f"[chatroom_bot] Registered as {BOT_NAME}", flush=True)

                async for raw in ws:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    msg_type = data.get("type") or data.get("kind") or ""

                    if msg_type == "shutdown":
                        print("[chatroom_bot] Received shutdown", flush=True)
                        _shutting_down = True
                        return

                    # Server formats messages with kind=user|bot; also accept raw type=message
                    if msg_type in ("message", "user", "bot"):
                        username = data.get("sender") or data.get("username", "")
                        content = data.get("text") or data.get("content", "")

                        # Store in context
                        _recent_context.append((username, content))
                        if len(_recent_context) > MAX_CONTEXT * 2:
                            _recent_context = _recent_context[-MAX_CONTEXT:]

                        # Check for @mention
                        mention = f"@{BOT_NAME}"
                        if mention not in content:
                            continue

                        print(f"[chatroom_bot] Mentioned by {username}", flush=True)

                        # Send thinking status
                        await ws.send(json.dumps({
                            "type": "thinking",
                            "username": BOT_NAME,
                            "status": "processing",
                        }, ensure_ascii=False))

                        # Generate reply
                        if SIMULATE:
                            reply = _simulate_reply(content)
                        else:
                            prompt = _build_prompt(content)
                            reply = await _run_hermes(prompt)

                        # Send thinking end
                        await ws.send(json.dumps({
                            "type": "thinking_end",
                            "username": BOT_NAME,
                        }, ensure_ascii=False))

                        # Send reply
                        await ws.send(json.dumps({
                            "type": "message",
                            "username": BOT_NAME,
                            "content": reply,
                        }, ensure_ascii=False))

                        print(f"[chatroom_bot] Replied ({len(reply)} chars)", flush=True)

                    elif msg_type == "history":
                        # Load history into context (raw format: type=history + messages)
                        msgs = data.get("messages", [])
                        for m in msgs[-MAX_CONTEXT:]:
                            who = m.get("username") or m.get("sender", "?")
                            what = m.get("content") or m.get("text", "")
                            _recent_context.append((who, what))

                    # Handle _replay envelope (server sends on join)
                    replay = data.get("_replay")
                    if replay and isinstance(replay, list):
                        for m in replay[-MAX_CONTEXT:]:
                            who = m.get("username") or m.get("sender", "?")
                            what = m.get("content") or m.get("text", "")
                            _recent_context.append((who, what))

        except Exception as e:
            if _shutting_down:
                return
            print(f"[chatroom_bot] Connection error: {e}, reconnecting in {reconnect_delay}s",
                  flush=True)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect)


def main() -> None:
    # Handle SIGTERM gracefully
    loop = asyncio.get_event_loop()

    def _on_sigterm():
        global _shutting_down
        _shutting_down = True

    try:
        loop.add_signal_handler(signal.SIGTERM, _on_sigterm)
        loop.add_signal_handler(signal.SIGINT, _on_sigterm)
    except NotImplementedError:
        pass  # Windows

    try:
        loop.run_until_complete(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
