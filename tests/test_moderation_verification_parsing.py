from __future__ import annotations

from app.bot.handlers.moderation import _parse_chat_and_description, _parse_tg_user_and_description


def test_parse_tg_user_and_description_with_optional_text() -> None:
    parsed = _parse_tg_user_and_description("/verifyuser 12345 trusted seller")

    assert parsed == (12345, "trusted seller")


def test_parse_tg_user_and_description_without_optional_text() -> None:
    parsed = _parse_tg_user_and_description("/unverifyuser 9876")

    assert parsed == (9876, None)


def test_parse_chat_and_description_accepts_negative_chat_id() -> None:
    parsed = _parse_chat_and_description("/verifychat -1001234567890 verified official channel")

    assert parsed == (-1001234567890, "verified official channel")


def test_parse_chat_and_description_rejects_invalid_id() -> None:
    assert _parse_chat_and_description("/verifychat not-a-number test") is None
