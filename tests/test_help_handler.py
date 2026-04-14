from __future__ import annotations

from app.bot.handlers.help import build_help_text, build_group_help_text


def test_build_help_text_contains_commands() -> None:
    text = build_help_text()
    assert "/mybids" in text
    assert "/points" in text
    assert "/myrep" in text
    assert "/guarant" in text


def test_build_help_text_mentions_bidding() -> None:
    text = build_help_text()
    assert "ставк" in text.lower() or "bid" in text.lower()


def test_build_group_help_text_is_shorter() -> None:
    group_text = build_group_help_text()
    full_text = build_help_text()
    assert len(group_text) < len(full_text)
