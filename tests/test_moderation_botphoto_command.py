from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.bot.handlers.moderation import mod_botphoto
from app.db.enums import ModerationAction
from app.services.bot_profile_photo_service import BotProfilePhotoResult


class _DummyFromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _DummyMessage:
    def __init__(self, text: str, user_id: int = 1234) -> None:
        self.text = text
        self.from_user = _DummyFromUser(user_id)
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


class _DummyBegin:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _DummySession:
    def begin(self) -> _DummyBegin:
        return _DummyBegin()


class _DummySessionFactoryCtx:
    async def __aenter__(self) -> _DummySession:
        return _DummySession()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _DummySessionFactory:
    def __call__(self) -> _DummySessionFactoryCtx:
        return _DummySessionFactoryCtx()


@pytest.mark.asyncio
async def test_mod_botphoto_list_shows_presets(monkeypatch) -> None:
    from app.config import settings

    message = _DummyMessage("/botphoto list")

    async def ensure_topic(_message, _bot, _command_hint):
        return True

    async def require_scope(_message, _scope):
        return True

    monkeypatch.setattr(settings, "bot_profile_photo_presets", "default=file_a,campaign=file_b")
    monkeypatch.setattr(settings, "bot_profile_photo_default_preset", "default")
    monkeypatch.setattr("app.bot.handlers.moderation._ensure_moderation_topic", ensure_topic)
    monkeypatch.setattr("app.bot.handlers.moderation._require_scope_message", require_scope)

    await mod_botphoto(message, bot=SimpleNamespace())

    assert len(message.answers) == 1
    assert "default (default)" in message.answers[0]
    assert "campaign" in message.answers[0]


@pytest.mark.asyncio
async def test_mod_botphoto_set_logs_audit(monkeypatch) -> None:
    message = _DummyMessage("/botphoto set campaign", user_id=42)
    logged: list[dict[str, object]] = []

    async def ensure_topic(_message, _bot, _command_hint):
        return True

    async def require_scope(_message, _scope):
        return True

    async def apply_preset(_bot, *, preset: str):
        assert preset == "campaign"
        return BotProfilePhotoResult(
            ok=True,
            message="done",
            action=ModerationAction.SET_BOT_PROFILE_PHOTO,
            reason="set campaign",
            payload={"preset": "campaign"},
        )

    async def fake_upsert_user(_session, _from_user, mark_private_started: bool = False):
        return SimpleNamespace(id=700)

    async def fake_log_action(_session, **kwargs):
        logged.append(kwargs)

    monkeypatch.setattr("app.bot.handlers.moderation._ensure_moderation_topic", ensure_topic)
    monkeypatch.setattr("app.bot.handlers.moderation._require_scope_message", require_scope)
    monkeypatch.setattr("app.bot.handlers.moderation.apply_bot_profile_photo_preset", apply_preset)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", _DummySessionFactory())
    monkeypatch.setattr("app.bot.handlers.moderation.upsert_user", fake_upsert_user)
    monkeypatch.setattr("app.bot.handlers.moderation.log_moderation_action", fake_log_action)

    await mod_botphoto(message, bot=SimpleNamespace())

    assert message.answers == ["done"]
    assert len(logged) == 1
    assert logged[0]["actor_user_id"] == 700
    assert logged[0]["action"] == ModerationAction.SET_BOT_PROFILE_PHOTO
    assert logged[0]["reason"] == "set campaign"
    assert logged[0]["payload"] == {"preset": "campaign"}
