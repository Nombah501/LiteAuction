from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.bot.handlers.bid_actions import (
    _compose_bid_success_alert,
    _extract_amount_from_alert_text,
    _notify_moderators_about_fraud,
)
from app.services.moderation_topic_router import ModerationTopicSection


def test_extract_amount_from_alert_text_returns_integer() -> None:
    assert _extract_amount_from_alert_text("Ставка принята: $95") == 95


def test_extract_amount_from_alert_text_returns_none_when_absent() -> None:
    assert _extract_amount_from_alert_text("Ставка принята") is None


def test_compose_bid_success_alert_prefers_explicit_amount() -> None:
    text = _compose_bid_success_alert(
        alert_text="Ставка принята",
        placed_bid_amount=120,
        include_soft_gate_hint=False,
    )

    assert text == "✅ Ставка зафиксирована: $120"


def test_compose_bid_success_alert_uses_amount_from_alert_text() -> None:
    text = _compose_bid_success_alert(
        alert_text="Ставка принята: $77",
        placed_bid_amount=None,
        include_soft_gate_hint=False,
    )

    assert text == "✅ Ставка зафиксирована: $77"


def test_compose_bid_success_alert_keeps_amount_with_soft_gate_hint(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "bot_username", "liteauctionbot")
    text = _compose_bid_success_alert(
        alert_text="Ставка принята: $88",
        placed_bid_amount=None,
        include_soft_gate_hint=True,
    )

    assert "✅ Ставка зафиксирована: $88" in text
    assert "@liteauctionbot" in text


def test_compose_bid_success_alert_marks_buyout_when_present() -> None:
    text = _compose_bid_success_alert(
        alert_text="Выкуп оформлен за $150",
        placed_bid_amount=150,
        include_soft_gate_hint=False,
    )

    assert text == "✅ Выкуп оформлен: $150"


@pytest.mark.asyncio
async def test_notify_moderators_about_fraud_uses_fraud_section_with_callbacks(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _send_section_message_stub(bot, *, section, text, reply_markup=None):
        _ = bot
        captured["section"] = section
        captured["text"] = text
        captured["reply_markup"] = reply_markup
        return (777, 55)

    monkeypatch.setattr("app.bot.handlers.bid_actions.send_section_message", _send_section_message_stub)

    ref = await _notify_moderators_about_fraud(
        bot=SimpleNamespace(),
        signal_id=42,
        text="fraud signal",
    )

    assert ref == (777, 55)
    assert captured["section"] == ModerationTopicSection.FRAUD
    reply_markup = captured["reply_markup"]
    assert reply_markup is not None
    rows = getattr(reply_markup, "inline_keyboard", [])
    callback_data = {
        button.callback_data
        for row in rows
        for button in row
        if button.callback_data
    }
    assert callback_data
    assert all(data.startswith("modrisk:") for data in callback_data)
