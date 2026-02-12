from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.db.enums import AuctionStatus
from app.services.rbac_service import SCOPE_AUCTION_MANAGE, SCOPE_BID_MANAGE
from app.web.auth import AdminAuthContext
from app.web.main import manage_auction


class _DummySession:
    def __init__(self, auction) -> None:
        self._auction = auction

    async def scalar(self, _query):
        return self._auction


class _DummySessionFactoryCtx:
    def __init__(self, auction) -> None:
        self._auction = auction

    async def __aenter__(self):
        return _DummySession(self._auction)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _DummySessionFactory:
    def __init__(self, auction) -> None:
        self._auction = auction

    def __call__(self) -> _DummySessionFactoryCtx:
        return _DummySessionFactoryCtx(self._auction)


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
        scopes=frozenset({SCOPE_AUCTION_MANAGE, SCOPE_BID_MANAGE}),
        tg_user_id=None,
    )


@pytest.mark.asyncio
async def test_manage_auction_preserves_timeline_context_in_link(monkeypatch) -> None:
    auction_id = uuid.uuid4()
    request = _make_request(
        f"/manage/auction/{auction_id}",
        query="timeline_page=2&timeline_limit=25&timeline_source=moderation,complaint",
    )

    auction = SimpleNamespace(id=auction_id, status=AuctionStatus.ACTIVE, seller_user_id=7)

    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory(auction))

    async def fake_recent_bids(*_args, **_kwargs):
        return []

    monkeypatch.setattr("app.web.main.list_recent_bids", fake_recent_bids)
    monkeypatch.setattr("app.web.main._csrf_hidden_input", lambda *_args, **_kwargs: "")

    response = await manage_auction(request, str(auction_id))

    assert response.status_code == 200
    body = bytes(response.body).decode("utf-8")
    assert (
        f"/timeline/auction/{auction_id}?page=2&amp;limit=25&amp;source=moderation%2Ccomplaint"
        in body
    )
