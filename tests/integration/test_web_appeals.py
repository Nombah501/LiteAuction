from __future__ import annotations

from fastapi import HTTPException
import pytest
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AppealSourceType, AppealStatus, AuctionStatus, ModerationAction
from app.db.models import Appeal, Auction, FraudSignal, ModerationLog, User
from app.services.rbac_service import SCOPE_USER_BAN
from app.web.auth import AdminAuthContext
from app.web.main import action_reject_appeal, action_resolve_appeal, appeals


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
async def test_appeals_page_filters_status_and_source(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=99201, username="appeal_user")
            session.add(user)
            await session.flush()

            session.add_all(
                [
                    Appeal(
                        appeal_ref="risk_701",
                        source_type=AppealSourceType.RISK,
                        source_id=701,
                        appellant_user_id=user.id,
                        status=AppealStatus.OPEN,
                    ),
                    Appeal(
                        appeal_ref="complaint_702",
                        source_type=AppealSourceType.COMPLAINT,
                        source_id=702,
                        appellant_user_id=user.id,
                        status=AppealStatus.OPEN,
                    ),
                    Appeal(
                        appeal_ref="manual_703",
                        source_type=AppealSourceType.MANUAL,
                        source_id=None,
                        appellant_user_id=user.id,
                        status=AppealStatus.RESOLVED,
                    ),
                ]
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/appeals")
    response = await appeals(request, status="open", source="risk", page=0, q="")

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "risk_701" in body
    assert "complaint_702" not in body
    assert "manual_703" not in body


@pytest.mark.asyncio
async def test_appeals_page_rejects_invalid_status(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    request = _make_request("/appeals")

    with pytest.raises(HTTPException) as exc:
        await appeals(request, status="broken", source="all", page=0, q="")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_action_resolve_appeal_updates_status(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=99300, username="seller")
            appellant = User(tg_user_id=99301, username="appellant")
            actor = User(tg_user_id=99302, username="actor")
            session.add_all([seller, appellant, actor])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="web appeal lot",
                photo_file_id="photo",
                start_price=25,
                buyout_price=None,
                min_step=1,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
            )
            session.add(auction)
            await session.flush()

            signal = FraudSignal(
                auction_id=auction.id,
                user_id=appellant.id,
                bid_id=None,
                score=81,
                reasons={"rules": [{"code": "WEB_APPEAL", "detail": "risk", "score": 81}]},
                status="OPEN",
            )
            session.add(signal)
            await session.flush()

            appeal = Appeal(
                appeal_ref=f"risk_{signal.id}",
                source_type=AppealSourceType.RISK,
                source_id=signal.id,
                appellant_user_id=appellant.id,
                status=AppealStatus.OPEN,
            )
            session.add(appeal)
            await session.flush()
            appeal_id = appeal.id
            actor_user_id = actor.id
            appellant_user_id = appellant.id
            auction_id = auction.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)

    async def _resolve_actor(_auth):
        return actor_user_id

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _resolve_actor)

    request = _make_request("/actions/appeal/resolve", method="POST")
    response = await action_resolve_appeal(
        request,
        appeal_id=appeal_id,
        reason="checked",
        return_to="/appeals?status=open&source=all",
        csrf_token="ok",
        confirmed="1",
    )

    assert response.status_code == 303

    async with session_factory() as session:
        appeal_row = await session.scalar(select(Appeal).where(Appeal.id == appeal_id))
        audit_logs = (
            await session.execute(
                select(ModerationLog).where(
                    ModerationLog.action == ModerationAction.RESOLVE_APPEAL,
                    ModerationLog.target_user_id == appellant_user_id,
                    ModerationLog.auction_id == auction_id,
                )
            )
        ).scalars().all()

    assert appeal_row is not None
    assert appeal_row.status == AppealStatus.RESOLVED
    assert appeal_row.resolution_note == "[web] checked"
    assert appeal_row.resolver_user_id == actor_user_id
    assert len(audit_logs) == 1
    assert audit_logs[0].payload is not None
    assert audit_logs[0].payload.get("appeal_id") == appeal_id


@pytest.mark.asyncio
async def test_action_reject_appeal_renders_confirmation(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)

    request = _make_request("/actions/appeal/reject", method="POST")
    response = await action_reject_appeal(
        request,
        appeal_id=15,
        reason="insufficient basis",
        return_to="/appeals?status=open&source=all",
        csrf_token="ok",
        confirmed=None,
    )

    assert response.status_code == 200
    body = bytes(response.body).decode("utf-8")
    assert "Подтверждение отклонения апелляции" in body
    assert "name='confirmed' value='1'" in body
