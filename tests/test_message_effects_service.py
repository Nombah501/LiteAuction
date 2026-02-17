from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app.services.message_effects_service import (
    AuctionMessageEffectEvent,
    resolve_auction_message_effect_id,
)
from app.services.private_topics_service import PrivateTopicPurpose, send_user_topic_message


def test_resolve_auction_message_effect_respects_feature_flag(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "auction_message_effects_enabled", False)
    monkeypatch.setattr(settings, "auction_effect_outbid_id", "5104841245755180586")

    assert resolve_auction_message_effect_id(AuctionMessageEffectEvent.OUTBID) is None


def test_resolve_auction_message_effect_returns_configured_mapping(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "auction_message_effects_enabled", True)
    monkeypatch.setattr(settings, "auction_effect_outbid_id", " 5104841245755180586 ")

    assert (
        resolve_auction_message_effect_id(AuctionMessageEffectEvent.OUTBID)
        == "5104841245755180586"
    )


def test_resolve_auction_message_effect_falls_back_to_default_id(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "auction_message_effects_enabled", True)
    monkeypatch.setattr(settings, "auction_effect_default_id", " 5104841245755180586 ")
    monkeypatch.setattr(settings, "auction_effect_outbid_id", "")

    assert (
        resolve_auction_message_effect_id(AuctionMessageEffectEvent.OUTBID)
        == "5104841245755180586"
    )


def test_resolve_auction_message_effect_prefers_event_specific_id(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "auction_message_effects_enabled", True)
    monkeypatch.setattr(settings, "auction_effect_default_id", "5104841245755180586")
    monkeypatch.setattr(settings, "auction_effect_outbid_id", "5107584321108051014")

    assert (
        resolve_auction_message_effect_id(AuctionMessageEffectEvent.OUTBID)
        == "5107584321108051014"
    )


@pytest.mark.asyncio
async def test_send_user_topic_message_retries_without_unsupported_effect(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "private_topics_enabled", False)

    calls: list[dict[str, object]] = []

    class _DummyBot:
        async def send_message(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise TelegramBadRequest(
                    method=SendMessage(chat_id=123, text="hello"),
                    message="Bad Request: message effect is unsupported",
                )
            return SimpleNamespace(chat=SimpleNamespace(id=kwargs["chat_id"]), message_id=10)

    delivered = await send_user_topic_message(
        cast(Bot, _DummyBot()),
        tg_user_id=123,
        purpose=PrivateTopicPurpose.AUCTIONS,
        text="hello",
        message_effect_id="5104841245755180586",
    )

    assert delivered is True
    assert len(calls) == 2
    assert calls[0].get("message_effect_id") == "5104841245755180586"
    assert "message_effect_id" not in calls[1]


@pytest.mark.asyncio
async def test_send_user_topic_message_retries_without_effect_on_bad_request(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "private_topics_enabled", False)

    calls: list[dict[str, object]] = []

    class _DummyBot:
        async def send_message(self, **kwargs):
            calls.append(kwargs)
            raise TelegramBadRequest(
                method=SendMessage(chat_id=123, text="hello"),
                message="Bad Request: chat not found",
            )

    delivered = await send_user_topic_message(
        cast(Bot, _DummyBot()),
        tg_user_id=123,
        purpose=PrivateTopicPurpose.AUCTIONS,
        text="hello",
        message_effect_id="5104841245755180586",
    )

    assert delivered is False
    assert len(calls) == 2
    assert calls[0].get("message_effect_id") == "5104841245755180586"
    assert "message_effect_id" not in calls[1]
