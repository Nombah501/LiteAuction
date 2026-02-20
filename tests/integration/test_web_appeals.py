from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
import pytest
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AppealSourceType, AppealStatus, AuctionStatus, ModerationAction
from app.db.models import Appeal, Auction, Complaint, FraudSignal, ModerationLog, User
from app.services.rbac_service import SCOPE_USER_BAN
from app.web.auth import AdminAuthContext
from app.web.main import (
    action_reject_appeal,
    action_resolve_appeal,
    action_review_appeal,
    action_triage_detail_section,
    appeals,
)


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
    assert "Риск апеллянта" in body
    assert "risk_701" in body
    assert "complaint_702" not in body
    assert "manual_703" not in body


@pytest.mark.asyncio
async def test_appeals_page_shows_appellant_risk_indicator(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            risky = User(tg_user_id=99501, username="appeal_risky")
            safe = User(tg_user_id=99502, username="appeal_safe")
            reporter = User(tg_user_id=99503, username="appeal_reporter")
            session.add_all([risky, safe, reporter])
            await session.flush()

            auction = Auction(
                seller_user_id=safe.id,
                description="appeal risk lot",
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
                    score=86,
                    reasons={"rules": [{"code": "TEST", "detail": "risk", "score": 86}]},
                    status="OPEN",
                )
            )

            session.add_all(
                [
                    Appeal(
                        appeal_ref="manual_risky_appellant",
                        source_type=AppealSourceType.MANUAL,
                        source_id=None,
                        appellant_user_id=risky.id,
                        status=AppealStatus.OPEN,
                    ),
                    Appeal(
                        appeal_ref="manual_safe_appellant",
                        source_type=AppealSourceType.MANUAL,
                        source_id=None,
                        appellant_user_id=safe.id,
                        status=AppealStatus.OPEN,
                    ),
                ]
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/appeals")
    response = await appeals(request, status="open", source="all", overdue="all", escalated="all", page=0, q="manual_")

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "manual_risky_appellant" in body
    assert "manual_safe_appellant" in body
    assert "HIGH (" in body
    assert "LOW (0)" in body


@pytest.mark.asyncio
async def test_appeals_page_rejects_invalid_status(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    request = _make_request("/appeals")

    with pytest.raises(HTTPException) as exc:
        await appeals(request, status="broken", source="all", page=0, q="")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_appeals_page_rejects_invalid_overdue_filter(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    request = _make_request("/appeals")

    with pytest.raises(HTTPException) as exc:
        await appeals(request, status="open", source="all", overdue="broken", page=0, q="")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_appeals_page_rejects_invalid_escalated_filter(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    request = _make_request("/appeals")

    with pytest.raises(HTTPException) as exc:
        await appeals(request, status="open", source="all", overdue="all", escalated="broken", page=0, q="")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_appeals_page_requires_user_ban_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.web.main._require_scope_permission",
        lambda _req, _scope: (HTMLResponse("forbidden", status_code=403), _stub_auth()),
    )
    request = _make_request("/appeals")

    response = await appeals(request, status="open", source="all", page=0, q="")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_action_resolve_appeal_requires_user_ban_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.web.main._require_scope_permission",
        lambda _req, _scope: (HTMLResponse("forbidden", status_code=403), _stub_auth()),
    )

    request = _make_request("/actions/appeal/resolve", method="POST")
    response = await action_resolve_appeal(
        request,
        appeal_id=42,
        reason="checked",
        return_to="/appeals?status=open&source=all",
        csrf_token="ok",
        confirmed="1",
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_action_review_appeal_requires_user_ban_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.web.main._require_scope_permission",
        lambda _req, _scope: (HTMLResponse("forbidden", status_code=403), _stub_auth()),
    )

    request = _make_request("/actions/appeal/review", method="POST")
    response = await action_review_appeal(
        request,
        appeal_id=42,
        reason="picked",
        return_to="/appeals?status=open&source=all",
        csrf_token="ok",
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_action_resolve_appeal_rejects_invalid_csrf(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: False)

    response = await action_resolve_appeal(
        _make_request("/actions/appeal/resolve", method="POST"),
        appeal_id=7,
        reason="checked",
        return_to="/appeals?status=open&source=all",
        csrf_token="bad",
        confirmed="1",
    )

    assert response.status_code == 403
    assert "CSRF check failed" in bytes(response.body).decode("utf-8")


@pytest.mark.asyncio
async def test_action_review_appeal_rejects_invalid_csrf(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: False)

    response = await action_review_appeal(
        _make_request("/actions/appeal/review", method="POST"),
        appeal_id=7,
        reason="checked",
        return_to="/appeals?status=open&source=all",
        csrf_token="bad",
    )

    assert response.status_code == 403
    assert "CSRF check failed" in bytes(response.body).decode("utf-8")


@pytest.mark.asyncio
async def test_action_reject_appeal_rejects_invalid_csrf(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: False)

    response = await action_reject_appeal(
        _make_request("/actions/appeal/reject", method="POST"),
        appeal_id=7,
        reason="checked",
        return_to="/appeals?status=open&source=all",
        csrf_token="bad",
        confirmed="1",
    )

    assert response.status_code == 403
    assert "CSRF check failed" in bytes(response.body).decode("utf-8")


@pytest.mark.asyncio
async def test_action_review_appeal_updates_status(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            appellant = User(tg_user_id=99271, username="review_appellant")
            actor = User(tg_user_id=99272, username="review_actor")
            session.add_all([appellant, actor])
            await session.flush()

            appeal = Appeal(
                appeal_ref="manual_review_901",
                source_type=AppealSourceType.MANUAL,
                source_id=None,
                appellant_user_id=appellant.id,
                status=AppealStatus.OPEN,
            )
            session.add(appeal)
            await session.flush()
            appeal_id = appeal.id
            actor_user_id = actor.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)

    async def _resolve_actor(_auth):
        return actor_user_id

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _resolve_actor)

    request = _make_request("/actions/appeal/review", method="POST")
    response = await action_review_appeal(
        request,
        appeal_id=appeal_id,
        reason="picked",
        return_to="/appeals?status=open&source=all",
        csrf_token="ok",
    )

    assert response.status_code == 303

    async with session_factory() as session:
        appeal_row = await session.scalar(select(Appeal).where(Appeal.id == appeal_id))

    assert appeal_row is not None
    assert appeal_row.status == AppealStatus.IN_REVIEW
    assert appeal_row.resolution_note == "[web-review] picked"
    assert appeal_row.resolver_user_id == actor_user_id
    assert appeal_row.resolved_at is None
    assert appeal_row.in_review_started_at is not None
    assert appeal_row.sla_deadline_at is not None


@pytest.mark.asyncio
async def test_appeals_page_overdue_filter_and_pagination_context(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=99281, username="overdue_filter_user")
            session.add(user)
            await session.flush()

            for idx in range(31):
                session.add(
                    Appeal(
                        appeal_ref=f"manual_due_{idx}",
                        source_type=AppealSourceType.MANUAL,
                        source_id=None,
                        appellant_user_id=user.id,
                        status=AppealStatus.OPEN,
                        sla_deadline_at=now - timedelta(minutes=idx + 1),
                    )
                )

            session.add(
                Appeal(
                    appeal_ref="manual_not_due",
                    source_type=AppealSourceType.MANUAL,
                    source_id=None,
                    appellant_user_id=user.id,
                    status=AppealStatus.OPEN,
                    sla_deadline_at=now + timedelta(hours=1),
                )
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/appeals")
    response_page_0 = await appeals(request, status="open", source="all", overdue="only", page=0, q="manual_due")

    body_page_0 = bytes(response_page_0.body).decode("utf-8")
    assert response_page_0.status_code == 200
    assert "manual_not_due" not in body_page_0
    assert (
        "/appeals?status=open&amp;source=all&amp;overdue=only&amp;escalated=all"
        "&amp;sla_health=all&amp;aging=all&amp;page=1&amp;q=manual_due"
    ) in body_page_0

    response_page_1 = await appeals(request, status="open", source="all", overdue="only", page=1, q="manual_due")
    body_page_1 = bytes(response_page_1.body).decode("utf-8")
    assert response_page_1.status_code == 200
    assert (
        "/appeals?status=open&amp;source=all&amp;overdue=only&amp;escalated=all"
        "&amp;sla_health=all&amp;aging=all&amp;page=0&amp;q=manual_due"
    ) in body_page_1


@pytest.mark.asyncio
async def test_appeals_page_sla_health_and_aging_filters_render_metadata(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=99295, username="sla_filter_user")
            session.add(user)
            await session.flush()

            session.add_all(
                [
                    Appeal(
                        appeal_ref="manual_warning_bucket",
                        source_type=AppealSourceType.MANUAL,
                        source_id=None,
                        appellant_user_id=user.id,
                        status=AppealStatus.OPEN,
                        created_at=now - timedelta(hours=8),
                        sla_deadline_at=now + timedelta(hours=4),
                    ),
                    Appeal(
                        appeal_ref="manual_healthy_bucket",
                        source_type=AppealSourceType.MANUAL,
                        source_id=None,
                        appellant_user_id=user.id,
                        status=AppealStatus.OPEN,
                        created_at=now - timedelta(hours=2),
                        sla_deadline_at=now + timedelta(hours=9),
                    ),
                ]
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/appeals")
    response = await appeals(
        request,
        status="open",
        source="all",
        overdue="all",
        escalated="all",
        sla_health="warning",
        aging="aging",
        page=0,
        q="manual_",
    )

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "manual_warning_bucket" in body
    assert "manual_healthy_bucket" not in body
    assert "data-sla-health='warning'" in body
    assert "data-aging-bucket='aging'" in body
    assert "SLA health:" in body
    assert "Возраст:" in body


@pytest.mark.asyncio
async def test_appeals_page_escalated_filter_and_sla_markers(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=99291, username="escalated_filter_user")
            session.add(user)
            await session.flush()

            session.add_all(
                [
                    Appeal(
                        appeal_ref="manual_escalated_overdue",
                        source_type=AppealSourceType.MANUAL,
                        source_id=None,
                        appellant_user_id=user.id,
                        status=AppealStatus.OPEN,
                        sla_deadline_at=now - timedelta(hours=1),
                        escalated_at=now - timedelta(minutes=30),
                        escalation_level=1,
                    ),
                    Appeal(
                        appeal_ref="manual_not_escalated_due_soon",
                        source_type=AppealSourceType.MANUAL,
                        source_id=None,
                        appellant_user_id=user.id,
                        status=AppealStatus.OPEN,
                        sla_deadline_at=now + timedelta(minutes=40),
                        escalated_at=None,
                        escalation_level=0,
                    ),
                ]
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))

    request = _make_request("/appeals")
    response_escalated = await appeals(
        request,
        status="open",
        source="all",
        overdue="all",
        escalated="only",
        page=0,
        q="manual_",
    )
    body_escalated = bytes(response_escalated.body).decode("utf-8")

    assert response_escalated.status_code == 200
    assert "manual_escalated_overdue" in body_escalated
    assert "manual_not_escalated_due_soon" not in body_escalated
    assert "Просрочена, эскалация L1" in body_escalated
    assert "L1 (" in body_escalated

    response_not_escalated = await appeals(
        request,
        status="open",
        source="all",
        overdue="all",
        escalated="none",
        page=0,
        q="manual_",
    )
    body_not_escalated = bytes(response_not_escalated.body).decode("utf-8")

    assert response_not_escalated.status_code == 200
    assert "manual_not_escalated_due_soon" in body_not_escalated
    assert "manual_escalated_overdue" not in body_not_escalated
    assert "До SLA:" in body_not_escalated


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
    artifact = audit_logs[0].payload.get("rationale_artifact")
    assert isinstance(artifact, dict)
    assert artifact.get("summary") == "checked"
    assert artifact.get("actor_user_id") == actor_user_id
    assert artifact.get("immutable") is True
    assert isinstance(artifact.get("recorded_at"), str)


@pytest.mark.asyncio
async def test_triage_detail_section_renders_appeal_evidence_and_audit(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=99310, username="timeline_seller")
            appellant = User(tg_user_id=99311, username="timeline_appellant")
            actor = User(tg_user_id=99312, username="timeline_actor")
            session.add_all([seller, appellant, actor])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="timeline lot",
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
                score=73,
                reasons={"rules": [{"code": "TIMELINE", "detail": "risk", "score": 73}]},
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

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)

    async def _resolve_actor(_auth):
        return actor_user_id

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _resolve_actor)

    request = _make_request("/actions/appeal/resolve", method="POST")
    resolve_response = await action_resolve_appeal(
        request,
        appeal_id=appeal_id,
        reason="timeline-check",
        return_to="/appeals?status=open&source=all",
        csrf_token="ok",
        confirmed="1",
    )
    assert resolve_response.status_code == 303

    payload_primary = await action_triage_detail_section(
        _make_request("/actions/triage/detail-section"),
        queue_key="appeals",
        row_id=appeal_id,
        section="primary",
        risk_level="high",
        priority_level="high",
    )
    assert payload_primary["ok"] is True
    assert "Evidence timeline:" in str(payload_primary["html"])
    assert "Appeal created" in str(payload_primary["html"])

    payload_secondary = await action_triage_detail_section(
        _make_request("/actions/triage/detail-section"),
        queue_key="appeals",
        row_id=appeal_id,
        section="secondary",
        risk_level="high",
        priority_level="high",
        depth_override="inline_full",
    )
    assert payload_secondary["ok"] is True
    assert "Rationale artifacts:" in str(payload_secondary["html"])
    assert "web.appeals.resolve" in str(payload_secondary["html"])

    payload_audit = await action_triage_detail_section(
        _make_request("/actions/triage/detail-section"),
        queue_key="appeals",
        row_id=appeal_id,
        section="audit",
        risk_level="high",
        priority_level="high",
        depth_override="inline_full",
    )
    assert payload_audit["ok"] is True
    assert "Audit trail (immutable)" in str(payload_audit["html"])
    assert "record_policy=append_only" in str(payload_audit["html"])


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
