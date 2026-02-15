from __future__ import annotations

from typing import cast

import pytest
from aiogram import Bot

from app.db.enums import ModerationAction
from app.services.bot_profile_photo_service import (
    apply_bot_profile_photo_preset,
    list_bot_profile_photo_presets,
    rollback_bot_profile_photo,
)


def test_list_bot_profile_photo_presets_returns_sorted_names(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "bot_profile_photo_presets", "campaign=file_b, default=file_a")

    assert list_bot_profile_photo_presets() == ["campaign", "default"]


@pytest.mark.asyncio
async def test_apply_bot_profile_photo_preset_sets_configured_file_id(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "bot_profile_photo_presets", "campaign=file_campaign")

    captured: dict[str, object] = {}

    class _DummyBot:
        async def set_my_profile_photo(self, *, photo):
            captured["photo"] = photo
            return True

    result = await apply_bot_profile_photo_preset(cast(Bot, _DummyBot()), preset="campaign")

    assert result.ok is True
    assert result.action == ModerationAction.SET_BOT_PROFILE_PHOTO
    assert result.payload == {"preset": "campaign"}
    assert getattr(captured["photo"], "photo") == "file_campaign"


@pytest.mark.asyncio
async def test_rollback_bot_profile_photo_prefers_default_preset(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "bot_profile_photo_presets", "default=file_default,campaign=file_campaign")
    monkeypatch.setattr(settings, "bot_profile_photo_default_preset", "default")

    called = {"set": 0, "remove": 0}

    class _DummyBot:
        async def set_my_profile_photo(self, *, photo):
            called["set"] += 1
            assert getattr(photo, "photo") == "file_default"
            return True

        async def remove_my_profile_photo(self):
            called["remove"] += 1
            return True

    result = await rollback_bot_profile_photo(cast(Bot, _DummyBot()))

    assert result.ok is True
    assert result.action == ModerationAction.SET_BOT_PROFILE_PHOTO
    assert called == {"set": 1, "remove": 0}


@pytest.mark.asyncio
async def test_rollback_bot_profile_photo_removes_when_default_missing(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "bot_profile_photo_presets", "campaign=file_campaign")
    monkeypatch.setattr(settings, "bot_profile_photo_default_preset", "default")

    called = {"set": 0, "remove": 0}

    class _DummyBot:
        async def set_my_profile_photo(self, *, photo):
            called["set"] += 1
            return True

        async def remove_my_profile_photo(self):
            called["remove"] += 1
            return True

    result = await rollback_bot_profile_photo(cast(Bot, _DummyBot()))

    assert result.ok is True
    assert result.action == ModerationAction.REMOVE_BOT_PROFILE_PHOTO
    assert called == {"set": 0, "remove": 1}
