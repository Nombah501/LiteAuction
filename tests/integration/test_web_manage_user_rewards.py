from __future__ import annotations

from datetime import UTC, datetime

import pytest
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import PointsEventType
from app.db.models import PointsLedgerEntry, User
from app.services.rbac_service import (
    SCOPE_AUCTION_MANAGE,
    SCOPE_BID_MANAGE,
    SCOPE_ROLE_MANAGE,
    SCOPE_USER_BAN,
)
from app.web.auth import AdminAuthContext
from app.web.main import manage_user


def _make_request(path: str, query: str = "") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query.encode("utf-8"),
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
        scopes=frozenset({SCOPE_AUCTION_MANAGE, SCOPE_BID_MANAGE, SCOPE_USER_BAN, SCOPE_ROLE_MANAGE}),
        tg_user_id=None,
    )


@pytest.mark.asyncio
async def test_manage_user_shows_points_widget(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=93801, username="rewards_user")
            session.add(user)
            await session.flush()

            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=30,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="feedback:web:1",
                    reason="Награда",
                    payload=None,
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=-5,
                    event_type=PointsEventType.MANUAL_ADJUSTMENT,
                    dedupe_key="manual:web:1",
                    reason="Корректировка",
                    payload=None,
                    created_at=datetime.now(UTC),
                )
            )
            user_id = user.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._csrf_hidden_input", lambda *_args, **_kwargs: "")

    request = _make_request(f"/manage/user/{user_id}")
    response = await manage_user(request, user_id=user_id)

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "Rewards / points" in body
    assert "Points баланс" in body
    assert "Начислено всего:</b> +30" in body
    assert "Списано всего:</b> -5" in body
    assert "Награда за фидбек" in body
    assert "Ручная корректировка" in body


@pytest.mark.asyncio
async def test_manage_user_points_filter_and_paging(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=93811, username="rewards_filter")
            session.add(user)
            await session.flush()

            for idx in range(11):
                session.add(
                    PointsLedgerEntry(
                        user_id=user.id,
                        amount=1,
                        event_type=PointsEventType.MANUAL_ADJUSTMENT,
                        dedupe_key=f"manual:web:{idx}",
                        reason=f"manual-{idx}",
                        payload=None,
                    )
                )
            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=20,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="feedback:web:2",
                    reason="feedback",
                    payload=None,
                )
            )
            user_id = user.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._csrf_hidden_input", lambda *_args, **_kwargs: "")

    request = _make_request(f"/manage/user/{user_id}", query="points_page=2&points_filter=manual")
    response = await manage_user(request, user_id=user_id, points_page=2, points_filter="manual")

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "Фильтр:</b> manual" in body
    assert "Страница:</b> 2/2" in body
    assert "Записей:</b>" in body
    assert "manual-0" in body
    assert "manual-10" not in body
    assert "points_page=1&amp;points_filter=manual" in body
