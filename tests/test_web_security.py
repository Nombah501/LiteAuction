from __future__ import annotations

import uuid

import pytest
from starlette.requests import Request

from app.web.auth import build_admin_session_cookie, get_admin_auth_context
from app.web.main import (
    _build_csrf_token,
    _require_scope_permission,
    _safe_return_to,
    _validate_csrf_token,
    action_ban_user,
    action_end_auction,
)
from app.services.rbac_service import SCOPE_AUCTION_MANAGE, SCOPE_USER_BAN


def _make_request(
    *,
    path: str,
    method: str = "GET",
    query: str = "",
    cookie: str | None = None,
    referer: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", cookie.encode("utf-8")))
    if referer is not None:
        headers.append((b"referer", referer.encode("utf-8")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
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


def test_operator_scope_restrictions(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_user_ids", "1001,2002")
    monkeypatch.setattr(settings, "admin_operator_user_ids", "2002")
    monkeypatch.setattr(settings, "admin_web_session_secret", "unit-test-secret")

    cookie_value = build_admin_session_cookie(2002)
    request = _make_request(
        path="/actions/user/ban",
        method="POST",
        cookie=f"la_admin_session={cookie_value}",
    )

    denied_response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    assert auth.authorized is True
    assert auth.role == "operator"
    assert denied_response is not None
    assert denied_response.status_code == 403

    allowed_response, _ = _require_scope_permission(request, SCOPE_AUCTION_MANAGE)
    assert allowed_response is None


def test_scope_denied_preserves_referer_back_link(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_user_ids", "1001,2002")
    monkeypatch.setattr(settings, "admin_operator_user_ids", "2002")
    monkeypatch.setattr(settings, "admin_web_session_secret", "unit-test-secret")

    cookie_value = build_admin_session_cookie(2002)
    request = _make_request(
        path="/actions/user/ban",
        method="POST",
        cookie=f"la_admin_session={cookie_value}",
        referer="http://testserver/manage/auction/abc?timeline_page=2&timeline_limit=25",
    )

    denied_response, _ = _require_scope_permission(request, SCOPE_USER_BAN)
    assert denied_response is not None
    assert denied_response.status_code == 403
    body = bytes(denied_response.body).decode("utf-8")
    assert "/manage/auction/abc?timeline_page=2&amp;timeline_limit=25" in body


def test_scope_denied_uses_safe_return_to_query(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_user_ids", "1001,2002")
    monkeypatch.setattr(settings, "admin_operator_user_ids", "2002")
    monkeypatch.setattr(settings, "admin_web_session_secret", "unit-test-secret")

    cookie_value = build_admin_session_cookie(2002)
    request = _make_request(
        path="/actions/user/ban",
        method="POST",
        query="return_to=/manage/users?page=3",
        cookie=f"la_admin_session={cookie_value}",
    )

    denied_response, _ = _require_scope_permission(request, SCOPE_USER_BAN)
    assert denied_response is not None
    body = bytes(denied_response.body).decode("utf-8")
    assert "/manage/users?page=3" in body


def test_scope_denied_rejects_protocol_relative_return_to(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_user_ids", "1001,2002")
    monkeypatch.setattr(settings, "admin_operator_user_ids", "2002")
    monkeypatch.setattr(settings, "admin_web_session_secret", "unit-test-secret")

    cookie_value = build_admin_session_cookie(2002)
    request = _make_request(
        path="/actions/user/ban",
        method="POST",
        query="return_to=//evil.example/steal",
        cookie=f"la_admin_session={cookie_value}",
    )

    denied_response, _ = _require_scope_permission(request, SCOPE_USER_BAN)
    assert denied_response is not None
    body = bytes(denied_response.body).decode("utf-8")
    assert "//evil.example/steal" not in body
    assert "href='/'" in body


def test_scope_denied_rejects_protocol_relative_referer_path(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_user_ids", "1001,2002")
    monkeypatch.setattr(settings, "admin_operator_user_ids", "2002")
    monkeypatch.setattr(settings, "admin_web_session_secret", "unit-test-secret")

    cookie_value = build_admin_session_cookie(2002)
    request = _make_request(
        path="/actions/user/ban",
        method="POST",
        cookie=f"la_admin_session={cookie_value}",
        referer="http://testserver//evil.example/steal?x=1",
    )

    denied_response, _ = _require_scope_permission(request, SCOPE_USER_BAN)
    assert denied_response is not None
    body = bytes(denied_response.body).decode("utf-8")
    assert "//evil.example/steal" not in body
    assert "href='/'" in body


def test_safe_return_to_rejects_protocol_relative_paths() -> None:
    assert _safe_return_to("//evil.example/steal", "/manage/users") == "/manage/users"
    assert _safe_return_to("/manage/users?page=2", "/") == "/manage/users?page=2"


def test_csrf_token_validation_by_subject(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_panel_token", "test-admin-token")
    monkeypatch.setattr(settings, "admin_web_session_secret", "csrf-secret")

    request = _make_request(path="/actions/auction/end", method="POST", query="token=test-admin-token")
    auth = get_admin_auth_context(request)
    assert auth.authorized is True

    token = _build_csrf_token(request, auth)
    assert _validate_csrf_token(request, auth, token) is True

    other_request = _make_request(path="/actions/auction/end", method="POST", query="token=other-token")
    assert _validate_csrf_token(other_request, auth, token) is False


@pytest.mark.asyncio
async def test_end_action_returns_confirmation_page(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_panel_token", "test-admin-token")
    monkeypatch.setattr(settings, "admin_web_session_secret", "csrf-secret")

    auction_id = str(uuid.uuid4())
    request = _make_request(path="/actions/auction/end", method="POST", query="token=test-admin-token")
    auth = get_admin_auth_context(request)
    csrf_token = _build_csrf_token(request, auth)

    response = await action_end_auction(
        request,
        auction_id=auction_id,
        reason="manual end",
        return_to=f"/manage/auction/{auction_id}",
        csrf_token=csrf_token,
        confirmed=None,
    )

    assert response.status_code == 200
    body = bytes(response.body).decode("utf-8")
    assert "Подтверждение завершения аукциона" in body
    assert "name='confirmed' value='1'" in body


@pytest.mark.asyncio
async def test_ban_action_rejects_invalid_csrf(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_panel_token", "test-admin-token")
    monkeypatch.setattr(settings, "admin_web_session_secret", "csrf-secret")

    request = _make_request(path="/actions/user/ban", method="POST", query="token=test-admin-token")

    response = await action_ban_user(
        request,
        target_tg_user_id=99999,
        reason="fraud",
        return_to="/manage/users",
        csrf_token="invalid-token",
        confirmed="1",
    )

    assert response.status_code == 403
    assert "CSRF check failed" in bytes(response.body).decode("utf-8")
