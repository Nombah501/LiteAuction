from __future__ import annotations

import logging
from typing import cast

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendMessage

from app.services import private_topics_service
from app.services.notification_policy_service import NotificationDeliveryDecision, NotificationEventType


class _SessionCtx:
    async def __aenter__(self):  # noqa: ANN204
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001, ANN204
        return False


@pytest.mark.asyncio
async def test_delivery_decision_is_logged_with_reason_code(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(private_topics_service.settings, "private_topics_enabled", False)
    monkeypatch.setattr(private_topics_service, "SessionFactory", lambda: _SessionCtx())

    async def _decision(*_args, **_kwargs) -> NotificationDeliveryDecision:
        return NotificationDeliveryDecision(allowed=False, reason="blocked_event_toggle")

    async def _record_suppressed(*, event_type: NotificationEventType, reason: str) -> None:  # noqa: ARG001
        return None

    async def _record_aggregated(*, event_type: NotificationEventType, reason: str, count: int = 1) -> None:  # noqa: ARG001
        return None

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(private_topics_service, "notification_delivery_decision", _decision)
    monkeypatch.setattr(private_topics_service, "record_notification_suppressed", _record_suppressed)
    monkeypatch.setattr(private_topics_service, "record_notification_aggregated", _record_aggregated)

    class _BotStub:
        async def send_message(self, **_kwargs):  # noqa: ANN201
            raise AssertionError("send_message should not run for blocked decision")

    delivered = await private_topics_service.send_user_topic_message(
        cast(Bot, _BotStub()),
        tg_user_id=321,
        purpose=private_topics_service.PrivateTopicPurpose.AUCTIONS,
        text="hello",
        notification_event=NotificationEventType.POINTS,
    )

    assert delivered is False
    assert "notification_delivery_decision" in caplog.text
    assert "event=points" in caplog.text
    assert "allowed=False" in caplog.text
    assert "reason=blocked_event_toggle" in caplog.text


@pytest.mark.asyncio
async def test_delivery_failure_log_includes_event_and_failure_class(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(private_topics_service.settings, "private_topics_enabled", False)
    monkeypatch.setattr(private_topics_service, "SessionFactory", lambda: _SessionCtx())

    async def _decision(*_args, **_kwargs) -> NotificationDeliveryDecision:
        return NotificationDeliveryDecision(allowed=True, reason="allowed")

    async def _record_sent(*, event_type: NotificationEventType, reason: str = "delivered") -> None:  # noqa: ARG001
        return None

    async def _record_suppressed(*, event_type: NotificationEventType, reason: str) -> None:  # noqa: ARG001
        return None

    async def _record_aggregated(*, event_type: NotificationEventType, reason: str, count: int = 1) -> None:  # noqa: ARG001
        return None

    async def _pop_deferred(*, tg_user_id: int, event_type: NotificationEventType) -> int:  # noqa: ARG001
        return 0

    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(private_topics_service, "notification_delivery_decision", _decision)
    monkeypatch.setattr(private_topics_service, "record_notification_sent", _record_sent)
    monkeypatch.setattr(private_topics_service, "record_notification_suppressed", _record_suppressed)
    monkeypatch.setattr(private_topics_service, "record_notification_aggregated", _record_aggregated)
    monkeypatch.setattr(private_topics_service, "pop_deferred_notification_count", _pop_deferred)

    class _BotStub:
        async def send_message(self, **kwargs):  # noqa: ANN201
            raise TelegramBadRequest(
                method=SendMessage(chat_id=kwargs["chat_id"], text=kwargs["text"]),
                message="Bad Request: chat not found",
            )

    delivered = await private_topics_service.send_user_topic_message(
        cast(Bot, _BotStub()),
        tg_user_id=777,
        purpose=private_topics_service.PrivateTopicPurpose.AUCTIONS,
        text="hello",
        notification_event=NotificationEventType.AUCTION_OUTBID,
    )

    assert delivered is False
    assert "notification_delivery_failed" in caplog.text
    assert "event=auction_outbid" in caplog.text
    assert "reason=bad_request" in caplog.text
    assert "failure_class=TelegramBadRequest" in caplog.text


@pytest.mark.asyncio
async def test_delivery_failure_log_includes_forbidden_reason_and_metric(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(private_topics_service.settings, "private_topics_enabled", False)
    monkeypatch.setattr(private_topics_service, "SessionFactory", lambda: _SessionCtx())

    async def _decision(*_args, **_kwargs) -> NotificationDeliveryDecision:
        return NotificationDeliveryDecision(allowed=True, reason="allowed")

    async def _record_sent(*, event_type: NotificationEventType, reason: str = "delivered") -> None:  # noqa: ARG001
        return None

    suppressed_reasons: list[str] = []

    async def _record_suppressed(*, event_type: NotificationEventType, reason: str) -> None:  # noqa: ARG001
        suppressed_reasons.append(reason)

    async def _record_aggregated(*, event_type: NotificationEventType, reason: str, count: int = 1) -> None:  # noqa: ARG001
        return None

    async def _pop_deferred(*, tg_user_id: int, event_type: NotificationEventType) -> int:  # noqa: ARG001
        return 0

    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(private_topics_service, "notification_delivery_decision", _decision)
    monkeypatch.setattr(private_topics_service, "record_notification_sent", _record_sent)
    monkeypatch.setattr(private_topics_service, "record_notification_suppressed", _record_suppressed)
    monkeypatch.setattr(private_topics_service, "record_notification_aggregated", _record_aggregated)
    monkeypatch.setattr(private_topics_service, "pop_deferred_notification_count", _pop_deferred)

    class _BotStub:
        async def send_message(self, **kwargs):  # noqa: ANN201
            raise TelegramForbiddenError(
                method=SendMessage(chat_id=kwargs["chat_id"], text=kwargs["text"]),
                message="Forbidden: bot was blocked by the user",
            )

    delivered = await private_topics_service.send_user_topic_message(
        cast(Bot, _BotStub()),
        tg_user_id=888,
        purpose=private_topics_service.PrivateTopicPurpose.AUCTIONS,
        text="hello",
        notification_event=NotificationEventType.AUCTION_OUTBID,
    )

    assert delivered is False
    assert "notification_delivery_failed" in caplog.text
    assert "reason=forbidden" in caplog.text
    assert "failure_class=TelegramForbiddenError" in caplog.text
    assert suppressed_reasons == ["forbidden"]


@pytest.mark.asyncio
async def test_delivery_failure_log_includes_telegram_api_error_reason_and_metric(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(private_topics_service.settings, "private_topics_enabled", False)
    monkeypatch.setattr(private_topics_service, "SessionFactory", lambda: _SessionCtx())

    async def _decision(*_args, **_kwargs) -> NotificationDeliveryDecision:
        return NotificationDeliveryDecision(allowed=True, reason="allowed")

    async def _record_sent(*, event_type: NotificationEventType, reason: str = "delivered") -> None:  # noqa: ARG001
        return None

    suppressed_reasons: list[str] = []

    async def _record_suppressed(*, event_type: NotificationEventType, reason: str) -> None:  # noqa: ARG001
        suppressed_reasons.append(reason)

    async def _record_aggregated(*, event_type: NotificationEventType, reason: str, count: int = 1) -> None:  # noqa: ARG001
        return None

    async def _pop_deferred(*, tg_user_id: int, event_type: NotificationEventType) -> int:  # noqa: ARG001
        return 0

    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(private_topics_service, "notification_delivery_decision", _decision)
    monkeypatch.setattr(private_topics_service, "record_notification_sent", _record_sent)
    monkeypatch.setattr(private_topics_service, "record_notification_suppressed", _record_suppressed)
    monkeypatch.setattr(private_topics_service, "record_notification_aggregated", _record_aggregated)
    monkeypatch.setattr(private_topics_service, "pop_deferred_notification_count", _pop_deferred)

    class _BotStub:
        async def send_message(self, **kwargs):  # noqa: ANN201
            raise TelegramAPIError(
                method=SendMessage(chat_id=kwargs["chat_id"], text=kwargs["text"]),
                message="Internal Telegram API error",
            )

    delivered = await private_topics_service.send_user_topic_message(
        cast(Bot, _BotStub()),
        tg_user_id=999,
        purpose=private_topics_service.PrivateTopicPurpose.AUCTIONS,
        text="hello",
        notification_event=NotificationEventType.AUCTION_OUTBID,
    )

    assert delivered is False
    assert "notification_delivery_failed" in caplog.text
    assert "reason=telegram_api_error" in caplog.text
    assert "failure_class=TelegramAPIError" in caplog.text
    assert suppressed_reasons == ["telegram_api_error"]
