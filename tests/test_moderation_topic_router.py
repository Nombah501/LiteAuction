from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from app.services.moderation_topic_router import (
    ModerationTopicSection,
    resolve_topic_thread_id,
    send_section_message,
)


def test_resolve_topic_thread_id_prefers_section_specific_value(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "moderation_thread_id", "500")
    monkeypatch.setattr(settings, "moderation_topic_appeals_id", "701")

    assert resolve_topic_thread_id(ModerationTopicSection.APPEALS) == 701
    assert resolve_topic_thread_id("unknown_section") == 500


def test_resolve_topic_thread_id_fraud_falls_back_to_legacy_bugs_topic(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "moderation_thread_id", "500")
    monkeypatch.setattr(settings, "moderation_topic_fraud_id", "")
    monkeypatch.setattr(settings, "moderation_topic_bugs_id", "777")

    assert resolve_topic_thread_id(ModerationTopicSection.FRAUD) == 777


def test_resolve_topic_thread_id_channel_dm_guard_prefers_dedicated_topic(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "moderation_thread_id", "500")
    monkeypatch.setattr(settings, "moderation_topic_bugs_id", "777")
    monkeypatch.setattr(settings, "moderation_topic_channel_dm_guard_id", "909")

    assert resolve_topic_thread_id(ModerationTopicSection.CHANNEL_DM_GUARD) == 909


@pytest.mark.asyncio
async def test_send_section_message_uses_section_topic_id(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "moderation_chat_id", "-1001001")
    monkeypatch.setattr(settings, "moderation_thread_id", "500")
    monkeypatch.setattr(settings, "moderation_topic_complaints_id", "701")
    monkeypatch.setattr(settings, "admin_user_ids", "2001,2002")

    calls: list[tuple[int, str, dict]] = []

    class _DummyBot:
        async def send_message(self, chat_id: int, text: str, **kwargs):
            calls.append((chat_id, text, kwargs))
            return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=11)

    ref = await send_section_message(
        cast(Bot, _DummyBot()),
        section=ModerationTopicSection.COMPLAINTS,
        text="new complaint",
    )

    assert ref == (-1001001, 11)
    assert len(calls) == 1
    assert calls[0][0] == -1001001
    assert calls[0][2].get("message_thread_id") == 701


@pytest.mark.asyncio
async def test_send_section_message_falls_back_to_admins_without_moderation_chat(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "moderation_chat_id", "")
    monkeypatch.setattr(settings, "moderation_thread_id", "")
    monkeypatch.setattr(settings, "admin_user_ids", "3001,3002")

    calls: list[int] = []

    class _DummyBot:
        async def send_message(self, chat_id: int, text: str, **kwargs):
            calls.append(chat_id)
            return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=22)

    ref = await send_section_message(
        cast(Bot, _DummyBot()),
        section=ModerationTopicSection.APPEALS,
        text="new appeal",
    )

    assert ref == (3001, 22)
    assert calls == [3001, 3002]


@pytest.mark.asyncio
async def test_send_section_message_falls_back_to_admins_on_forbidden_in_moderation_chat(
    monkeypatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "moderation_chat_id", "-1001001")
    monkeypatch.setattr(settings, "moderation_thread_id", "500")
    monkeypatch.setattr(settings, "moderation_topic_appeals_id", "701")
    monkeypatch.setattr(settings, "admin_user_ids", "3001,3002")

    calls: list[int] = []

    class _DummyBot:
        async def send_message(self, chat_id: int, text: str, **kwargs):
            _ = text
            _ = kwargs
            calls.append(chat_id)
            if chat_id == -1001001:
                raise TelegramForbiddenError(
                    method=SendMessage(chat_id=chat_id, text="fallback"),
                    message="Forbidden: bot was kicked from the group chat",
                )
            return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=33)

    ref = await send_section_message(
        cast(Bot, _DummyBot()),
        section=ModerationTopicSection.APPEALS,
        text="new appeal",
    )

    assert ref == (3001, 33)
    assert calls == [-1001001, 3001, 3002]
