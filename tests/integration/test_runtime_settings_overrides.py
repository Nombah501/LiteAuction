from __future__ import annotations

import pytest
from fastapi.responses import HTMLResponse
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import User
from app.services.runtime_settings_service import resolve_runtime_setting_value
from app.web.auth import AdminAuthContext
from app.web.main import action_delete_runtime_setting, action_set_runtime_setting


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


def _stub_owner_auth() -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="token",
        role="owner",
        can_manage=True,
        scopes=frozenset(),
        tg_user_id=None,
    )


def _stub_operator_auth() -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="telegram",
        role="operator",
        can_manage=True,
        scopes=frozenset(),
        tg_user_id=42,
    )


@pytest.mark.asyncio
async def test_owner_can_set_and_delete_runtime_override(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            actor = User(tg_user_id=77701, username="owner")
            session.add(actor)
            await session.flush()
            actor_id = actor.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_owner_permission", lambda _req: (None, _stub_owner_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _csrf: True)

    async def _resolve_actor(_auth):  # noqa: ANN001
        return actor_id

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _resolve_actor)

    set_response = await action_set_runtime_setting(
        _make_request("/actions/settings/runtime/set"),
        key="fraud_alert_threshold",
        value="88",
        return_to="/settings",
        csrf_token="ok",
    )
    assert set_response.status_code == 303

    async with session_factory() as session:
        value_after_set = await resolve_runtime_setting_value(session, "fraud_alert_threshold")
        assert value_after_set == 88

    delete_response = await action_delete_runtime_setting(
        _make_request("/actions/settings/runtime/delete"),
        key="fraud_alert_threshold",
        return_to="/settings",
        csrf_token="ok",
    )
    assert delete_response.status_code == 303

    async with session_factory() as session:
        value_after_delete = await resolve_runtime_setting_value(session, "fraud_alert_threshold")
        assert value_after_delete == 60


@pytest.mark.asyncio
async def test_non_owner_cannot_set_runtime_override(monkeypatch) -> None:
    forbidden = HTMLResponse("forbidden", status_code=403)
    monkeypatch.setattr(
        "app.web.main._require_owner_permission",
        lambda _req: (forbidden, _stub_operator_auth()),
    )

    response = await action_set_runtime_setting(
        _make_request("/actions/settings/runtime/set"),
        key="fraud_alert_threshold",
        value="90",
        return_to="/settings",
        csrf_token="ok",
    )
    assert response.status_code == 403
