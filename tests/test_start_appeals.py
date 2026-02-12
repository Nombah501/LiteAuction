from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from aiogram import Bot
from aiogram.types import Message

from app.bot.handlers.start import (
    _appeal_acceptance_text,
    _extract_start_payload,
    _notify_moderators_about_appeal,
)


def test_extract_start_payload() -> None:
    assert _extract_start_payload(cast(Message, SimpleNamespace(text="/start"))) is None
    assert (
        _extract_start_payload(cast(Message, SimpleNamespace(text="/start appeal_risk_10")))
        == "appeal_risk_10"
    )
    assert (
        _extract_start_payload(cast(Message, SimpleNamespace(text="/start   appeal_complaint_1   ")))
        == "appeal_complaint_1"
    )


def test_appeal_acceptance_text_contains_appeal_id() -> None:
    assert "#145" in _appeal_acceptance_text(145)


@pytest.mark.asyncio
async def test_notify_moderators_about_appeal_uses_admin_fallback(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "moderation_chat_id", "")
    monkeypatch.setattr(settings, "moderation_thread_id", "")
    monkeypatch.setattr(settings, "admin_user_ids", "1001,1002")

    sent: list[tuple[int, str]] = []

    class _DummyBot:
        async def send_message(self, chat_id: int, text: str, **_kwargs) -> None:
            sent.append((chat_id, text))

    message = cast(
        Message,
        SimpleNamespace(
            from_user=SimpleNamespace(id=777, username="alice"),
        ),
    )

    await _notify_moderators_about_appeal(cast(Bot, _DummyBot()), message, "risk_42", appeal_id=54)

    assert [item[0] for item in sent] == [1001, 1002]
    assert all("Новая апелляция" in item[1] for item in sent)
    assert all("Appeal ID: 54" in item[1] for item in sent)
    assert all("risk_42" in item[1] for item in sent)
