from __future__ import annotations

from app.services.chat_owner_guard_service import (
    EVENT_CHAT_OWNER_CHANGED,
    EVENT_CHAT_OWNER_LEFT,
    ChatOwnerServiceEvent,
    build_chat_owner_guard_alert_text,
    parse_chat_owner_service_event,
)


class _DummyUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _DummyChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class _DummyOwnerChanged:
    def __init__(self, *, old_owner: _DummyUser | None = None, new_owner: _DummyUser | None = None) -> None:
        self.old_owner_user = old_owner
        self.new_owner_user = new_owner


class _DummyOwnerLeft:
    def __init__(self, *, owner_id: int | None = None) -> None:
        self.owner_user_id = owner_id


class _DummyMessage:
    def __init__(
        self,
        *,
        chat_id: int = -100500,
        message_id: int = 10,
        owner_changed: _DummyOwnerChanged | None = None,
        owner_left: _DummyOwnerLeft | None = None,
    ) -> None:
        self.chat = _DummyChat(chat_id)
        self.message_id = message_id
        self.chat_owner_changed = owner_changed
        self.chat_owner_left = owner_left


def test_parse_chat_owner_changed_service_event() -> None:
    message = _DummyMessage(
        owner_changed=_DummyOwnerChanged(old_owner=_DummyUser(101), new_owner=_DummyUser(202))
    )

    parsed = parse_chat_owner_service_event(message)

    assert parsed is not None
    assert parsed.event_type == EVENT_CHAT_OWNER_CHANGED
    assert parsed.old_owner_tg_user_id == 101
    assert parsed.new_owner_tg_user_id == 202
    assert parsed.payload["chat_id"] == -100500


def test_parse_chat_owner_left_service_event() -> None:
    message = _DummyMessage(owner_left=_DummyOwnerLeft(owner_id=333))

    parsed = parse_chat_owner_service_event(message)

    assert parsed is not None
    assert parsed.event_type == EVENT_CHAT_OWNER_LEFT
    assert parsed.old_owner_tg_user_id == 333
    assert parsed.new_owner_tg_user_id is None


def test_parse_chat_owner_service_event_ignores_regular_messages() -> None:
    parsed = parse_chat_owner_service_event(_DummyMessage())

    assert parsed is None


def test_build_chat_owner_guard_alert_text_contains_confirmation_command() -> None:
    event = ChatOwnerServiceEvent(
        event_type=EVENT_CHAT_OWNER_CHANGED,
        old_owner_tg_user_id=11,
        new_owner_tg_user_id=22,
        payload={},
    )

    text = build_chat_owner_guard_alert_text(chat_id=-100900, event=event, audit_id=77)

    assert "confirmowner" in text
    assert "-100900" in text
    assert "77" in text
