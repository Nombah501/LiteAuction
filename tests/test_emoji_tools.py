from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from aiogram.types import Message

from app.bot.handlers.emoji_tools import _auction_effect_env_template, _collect_message_effect_id
from app.bot.handlers.emoji_tools import _cached_effect_id_for_user, _remember_effect_id_for_user


def test_collect_message_effect_id_from_message() -> None:
    message = cast(Message, SimpleNamespace(effect_id=" 5104841245755180586 "))

    assert _collect_message_effect_id(message) == "5104841245755180586"


def test_collect_message_effect_id_returns_none_when_missing() -> None:
    message = cast(Message, SimpleNamespace(effect_id=None))

    assert _collect_message_effect_id(message) is None


def test_auction_effect_env_template_contains_default_and_event_keys() -> None:
    rendered = _auction_effect_env_template("5104841245755180586")

    assert "AUCTION_MESSAGE_EFFECTS_ENABLED=true" in rendered
    assert "AUCTION_EFFECT_DEFAULT_ID=5104841245755180586" in rendered
    assert "AUCTION_EFFECT_OUTBID_ID=" in rendered
    assert "AUCTION_EFFECT_ENDED_WINNER_ID=" in rendered


def test_effect_id_cache_roundtrip() -> None:
    _remember_effect_id_for_user(123, "5104841245755180586")

    assert _cached_effect_id_for_user(123) == "5104841245755180586"
