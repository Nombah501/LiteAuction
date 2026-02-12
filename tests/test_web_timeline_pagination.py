from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.db.enums import AuctionStatus
from app.services.timeline_service import AuctionTimelineItem
from app.web.auth import AdminAuthContext
from app.web.main import auction_timeline


class _DummySessionFactoryCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _DummySessionFactory:
    def __call__(self) -> _DummySessionFactoryCtx:
        return _DummySessionFactoryCtx()


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
        scopes=frozenset(),
        tg_user_id=None,
    )


def _timeline_items() -> list[AuctionTimelineItem]:
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    return [
        AuctionTimelineItem(
            happened_at=base + timedelta(minutes=idx),
            source="test",
            title=f"Event-{idx + 1}",
            details=f"details-{idx + 1}",
        )
        for idx in range(5)
    ]


@pytest.mark.asyncio
async def test_timeline_first_page_uses_limit_and_next_link(monkeypatch) -> None:
    request = _make_request("/timeline/auction/test")
    auction_id = uuid.uuid4()

    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory())

    async def fake_build(_session, _auction_id, *, page, limit, sources):
        assert page == 0
        assert limit == 2
        assert sources is None
        auction = SimpleNamespace(id=auction_id, status=AuctionStatus.ACTIVE, seller_user_id=123)
        items = _timeline_items()
        return auction, items[:2], len(items)

    monkeypatch.setattr("app.web.main.build_auction_timeline_page", fake_build)

    response = await auction_timeline(request, str(auction_id), page=0, limit=2)

    assert response.status_code == 200
    body = bytes(response.body).decode("utf-8")
    assert "Event-1" in body
    assert "Event-2" in body
    assert "Event-3" not in body
    assert "page=1&amp;limit=2" in body
    assert "← Назад" not in body
    assert "Показано:</b> 1-2 из 5" in body


@pytest.mark.asyncio
async def test_timeline_middle_page_keeps_order_and_both_links(monkeypatch) -> None:
    request = _make_request("/timeline/auction/test")
    auction_id = uuid.uuid4()

    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory())

    async def fake_build(_session, _auction_id, *, page, limit, sources):
        assert page == 1
        assert limit == 2
        assert sources is None
        auction = SimpleNamespace(id=auction_id, status=AuctionStatus.ACTIVE, seller_user_id=123)
        items = _timeline_items()
        return auction, items[2:4], len(items)

    monkeypatch.setattr("app.web.main.build_auction_timeline_page", fake_build)

    response = await auction_timeline(request, str(auction_id), page=1, limit=2)

    assert response.status_code == 200
    body = bytes(response.body).decode("utf-8")
    assert "Event-2" not in body
    assert "Event-3" in body
    assert "Event-4" in body
    assert "Event-5" not in body
    assert body.index("Event-3") < body.index("Event-4")
    assert "page=0&amp;limit=2" in body
    assert "page=2&amp;limit=2" in body
    assert "Показано:</b> 3-4 из 5" in body


@pytest.mark.asyncio
async def test_timeline_last_page_has_no_next_link(monkeypatch) -> None:
    request = _make_request("/timeline/auction/test")
    auction_id = uuid.uuid4()

    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory())

    async def fake_build(_session, _auction_id, *, page, limit, sources):
        assert page == 2
        assert limit == 2
        assert sources is None
        auction = SimpleNamespace(id=auction_id, status=AuctionStatus.ACTIVE, seller_user_id=123)
        items = _timeline_items()
        return auction, items[4:5], len(items)

    monkeypatch.setattr("app.web.main.build_auction_timeline_page", fake_build)

    response = await auction_timeline(request, str(auction_id), page=2, limit=2)

    assert response.status_code == 200
    body = bytes(response.body).decode("utf-8")
    assert "Event-5" in body
    assert "Event-4" not in body
    assert "page=1&amp;limit=2" in body
    assert "Вперед →" not in body
    assert "Показано:</b> 5-5 из 5" in body


@pytest.mark.asyncio
async def test_timeline_rejects_invalid_pagination_values(monkeypatch) -> None:
    request = _make_request("/timeline/auction/test")

    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory())

    with pytest.raises(HTTPException):
        await auction_timeline(request, str(uuid.uuid4()), page=-1, limit=100)

    with pytest.raises(HTTPException):
        await auction_timeline(request, str(uuid.uuid4()), page=0, limit=0)

    with pytest.raises(HTTPException):
        await auction_timeline(request, str(uuid.uuid4()), page=0, limit=501)


@pytest.mark.asyncio
async def test_timeline_source_filter_forwarded_and_preserved(monkeypatch) -> None:
    request = _make_request("/timeline/auction/test")
    auction_id = uuid.uuid4()
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory())

    async def fake_build(_session, _auction_id, *, page, limit, sources):
        captured["sources"] = sources
        auction = SimpleNamespace(id=auction_id, status=AuctionStatus.ACTIVE, seller_user_id=123)
        items = _timeline_items()
        return auction, items[:1], 120

    monkeypatch.setattr("app.web.main.build_auction_timeline_page", fake_build)

    response = await auction_timeline(
        request,
        str(auction_id),
        page=0,
        limit=50,
        source="moderation,complaint",
    )

    assert response.status_code == 200
    assert captured["sources"] == ["moderation", "complaint"]
    body = bytes(response.body).decode("utf-8")
    assert "Фильтр source:</b> moderation,complaint" in body
    assert "source=moderation%2Ccomplaint" in body


@pytest.mark.asyncio
async def test_timeline_invalid_source_filter_returns_400(monkeypatch) -> None:
    request = _make_request("/timeline/auction/test")

    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory())

    async def fake_build(_session, _auction_id, *, page, limit, sources):
        raise ValueError("Unknown timeline source filter: bad")

    monkeypatch.setattr("app.web.main.build_auction_timeline_page", fake_build)

    with pytest.raises(HTTPException) as exc:
        await auction_timeline(request, str(uuid.uuid4()), page=0, limit=50, source="bad")

    assert exc.value.status_code == 400
    assert "Unknown timeline source filter" in str(exc.value.detail)
