from __future__ import annotations

from app.bot.handlers.bid_actions import _compose_bid_success_alert, _extract_amount_from_alert_text


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
