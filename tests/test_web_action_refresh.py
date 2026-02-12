from __future__ import annotations

import uuid

import pytest
from starlette.requests import Request

from app.services.moderation_service import ModerationResult
from app.web.auth import AdminAuthContext
from app.web.main import action_freeze_auction, action_remove_bid
from app.services.rbac_service import SCOPE_AUCTION_MANAGE, SCOPE_BID_MANAGE


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


def _stub_auth(scope: str) -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="token",
        role="owner",
        can_manage=True,
        scopes=frozenset({scope}),
        tg_user_id=None,
    )


@pytest.mark.asyncio
async def test_freeze_action_triggers_refresh_on_success(monkeypatch) -> None:
    request = _make_request("/actions/auction/freeze")
    auction_id = uuid.uuid4()
    refreshed: list[uuid.UUID | None] = []

    monkeypatch.setattr(
        "app.web.main._require_scope_permission",
        lambda _request, _scope: (None, _stub_auth(SCOPE_AUCTION_MANAGE)),
    )
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory())

    async def fake_actor(_auth):
        return 1

    async def fake_freeze(_session, **kwargs):
        return ModerationResult(ok=True, message="ok", auction_id=kwargs["auction_id"])

    async def fake_refresh(auction_uuid):
        refreshed.append(auction_uuid)

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", fake_actor)
    monkeypatch.setattr("app.web.main.freeze_auction", fake_freeze)
    monkeypatch.setattr("app.web.main._refresh_auction_posts_from_web", fake_refresh)

    response = await action_freeze_auction(
        request,
        auction_id=str(auction_id),
        reason="freeze for test",
        return_to=f"/manage/auction/{auction_id}",
        csrf_token="ok",
    )

    assert response.status_code == 303
    assert refreshed == [auction_id]


@pytest.mark.asyncio
async def test_freeze_action_skips_refresh_on_failure(monkeypatch) -> None:
    request = _make_request("/actions/auction/freeze")
    auction_id = uuid.uuid4()
    refreshed: list[uuid.UUID | None] = []

    monkeypatch.setattr(
        "app.web.main._require_scope_permission",
        lambda _request, _scope: (None, _stub_auth(SCOPE_AUCTION_MANAGE)),
    )
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory())

    async def fake_actor(_auth):
        return 1

    async def fake_freeze(_session, **kwargs):
        return ModerationResult(ok=False, message="already frozen", auction_id=kwargs["auction_id"])

    async def fake_refresh(auction_uuid):
        refreshed.append(auction_uuid)

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", fake_actor)
    monkeypatch.setattr("app.web.main.freeze_auction", fake_freeze)
    monkeypatch.setattr("app.web.main._refresh_auction_posts_from_web", fake_refresh)

    response = await action_freeze_auction(
        request,
        auction_id=str(auction_id),
        reason="freeze for test",
        return_to=f"/manage/auction/{auction_id}",
        csrf_token="ok",
    )

    assert response.status_code == 400
    assert refreshed == []


@pytest.mark.asyncio
async def test_remove_bid_refresh_uses_result_auction_id(monkeypatch) -> None:
    request = _make_request("/actions/bid/remove")
    bid_id = uuid.uuid4()
    auction_id = uuid.uuid4()
    refreshed: list[uuid.UUID | None] = []

    monkeypatch.setattr(
        "app.web.main._require_scope_permission",
        lambda _request, _scope: (None, _stub_auth(SCOPE_BID_MANAGE)),
    )
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.web.main.SessionFactory", _DummySessionFactory())

    async def fake_actor(_auth):
        return 1

    async def fake_remove_bid(_session, **_kwargs):
        return ModerationResult(ok=True, message="removed", auction_id=auction_id)

    async def fake_refresh(auction_uuid):
        refreshed.append(auction_uuid)

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", fake_actor)
    monkeypatch.setattr("app.web.main.remove_bid", fake_remove_bid)
    monkeypatch.setattr("app.web.main._refresh_auction_posts_from_web", fake_refresh)

    response = await action_remove_bid(
        request,
        bid_id=str(bid_id),
        reason="remove bid",
        return_to="/manage/auction/test",
        csrf_token="ok",
        confirmed="1",
    )

    assert response.status_code == 303
    assert refreshed == [auction_id]
