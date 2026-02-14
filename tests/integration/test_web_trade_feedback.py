from __future__ import annotations

from datetime import UTC, datetime
import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AuctionStatus, ModerationAction
from app.db.models import Auction, ModerationLog, TradeFeedback, User
from app.services.rbac_service import SCOPE_USER_BAN
from app.web.auth import AdminAuthContext
from app.web.main import action_hide_trade_feedback, trade_feedback


def _make_request(path: str, *, method: str = "GET") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
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
async def test_trade_feedback_page_filters_by_status(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    auction_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=99701, username="seller")
            winner = User(tg_user_id=99702, username="winner")
            moderator = User(tg_user_id=99703, username="moderator")
            session.add_all([seller, winner, moderator])
            await session.flush()

            session.add(
                Auction(
                    id=auction_id,
                    seller_user_id=seller.id,
                    winner_user_id=winner.id,
                    description="ended lot",
                    photo_file_id="photo",
                    start_price=100,
                    buyout_price=None,
                    min_step=5,
                    duration_hours=24,
                    status=AuctionStatus.ENDED,
                )
            )
            session.add_all(
                [
                    TradeFeedback(
                        auction_id=auction_id,
                        author_user_id=seller.id,
                        target_user_id=winner.id,
                        rating=5,
                        comment="visible feedback",
                        status="VISIBLE",
                    ),
                    TradeFeedback(
                        auction_id=auction_id,
                        author_user_id=winner.id,
                        target_user_id=seller.id,
                        rating=2,
                        comment="hidden feedback",
                        status="HIDDEN",
                        moderator_user_id=moderator.id,
                    ),
                ]
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/trade-feedback")
    response = await trade_feedback(request, status="visible", page=0, q="")

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "visible feedback" in body
    assert "hidden feedback" not in body


@pytest.mark.asyncio
async def test_trade_feedback_hide_action_updates_status(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    auction_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=99711, username="seller")
            winner = User(tg_user_id=99712, username="winner")
            moderator = User(tg_user_id=99713, username="moderator")
            session.add_all([seller, winner, moderator])
            await session.flush()
            moderator_user_id = moderator.id

            session.add(
                Auction(
                    id=auction_id,
                    seller_user_id=seller.id,
                    winner_user_id=winner.id,
                    description="ended lot",
                    photo_file_id="photo",
                    start_price=100,
                    buyout_price=None,
                    min_step=5,
                    duration_hours=24,
                    status=AuctionStatus.ENDED,
                )
            )
            feedback = TradeFeedback(
                auction_id=auction_id,
                author_user_id=seller.id,
                target_user_id=winner.id,
                rating=4,
                comment="needs review",
                status="VISIBLE",
            )
            session.add(feedback)
            await session.flush()
            feedback_id = feedback.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)

    async def _resolve_actor(_auth):
        return moderator_user_id

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _resolve_actor)

    request = _make_request("/actions/trade-feedback/hide", method="POST")
    response = await action_hide_trade_feedback(
        request,
        feedback_id=feedback_id,
        reason="spam",
        return_to="/trade-feedback?status=visible",
        csrf_token="ok",
    )

    assert response.status_code == 303

    async with session_factory() as session:
        row = await session.scalar(select(TradeFeedback).where(TradeFeedback.id == feedback_id))
        log_row = await session.scalar(
            select(ModerationLog)
            .where(ModerationLog.action == ModerationAction.HIDE_TRADE_FEEDBACK)
            .order_by(ModerationLog.id.desc())
            .limit(1)
        )

    assert row is not None
    assert row.status == "HIDDEN"
    assert row.moderation_note == "spam"
    assert row.moderator_user_id == moderator_user_id
    assert log_row is not None
    assert log_row.target_user_id == row.target_user_id
    assert log_row.payload is not None
    assert log_row.payload.get("feedback_id") == feedback_id
    assert log_row.payload.get("from_status") == "VISIBLE"
    assert log_row.payload.get("to_status") == "HIDDEN"


@pytest.mark.asyncio
async def test_trade_feedback_page_rejects_invalid_status(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/trade-feedback")
    with pytest.raises(HTTPException) as exc:
        await trade_feedback(request, status="broken", page=0, q="")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_trade_feedback_page_filters_by_rating_and_actor(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    auction_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=99721, username="seller3")
            winner_a = User(tg_user_id=99722, username="winner_a")
            winner_b = User(tg_user_id=99723, username="winner_b")
            session.add_all([seller, winner_a, winner_b])
            await session.flush()

            session.add(
                Auction(
                    id=auction_id,
                    seller_user_id=seller.id,
                    winner_user_id=winner_a.id,
                    description="ended lot",
                    photo_file_id="photo",
                    start_price=100,
                    buyout_price=None,
                    min_step=5,
                    duration_hours=24,
                    status=AuctionStatus.ENDED,
                )
            )
            session.add_all(
                [
                    TradeFeedback(
                        auction_id=auction_id,
                        author_user_id=winner_a.id,
                        target_user_id=seller.id,
                        rating=5,
                        comment="great",
                        status="VISIBLE",
                    ),
                    TradeFeedback(
                        auction_id=auction_id,
                        author_user_id=winner_b.id,
                        target_user_id=seller.id,
                        rating=2,
                        comment="bad",
                        status="VISIBLE",
                    ),
                ]
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/trade-feedback")
    response = await trade_feedback(
        request,
        status="all",
        page=0,
        q="",
        min_rating="4",
        author_tg="99722",
        target_tg="99721",
    )

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "great" in body
    assert "bad" not in body
    assert "Автор TG: 99722" in body
    assert "Получатель TG: 99721" in body


@pytest.mark.asyncio
async def test_trade_feedback_page_filters_by_moderator_and_moderated_state(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    auction_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=99731, username="seller_moderation")
            winner = User(tg_user_id=99732, username="winner_moderation")
            moderator = User(tg_user_id=99733, username="moderator_moderation")
            session.add_all([seller, winner, moderator])
            await session.flush()

            session.add(
                Auction(
                    id=auction_id,
                    seller_user_id=seller.id,
                    winner_user_id=winner.id,
                    description="ended lot",
                    photo_file_id="photo",
                    start_price=100,
                    buyout_price=None,
                    min_step=5,
                    duration_hours=24,
                    status=AuctionStatus.ENDED,
                )
            )
            session.add_all(
                [
                    TradeFeedback(
                        auction_id=auction_id,
                        author_user_id=seller.id,
                        target_user_id=winner.id,
                        rating=5,
                        comment="still visible",
                        status="VISIBLE",
                    ),
                    TradeFeedback(
                        auction_id=auction_id,
                        author_user_id=winner.id,
                        target_user_id=seller.id,
                        rating=2,
                        comment="moderated hidden",
                        status="HIDDEN",
                        moderator_user_id=moderator.id,
                        moderation_note="abusive",
                        moderated_at=datetime.now(UTC),
                    ),
                ]
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/trade-feedback")
    response = await trade_feedback(
        request,
        status="all",
        moderated="only",
        page=0,
        q="",
        moderator_tg="99733",
    )

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "moderated hidden" in body
    assert "still visible" not in body
    assert "abusive" in body
    assert "Модератор TG: 99733" in body


@pytest.mark.asyncio
async def test_trade_feedback_page_rejects_invalid_min_rating(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/trade-feedback")
    with pytest.raises(HTTPException) as exc:
        await trade_feedback(request, status="all", page=0, q="", min_rating="9")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_trade_feedback_page_rejects_invalid_moderated_filter(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/trade-feedback")
    with pytest.raises(HTTPException) as exc:
        await trade_feedback(request, status="all", moderated="broken", page=0, q="")

    assert exc.value.status_code == 400
