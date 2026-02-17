from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app.services import private_topics_service
from app.services.notification_policy_service import (
    NotificationDeliveryDecision,
    NotificationEventType,
)


class _SessionCtx:
    async def __aenter__(self):  # noqa: ANN204
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001, ANN204
        return False


@pytest.mark.asyncio
async def test_send_user_topic_message_records_policy_suppression_metric(monkeypatch) -> None:
    monkeypatch.setattr(private_topics_service.settings, "private_topics_enabled", False)
    monkeypatch.setattr(private_topics_service, "SessionFactory", lambda: _SessionCtx())

    async def _blocked(*_args, **_kwargs) -> NotificationDeliveryDecision:
        return NotificationDeliveryDecision(allowed=False, reason="blocked_master")

    sent_metrics: list[tuple[str, str]] = []
    suppressed_metrics: list[tuple[str, str]] = []

    async def _record_sent(*, event_type: NotificationEventType, reason: str = "delivered") -> None:
        sent_metrics.append((event_type.value, reason))

    async def _record_suppressed(*, event_type: NotificationEventType, reason: str) -> None:
        suppressed_metrics.append((event_type.value, reason))

    async def _record_aggregated(
        *, event_type: NotificationEventType, reason: str, count: int = 1  # noqa: ARG001
    ) -> None:
        raise AssertionError(f"aggregated metric should not be emitted: {event_type.value}:{reason}")

    class _BotStub:
        async def send_message(self, **_kwargs):  # noqa: ANN201
            raise AssertionError("send_message should not be called for blocked policy")

    monkeypatch.setattr(private_topics_service, "notification_delivery_decision", _blocked)
    monkeypatch.setattr(private_topics_service, "record_notification_sent", _record_sent)
    monkeypatch.setattr(private_topics_service, "record_notification_suppressed", _record_suppressed)
    monkeypatch.setattr(private_topics_service, "record_notification_aggregated", _record_aggregated)

    delivered = await private_topics_service.send_user_topic_message(
        cast(Bot, _BotStub()),
        tg_user_id=321,
        purpose=private_topics_service.PrivateTopicPurpose.AUCTIONS,
        text="hello",
        notification_event=NotificationEventType.AUCTION_OUTBID,
    )

    assert delivered is False
    assert sent_metrics == []
    assert suppressed_metrics == [(NotificationEventType.AUCTION_OUTBID.value, "blocked_master")]


@pytest.mark.asyncio
async def test_send_user_topic_message_records_sent_metric_on_delivery(monkeypatch) -> None:
    monkeypatch.setattr(private_topics_service.settings, "private_topics_enabled", False)
    monkeypatch.setattr(private_topics_service, "SessionFactory", lambda: _SessionCtx())

    async def _allowed(*_args, **_kwargs) -> NotificationDeliveryDecision:
        return NotificationDeliveryDecision(allowed=True, reason="allowed")

    sent_metrics: list[tuple[str, str]] = []

    async def _record_sent(*, event_type: NotificationEventType, reason: str = "delivered") -> None:
        sent_metrics.append((event_type.value, reason))

    async def _record_suppressed(*, event_type: NotificationEventType, reason: str) -> None:
        raise AssertionError(f"suppressed metric should not be emitted: {event_type.value}:{reason}")

    async def _record_aggregated(
        *, event_type: NotificationEventType, reason: str, count: int = 1  # noqa: ARG001
    ) -> None:
        return None

    async def _pop_deferred(*, tg_user_id: int, event_type: NotificationEventType) -> int:  # noqa: ARG001
        return 0

    class _BotStub:
        async def send_message(self, **kwargs):  # noqa: ANN201
            return SimpleNamespace(chat=SimpleNamespace(id=kwargs["chat_id"]), message_id=1)

    monkeypatch.setattr(private_topics_service, "notification_delivery_decision", _allowed)
    monkeypatch.setattr(private_topics_service, "record_notification_sent", _record_sent)
    monkeypatch.setattr(private_topics_service, "record_notification_suppressed", _record_suppressed)
    monkeypatch.setattr(private_topics_service, "record_notification_aggregated", _record_aggregated)
    monkeypatch.setattr(private_topics_service, "pop_deferred_notification_count", _pop_deferred)

    delivered = await private_topics_service.send_user_topic_message(
        cast(Bot, _BotStub()),
        tg_user_id=321,
        purpose=private_topics_service.PrivateTopicPurpose.AUCTIONS,
        text="hello",
        notification_event=NotificationEventType.AUCTION_OUTBID,
    )

    assert delivered is True
    assert sent_metrics == [(NotificationEventType.AUCTION_OUTBID.value, "delivered")]


@pytest.mark.asyncio
async def test_send_user_topic_message_records_bad_request_suppression_metric(monkeypatch) -> None:
    monkeypatch.setattr(private_topics_service.settings, "private_topics_enabled", False)
    monkeypatch.setattr(private_topics_service, "SessionFactory", lambda: _SessionCtx())

    async def _allowed(*_args, **_kwargs) -> NotificationDeliveryDecision:
        return NotificationDeliveryDecision(allowed=True, reason="allowed")

    suppressed_metrics: list[tuple[str, str]] = []

    async def _record_sent(*, event_type: NotificationEventType, reason: str = "delivered") -> None:
        raise AssertionError(f"sent metric should not be emitted: {event_type.value}:{reason}")

    async def _record_suppressed(*, event_type: NotificationEventType, reason: str) -> None:
        suppressed_metrics.append((event_type.value, reason))

    async def _record_aggregated(
        *, event_type: NotificationEventType, reason: str, count: int = 1  # noqa: ARG001
    ) -> None:
        return None

    async def _pop_deferred(*, tg_user_id: int, event_type: NotificationEventType) -> int:  # noqa: ARG001
        return 0

    class _BotStub:
        async def send_message(self, **kwargs):  # noqa: ANN201
            raise TelegramBadRequest(
                method=SendMessage(chat_id=kwargs["chat_id"], text=kwargs["text"]),
                message="Bad Request: chat not found",
            )

    monkeypatch.setattr(private_topics_service, "notification_delivery_decision", _allowed)
    monkeypatch.setattr(private_topics_service, "record_notification_sent", _record_sent)
    monkeypatch.setattr(private_topics_service, "record_notification_suppressed", _record_suppressed)
    monkeypatch.setattr(private_topics_service, "record_notification_aggregated", _record_aggregated)
    monkeypatch.setattr(private_topics_service, "pop_deferred_notification_count", _pop_deferred)

    delivered = await private_topics_service.send_user_topic_message(
        cast(Bot, _BotStub()),
        tg_user_id=321,
        purpose=private_topics_service.PrivateTopicPurpose.AUCTIONS,
        text="hello",
        notification_event=NotificationEventType.AUCTION_OUTBID,
    )

    assert delivered is False
    assert suppressed_metrics == [(NotificationEventType.AUCTION_OUTBID.value, "bad_request")]


@pytest.mark.asyncio
async def test_send_user_topic_message_defers_quiet_hours_events(monkeypatch) -> None:
    monkeypatch.setattr(private_topics_service.settings, "private_topics_enabled", False)
    monkeypatch.setattr(private_topics_service, "SessionFactory", lambda: _SessionCtx())

    async def _quiet_hours(*_args, **_kwargs) -> NotificationDeliveryDecision:
        return NotificationDeliveryDecision(allowed=False, reason="quiet_hours_deferred")

    suppressed_metrics: list[tuple[str, str]] = []
    aggregated_metrics: list[tuple[str, str]] = []
    deferred_calls: list[str] = []

    async def _record_sent(*, event_type: NotificationEventType, reason: str = "delivered") -> None:
        raise AssertionError(f"sent metric should not be emitted: {event_type.value}:{reason}")

    async def _record_suppressed(*, event_type: NotificationEventType, reason: str) -> None:
        suppressed_metrics.append((event_type.value, reason))

    async def _record_aggregated(*, event_type: NotificationEventType, reason: str, count: int = 1) -> None:
        aggregated_metrics.append((event_type.value, reason))

    async def _defer(*, tg_user_id: int, event_type: NotificationEventType) -> int:  # noqa: ARG001
        deferred_calls.append(event_type.value)
        return 2

    async def _pop_deferred(*, tg_user_id: int, event_type: NotificationEventType) -> int:  # noqa: ARG001
        return 0

    class _BotStub:
        async def send_message(self, **_kwargs):  # noqa: ANN201
            raise AssertionError("send_message should not be called during quiet-hours deferral")

    monkeypatch.setattr(private_topics_service, "notification_delivery_decision", _quiet_hours)
    monkeypatch.setattr(private_topics_service, "record_notification_sent", _record_sent)
    monkeypatch.setattr(private_topics_service, "record_notification_suppressed", _record_suppressed)
    monkeypatch.setattr(private_topics_service, "record_notification_aggregated", _record_aggregated)
    monkeypatch.setattr(private_topics_service, "defer_notification_event", _defer)
    monkeypatch.setattr(private_topics_service, "pop_deferred_notification_count", _pop_deferred)

    delivered = await private_topics_service.send_user_topic_message(
        cast(Bot, _BotStub()),
        tg_user_id=321,
        purpose=private_topics_service.PrivateTopicPurpose.AUCTIONS,
        text="hello",
        notification_event=NotificationEventType.AUCTION_OUTBID,
    )

    assert delivered is False
    assert suppressed_metrics == [(NotificationEventType.AUCTION_OUTBID.value, "quiet_hours_deferred")]
    assert aggregated_metrics == [(NotificationEventType.AUCTION_OUTBID.value, "quiet_hours_deferred")]
    assert deferred_calls == [NotificationEventType.AUCTION_OUTBID.value]
