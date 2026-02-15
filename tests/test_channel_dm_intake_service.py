from __future__ import annotations

from aiogram.enums import ChatType
from aiogram.types import User

from app.config import settings
from app.services.channel_dm_intake_service import (
    AuctionIntakeKind,
    extract_direct_messages_topic_id,
    resolve_auction_intake_actor,
    resolve_auction_intake_context,
)


class _DummyChat:
    def __init__(self, *, chat_type: ChatType, chat_id: int, is_direct_messages: bool = False) -> None:
        self.type = chat_type
        self.id = chat_id
        self.is_direct_messages = is_direct_messages


class _DummyDirectMessagesTopic:
    def __init__(self, *, topic_id: int | None = None, user: User | None = None) -> None:
        self.topic_id = topic_id
        self.user = user


class _DummyMessage:
    def __init__(
        self,
        *,
        chat: _DummyChat,
        from_user: User | None = None,
        direct_topic: _DummyDirectMessagesTopic | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        self.chat = chat
        self.from_user = from_user
        self.direct_messages_topic = direct_topic
        self.message_thread_id = message_thread_id


def test_private_chat_context_detected() -> None:
    message = _DummyMessage(chat=_DummyChat(chat_type=ChatType.PRIVATE, chat_id=100), message_thread_id=55)

    context = resolve_auction_intake_context(message)

    assert context.kind == AuctionIntakeKind.PRIVATE
    assert context.chat_id == 100
    assert context.message_thread_id == 55
    assert context.direct_messages_topic_id is None


def test_channel_dm_context_detected_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "channel_dm_intake_enabled", True)
    monkeypatch.setattr(settings, "channel_dm_intake_chat_id", 0)
    message = _DummyMessage(
        chat=_DummyChat(chat_type=ChatType.SUPERGROUP, chat_id=-1001, is_direct_messages=True),
        direct_topic=_DummyDirectMessagesTopic(topic_id=321),
    )

    context = resolve_auction_intake_context(message)

    assert context.kind == AuctionIntakeKind.CHANNEL_DM
    assert context.chat_id == -1001
    assert context.direct_messages_topic_id == 321


def test_channel_dm_missing_topic_id_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "channel_dm_intake_enabled", True)
    monkeypatch.setattr(settings, "channel_dm_intake_chat_id", 0)
    message = _DummyMessage(
        chat=_DummyChat(chat_type=ChatType.SUPERGROUP, chat_id=-1001, is_direct_messages=True),
        direct_topic=_DummyDirectMessagesTopic(topic_id=None),
    )

    context = resolve_auction_intake_context(message)

    assert context.kind == AuctionIntakeKind.UNSUPPORTED
    assert context.reason == "missing_direct_topic_id"


def test_channel_dm_disallowed_chat_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "channel_dm_intake_enabled", True)
    monkeypatch.setattr(settings, "channel_dm_intake_chat_id", -10055)
    message = _DummyMessage(
        chat=_DummyChat(chat_type=ChatType.SUPERGROUP, chat_id=-1001, is_direct_messages=True),
        direct_topic=_DummyDirectMessagesTopic(topic_id=42),
    )

    context = resolve_auction_intake_context(message)

    assert context.kind == AuctionIntakeKind.UNSUPPORTED
    assert context.reason == "channel_dm_chat_not_allowed"


def test_actor_falls_back_to_direct_topic_user() -> None:
    topic_user = User(id=501, is_bot=False, first_name="Seller")
    message = _DummyMessage(
        chat=_DummyChat(chat_type=ChatType.SUPERGROUP, chat_id=-1001, is_direct_messages=True),
        direct_topic=_DummyDirectMessagesTopic(topic_id=77, user=topic_user),
        from_user=None,
    )

    actor = resolve_auction_intake_actor(message)

    assert actor is not None
    assert actor.id == 501
    assert extract_direct_messages_topic_id(message) == 77
