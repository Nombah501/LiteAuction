from __future__ import annotations

import pytest
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AuctionStatus
from app.db.models import Auction, Complaint, FraudSignal, User
from app.services.rbac_service import SCOPE_USER_BAN
from app.web.auth import AdminAuthContext
from app.web.main import signals


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
async def test_signals_page_shows_user_risk_column(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            risky = User(tg_user_id=99511, username="signals_risky")
            safe = User(tg_user_id=99512, username="signals_safe")
            reporter = User(tg_user_id=99513, username="signals_reporter")
            seller = User(tg_user_id=99514, username="signals_seller")
            session.add_all([risky, safe, reporter, seller])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="signals lot",
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

            session.add_all(
                [
                    FraudSignal(
                        auction_id=auction.id,
                        user_id=risky.id,
                        bid_id=None,
                        score=91,
                        reasons={"rules": [{"code": "TEST", "detail": "risk", "score": 91}]},
                        status="OPEN",
                    ),
                    FraudSignal(
                        auction_id=auction.id,
                        user_id=safe.id,
                        bid_id=None,
                        score=28,
                        reasons={"rules": [{"code": "TEST", "detail": "low", "score": 28}]},
                        status="OPEN",
                    ),
                ]
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))

    request = _make_request("/signals")
    response = await signals(request, status="OPEN", page=0)

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "User Risk" in body
    assert "HIGH (" in body
    assert "MEDIUM (40)" in body
