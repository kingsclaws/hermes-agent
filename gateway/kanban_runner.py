"""
Kanban board management methods for GatewayRunner.

Extracted from gateway/run.py as a mixin class.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from agent.i18n import t
from hermes_cli.config import cfg_get

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource

logger = logging.getLogger(__name__)

import gateway.run as _gw


class KanbanMixin:
    """Kanban board management methods for GatewayRunner."""

    async def _kanban_notifier_watcher(self, interval: float = 5.0) -> None:
        """Poll ``kanban_notify_subs`` and deliver terminal events to users.

        For each subscription row, fetches ``task_events`` newer than the
        stored cursor with kind in the terminal set (``completed``,
        ``blocked``, ``gave_up``, ``crashed``, ``timed_out``). Sends one
        message per new event to ``(platform, chat_id, thread_id)``,
        then advances the cursor. When a task reaches a terminal state
        (``completed`` / ``archived``), the subscription is removed.

        Runs in the gateway event loop; all SQLite work is pushed to a
        thread via ``asyncio.to_thread`` so the loop never blocks on the
        WAL lock. Failures in one tick don't stop subsequent ticks.

        **Multi-board:** iterates every board discovered on disk per
        tick. Subscriptions live inside each board's own DB and cannot
        cross boards, so delivery semantics are unchanged — this is
        purely a fan-out of the single-DB poll.
        """
        from gateway.config import Platform as _Platform
        try:
            from hermes_cli import kanban_db as _kb
        except Exception:
            logger.warning("kanban notifier: kanban_db not importable; notifier disabled")
            return

        TERMINAL_KINDS = ("completed", "blocked", "gave_up", "crashed", "timed_out")
        # Subscriptions are removed only when the task reaches a truly final
        # status (done / archived). We used to also unsub on any terminal
        # event kind (gave_up / crashed / timed_out / blocked), but that
        # silently dropped the user out of the loop whenever the dispatcher
        # respawned the task: a worker that crashes, gets reclaimed, runs
        # again, and crashes a second time would only notify on the first
        # crash because the subscription was deleted after the first event.
        # Same shape as the reblock-after-unblock cycle that PR #22941
        # fixed for `blocked`. Keeping the subscription alive until the
        # task is genuinely done lets the cursor (advanced atomically by
        # claim_unseen_events_for_sub) handle dedup, and any retry-loop
        # event reaches the user.
        # Per-subscription send-failure counter. Adapter.send raising
        # means the chat is dead (deleted, bot kicked, etc.) — after N
        # consecutive send failures the sub is dropped so we don't spin
        # against a dead chat every 5 seconds forever.
        MAX_SEND_FAILURES = 3
        sub_fail_counts: dict[tuple, int] = getattr(
            self, "_kanban_sub_fail_counts", {}
        )
        self._kanban_sub_fail_counts = sub_fail_counts
        notifier_profile = getattr(self, "_kanban_notifier_profile", None)
        if not notifier_profile:
            notifier_profile = self._active_profile_name()
            self._kanban_notifier_profile = notifier_profile

        # Initial delay so the gateway can finish wiring adapters.
        await asyncio.sleep(5)

        while self._running:
            try:
                def _collect():
                    deliveries: list[dict] = []
                    active_platforms = {
                        getattr(platform, "value", str(platform)).lower()
                        for platform in self.adapters.keys()
                    }
                    # `platform=session` is an internal subscription used by
                    # swarm_task_create to wake the originating CLI/TUI/WebUI
                    # coordinator session. It does not require a messaging
                    # adapter to be connected.
                    active_platforms.add("session")

                    # Enumerate every board on disk, but poll each resolved DB
                    # path once. Multiple slugs can point at the same DB when
                    # HERMES_KANBAN_DB pins the board path; without this guard
                    # one gateway could collect the same subscription/event
                    # more than once before advancing the cursor.
                    try:
                        boards = _kb.list_boards(include_archived=False)
                    except Exception:
                        boards = [_kb.read_board_metadata(_kb.DEFAULT_BOARD)]
                    seen_db_paths: set[str] = set()
                    for board_meta in boards:
                        slug = board_meta.get("slug") or _kb.DEFAULT_BOARD
                        db_path = board_meta.get("db_path")
                        try:
                            resolved_db_path = str(Path(db_path).expanduser().resolve()) if db_path else str(_kb.kanban_db_path(slug).resolve())
                        except Exception:
                            resolved_db_path = f"slug:{slug}"
                        if resolved_db_path in seen_db_paths:
                            logger.debug(
                                "kanban notifier: skipping duplicate board slug %s for DB %s",
                                slug, resolved_db_path,
                            )
                            continue
                        seen_db_paths.add(resolved_db_path)
                        try:
                            conn = _kb.connect(board=slug)
                        except Exception as exc:
                            logger.debug("kanban notifier: cannot open board %s: %s", slug, exc)
                            continue
                        try:
                            # `connect()` runs the schema + idempotent migration
                            # on first open per process, so an explicit
                            # `init_db()` here would be redundant. Worse:
                            # `init_db()` deliberately busts the per-process
                            # cache and re-runs the migration on a *second*
                            # connection, which races the first and used to
                            # log a benign but noisy `duplicate column name`
                            # traceback (and intermittent "database is locked"
                            # — issue #21378) on every gateway start against
                            # a legacy DB. `_add_column_if_missing` now
                            # tolerates that race, but we still skip the
                            # redundant call to avoid the wasted work.
                            subs = _kb.list_notify_subs(conn)
                            if not subs:
                                logger.debug("kanban notifier: board %s has no subscriptions", slug)
                            for sub in subs:
                                owner_profile = sub.get("notifier_profile") or None
                                if owner_profile and owner_profile != notifier_profile:
                                    logger.debug(
                                        "kanban notifier: subscription for %s owned by profile %s; current profile %s skipping",
                                        sub.get("task_id"), owner_profile, notifier_profile,
                                    )
                                    continue
                                platform = (sub.get("platform") or "").lower()
                                if platform not in active_platforms:
                                    logger.debug(
                                        "kanban notifier: subscription for %s on %s skipped; adapter not connected",
                                        sub.get("task_id"), platform or "<missing>",
                                    )
                                    continue
                                old_cursor, cursor, events = _kb.claim_unseen_events_for_sub(
                                    conn,
                                    task_id=sub["task_id"],
                                    platform=sub["platform"],
                                    chat_id=sub["chat_id"],
                                    thread_id=sub.get("thread_id") or "",
                                    kinds=TERMINAL_KINDS,
                                )
                                if not events:
                                    continue
                                task = _kb.get_task(conn, sub["task_id"])
                                logger.debug(
                                    "kanban notifier: claimed %d event(s) for %s on board %s cursor %s→%s",
                                    len(events), sub["task_id"], slug, old_cursor, cursor,
                                )
                                deliveries.append({
                                    "sub": sub,
                                    "old_cursor": old_cursor,
                                    "cursor": cursor,
                                    "events": events,
                                    "task": task,
                                    "board": slug,
                                })
                        finally:
                            conn.close()
                    return deliveries

                deliveries = await asyncio.to_thread(_collect)
                for d in deliveries:
                    sub = d["sub"]
                    task = d["task"]
                    board_slug = d.get("board")
                    platform_str = (sub["platform"] or "").lower()
                    try:
                        plat = _Platform(platform_str)
                    except ValueError:
                        if platform_str != "session":
                            # Unknown platform string; skip and advance cursor so
                            # we don't replay forever.
                            await asyncio.to_thread(
                                self._kanban_advance, sub, d["cursor"], board_slug,
                            )
                            continue
                        plat = None
                    if platform_str == "session":
                        for ev in d["events"]:
                            try:
                                await self._inject_kanban_session_wake(
                                    event=ev,
                                    task=task,
                                    session_id=sub["chat_id"],
                                    board=board_slug,
                                )
                            except Exception as exc:
                                logger.debug(
                                    "kanban notifier: session wake for %s failed: %s",
                                    sub["task_id"], exc,
                                )
                                await asyncio.to_thread(
                                    self._kanban_rewind,
                                    sub,
                                    d["cursor"],
                                    d.get("old_cursor", 0),
                                    board_slug,
                                )
                                break
                        else:
                            await asyncio.to_thread(
                                self._kanban_advance, sub, d["cursor"], board_slug,
                            )
                            task_terminal = task and task.status in {"done", "archived"}
                            if task_terminal:
                                await asyncio.to_thread(
                                    self._kanban_unsub, sub, board_slug,
                                )
                        continue
                    adapter = self.adapters.get(plat)
                    if adapter is None:
                        logger.debug(
                            "kanban notifier: adapter %s disconnected before delivery for %s; rewinding claim",
                            platform_str, sub["task_id"],
                        )
                        await asyncio.to_thread(
                            self._kanban_rewind,
                            sub,
                            d["cursor"],
                            d.get("old_cursor", 0),
                            board_slug,
                        )
                        continue
                    title = (task.title if task else sub["task_id"])[:120]
                    for ev in d["events"]:
                        kind = ev.kind
                        # Identity prefix: attribute terminal pings to the
                        # worker that did the work. Makes fleets (where one
                        # chat subscribes to many tasks) legible at a glance.
                        who = (task.assignee if task and task.assignee else None)
                        tag = f"@{who} " if who else ""
                        if kind == "completed":
                            # Prefer the run's summary (the worker's
                            # intentional human-facing handoff, carried
                            # in the event payload), then fall back to
                            # task.result for legacy rows written before
                            # runs shipped.
                            handoff = ""
                            payload_summary = None
                            if ev.payload and ev.payload.get("summary"):
                                payload_summary = str(ev.payload["summary"])
                            if payload_summary:
                                h = payload_summary.strip().splitlines()[0][:200]
                                handoff = f"\n{h}"
                            elif task and task.result:
                                r = task.result.strip().splitlines()[0][:160]
                                handoff = f"\n{r}"
                            msg = (
                                f"✔ {tag}Kanban {sub['task_id']} done"
                                f" — {title}{handoff}"
                            )
                        elif kind == "blocked":
                            reason = ""
                            if ev.payload and ev.payload.get("reason"):
                                reason = f": {str(ev.payload['reason'])[:160]}"
                            msg = f"⏸ {tag}Kanban {sub['task_id']} blocked{reason}"
                        elif kind == "gave_up":
                            err = ""
                            if ev.payload and ev.payload.get("error"):
                                err = f"\n{str(ev.payload['error'])[:200]}"
                            msg = (
                                f"✖ {tag}Kanban {sub['task_id']} gave up "
                                f"after repeated spawn failures{err}"
                            )
                        elif kind == "crashed":
                            msg = (
                                f"✖ {tag}Kanban {sub['task_id']} worker crashed "
                                f"(pid gone); dispatcher will retry"
                            )
                        elif kind == "timed_out":
                            limit = 0
                            if ev.payload and ev.payload.get("limit_seconds"):
                                limit = int(ev.payload["limit_seconds"])
                            msg = (
                                f"⏱ {tag}Kanban {sub['task_id']} timed out "
                                f"(max_runtime={limit}s); will retry"
                            )
                        else:
                            continue
                        metadata: dict[str, Any] = {}
                        if sub.get("thread_id"):
                            metadata["thread_id"] = sub["thread_id"]
                        sub_key = (
                            sub["task_id"], sub["platform"],
                            sub["chat_id"], sub.get("thread_id") or "",
                        )
                        try:
                            await adapter.send(
                                sub["chat_id"], msg, metadata=metadata,
                            )
                            logger.debug(
                                "kanban notifier: delivered %s event for %s to %s/%s on board %s",
                                kind, sub["task_id"], platform_str, sub["chat_id"], board_slug,
                            )
                            # Also deliver to the originating chat session
                            # (WebUI/CLI) if the task records a session_id.
                            _task_row = task.__dict__ if task and hasattr(task, '__dict__') else {}
                            session_id_val = _task_row.get("session_id") or (task.session_id if task and hasattr(task, 'session_id') else None)
                            if session_id_val:
                                try:
                                    await self._deliver_to_chat_session(ev, _task_row, session_id_val)
                                except Exception as _se:
                                    logger.debug(
                                        "kanban notifier: session delivery for %s failed: %s",
                                        sub["task_id"], _se,
                                    )
                            # After delivering the text notification, surface
                            # any artifact paths the worker referenced in
                            # ``kanban_complete(summary=..., artifacts=[...])``
                            # (or the legacy ``result`` field) as native
                            # uploads. ``extract_local_files`` finds bare
                            # absolute paths in the summary;
                            # ``send_document`` / ``send_image_file`` uploads
                            # them. Only fires on the ``completed`` event so
                            # we never spam attachments on retries.
                            if kind == "completed":
                                try:
                                    await self._deliver_kanban_artifacts(
                                        adapter=adapter,
                                        chat_id=sub["chat_id"],
                                        metadata=metadata,
                                        event_payload=getattr(ev, "payload", None),
                                        task=task,
                                    )
                                except Exception as art_exc:
                                    logger.debug(
                                        "kanban notifier: artifact delivery for %s failed: %s",
                                        sub["task_id"], art_exc,
                                    )
                            # Reset the failure counter on success.
                            sub_fail_counts.pop(sub_key, None)
                        except Exception as exc:
                            fails = sub_fail_counts.get(sub_key, 0) + 1
                            sub_fail_counts[sub_key] = fails
                            logger.warning(
                                "kanban notifier: send failed for %s on %s "
                                "(attempt %d/%d): %s",
                                sub["task_id"], platform_str, fails,
                                MAX_SEND_FAILURES, exc,
                            )
                            if fails >= MAX_SEND_FAILURES:
                                logger.warning(
                                    "kanban notifier: dropping subscription "
                                    "%s on %s after %d consecutive send failures",
                                    sub["task_id"], platform_str, fails,
                                )
                                await asyncio.to_thread(self._kanban_unsub, sub, board_slug)
                                sub_fail_counts.pop(sub_key, None)
                            else:
                                await asyncio.to_thread(
                                    self._kanban_rewind,
                                    sub,
                                    d["cursor"],
                                    d.get("old_cursor", 0),
                                    board_slug,
                                )
                            # Rewind the pre-send claim on transient failure so
                            # a later tick can retry. After too many failures,
                            # dropping the subscription is the terminal action.
                            break
                    else:
                        # All events delivered; advance cursor. The cursor
                        # is the dedup mechanism — it prevents re-delivery
                        # of the same event on subsequent ticks.
                        await asyncio.to_thread(
                            self._kanban_advance, sub, d["cursor"], board_slug,
                        )
                        # Unsubscribe only when the task has reached a truly
                        # final status (done / archived). For blocked /
                        # gave_up / crashed / timed_out the subscription is
                        # kept alive so the user gets notified again if the
                        # dispatcher respawns the task and it cycles into the
                        # same state. See the longer comment on TERMINAL_KINDS
                        # above for the failure mode this prevents.
                        task_terminal = task and task.status in {"done", "archived"}
                        if task_terminal:
                            await asyncio.to_thread(
                                self._kanban_unsub, sub, board_slug,
                            )
            except Exception as exc:
                logger.warning("kanban notifier tick failed: %s", exc)
            # Sleep with cancellation checks.
            for _ in range(int(max(1, interval))):
                if not self._running:
                    return
                await asyncio.sleep(1)

    def _kanban_advance(
        self, sub: dict, cursor: int, board: Optional[str] = None,
    ) -> None:
        """Sync helper: advance a subscription's cursor. Runs in to_thread.

        ``board`` scopes the DB connection to the board that owns this
        subscription. Unsub cursors in one board can't touch another's.
        """
        from hermes_cli import kanban_db as _kb
        conn = _kb.connect(board=board)
        try:
            _kb.advance_notify_cursor(
                conn,
                task_id=sub["task_id"],
                platform=sub["platform"],
                chat_id=sub["chat_id"],
                thread_id=sub.get("thread_id") or "",
                new_cursor=cursor,
            )
        finally:
            conn.close()

    def _kanban_unsub(self, sub: dict, board: Optional[str] = None) -> None:
        from hermes_cli import kanban_db as _kb
        conn = _kb.connect(board=board)
        try:
            _kb.remove_notify_sub(
                conn,
                task_id=sub["task_id"],
                platform=sub["platform"],
                chat_id=sub["chat_id"],
                thread_id=sub.get("thread_id") or "",
            )
        finally:
            conn.close()

    def _kanban_rewind(
        self,
        sub: dict,
        claimed_cursor: int,
        old_cursor: int,
        board: Optional[str] = None,
    ) -> None:
        """Sync helper: undo a claimed notification cursor after send failure."""
        from hermes_cli import kanban_db as _kb
        conn = _kb.connect(board=board)
        try:
            _kb.rewind_notify_cursor(
                conn,
                task_id=sub["task_id"],
                platform=sub["platform"],
                chat_id=sub["chat_id"],
                thread_id=sub.get("thread_id") or "",
                claimed_cursor=claimed_cursor,
                old_cursor=old_cursor,
            )
        finally:
            conn.close()

    async def _deliver_kanban_artifacts(
        self,
        *,
        adapter,
        chat_id: str,
        metadata: dict,
        event_payload: Optional[dict],
        task,
    ) -> None:
        """Upload artifact files referenced by a completed kanban task.

        Workers passing ``kanban_complete(artifacts=[...])`` ship absolute
        file paths through the completion event so downstream humans get
        the deliverable as a native upload instead of a path printed in
        chat.

        Sources scanned, in priority order:
          1. ``event_payload['artifacts']`` (explicit list — preferred)
          2. ``event_payload['summary']`` (truncated first line)
          3. ``task.result`` (legacy fallback)

        Files are deduplicated, missing files are silently skipped (the
        path may have been mentioned for reference only), and delivery
        errors are logged but do not break the notifier loop.
        """
        from pathlib import Path as _Path

        candidates: list[str] = []
        seen: set[str] = set()

        def _add(path: str) -> None:
            if not path:
                return
            expanded = os.path.expanduser(path)
            if expanded in seen:
                return
            if not os.path.isfile(expanded):
                return
            seen.add(expanded)
            candidates.append(expanded)

        # 1. Explicit artifacts list in payload.
        if isinstance(event_payload, dict):
            raw = event_payload.get("artifacts")
            if isinstance(raw, (list, tuple)):
                for item in raw:
                    if isinstance(item, str):
                        _add(item)

            # 2. Paths embedded in the payload summary.
            summary = event_payload.get("summary")
            if isinstance(summary, str) and summary:
                paths, _ = adapter.extract_local_files(summary)
                for p in paths:
                    _add(p)

        # 3. Legacy: paths embedded in task.result.
        if task is not None and getattr(task, "result", None):
            result_text = str(task.result)
            paths, _ = adapter.extract_local_files(result_text)
            for p in paths:
                _add(p)

        if not candidates:
            return

        from gateway.platforms.base import BasePlatformAdapter
        candidates = BasePlatformAdapter.filter_local_delivery_paths(candidates)
        if not candidates:
            return

        _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}

        from urllib.parse import quote as _quote

        # Partition images so they ride a single send_multiple_images call
        # on platforms that support batch image uploads (Signal/Slack RPCs).
        image_paths = [p for p in candidates if _Path(p).suffix.lower() in _IMAGE_EXTS]
        other_paths = [p for p in candidates if _Path(p).suffix.lower() not in _IMAGE_EXTS]

        if image_paths:
            try:
                batch = [(f"file://{_quote(p)}", "") for p in image_paths]
                await adapter.send_multiple_images(
                    chat_id=chat_id, images=batch, metadata=metadata,
                )
            except Exception as exc:
                logger.warning(
                    "kanban notifier: image batch upload failed: %s", exc,
                )

        for path in other_paths:
            ext = _Path(path).suffix.lower()
            try:
                if ext in _VIDEO_EXTS:
                    await adapter.send_video(
                        chat_id=chat_id, video_path=path, metadata=metadata,
                    )
                else:
                    await adapter.send_document(
                        chat_id=chat_id, file_path=path, metadata=metadata,
                    )
            except Exception as exc:
                logger.warning(
                    "kanban notifier: artifact upload (%s) failed: %s",
                    path, exc,
                )

    async def _deliver_to_chat_session(self, event: Any, task_row: dict, session_id: str) -> None:
        """Deliver a kanban event to the originating chat session.

        Routes through the dashboard's ``POST /api/kanban/broadcast`` so the
        WebUI right-panel subscribers attached to that session's channel see
        live task status updates.
        """
        if not session_id:
            return

        title = task_row.get("title", "Unknown")
        worker = task_row.get("assignee", "worker")
        # event may be an Event namedtuple/dataclass or a plain dict
        if hasattr(event, "kind"):
            kind = event.kind
            ev_payload = getattr(event, "payload", None) or {}
        elif isinstance(event, dict):
            kind = event.get("kind", "unknown")
            ev_payload = event.get("payload", {}) or {}
        else:
            kind = "unknown"
            ev_payload = {}

        # Try to broadcast to dashboard /api/kanban/broadcast for WebUI
        try:
            await self._broadcast_to_dashboard(session_id, kind, ev_payload, task_row)
        except Exception:
            logger.debug("kanban notifier: dashboard broadcast failed for %s", session_id)

    async def _inject_kanban_session_wake(
        self,
        *,
        event: Any,
        task: Any,
        session_id: str,
        board: Optional[str] = None,
    ) -> None:
        """Inject a synthetic Coordinator turn for an internal session subscription."""
        if not session_id:
            return
        store = getattr(self, "session_store", None)
        if store is None:
            logger.debug("kanban session-wake: no session_store for %s", session_id)
            return

        source = None
        try:
            store._ensure_loaded()
            with store._lock:  # noqa: SLF001 - same gateway-internal access as _inject_swarm_wake
                for entry in store._entries.values():  # noqa: SLF001
                    if getattr(entry, "session_id", None) == session_id:
                        source = entry.origin
                        break
        except Exception as exc:
            logger.debug("kanban session-wake: store lookup failed for %s: %s", session_id, exc)
            return
        if source is None:
            logger.debug("kanban session-wake: session %s not hosted here", session_id)
            return

        kind = getattr(event, "kind", None) or (event.get("kind") if isinstance(event, dict) else "")
        payload = getattr(event, "payload", None) or (event.get("payload") if isinstance(event, dict) else {}) or {}
        task_id = getattr(task, "id", "") or (task.get("id") if isinstance(task, dict) else "")
        title = getattr(task, "title", "") or (task.get("title") if isinstance(task, dict) else "") or task_id
        status = getattr(task, "status", "") or (task.get("status") if isinstance(task, dict) else "")
        project_path = (
            getattr(task, "workspace_path", "")
            or (task.get("workspace_path") if isinstance(task, dict) else "")
            or ""
        )

        if kind == "completed":
            reason = "已完成（done）"
            detail = str(payload.get("summary") or "").strip()
            action = (
                f'请运行 swarm_task_collect(task_id="{task_id}", project_path="{project_path}") '
                "收集 Worker 产物、核对 File Evidence Ledger，并向用户汇报结果。"
            )
        elif kind == "blocked":
            reason_text = str(payload.get("reason") or "").strip()
            reason = f"被阻塞（blocked）{': ' + reason_text if reason_text else ''}"
            detail = reason_text
            action = (
                f'请运行 swarm_task_collect(task_id="{task_id}", project_path="{project_path}") '
                "查看阻塞原因，向用户汇报并给出下一步建议。"
            )
        elif kind in {"gave_up", "crashed", "timed_out"}:
            reason = {
                "gave_up": "多次启动失败后放弃（gave_up）",
                "crashed": "worker 进程崩溃（crashed）",
                "timed_out": "worker 超时（timed_out）",
            }.get(kind, kind)
            detail = str(payload.get("error") or payload.get("reason") or "").strip()
            action = (
                f'请运行 swarm_task_collect(task_id="{task_id}", project_path="{project_path}") '
                "查看运行历史，决定是否重新分派、拆小任务或向用户请求人工处理。"
            )
        else:
            return

        synth_text = (
            f"[kanban 自动通知] 你之前分派的任务 {task_id}（{title}）{reason}。\n"
            f"Board: {board or ''}；当前状态: {status or kind}。\n"
            + (f"摘要: {detail[:500]}\n" if detail else "")
            + action
        )
        synth_event = MessageEvent(
            text=synth_text,
            message_type=MessageType.TEXT,
            source=source,
            internal=True,
        )
        logger.info(
            "kanban session-wake: injecting Coordinator turn for task %s (%s) into session %s",
            task_id, kind, session_id,
        )
        await self._handle_message(synth_event)

    async def _broadcast_to_dashboard(
        self, session_id: str, kind: str, ev_payload: dict, task_row: dict,
    ) -> None:
        """POST to dashboard's ``/api/kanban/broadcast`` endpoint."""
        import aiohttp

        dashboard_port = os.environ.get("HERMES_DASHBOARD_PORT", "8000")
        url = f"http://127.0.0.1:{dashboard_port}/api/kanban/broadcast"

        summary = ""
        if isinstance(ev_payload, dict):
            summary = str(ev_payload.get("summary", "") or "")

        payload = {
            "channel": session_id,
            "event_type": f"kanban.task.{kind}",
            "payload": {
                "task_id": task_row.get("id", ""),
                "title": task_row.get("title", ""),
                "status": task_row.get("status", ""),
                "worker": task_row.get("assignee", ""),
                "summary": summary,
                "board": task_row.get("board_id", ""),
            },
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        logger.debug("kanban broadcast returned %d", resp.status)
        except Exception as e:
            logger.debug("kanban broadcast failed: %s", e)

    async def _swarm_session_wake_watcher(self, interval: float = 6.0) -> None:
        """Wake the Coordinator session when a swarm task it dispatched finishes.

        The ``swarm_task_*`` tools (tools/kanban_toolset.py) write to a
        *self-contained* per-project DB at ``<project>/kanban/kanban.db`` — a
        different substrate from ``hermes_cli.kanban_db`` that
        ``_kanban_notifier_watcher`` polls, so that notifier never sees swarm
        tasks. This watcher closes the gap: it polls the swarm-toolset DBs for
        terminal/reject events on tasks that recorded a ``session_id`` (the
        coordinator's ``HERMES_SESSION_ID``) and injects a synthetic agent turn
        into that session so the Coordinator runs ``swarm_task_collect`` and
        reports back — enabling non-blocking batch dispatch.

        Wakes on: ``rejected`` (always), ``completed`` (no-gate done),
        ``blocked`` (defensive), and ``approved`` *only when final* (all gates
        passed). Intermediate gate approvals do NOT wake — the task is still
        moving to the next reviewer.

        Single-owner: runs only in the gateway that also hosts the kanban
        dispatcher (the coordinator gateway, per ``kanban.dispatch_in_gateway``),
        so a per-DB linear cursor is race-free. First sight of a DB seeds the
        cursor at the current max event id, so a restart never replays stale
        terminal events as a wake storm. No feedback loop: the injected turn
        drives a read-only ``swarm_task_collect``, which emits no terminal events.
        """
        env_override = os.environ.get("HERMES_SWARM_SESSION_WAKE", "").strip().lower()
        if env_override in {"0", "false", "no", "off"}:
            logger.info("swarm session-wake: disabled via HERMES_SWARM_SESSION_WAKE env")
            return
        try:
            from hermes_cli.config import load_config as _load_config
            cfg = _load_config()
            kanban_cfg = cfg.get("kanban", {}) if isinstance(cfg, dict) else {}
        except Exception:
            kanban_cfg = {}
        # Only the dispatcher (coordinator) gateway wakes sessions: it owns the
        # coordinator session + adapter, giving a single cursor owner.
        if env_override not in {"1", "true", "yes", "on"} and not kanban_cfg.get("dispatch_in_gateway", False):
            logger.info("swarm session-wake: not the dispatcher gateway; watcher idle")
            return

        roots: list[str] = []
        for r in os.environ.get("LEX_PROJECTS_ROOT", "/workingfile/projects").split(":"):
            r = r.strip()
            if r:
                roots.append(r)
        roots.append("/workingfile")

        WAKE_KINDS = ("rejected", "completed", "blocked", "approved")

        await asyncio.sleep(8)  # let adapters + session store settle

        while self._running:
            try:
                def _scan():
                    import sqlite3 as _sql
                    import json as _json
                    found: list[dict] = []
                    seen_paths: set[str] = set()
                    db_paths: list[Path] = []
                    for root in roots:
                        rp = Path(root)
                        try:
                            db_paths.extend(rp.glob("*/kanban/kanban.db"))
                            direct = rp / "kanban" / "kanban.db"
                            if direct.exists():
                                db_paths.append(direct)
                        except Exception:
                            continue
                    for db in db_paths:
                        try:
                            rk = str(db.resolve())
                        except Exception:
                            rk = str(db)
                        if rk in seen_paths:
                            continue
                        seen_paths.add(rk)
                        try:
                            conn = _sql.connect(rk, timeout=5)
                            conn.row_factory = _sql.Row
                        except Exception:
                            continue
                        try:
                            conn.execute(
                                "CREATE TABLE IF NOT EXISTS swarm_wake_cursor "
                                "(id INTEGER PRIMARY KEY CHECK(id=1), last_event_id INTEGER NOT NULL DEFAULT 0)"
                            )
                            cur_row = conn.execute(
                                "SELECT last_event_id FROM swarm_wake_cursor WHERE id=1"
                            ).fetchone()
                            high_row = conn.execute(
                                "SELECT COALESCE(MAX(id),0) AS m FROM task_events"
                            ).fetchone()
                            new_high = int(high_row["m"]) if high_row else 0
                            if cur_row is None:
                                # First sight: seed at current high so we never
                                # replay historical terminal events as a wake storm.
                                conn.execute(
                                    "INSERT INTO swarm_wake_cursor (id, last_event_id) VALUES (1, ?)",
                                    (new_high,),
                                )
                                conn.commit()
                                continue
                            last_id = int(cur_row["last_event_id"])
                            if new_high <= last_id:
                                continue
                            rows = conn.execute(
                                "SELECT e.id AS eid, e.task_id AS task_id, e.kind AS kind, "
                                "       e.payload AS payload, t.title AS title, t.status AS status, "
                                "       t.session_id AS session_id, b.project_path AS project_path "
                                "FROM task_events e "
                                "JOIN tasks t ON t.id = e.task_id "
                                "JOIN board b ON b.id = t.board_id "
                                "WHERE e.id > ? AND e.id <= ? AND e.kind IN (?,?,?,?) "
                                "      AND t.session_id IS NOT NULL AND t.session_id != '' "
                                "ORDER BY e.id ASC",
                                (last_id, new_high, *WAKE_KINDS),
                            ).fetchall()
                            for r in rows:
                                kind = r["kind"]
                                if kind == "approved":
                                    try:
                                        pl = _json.loads(r["payload"] or "{}")
                                    except Exception:
                                        pl = {}
                                    if not pl.get("final"):
                                        continue  # intermediate gate — keep waiting
                                found.append({
                                    "event_id": int(r["eid"]),
                                    "task_id": r["task_id"],
                                    "kind": kind,
                                    "title": r["title"] or r["task_id"],
                                    "status": r["status"],
                                    "session_id": r["session_id"],
                                    "project_path": r["project_path"] or "",
                                })
                            # Advance past everything examined (incl. intermediate
                            # approvals + non-wake kinds) so the cursor never stalls.
                            conn.execute(
                                "UPDATE swarm_wake_cursor SET last_event_id=? WHERE id=1",
                                (new_high,),
                            )
                            conn.commit()
                        finally:
                            conn.close()
                    return found

                wakes = await asyncio.to_thread(_scan)
                for w in wakes:
                    await self._inject_swarm_wake(w)
            except Exception as exc:
                logger.debug("swarm session-wake tick failed: %s", exc)
            await asyncio.sleep(interval)

    async def _inject_swarm_wake(self, wake: dict) -> None:
        """Inject one synthetic Coordinator turn for a swarm terminal/reject event."""
        session_id = wake.get("session_id") or ""
        if not session_id:
            return
        store = getattr(self, "session_store", None)
        if store is None:
            return
        source = None
        try:
            store._ensure_loaded()
            with store._lock:  # noqa: SLF001 — snapshot under lock (mirrors run.py)
                for entry in store._entries.values():  # noqa: SLF001
                    if getattr(entry, "session_id", None) == session_id:
                        # Routing source lives on entry.origin (Optional[SessionSource]),
                        # NOT entry.source — SessionEntry has no `source` attribute, so
                        # the old read raised AttributeError that the except below
                        # swallowed as "store lookup failed", dropping every wake.
                        # Mirrors run.py:2341 (source = entry.origin).
                        source = entry.origin
                        break
        except Exception as exc:
            logger.debug("swarm session-wake: store lookup failed for %s: %s", session_id, exc)
            return
        if source is None:
            logger.debug(
                "swarm session-wake: session %s not hosted here; dropping wake for %s",
                session_id, wake.get("task_id"),
            )
            return

        kind = wake.get("kind")
        tid = wake.get("task_id")
        title = wake.get("title")
        proj = wake.get("project_path") or ""
        if kind == "rejected":
            verb = "被审阅拒绝（rejected）"
            action = (
                f'请运行 swarm_task_collect(task_id="{tid}", project_path="{proj}") 查看拒绝理由与历史，'
                "然后向用户说明被拒原因，并询问是否要重新分派 Drafter 修改。"
            )
        elif kind == "blocked":
            verb = "被阻塞（blocked）"
            action = (
                f'请运行 swarm_task_collect(task_id="{tid}", project_path="{proj}") 查看阻塞原因，'
                "向用户汇报并给出建议。"
            )
        else:  # completed / approved-final
            verb = "已完成并通过全部门禁（done）"
            action = (
                f'请运行 swarm_task_collect(task_id="{tid}", project_path="{proj}") 收集 Worker 产物'
                "（修订对照表 / 审阅报告 / handoff_chain），整合后向用户汇报本任务结果。"
            )
        synth_text = (
            f"[swarm 自动通知] 你之前分派的任务 {tid}（{title}）{verb}。\n"
            f"{action}\n"
            "本次只处理这一个任务；其余正在进行的任务完成时会同样通知你。"
        )
        try:
            synth_event = MessageEvent(
                text=synth_text,
                message_type=MessageType.TEXT,
                source=source,
                internal=True,
            )
            logger.info(
                "swarm session-wake: injecting Coordinator turn for task %s (%s) into session %s (platform=%s)",
                tid, kind, session_id, source.platform,
            )
            # Route through the unified gateway ingress, NOT adapter.handle_message:
            # self.adapters only holds enabled messaging-platform adapters
            # (telegram/feishu). A coordinator session is typically cli/tui/webui,
            # served by the gateway socket/RPC server with no entry in self.adapters,
            # so adapter.handle_message would never reach it. _handle_message is the
            # path adapters themselves funnel into (adapter.set_message_handler) and
            # the handoff dispatcher uses the same inline call for CLI sessions. For
            # cli/tui/webui the reply streams back over the session's own socket;
            # for a messaging-platform session we additionally push the reply via
            # its adapter.
            response_text = await self._handle_message(synth_event)
            adapter = self.adapters.get(source.platform)
            if response_text and adapter is not None and getattr(source, "chat_id", None):
                try:
                    await adapter.send(chat_id=str(source.chat_id), content=response_text)
                except Exception as send_exc:
                    logger.error(
                        "swarm session-wake: adapter.send failed for task %s: %s",
                        tid, send_exc,
                    )
        except Exception as exc:
            logger.error("swarm session-wake: injection failed for task %s: %s", tid, exc)

    async def _kanban_dispatcher_watcher(self) -> None:
        """Embedded kanban dispatcher — one tick every `dispatch_interval_seconds`.

        Gated by `kanban.dispatch_in_gateway` in config.yaml (default True).
        When true, the gateway hosts the single dispatcher for this profile:
        no separate `hermes kanban daemon` process needed. When false, the
        loop exits immediately and an external daemon is expected.

        Each tick calls :func:`kanban_db.dispatch_once` inside
        ``asyncio.to_thread`` so the SQLite WAL lock never blocks the
        event loop. Failures in one tick don't stop subsequent ticks —
        same pattern as `_kanban_notifier_watcher`.

        Shutdown: the loop checks ``self._running`` between ticks; gateway
        stop() flips it to False and cancels pending tasks, and the
        in-flight ``to_thread`` returns on its own after the current
        ``dispatch_once`` call finishes (typically <1ms on an idle board).
        """
        # Read config once at boot. If the user flips the flag later, they
        # restart the gateway; same pattern as every other background
        # watcher here. Honours HERMES_KANBAN_DISPATCH_IN_GATEWAY env var
        # as an escape hatch (false-y value disables without editing YAML).
        try:
            from hermes_cli.config import load_config as _load_config
        except Exception:
            logger.warning("kanban dispatcher: config loader unavailable; disabled")
            return
        env_override = os.environ.get("HERMES_KANBAN_DISPATCH_IN_GATEWAY", "").strip().lower()
        if env_override in {"0", "false", "no", "off"}:
            logger.info("kanban dispatcher: disabled via HERMES_KANBAN_DISPATCH_IN_GATEWAY env")
            return

        try:
            cfg = _load_config()
        except Exception as exc:
            logger.warning("kanban dispatcher: cannot load config (%s); disabled", exc)
            return
        kanban_cfg = cfg.get("kanban", {}) if isinstance(cfg, dict) else {}
        if not kanban_cfg.get("dispatch_in_gateway", True):
            logger.info(
                "kanban dispatcher: disabled via config kanban.dispatch_in_gateway=false"
            )
            return

        try:
            from hermes_cli import kanban_db as _kb
        except Exception:
            logger.warning("kanban dispatcher: kanban_db not importable; dispatcher disabled")
            return

        interval = float(kanban_cfg.get("dispatch_interval_seconds", 60) or 60)
        interval = max(interval, 1.0)  # sanity floor — tighter than this is a footgun

        # Read max_spawn config to limit concurrent kanban tasks
        max_spawn = kanban_cfg.get("max_spawn", None)
        if max_spawn is not None:
            logger.info(f"kanban dispatcher: max_spawn={max_spawn}")

        # Cap the number of simultaneously running tasks so slow workers
        # (local LLMs, resource-constrained hosts) don't pile up and time
        # out. When set, the dispatcher skips spawning when the board
        # already has this many tasks in 'running' status.
        raw_max_in_progress = kanban_cfg.get("max_in_progress", None)
        max_in_progress = None
        if raw_max_in_progress is not None:
            try:
                max_in_progress = int(raw_max_in_progress)
            except (TypeError, ValueError):
                logger.warning(
                    "kanban dispatcher: invalid kanban.max_in_progress=%r; ignoring",
                    raw_max_in_progress,
                )
                max_in_progress = None
            else:
                if max_in_progress < 1:
                    logger.warning(
                        "kanban dispatcher: kanban.max_in_progress=%r is below 1; ignoring",
                        raw_max_in_progress,
                    )
                    max_in_progress = None
                else:
                    logger.info(f"kanban dispatcher: max_in_progress={max_in_progress}")

        raw_failure_limit = kanban_cfg.get("failure_limit", _kb.DEFAULT_FAILURE_LIMIT)
        try:
            failure_limit = int(raw_failure_limit)
        except (TypeError, ValueError):
            logger.warning(
                "kanban dispatcher: invalid kanban.failure_limit=%r; using default %d",
                raw_failure_limit,
                _kb.DEFAULT_FAILURE_LIMIT,
            )
            failure_limit = _kb.DEFAULT_FAILURE_LIMIT
        if failure_limit < 1:
            logger.warning(
                "kanban dispatcher: kanban.failure_limit=%r is below 1; using default %d",
                raw_failure_limit,
                _kb.DEFAULT_FAILURE_LIMIT,
            )
            failure_limit = _kb.DEFAULT_FAILURE_LIMIT

        # Read stale_timeout_seconds — 0 disables stale detection.
        raw_stale = kanban_cfg.get("dispatch_stale_timeout_seconds", 0)
        try:
            stale_timeout_seconds = int(raw_stale or 0)
        except (TypeError, ValueError):
            logger.warning(
                "kanban dispatcher: invalid kanban.dispatch_stale_timeout_seconds=%r; "
                "disabling stale detection",
                raw_stale,
            )
            stale_timeout_seconds = 0

        # Initial delay so the gateway finishes wiring adapters before the
        # dispatcher spawns workers (those workers may hit gateway notify
        # subscriptions etc.). Matches the notifier watcher's delay.
        await asyncio.sleep(5)

        # Health telemetry mirrored from `_cmd_daemon`: warn when ready
        # queue is non-empty but spawns are 0 for N consecutive ticks —
        # usually means broken PATH, missing venv, or credential loss.
        HEALTH_WINDOW = 6
        bad_ticks = 0
        last_warn_at = 0
        tick_counter = 0
        # Avoid hot-looping corrupt-looking board DBs, but do not suppress
        # same-fingerprint retries forever: transient WAL/open races can
        # surface as "database disk image is malformed" for one tick.
        CORRUPT_BOARD_RETRY_AFTER_SECONDS = 300
        disabled_corrupt_boards: dict[
            str, tuple[tuple[str, int | None, int | None], float]
        ] = {}

        def _board_db_fingerprint(slug: str) -> tuple[str, int | None, int | None]:
            path = _kb.kanban_db_path(slug)
            try:
                resolved = str(path.expanduser().resolve())
            except Exception:
                resolved = str(path)
            try:
                stat = path.stat()
            except OSError:
                return (resolved, None, None)
            return (resolved, stat.st_mtime_ns, stat.st_size)

        def _is_corrupt_board_db_error(exc: Exception) -> bool:
            corrupt_guard_error = getattr(_kb, "KanbanDbCorruptError", None)
            if corrupt_guard_error is not None and isinstance(exc, corrupt_guard_error):
                return True
            if not isinstance(exc, sqlite3.DatabaseError):
                return False
            msg = str(exc).lower()
            return (
                "file is not a database" in msg
                or "database disk image is malformed" in msg
            )

        def _tick_once_for_board(slug: str) -> "Optional[object]":
            """Run one dispatch_once for a specific board.

            Runs in a worker thread via `asyncio.to_thread`. `board=slug`
            is passed through `dispatch_once` so `resolve_workspace` and
            `_default_spawn` see the right paths. The per-board DB is
            opened explicitly so concurrent boards never share a
            connection handle or accidentally claim across each other.
            """
            conn = None
            fingerprint = _board_db_fingerprint(slug)
            disabled_entry = disabled_corrupt_boards.get(slug)
            if disabled_entry is not None:
                disabled_fingerprint, disabled_at = disabled_entry
                age = time.monotonic() - disabled_at
                if (
                    disabled_fingerprint == fingerprint
                    and age < CORRUPT_BOARD_RETRY_AFTER_SECONDS
                ):
                    return None
                if disabled_fingerprint == fingerprint:
                    logger.info(
                        "kanban dispatcher: board %s database fingerprint unchanged "
                        "after %.0fs quarantine; retrying dispatch",
                        slug,
                        age,
                    )
                else:
                    logger.info(
                        "kanban dispatcher: board %s database changed; retrying dispatch",
                        slug,
                    )
                disabled_corrupt_boards.pop(slug, None)
            try:
                conn = _kb.connect(board=slug)
                # `connect()` runs the schema + idempotent migration on
                # first open per process; the previous explicit
                # `init_db()` call here busted the per-process cache and
                # re-ran the migration on a second connection, racing
                # the first. See the matching comment in
                # `_kanban_notifier_watcher` and issue #21378.
                return _kb.dispatch_once(
                    conn,
                    board=slug,
                    max_spawn=max_spawn,
                    max_in_progress=max_in_progress,
                    failure_limit=failure_limit,
                    stale_timeout_seconds=stale_timeout_seconds,
                )
            except sqlite3.DatabaseError as exc:
                if _is_corrupt_board_db_error(exc):
                    disabled_corrupt_boards[slug] = (fingerprint, time.monotonic())
                    logger.error(
                        "kanban dispatcher: board %s database %s is not a valid "
                        "SQLite database; pausing dispatch for this board until "
                        "the file changes, the gateway restarts, or the "
                        "quarantine timer expires. Move or restore the file, "
                        "then run `hermes kanban init` if you need a fresh board.",
                        slug,
                        fingerprint[0],
                    )
                    return None
                logger.exception("kanban dispatcher: tick failed on board %s", slug)
                return None
            except Exception as exc:
                if _is_corrupt_board_db_error(exc):
                    disabled_corrupt_boards[slug] = (fingerprint, time.monotonic())
                    logger.error(
                        "kanban dispatcher: board %s database %s is not a valid "
                        "SQLite database; pausing dispatch for this board until "
                        "the file changes, the gateway restarts, or the "
                        "quarantine timer expires. Move or restore the file, "
                        "then run `hermes kanban init` if you need a fresh board.",
                        slug,
                        fingerprint[0],
                    )
                    return None
                logger.exception("kanban dispatcher: tick failed on board %s", slug)
                return None
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        def _tick_once() -> "list[tuple[str, Optional[object]]]":
            """Run one dispatch_once per board. Returns (slug, result) pairs.

            Enumerating boards on every tick keeps the dispatcher honest
            when users create a new board mid-run: no restart required,
            the next tick picks it up automatically.
            """
            try:
                boards = _kb.list_boards(include_archived=False)
            except Exception:
                boards = [_kb.read_board_metadata(_kb.DEFAULT_BOARD)]
            out: list[tuple[str, "Optional[object]"]] = []
            for b in boards:
                slug = b.get("slug") or _kb.DEFAULT_BOARD
                out.append((slug, _tick_once_for_board(slug)))
            return out

        def _ready_nonempty() -> bool:
            """Cheap probe: is there at least one ready+assigned+unclaimed
            task on ANY board whose assignee maps to a real Hermes profile
            (i.e. one the dispatcher would actually spawn for)?

            Tasks assigned to control-plane lanes (e.g. ``orion-cc``,
            ``orion-research``) are pulled by terminals via
            ``claim_task`` directly and never spawnable, so a queue full
            of those is "correctly idle", not "stuck". Filtering them out
            here keeps the stuck-warn fire only on real failures (broken
            PATH, missing venv, credential loss for a real Hermes profile).
            """
            try:
                boards = _kb.list_boards(include_archived=False)
            except Exception:
                boards = [_kb.read_board_metadata(_kb.DEFAULT_BOARD)]
            for b in boards:
                slug = b.get("slug") or _kb.DEFAULT_BOARD
                conn = None
                try:
                    conn = _kb.connect(board=slug)
                    if _kb.has_spawnable_ready(conn):
                        return True
                    if _kb.has_spawnable_review(conn):
                        return True
                except Exception:
                    continue
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
            return False

        # Auto-decompose: turn fresh triage tasks into ready workgraphs
        # before the dispatcher fans out workers. Gated by
        # ``kanban.auto_decompose`` (default True). Capped by
        # ``kanban.auto_decompose_per_tick`` (default 3) so a bulk-load
        # of triage tasks doesn't burst-spend the aux LLM in one tick;
        # remainder defers to subsequent ticks.
        auto_decompose_enabled = bool(kanban_cfg.get("auto_decompose", True))
        try:
            auto_decompose_per_tick = int(
                kanban_cfg.get("auto_decompose_per_tick", 3) or 3
            )
        except (TypeError, ValueError):
            auto_decompose_per_tick = 3
        if auto_decompose_per_tick < 1:
            auto_decompose_per_tick = 1

        def _auto_decompose_tick() -> int:
            """Run the auto-decomposer for up to N triage tasks across all
            boards. Returns the number of triage tasks that were
            successfully decomposed or specified this tick.
            """
            try:
                from hermes_cli import kanban_decompose as _decomp
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "kanban auto-decompose: import failed (%s); skipping", exc,
                )
                return 0
            try:
                boards = _kb.list_boards(include_archived=False)
            except Exception:
                boards = [_kb.read_board_metadata(_kb.DEFAULT_BOARD)]
            attempted = 0
            successes = 0
            for b in boards:
                slug = b.get("slug") or _kb.DEFAULT_BOARD
                if attempted >= auto_decompose_per_tick:
                    break
                # Pin this board for the duration of the call — same
                # pattern as the dashboard specify endpoint. The
                # decomposer module connects with no board kwarg and
                # relies on the env var.
                prev_env = os.environ.get("HERMES_KANBAN_BOARD")
                try:
                    os.environ["HERMES_KANBAN_BOARD"] = slug
                    try:
                        triage_ids = _decomp.list_triage_ids()
                    except Exception as exc:
                        logger.debug(
                            "kanban auto-decompose: list_triage_ids failed on board %s (%s)",
                            slug, exc,
                        )
                        triage_ids = []
                    for tid in triage_ids:
                        if attempted >= auto_decompose_per_tick:
                            break
                        attempted += 1
                        try:
                            outcome = _decomp.decompose_task(
                                tid, author="auto-decomposer",
                            )
                        except Exception:
                            logger.exception(
                                "kanban auto-decompose: decompose_task crashed on %s",
                                tid,
                            )
                            continue
                        if outcome.ok:
                            successes += 1
                            if outcome.fanout and outcome.child_ids:
                                logger.info(
                                    "kanban auto-decompose [%s]: %s → %d children",
                                    slug, tid, len(outcome.child_ids),
                                )
                            else:
                                logger.info(
                                    "kanban auto-decompose [%s]: %s → single task (no fanout)",
                                    slug, tid,
                                )
                        else:
                            # Common no-op reasons (no aux client configured) shouldn't
                            # spam logs every tick. Log at debug.
                            logger.debug(
                                "kanban auto-decompose [%s]: %s skipped: %s",
                                slug, tid, outcome.reason,
                            )
                finally:
                    if prev_env is None:
                        os.environ.pop("HERMES_KANBAN_BOARD", None)
                    else:
                        os.environ["HERMES_KANBAN_BOARD"] = prev_env
            return successes

        logger.info(
            "kanban dispatcher: embedded in gateway (interval=%.1fs)", interval
        )
        while self._running:
            try:
                # Reap zombie children before per-board work so a board DB
                # failure cannot block cleanup of unrelated workers.
                pids = await asyncio.to_thread(_kb.reap_worker_zombies)
                if pids:
                    logger.info(
                        "kanban dispatcher: reaped %d zombie worker(s), pids=%s",
                        len(pids),
                        pids,
                    )
            except Exception:
                logger.exception("kanban dispatcher: zombie reaper failed")

            try:
                if auto_decompose_enabled:
                    await asyncio.to_thread(_auto_decompose_tick)
                results = await asyncio.to_thread(_tick_once)
                any_spawned = False
                for slug, res in (results or []):
                    if res is not None and getattr(res, "spawned", None):
                        any_spawned = True
                        # Quiet by default — only log when something actually
                        # happened, so an idle gateway stays silent.
                        logger.info(
                            "kanban dispatcher [%s]: spawned=%d reclaimed=%d "
                            "crashed=%d timed_out=%d promoted=%d auto_blocked=%d",
                            slug,
                            len(res.spawned),
                            res.reclaimed,
                            len(res.crashed) if hasattr(res.crashed, "__len__") else 0,
                            len(res.timed_out) if hasattr(res.timed_out, "__len__") else 0,
                            res.promoted,
                            len(res.auto_blocked) if hasattr(res.auto_blocked, "__len__") else 0,
                        )
                # Health telemetry (aggregate across boards)
                ready_pending = await asyncio.to_thread(_ready_nonempty)
                if ready_pending and not any_spawned:
                    bad_ticks += 1
                else:
                    bad_ticks = 0
                if bad_ticks >= HEALTH_WINDOW:
                    now = int(time.time())
                    if now - last_warn_at >= 300:
                        logger.warning(
                            "kanban dispatcher stuck: ready queue non-empty for "
                            "%d consecutive ticks but 0 workers spawned. Check "
                            "profile health (venv, PATH, credentials) and "
                            "`hermes kanban list --status ready`.",
                            bad_ticks,
                        )
                        last_warn_at = now
                # Dispatcher heartbeat: summarize boards scanned and tasks
                # spawned every 10 ticks so operators can confirm the loop
                # is alive without spamming logs on idle gateways.
                tick_counter += 1
                if tick_counter % 10 == 0:
                    boards_count = len(results) if results is not None else 0
                    spawned_total = sum(
                        len(res.spawned)
                        for _, res in (results or [])
                        if res is not None and getattr(res, "spawned", None)
                    )
                    logger.info(
                        "kanban dispatcher heartbeat: tick=%d boards=%d spawned=%d",
                        tick_counter, boards_count, spawned_total,
                    )
            except asyncio.CancelledError:
                logger.debug("kanban dispatcher: cancelled")
                raise
            except Exception:
                logger.exception("kanban dispatcher: unexpected watcher error")

            # Sleep in 1s slices so shutdown is snappy — otherwise a stop()
            # waits up to `interval` seconds for the current sleep to finish.
            slept = 0.0
            while slept < interval and self._running:
                await asyncio.sleep(min(1.0, interval - slept))
                slept += 1.0
