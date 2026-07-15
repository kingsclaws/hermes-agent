import queue

from tools import session_notifications as notifications


def test_notification_is_idempotent_and_scoped_by_session(monkeypatch, tmp_path):
    monkeypatch.setattr(notifications, "get_hermes_home", lambda: tmp_path)

    notifications.enqueue_notification(
        session_id="one", message="first", notification_id="same", kind="kanban",
    )
    notifications.enqueue_notification(
        session_id="one", message="duplicate", notification_id="same", kind="kanban",
    )
    notifications.enqueue_notification(
        session_id="two", message="other", notification_id="other", kind="kanban",
    )

    claim, rows = notifications.claim_notifications(session_id="one", consumer="test")
    assert [row["message"] for row in rows] == ["first"]
    assert notifications.acknowledge_notifications(["same"], claim) == 1
    _, rows = notifications.claim_notifications(session_id="one", consumer="test")
    assert rows == []
    _, rows = notifications.claim_notifications(session_id="two", consumer="test")
    assert [row["message"] for row in rows] == ["other"]


def test_expired_claim_can_be_recovered(monkeypatch, tmp_path):
    monkeypatch.setattr(notifications, "get_hermes_home", lambda: tmp_path)
    notifications.enqueue_notification(
        session_id="one", message="retry", notification_id="retry-1",
    )

    _, first = notifications.claim_notifications(session_id="one", consumer="dead")
    assert len(first) == 1
    _, blocked = notifications.claim_notifications(
        session_id="one", consumer="next", claim_ttl=300,
    )
    assert blocked == []
    _, recovered = notifications.claim_notifications(
        session_id="one", consumer="next", claim_ttl=-1,
    )
    assert [row["notification_id"] for row in recovered] == ["retry-1"]


def test_delivery_enters_only_the_active_cli_session_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(notifications, "get_hermes_home", lambda: tmp_path)
    notifications.enqueue_notification(
        session_id="active", message="kanban finished", notification_id="done-1",
    )
    notifications.enqueue_notification(
        session_id="other", message="private", notification_id="done-2",
    )
    pending_input = queue.Queue()

    delivered = notifications.deliver_notifications_to_queue(
        session_id="active", target_queue=pending_input,
    )

    assert delivered == 1
    assert pending_input.get_nowait() == "kanban finished"
    assert pending_input.empty()
    _, other = notifications.claim_notifications(session_id="other", consumer="test")
    assert [row["message"] for row in other] == ["private"]
