from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from aiogram import Bot

from app.bot.handlers import bid_actions
from app.services.notification_policy_service import NotificationEventType


class _BotStub:
    pass


@pytest.mark.asyncio
async def test_notify_outbid_skips_message_when_debounce_denies(monkeypatch) -> None:
    sent_calls: list[dict[str, object]] = []

    async def _deny_debounce(_auction_id: UUID, _tg_user_id: int) -> bool:
        return False

    async def _capture_send(*_args, **kwargs):
        sent_calls.append(kwargs)
        return True

    monkeypatch.setattr(bid_actions, "acquire_outbid_notification_debounce", _deny_debounce)
    monkeypatch.setattr(bid_actions, "send_user_topic_message", _capture_send)

    await bid_actions._notify_outbid(
        cast(Bot, _BotStub()),
        outbid_user_tg_id=10,
        actor_tg_id=20,
        auction_id=UUID("12345678-1234-5678-1234-567812345678"),
        post_url="https://t.me/example/10",
    )

    assert sent_calls == []


@pytest.mark.asyncio
async def test_notify_outbid_sends_message_when_debounce_allows(monkeypatch) -> None:
    sent_calls: list[dict[str, object]] = []

    async def _allow_debounce(_auction_id: UUID, _tg_user_id: int) -> bool:
        return True

    async def _capture_send(*_args, **kwargs):
        sent_calls.append(kwargs)
        return True

    monkeypatch.setattr(bid_actions, "acquire_outbid_notification_debounce", _allow_debounce)
    monkeypatch.setattr(bid_actions, "send_user_topic_message", _capture_send)

    auction_id = UUID("12345678-1234-5678-1234-567812345678")
    await bid_actions._notify_outbid(
        cast(Bot, _BotStub()),
        outbid_user_tg_id=10,
        actor_tg_id=20,
        auction_id=auction_id,
        post_url="https://t.me/example/10",
    )

    assert len(sent_calls) == 1
    assert sent_calls[0]["notification_event"] == NotificationEventType.AUCTION_OUTBID
    assert sent_calls[0]["auction_id"] == auction_id


@pytest.mark.asyncio
async def test_notify_outbid_skips_debounce_gate_when_policy_disables_it(monkeypatch) -> None:
    sent_calls: list[dict[str, object]] = []

    def _disable_debounce_policy(_event_type: NotificationEventType) -> bool:
        return False

    async def _raise_if_called(_auction_id: UUID, _tg_user_id: int) -> bool:
        raise AssertionError("debounce gate should not run when policy disables it")

    async def _capture_send(*_args, **kwargs):
        sent_calls.append(kwargs)
        return True

    monkeypatch.setattr(bid_actions, "should_apply_notification_debounce", _disable_debounce_policy)
    monkeypatch.setattr(bid_actions, "acquire_outbid_notification_debounce", _raise_if_called)
    monkeypatch.setattr(bid_actions, "send_user_topic_message", _capture_send)

    await bid_actions._notify_outbid(
        cast(Bot, _BotStub()),
        outbid_user_tg_id=10,
        actor_tg_id=20,
        auction_id=UUID("12345678-1234-5678-1234-567812345678"),
        post_url="https://t.me/example/10",
    )

    assert len(sent_calls) == 1
