from __future__ import annotations

import uuid

import pytest
from starlette.requests import Request

from app.services.moderation_service import RoleUpdateResult
from app.services.rbac_service import SCOPE_ROLE_MANAGE
from app.web.auth import AdminAuthContext, get_admin_auth_context
from app.web.main import action_grant_moderator, action_revoke_moderator


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


def _make_request(path: str, *, query: str = "", cookie: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", cookie.encode("utf-8")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query.encode("utf-8"),
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def _auth_with_scope(scope: str) -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="token",
        role="owner",
        can_manage=True,
        scopes=frozenset({scope}),
        tg_user_id=None,
    )


@pytest.mark.asyncio
async def test_web_grant_moderator_success(monkeypatch) -> None:
    request = _make_request("/actions/user/moderator/grant", query="token=test-token")
    called: list[int] = []

    monkeypatch.setattr("app.web.main._require_scope_permission", lambda *_: (None, _auth_with_scope(SCOPE_ROLE_MANAGE)))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory())

    async def fake_grant(_session, *, target_tg_user_id: int):
        called.append(target_tg_user_id)
        return RoleUpdateResult(ok=True, message="ok", target_tg_user_id=target_tg_user_id)

    monkeypatch.setattr("app.web.main.grant_moderator_role", fake_grant)

    response = await action_grant_moderator(
        request,
        target_tg_user_id=777,
        reason="grant role",
        return_to="/manage/users",
        csrf_token="ok",
    )

    assert response.status_code == 303
    assert called == [777]


@pytest.mark.asyncio
async def test_web_revoke_moderator_failure(monkeypatch) -> None:
    request = _make_request("/actions/user/moderator/revoke", query="token=test-token")
    called: list[int] = []

    monkeypatch.setattr("app.web.main._require_scope_permission", lambda *_: (None, _auth_with_scope(SCOPE_ROLE_MANAGE)))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory())

    async def fake_revoke(_session, *, target_tg_user_id: int):
        called.append(target_tg_user_id)
        return RoleUpdateResult(ok=False, message="not allowed", target_tg_user_id=target_tg_user_id)

    monkeypatch.setattr("app.web.main.revoke_moderator_role", fake_revoke)

    response = await action_revoke_moderator(
        request,
        target_tg_user_id=888,
        reason="revoke role",
        return_to="/manage/users",
        csrf_token="ok",
    )

    assert response.status_code == 400
    assert called == [888]


def test_cookie_auth_loses_access_after_allowlist_downgrade(monkeypatch) -> None:
    from app.config import settings
    from app.web.auth import build_admin_session_cookie

    monkeypatch.setattr(settings, "admin_user_ids", "1001")
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")
    monkeypatch.setattr(settings, "admin_web_session_secret", str(uuid.uuid4()))

    cookie_value = build_admin_session_cookie(1001)
    request = _make_request("/", cookie=f"la_admin_session={cookie_value}")

    auth_before = get_admin_auth_context(request)
    assert auth_before.authorized is True

    monkeypatch.setattr(settings, "admin_user_ids", "")
    auth_after = get_admin_auth_context(request)
    assert auth_after.authorized is False
