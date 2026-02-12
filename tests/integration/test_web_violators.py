from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import BlacklistEntry, User
from app.services.rbac_service import SCOPE_USER_BAN
from app.web.auth import AdminAuthContext
from app.web.main import violators


def _make_request(path: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
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
        scopes=frozenset({SCOPE_USER_BAN}),
        tg_user_id=None,
    )


@pytest.mark.asyncio
async def test_violators_page_filters_active_entries(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            target_active = User(tg_user_id=99001, username="active_user")
            target_inactive = User(tg_user_id=99002, username="inactive_user")
            actor = User(tg_user_id=99003, username="mod")
            session.add_all([target_active, target_inactive, actor])
            await session.flush()

            session.add(
                BlacklistEntry(
                    user_id=target_active.id,
                    reason="fraud active",
                    created_by_user_id=actor.id,
                    is_active=True,
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                BlacklistEntry(
                    user_id=target_inactive.id,
                    reason="historical",
                    created_by_user_id=actor.id,
                    is_active=False,
                    created_at=datetime.now(UTC),
                )
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/violators")
    response = await violators(request, status="active", page=0, q="")

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "active_user" in body
    assert "inactive_user" not in body
    assert "fraud active" in body


@pytest.mark.asyncio
async def test_violators_page_search_by_reason(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            target = User(tg_user_id=99101, username="test_user")
            actor = User(tg_user_id=99102, username="mod")
            session.add_all([target, actor])
            await session.flush()

            session.add(
                BlacklistEntry(
                    user_id=target.id,
                    reason="chargeback abuse",
                    created_by_user_id=actor.id,
                    is_active=True,
                    created_at=datetime.now(UTC),
                )
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/violators")
    response = await violators(request, status="all", page=0, q="chargeback")

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "chargeback abuse" in body
    assert "test_user" in body


@pytest.mark.asyncio
async def test_violators_page_filters_by_moderator_and_date(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)
    earlier = now - timedelta(days=4)

    async with session_factory() as session:
        async with session.begin():
            actor_recent = User(tg_user_id=99211, username="mod_recent")
            actor_old = User(tg_user_id=99212, username="mod_old")
            target_recent = User(tg_user_id=99213, username="recent_user")
            target_old = User(tg_user_id=99214, username="old_user")
            session.add_all([actor_recent, actor_old, target_recent, target_old])
            await session.flush()

            session.add(
                BlacklistEntry(
                    user_id=target_recent.id,
                    reason="recent violation",
                    created_by_user_id=actor_recent.id,
                    is_active=True,
                    created_at=now,
                )
            )
            session.add(
                BlacklistEntry(
                    user_id=target_old.id,
                    reason="old violation",
                    created_by_user_id=actor_old.id,
                    is_active=True,
                    created_at=earlier,
                )
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/violators")
    response = await violators(
        request,
        status="active",
        page=0,
        q="",
        by="mod_recent",
        created_from=(now - timedelta(days=1)).strftime("%Y-%m-%d"),
        created_to=now.strftime("%Y-%m-%d"),
    )

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "recent_user" in body
    assert "old_user" not in body
    assert "recent violation" in body
    assert "old violation" not in body


@pytest.mark.asyncio
async def test_violators_page_shows_unban_action_for_active_entries(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            target = User(tg_user_id=99311, username="active_for_unban")
            actor = User(tg_user_id=99312, username="mod")
            session.add_all([target, actor])
            await session.flush()

            session.add(
                BlacklistEntry(
                    user_id=target.id,
                    reason="active entry",
                    created_by_user_id=actor.id,
                    is_active=True,
                    created_at=datetime.now(UTC),
                )
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/violators")
    response = await violators(request, status="active", page=0, q="")

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "/actions/user/unban" in body
    assert "Причина разбана" in body
    assert "target_tg_user_id" in body


@pytest.mark.asyncio
async def test_violators_page_rejects_invalid_status(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    request = _make_request("/violators")

    with pytest.raises(HTTPException) as exc:
        await violators(request, status="broken", page=0, q="")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_violators_page_rejects_invalid_date_filter(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    request = _make_request("/violators")

    with pytest.raises(HTTPException) as exc:
        await violators(request, status="active", page=0, q="", created_from="2026-99-99")

    assert exc.value.status_code == 400
