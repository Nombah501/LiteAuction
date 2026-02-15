from __future__ import annotations

import pytest
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AuctionStatus
from app.db.models import Auction, Complaint, FraudSignal, TelegramUserVerification, User
from app.services.rbac_service import SCOPE_AUCTION_MANAGE, SCOPE_BID_MANAGE, SCOPE_USER_BAN
from app.web.auth import AdminAuthContext
from app.web.main import auctions, manage_users


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
        scopes=frozenset({SCOPE_AUCTION_MANAGE, SCOPE_BID_MANAGE, SCOPE_USER_BAN}),
        tg_user_id=None,
    )


@pytest.mark.asyncio
async def test_manage_users_shows_risk_column(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            risky = User(tg_user_id=99401, username="risky_seller")
            safe = User(tg_user_id=99402, username="safe_seller")
            reporter = User(tg_user_id=99403, username="reporter")
            session.add_all([risky, safe, reporter])
            await session.flush()

            auction = Auction(
                seller_user_id=risky.id,
                description="risk auction",
                photo_file_id="photo",
                start_price=100,
                buyout_price=None,
                min_step=5,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
            )
            session.add(auction)
            await session.flush()

            session.add_all(
                [
                    Complaint(
                        auction_id=auction.id,
                        reporter_user_id=reporter.id,
                        target_user_id=risky.id,
                        reason=f"complaint-{idx}",
                        status="OPEN",
                    )
                    for idx in range(3)
                ]
            )
            session.add(
                FraudSignal(
                    auction_id=auction.id,
                    user_id=risky.id,
                    bid_id=None,
                    score=83,
                    reasons={"rules": [{"code": "TEST", "detail": "risk", "score": 83}]},
                    status="OPEN",
                )
            )
            session.add(
                TelegramUserVerification(
                    tg_user_id=safe.tg_user_id,
                    is_verified=True,
                )
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))

    request = _make_request("/manage/users")
    response = await manage_users(request, page=0, q="")

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "<th>Verified</th>" in body
    assert "<th>Risk</th>" in body
    assert "HIGH (" in body
    assert "LOW (0)" in body
    assert "yes</td>" in body


@pytest.mark.asyncio
async def test_auctions_shows_seller_risk_column(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            risky = User(tg_user_id=99411, username="risky_seller_2")
            safe = User(tg_user_id=99412, username="safe_seller_2")
            reporter = User(tg_user_id=99413, username="reporter_2")
            session.add_all([risky, safe, reporter])
            await session.flush()

            risky_auction = Auction(
                seller_user_id=risky.id,
                description="risky lot",
                photo_file_id="photo",
                start_price=200,
                buyout_price=None,
                min_step=10,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
            )
            safe_auction = Auction(
                seller_user_id=safe.id,
                description="safe lot",
                photo_file_id="photo",
                start_price=120,
                buyout_price=None,
                min_step=5,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
            )
            session.add_all([risky_auction, safe_auction])
            await session.flush()

            session.add_all(
                [
                    Complaint(
                        auction_id=risky_auction.id,
                        reporter_user_id=reporter.id,
                        target_user_id=risky.id,
                        reason=f"complaint-{idx}",
                        status="OPEN",
                    )
                    for idx in range(3)
                ]
            )
            session.add(
                FraudSignal(
                    auction_id=risky_auction.id,
                    user_id=risky.id,
                    bid_id=None,
                    score=90,
                    reasons={"rules": [{"code": "TEST", "detail": "risk", "score": 90}]},
                    status="OPEN",
                )
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))

    request = _make_request("/auctions")
    response = await auctions(request, status="ACTIVE", page=0)

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "Seller Risk" in body
    assert "HIGH (" in body
    assert "LOW (0)" in body
