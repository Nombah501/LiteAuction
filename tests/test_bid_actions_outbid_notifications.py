from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from aiogram import Bot

from app.bot.handlers import bid_actions
from app.services.notification_digest_service import OutbidDigestDecision
from app.services.notification_policy_service import NotificationEventType


class _BotStub:
    pass


@pytest.mark.asyncio
async def test_notify_outbid_skips_message_when_debounce_denies(monkeypatch) -> None:
    sent_calls: list[dict[str, object]] = []
    suppressed_metrics: list[tuple[str, str]] = []
    aggregated_metrics: list[tuple[str, str]] = []

    async def _deny_debounce(_auction_id: UUID, _tg_user_id: int) -> bool:
        return False

    async def _capture_send(*_args, **kwargs):
        sent_calls.append(kwargs)
        return True

    async def _capture_suppressed(*, event_type: NotificationEventType, reason: str) -> None:
        suppressed_metrics.append((event_type.value, reason))

    async def _capture_aggregated(*, event_type: NotificationEventType, reason: str, count: int = 1) -> None:
        aggregated_metrics.append((event_type.value, reason))

    async def _digest_no_emit(*, tg_user_id: int, auction_id: UUID) -> OutbidDigestDecision:  # noqa: ARG001
        return OutbidDigestDecision(suppressed_count=1, window_seconds=180, should_emit_digest=False)

    monkeypatch.setattr(bid_actions, "acquire_outbid_notification_debounce", _deny_debounce)
    monkeypatch.setattr(bid_actions, "send_user_topic_message", _capture_send)
    monkeypatch.setattr(bid_actions, "record_notification_suppressed", _capture_suppressed)
    monkeypatch.setattr(bid_actions, "record_notification_aggregated", _capture_aggregated)
    monkeypatch.setattr(bid_actions, "register_outbid_notification_suppression", _digest_no_emit)

    await bid_actions._notify_outbid(
        cast(Bot, _BotStub()),
        outbid_user_tg_id=10,
        actor_tg_id=20,
        auction_id=UUID("12345678-1234-5678-1234-567812345678"),
        post_url="https://t.me/example/10",
    )

    assert sent_calls == []
    assert suppressed_metrics == [(NotificationEventType.AUCTION_OUTBID.value, "debounce_gate")]
    assert aggregated_metrics == [(NotificationEventType.AUCTION_OUTBID.value, "debounce_gate")]


@pytest.mark.asyncio
async def test_notify_outbid_sends_message_when_debounce_allows(monkeypatch) -> None:
    sent_calls: list[dict[str, object]] = []

    async def _allow_debounce(_auction_id: UUID, _tg_user_id: int) -> bool:
        return True

    async def _capture_send(*_args, **kwargs):
        sent_calls.append(kwargs)
        return True

    async def _noop_suppressed(*, event_type: NotificationEventType, reason: str) -> None:  # noqa: ARG001
        return None

    async def _noop_aggregated(
        *, event_type: NotificationEventType, reason: str, count: int = 1  # noqa: ARG001
    ) -> None:
        return None

    async def _raise_if_called(*, tg_user_id: int, auction_id: UUID) -> OutbidDigestDecision:  # noqa: ARG001
        raise AssertionError("digest register should not run when debounce allows")

    monkeypatch.setattr(bid_actions, "acquire_outbid_notification_debounce", _allow_debounce)
    monkeypatch.setattr(bid_actions, "send_user_topic_message", _capture_send)
    monkeypatch.setattr(bid_actions, "record_notification_suppressed", _noop_suppressed)
    monkeypatch.setattr(bid_actions, "record_notification_aggregated", _noop_aggregated)
    monkeypatch.setattr(bid_actions, "register_outbid_notification_suppression", _raise_if_called)

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

    async def _noop_suppressed(*, event_type: NotificationEventType, reason: str) -> None:  # noqa: ARG001
        return None

    async def _noop_aggregated(
        *, event_type: NotificationEventType, reason: str, count: int = 1  # noqa: ARG001
    ) -> None:
        return None

    async def _raise_if_called(*, tg_user_id: int, auction_id: UUID) -> OutbidDigestDecision:  # noqa: ARG001
        raise AssertionError("digest register should not run when suppression does not happen")

    monkeypatch.setattr(bid_actions, "should_apply_notification_debounce", _disable_debounce_policy)
    monkeypatch.setattr(bid_actions, "acquire_outbid_notification_debounce", _raise_if_called)
    monkeypatch.setattr(bid_actions, "send_user_topic_message", _capture_send)
    monkeypatch.setattr(bid_actions, "record_notification_suppressed", _noop_suppressed)
    monkeypatch.setattr(bid_actions, "record_notification_aggregated", _noop_aggregated)
    monkeypatch.setattr(bid_actions, "register_outbid_notification_suppression", _raise_if_called)

    await bid_actions._notify_outbid(
        cast(Bot, _BotStub()),
        outbid_user_tg_id=10,
        actor_tg_id=20,
        auction_id=UUID("12345678-1234-5678-1234-567812345678"),
        post_url="https://t.me/example/10",
    )

    assert len(sent_calls) == 1


@pytest.mark.asyncio
async def test_notify_outbid_sends_digest_message_when_suppression_threshold_reached(monkeypatch) -> None:
    sent_calls: list[dict[str, object]] = []

    async def _deny_debounce(_auction_id: UUID, _tg_user_id: int) -> bool:
        return False

    async def _digest_emit(*, tg_user_id: int, auction_id: UUID) -> OutbidDigestDecision:  # noqa: ARG001
        return OutbidDigestDecision(suppressed_count=3, window_seconds=180, should_emit_digest=True)

    async def _capture_send(*_args, **kwargs):
        sent_calls.append(kwargs)
        return True

    async def _noop_suppressed(*, event_type: NotificationEventType, reason: str) -> None:  # noqa: ARG001
        return None

    async def _noop_aggregated(
        *, event_type: NotificationEventType, reason: str, count: int = 1  # noqa: ARG001
    ) -> None:
        return None

    monkeypatch.setattr(bid_actions, "acquire_outbid_notification_debounce", _deny_debounce)
    monkeypatch.setattr(bid_actions, "register_outbid_notification_suppression", _digest_emit)
    monkeypatch.setattr(bid_actions, "send_user_topic_message", _capture_send)
    monkeypatch.setattr(bid_actions, "record_notification_suppressed", _noop_suppressed)
    monkeypatch.setattr(bid_actions, "record_notification_aggregated", _noop_aggregated)

    await bid_actions._notify_outbid(
        cast(Bot, _BotStub()),
        outbid_user_tg_id=10,
        actor_tg_id=20,
        auction_id=UUID("12345678-1234-5678-1234-567812345678"),
        post_url="https://t.me/example/10",
    )

    assert len(sent_calls) == 1
    assert "Дайджест по лоту #12345678" in str(sent_calls[0]["text"])
    assert "перебивали 3 раза" in str(sent_calls[0]["text"])
    assert sent_calls[0]["reply_markup"] is not None
