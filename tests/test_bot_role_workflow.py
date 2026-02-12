from __future__ import annotations

import pytest

from app.bot.handlers.moderation import mod_role_manage
from app.db.enums import UserRole
from app.services.moderation_service import RoleUpdateResult
from app.services.rbac_service import SCOPE_AUCTION_MANAGE, SCOPE_BID_MANAGE


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
async def test_role_list_workflow(monkeypatch) -> None:
    message = _DummyMessage("/role list 777")

    async def allow_scope(_message, _scope):
        return True

    async def fake_list_roles(_session, target_tg_user_id: int):
        assert target_tg_user_id == 777
        return {UserRole.MODERATOR}

    async def fake_scopes(_session, target_tg_user_id: int):
        assert target_tg_user_id == 777
        return frozenset({SCOPE_AUCTION_MANAGE, SCOPE_BID_MANAGE})

    monkeypatch.setattr("app.bot.handlers.moderation._require_scope_message", allow_scope)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", _DummySessionFactory())
    monkeypatch.setattr("app.bot.handlers.moderation.list_tg_user_roles", fake_list_roles)
    monkeypatch.setattr("app.bot.handlers.moderation.get_moderation_scopes", fake_scopes)
    monkeypatch.setattr(
        "app.bot.handlers.moderation.allowlist_role_and_scopes",
        lambda *_args, **_kwargs: ("viewer", frozenset()),
    )

    await mod_role_manage(message)

    assert len(message.answers) == 1
    text = message.answers[0]
    assert "TG user: 777" in text
    assert "Allowlist role: none" in text
    assert "DB roles: MODERATOR" in text
    assert "Scopes: auction:manage, bid:manage" in text


@pytest.mark.asyncio
async def test_role_grant_workflow(monkeypatch) -> None:
    message = _DummyMessage("/role grant 888 moderator")
    called: list[int] = []

    async def allow_scope(_message, _scope):
        return True

    async def fake_grant(_session, *, target_tg_user_id: int):
        called.append(target_tg_user_id)
        return RoleUpdateResult(ok=True, message="Права модератора выданы", target_tg_user_id=target_tg_user_id)

    monkeypatch.setattr("app.bot.handlers.moderation._require_scope_message", allow_scope)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", _DummySessionFactory())
    monkeypatch.setattr("app.bot.handlers.moderation.grant_moderator_role", fake_grant)

    await mod_role_manage(message)

    assert called == [888]
    assert message.answers == ["Права модератора выданы"]


@pytest.mark.asyncio
async def test_role_rejects_unsupported_role(monkeypatch) -> None:
    message = _DummyMessage("/role revoke 999 admin")

    async def allow_scope(_message, _scope):
        return True

    monkeypatch.setattr("app.bot.handlers.moderation._require_scope_message", allow_scope)

    await mod_role_manage(message)

    assert message.answers == ["Сейчас поддерживается только роль moderator"]
