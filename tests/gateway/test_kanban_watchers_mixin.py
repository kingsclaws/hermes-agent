"""Tests for the extracted GatewayKanbanWatchersMixin (god-file Phase 3).

The kanban watcher loops were lifted out of gateway/run.py into a mixin that
GatewayRunner inherits. These tests confirm the mixin exposes the methods and
that GatewayRunner picks them up via the MRO (behavior-neutral relocation).
"""

from __future__ import annotations

import asyncio
import inspect

from gateway.kanban_watchers import (
    GatewayKanbanWatchersMixin,
    _LocalSessionAdapter,
    _normalize_notification_platform,
    _notification_platforms,
)

KANBAN_METHODS = [
    "_kanban_notifier_watcher",
    "_kanban_dispatcher_watcher",
    "_kanban_advance",
    "_kanban_unsub",
    "_kanban_rewind",
    "_deliver_kanban_artifacts",
]


def test_mixin_defines_kanban_methods():
    for m in KANBAN_METHODS:
        assert hasattr(GatewayKanbanWatchersMixin, m), f"mixin missing {m}"


def test_gateway_runner_inherits_mixin():
    # Import here so a heavy gateway import only happens if the first test passed.
    from gateway.run import GatewayRunner

    assert issubclass(GatewayRunner, GatewayKanbanWatchersMixin)
    # Each kanban method resolves to the mixin's implementation via the MRO.
    for m in KANBAN_METHODS:
        owner = next(c for c in GatewayRunner.__mro__ if m in c.__dict__)
        assert owner is GatewayKanbanWatchersMixin, (
            f"{m} resolved to {owner.__name__}, expected the mixin"
        )


def test_watcher_loops_are_coroutines():
    # The two long-running watchers are async loops.
    assert inspect.iscoroutinefunction(GatewayKanbanWatchersMixin._kanban_notifier_watcher)
    assert inspect.iscoroutinefunction(GatewayKanbanWatchersMixin._kanban_dispatcher_watcher)


def test_local_session_adapter_persists_transport_free_notification():
    events = []
    notifications = []

    async def handle_message(event):
        events.append(event)
        return "handled"

    async def exercise():
        adapter = _LocalSessionAdapter(
            handle_message,
            enqueue_notification=lambda **kwargs: notifications.append(kwargs),
        )
        await adapter.send(
            "session-1", "notification", metadata={"notification_id": "evt-1"},
        )
        return await adapter.handle_message("wake")

    assert asyncio.run(exercise()) == "handled"
    assert events == ["wake"]
    assert notifications == [{
        "session_id": "session-1",
        "message": (
            "[IMPORTANT: Internal Kanban notification for this session.\n"
            "notification\n"
            "Inspect or collect the task if needed, then report the material "
            "result to the user without repeating this notification verbatim.]"
        ),
        "notification_id": "evt-1",
        "kind": "kanban",
    }]


def test_local_subscriptions_are_polled_without_connected_adapters():
    assert _notification_platforms({}) == {"local", "session"}


def test_legacy_session_subscription_uses_local_delivery():
    assert _normalize_notification_platform("session") == "local"
    assert _normalize_notification_platform("LOCAL") == "local"
    assert _normalize_notification_platform("telegram") == "telegram"


def test_singleton_dispatcher_lock_is_exclusive(tmp_path):
    """Only one holder of the dispatcher lock at a time — the backstop that
    stops concurrent dispatchers double reclaiming and corrupting shared
    kanban SQLite index pages under wal_autocheckpoint=0."""
    import os

    from gateway.kanban_watchers import _acquire_singleton_lock, _release_singleton_lock

    lock = tmp_path / "kanban" / ".dispatcher.lock"

    h1, st1 = _acquire_singleton_lock(lock)
    assert st1 == "held" and h1 is not None

    # A second acquire while the first is held must be refused, not granted.
    h2, st2 = _acquire_singleton_lock(lock)
    assert st2 == "contended" and h2 is None

    # Releasing the first lets a fresh acquire succeed (lock is reusable).
    _release_singleton_lock(h1)
    h3, st3 = _acquire_singleton_lock(lock)
    assert st3 == "held" and h3 is not None
    _release_singleton_lock(h3)
