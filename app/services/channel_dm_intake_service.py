from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aiogram.enums import ChatType
from aiogram.types import Message, User

from app.config import settings


class AuctionIntakeKind(StrEnum):
    PRIVATE = "private"
    CHANNEL_DM = "channel_dm"
    UNSUPPORTED = "unsupported"


@dataclass(slots=True)
class AuctionIntakeContext:
    kind: AuctionIntakeKind
    chat_id: int | None
    message_thread_id: int | None
    direct_messages_topic_id: int | None
    reason: str | None = None


def extract_direct_messages_topic_id(message: Message | None) -> int | None:
    topic = getattr(message, "direct_messages_topic", None)
    topic_id = getattr(topic, "topic_id", None)
    if isinstance(topic_id, int):
        return topic_id
    return None


def resolve_auction_intake_actor(message: Message | None) -> User | None:
    actor = getattr(message, "from_user", None)
    if actor is not None:
        return actor

    topic = getattr(message, "direct_messages_topic", None)
    topic_user = getattr(topic, "user", None)
    if isinstance(topic_user, User):
        return topic_user
    return None


def resolve_auction_intake_context(message: Message | None) -> AuctionIntakeContext:
    chat = getattr(message, "chat", None)
    chat_type = getattr(chat, "type", None)
    chat_id = getattr(chat, "id", None)
    thread_id = getattr(message, "message_thread_id", None)

    if chat_type == ChatType.PRIVATE:
        return AuctionIntakeContext(
            kind=AuctionIntakeKind.PRIVATE,
            chat_id=chat_id if isinstance(chat_id, int) else None,
            message_thread_id=thread_id if isinstance(thread_id, int) else None,
            direct_messages_topic_id=None,
        )

    is_direct_messages = bool(getattr(chat, "is_direct_messages", False))
    if chat_type == ChatType.SUPERGROUP and is_direct_messages:
        if not settings.channel_dm_intake_enabled:
            return AuctionIntakeContext(
                kind=AuctionIntakeKind.UNSUPPORTED,
                chat_id=chat_id if isinstance(chat_id, int) else None,
                message_thread_id=thread_id if isinstance(thread_id, int) else None,
                direct_messages_topic_id=None,
                reason="channel_dm_disabled",
            )

        allowed_chat_id = int(settings.channel_dm_intake_chat_id)
        normalized_chat_id = chat_id if isinstance(chat_id, int) else None
        if allowed_chat_id and normalized_chat_id != allowed_chat_id:
            return AuctionIntakeContext(
                kind=AuctionIntakeKind.UNSUPPORTED,
                chat_id=normalized_chat_id,
                message_thread_id=thread_id if isinstance(thread_id, int) else None,
                direct_messages_topic_id=None,
                reason="channel_dm_chat_not_allowed",
            )

        direct_topic_id = extract_direct_messages_topic_id(message)
        if direct_topic_id is None:
            return AuctionIntakeContext(
                kind=AuctionIntakeKind.UNSUPPORTED,
                chat_id=normalized_chat_id,
                message_thread_id=thread_id if isinstance(thread_id, int) else None,
                direct_messages_topic_id=None,
                reason="missing_direct_topic_id",
            )

        return AuctionIntakeContext(
            kind=AuctionIntakeKind.CHANNEL_DM,
            chat_id=normalized_chat_id,
            message_thread_id=thread_id if isinstance(thread_id, int) else None,
            direct_messages_topic_id=direct_topic_id,
        )

    return AuctionIntakeContext(
        kind=AuctionIntakeKind.UNSUPPORTED,
        chat_id=chat_id if isinstance(chat_id, int) else None,
        message_thread_id=thread_id if isinstance(thread_id, int) else None,
        direct_messages_topic_id=None,
        reason="unsupported_chat_type",
    )
