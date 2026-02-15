from __future__ import annotations

import pytest
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import User
from app.services.rbac_service import SCOPE_TRUST_MANAGE
from app.services.verification_service import get_user_verification_status
from app.web.auth import AdminAuthContext
from app.web.main import action_unverify_user, action_verify_user


def _make_request(path: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def _stub_auth() -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="token",
        role="owner",
        can_manage=True,
        scopes=frozenset({SCOPE_TRUST_MANAGE}),
        tg_user_id=None,
    )


class _BotSessionStub:
    async def close(self) -> None:
        return None


class _BotStub:
    def __init__(self, *, token: str, default):  # noqa: ANN001
        self.token = token
        self.default = default
        self.session = _BotSessionStub()

    async def verify_user(self, *, user_id: int, custom_description: str | None = None) -> bool:  # noqa: ARG002
        return True

    async def remove_user_verification(self, *, user_id: int) -> bool:  # noqa: ARG002
        return True


@pytest.mark.asyncio
async def test_web_verify_and_unverify_user_actions(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            actor = User(tg_user_id=99801, username="actor")
            target = User(tg_user_id=99802, username="target")
            session.add_all([actor, target])
            await session.flush()
            actor_id = actor.id
            target_tg_id = target.tg_user_id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _csrf: True)

    async def _resolve_actor(_auth):  # noqa: ANN001
        return actor_id

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _resolve_actor)
    monkeypatch.setattr("app.web.main.Bot", _BotStub)
    monkeypatch.setattr("app.web.main.settings.bot_token", "test-token")

    verify_response = await action_verify_user(
        _make_request("/actions/user/verify"),
        target_tg_user_id=target_tg_id,
        custom_description="official seller",
        return_to="/manage/users",
        csrf_token="ok",
        confirmed="1",
    )
    assert verify_response.status_code == 303

    async with session_factory() as session:
        status_after_verify = await get_user_verification_status(session, tg_user_id=target_tg_id)
        assert status_after_verify.is_verified is True

    unverify_response = await action_unverify_user(
        _make_request("/actions/user/unverify"),
        target_tg_user_id=target_tg_id,
        return_to="/manage/users",
        csrf_token="ok",
        confirmed="1",
    )
    assert unverify_response.status_code == 303

    async with session_factory() as session:
        status_after_unverify = await get_user_verification_status(session, tg_user_id=target_tg_id)
        assert status_after_unverify.is_verified is False
