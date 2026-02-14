from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.responses import HTMLResponse
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AuctionStatus, FeedbackStatus, FeedbackType, ModerationAction, PointsEventType
from app.db.models import Auction, FeedbackItem, ModerationLog, PointsLedgerEntry, TradeFeedback, User
from app.services.rbac_service import (
    SCOPE_AUCTION_MANAGE,
    SCOPE_BID_MANAGE,
    SCOPE_ROLE_MANAGE,
    SCOPE_USER_BAN,
)
from app.web.auth import AdminAuthContext
from app.web.main import action_adjust_user_points, dashboard, manage_user


def _make_request(path: str, query: str = "", *, method: str = "GET") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
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
    from app.config import settings

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
    monkeypatch.setattr(settings, "feedback_priority_boost_enabled", False)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 21)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 3)
    monkeypatch.setattr(settings, "guarantor_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 34)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 2)
    monkeypatch.setattr(settings, "appeal_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "appeal_priority_boost_cost_points", 13)
    monkeypatch.setattr(settings, "appeal_priority_boost_daily_limit", 4)
    monkeypatch.setattr(settings, "points_redemption_cooldown_seconds", 75)

    request = _make_request(f"/manage/user/{user_id}")
    response = await manage_user(request, user_id=user_id)

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "Rewards / points" in body
    assert "Points баланс" in body
    assert "Риск-уровень:</b> LOW" in body
    assert "Риск-скор:</b> 0" in body
    assert "Риск-факторы:</b> -" in body
    assert "Начислено всего:</b> +30" in body
    assert "Списано всего:</b> -5" in body
    assert "Бустов фидбека:</b> 0" in body
    assert "Списано на бусты:</b> -0" in body
    assert "Политика фидбек-буста:</b> off | cost 21 | limit 3/day" in body
    assert "Политика буста гаранта:</b> on | cost 34 | limit 2/day" in body
    assert "Политика буста апелляций:</b> on | cost 13 | limit 4/day" in body
    assert "Глобальный кулдаун редимпшена:</b> 75 сек" in body
    assert "Награда за фидбек" in body
    assert "Ручная корректировка" in body


@pytest.mark.asyncio
async def test_manage_user_shows_feedback_boost_totals(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=93805, username="boosted_user")
            session.add(user)
            await session.flush()

            session.add(
                FeedbackItem(
                    type=FeedbackType.SUGGESTION,
                    status=FeedbackStatus.IN_REVIEW,
                    submitter_user_id=user.id,
                    content="boosted once",
                    reward_points=0,
                    priority_boost_points_spent=25,
                    priority_boosted_at=datetime.now(UTC),
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
    assert "Бустов фидбека:</b> 1" in body
    assert "Бустов гаранта:</b> 0" in body
    assert "Бустов апелляций:</b> 0" in body
    assert "Списано на бусты:</b> -25" in body


@pytest.mark.asyncio
async def test_dashboard_shows_points_utility_metrics(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            recent_user = User(tg_user_id=93806, username="points_recent")
            old_user = User(tg_user_id=93807, username="points_old")
            session.add_all([recent_user, old_user])
            await session.flush()

            session.add(
                PointsLedgerEntry(
                    user_id=recent_user.id,
                    amount=30,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="dashboard:points:earned",
                    reason="seed",
                    payload=None,
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                PointsLedgerEntry(
                    user_id=recent_user.id,
                    amount=-10,
                    event_type=PointsEventType.FEEDBACK_PRIORITY_BOOST,
                    dedupe_key="dashboard:points:boost",
                    reason="seed",
                    payload=None,
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                PointsLedgerEntry(
                    user_id=old_user.id,
                    amount=5,
                    event_type=PointsEventType.MANUAL_ADJUSTMENT,
                    dedupe_key="dashboard:points:old",
                    reason="seed",
                    payload=None,
                    created_at=datetime.now(UTC) - timedelta(days=8),
                )
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))

    request = _make_request("/")
    response = await dashboard(request)

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "Points utility" in body
    assert "Активные points-пользователи (7д):</b> 1" in body
    assert "Пользователи с положительным балансом:</b> 2" in body
    assert "Редимеры points (7д):</b> 1 (50.0%)" in body
    assert "Редимеры фидбек-буста (7д):</b> 1" in body
    assert "Редимеры буста гаранта (7д):</b> 0" in body
    assert "Редимеры буста апелляции (7д):</b> 0" in body
    assert "Points начислено (24ч):</b> +30" in body
    assert "Points списано (24ч):</b> -10" in body
    assert "Бустов фидбека (24ч):</b> 1" in body
    assert "Бустов апелляций (24ч):</b> 0" in body


@pytest.mark.asyncio
async def test_manage_user_points_filter_supports_guarantor_boost(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=93808, username="gboost_filter")
            session.add(user)
            await session.flush()

            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=-11,
                    event_type=PointsEventType.GUARANTOR_PRIORITY_BOOST,
                    dedupe_key="web:gboost:1",
                    reason="gboost reason",
                    payload=None,
                )
            )
            user_id = user.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._csrf_hidden_input", lambda *_args, **_kwargs: "")

    request = _make_request(f"/manage/user/{user_id}", query="points_page=1&points_filter=gboost")
    response = await manage_user(request, user_id=user_id, points_page=1, points_filter="gboost")

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "Фильтр:</b> gboost" in body
    assert "Списание за приоритет гаранта" in body
    assert "gboost reason" in body


@pytest.mark.asyncio
async def test_manage_user_points_filter_supports_appeal_boost(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=93809, username="aboost_filter")
            session.add(user)
            await session.flush()

            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=-13,
                    event_type=PointsEventType.APPEAL_PRIORITY_BOOST,
                    dedupe_key="web:aboost:1",
                    reason="aboost reason",
                    payload=None,
                )
            )
            user_id = user.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._csrf_hidden_input", lambda *_args, **_kwargs: "")

    request = _make_request(f"/manage/user/{user_id}", query="points_page=1&points_filter=aboost")
    response = await manage_user(request, user_id=user_id, points_page=1, points_filter="aboost")

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "Фильтр:</b> aboost" in body
    assert "Списание за приоритет апелляции" in body
    assert "aboost reason" in body


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


@pytest.mark.asyncio
async def test_manage_user_shows_trade_feedback_reputation(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            target = User(tg_user_id=93871, username="seller_target")
            author_visible = User(tg_user_id=93872, username="winner_visible")
            author_hidden = User(tg_user_id=93873, username="winner_hidden")
            session.add_all([target, author_visible, author_hidden])
            await session.flush()

            auction_visible = Auction(
                seller_user_id=target.id,
                winner_user_id=author_visible.id,
                description="visible feedback auction",
                photo_file_id="photo",
                start_price=100,
                buyout_price=None,
                min_step=5,
                duration_hours=24,
                status=AuctionStatus.ENDED,
            )
            auction_hidden = Auction(
                seller_user_id=target.id,
                winner_user_id=author_hidden.id,
                description="hidden feedback auction",
                photo_file_id="photo",
                start_price=120,
                buyout_price=None,
                min_step=5,
                duration_hours=24,
                status=AuctionStatus.BOUGHT_OUT,
            )
            session.add_all([auction_visible, auction_hidden])
            await session.flush()

            session.add_all(
                [
                    TradeFeedback(
                        auction_id=auction_visible.id,
                        author_user_id=author_visible.id,
                        target_user_id=target.id,
                        rating=5,
                        comment="Отличная сделка",
                        status="VISIBLE",
                    ),
                    TradeFeedback(
                        auction_id=auction_hidden.id,
                        author_user_id=author_hidden.id,
                        target_user_id=target.id,
                        rating=2,
                        comment="Скрытый отзыв",
                        status="HIDDEN",
                    ),
                ]
            )
            target_user_id = target.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._csrf_hidden_input", lambda *_args, **_kwargs: "")

    request = _make_request(f"/manage/user/{target_user_id}")
    response = await manage_user(request, user_id=target_user_id)

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "Репутация по сделкам" in body
    assert "Отзывов получено:</b> 2" in body
    assert "Видимых отзывов:</b> 1" in body
    assert "Скрытых отзывов:</b> 1" in body
    assert "Средняя оценка (видимые):</b> 5.0" in body
    assert "Отличная сделка" in body
    assert "Скрытый отзыв" in body
    assert "Открыть отзывы пользователя в модерации" in body


@pytest.mark.asyncio
async def test_web_adjust_points_updates_totals_and_audit(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            actor = User(tg_user_id=93821, username="web_points_actor")
            target = User(tg_user_id=93822, username="web_points_target")
            session.add_all([actor, target])
            await session.flush()
            actor_user_id = actor.id
            target_user_id = target.id

            session.add(
                PointsLedgerEntry(
                    user_id=target.id,
                    amount=10,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="feedback:web-adjust:seed",
                    reason="Награда",
                    payload=None,
                    created_at=datetime.now(UTC),
                )
            )

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.web.main._csrf_hidden_input", lambda *_args, **_kwargs: "")

    async def _resolve_actor(_auth):
        return actor_user_id

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _resolve_actor)

    request = _make_request("/actions/user/points/adjust", method="POST")
    response = await action_adjust_user_points(
        request,
        target_tg_user_id=93822,
        amount="-3",
        reason="manual web correction",
        return_to=f"/manage/user/{target_user_id}?points_page=1&points_filter=all",
        csrf_token="ok",
        action_id="web-adjust-1",
    )

    assert response.status_code == 303

    async with session_factory() as session:
        entries = (
            await session.execute(
                select(PointsLedgerEntry)
                .where(PointsLedgerEntry.user_id == target_user_id)
                .order_by(PointsLedgerEntry.id.asc())
            )
        ).scalars().all()
        log_rows = (
            await session.execute(
                select(ModerationLog)
                .where(
                    ModerationLog.action == ModerationAction.ADJUST_USER_POINTS,
                    ModerationLog.target_user_id == target_user_id,
                )
                .order_by(ModerationLog.id.asc())
            )
        ).scalars().all()

    assert len(entries) == 2
    assert entries[-1].amount == -3
    assert entries[-1].event_type == PointsEventType.MANUAL_ADJUSTMENT
    assert entries[-1].reason == "manual web correction"
    assert entries[-1].dedupe_key == f"web:modpoints:{actor_user_id}:{target_user_id}:web-adjust-1"
    assert len(log_rows) == 1
    assert log_rows[0].target_user_id == target_user_id
    assert log_rows[0].payload is not None
    assert log_rows[0].payload.get("amount") == -3

    manage_request = _make_request(f"/manage/user/{target_user_id}")
    manage_response = await manage_user(manage_request, user_id=target_user_id)
    body = bytes(manage_response.body).decode("utf-8")

    assert manage_response.status_code == 200
    assert "Points баланс:</b> 7" in body
    assert "Начислено всего:</b> +10" in body
    assert "Списано всего:</b> -3" in body
    assert "manual web correction" in body


@pytest.mark.asyncio
async def test_web_adjust_points_requires_role_manage_scope(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            target = User(tg_user_id=93831, username="web_points_scope_target")
            session.add(target)
            await session.flush()
            target_user_id = target.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr(
        "app.web.main._require_scope_permission",
        lambda _req, _scope: (HTMLResponse("forbidden", status_code=403), _stub_auth()),
    )

    request = _make_request("/actions/user/points/adjust", method="POST")
    response = await action_adjust_user_points(
        request,
        target_tg_user_id=93831,
        amount="8",
        reason="scope denied",
        return_to=f"/manage/user/{target_user_id}",
        csrf_token="ok",
        action_id="web-adjust-scope",
    )

    assert response.status_code == 403

    async with session_factory() as session:
        entries = (
            await session.execute(select(PointsLedgerEntry).where(PointsLedgerEntry.user_id == target_user_id))
        ).scalars().all()
        log_rows = (
            await session.execute(
                select(ModerationLog).where(
                    ModerationLog.action == ModerationAction.ADJUST_USER_POINTS,
                    ModerationLog.target_user_id == target_user_id,
                )
            )
        ).scalars().all()

    assert entries == []
    assert log_rows == []


@pytest.mark.asyncio
async def test_web_adjust_points_enforces_csrf(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            target = User(tg_user_id=93841, username="web_points_csrf_target")
            session.add(target)
            await session.flush()
            target_user_id = target.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: False)

    request = _make_request("/actions/user/points/adjust", method="POST")
    response = await action_adjust_user_points(
        request,
        target_tg_user_id=93841,
        amount="8",
        reason="csrf denied",
        return_to=f"/manage/user/{target_user_id}",
        csrf_token="invalid",
        action_id="web-adjust-csrf",
    )

    assert response.status_code == 403
    assert "CSRF check failed" in bytes(response.body).decode("utf-8")

    async with session_factory() as session:
        entries = (
            await session.execute(select(PointsLedgerEntry).where(PointsLedgerEntry.user_id == target_user_id))
        ).scalars().all()
        log_rows = (
            await session.execute(
                select(ModerationLog).where(
                    ModerationLog.action == ModerationAction.ADJUST_USER_POINTS,
                    ModerationLog.target_user_id == target_user_id,
                )
            )
        ).scalars().all()

    assert entries == []
    assert log_rows == []


@pytest.mark.asyncio
async def test_web_adjust_points_is_idempotent_by_action_id(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            actor = User(tg_user_id=93851, username="web_points_idempot_actor")
            target = User(tg_user_id=93852, username="web_points_idempot_target")
            session.add_all([actor, target])
            await session.flush()
            actor_user_id = actor.id
            target_user_id = target.id

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda *_args, **_kwargs: True)

    async def _resolve_actor(_auth):
        return actor_user_id

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _resolve_actor)

    request = _make_request("/actions/user/points/adjust", method="POST")
    response_first = await action_adjust_user_points(
        request,
        target_tg_user_id=93852,
        amount="12",
        reason="idempotent test",
        return_to=f"/manage/user/{target_user_id}",
        csrf_token="ok",
        action_id="web-adjust-idempotent",
    )
    response_second = await action_adjust_user_points(
        request,
        target_tg_user_id=93852,
        amount="12",
        reason="idempotent test",
        return_to=f"/manage/user/{target_user_id}",
        csrf_token="ok",
        action_id="web-adjust-idempotent",
    )

    assert response_first.status_code == 303
    assert response_second.status_code == 303

    async with session_factory() as session:
        entries = (
            await session.execute(select(PointsLedgerEntry).where(PointsLedgerEntry.user_id == target_user_id))
        ).scalars().all()
        log_rows = (
            await session.execute(
                select(ModerationLog).where(
                    ModerationLog.action == ModerationAction.ADJUST_USER_POINTS,
                    ModerationLog.target_user_id == target_user_id,
                )
            )
        ).scalars().all()

    assert len(entries) == 1
    assert entries[0].amount == 12
    assert len(log_rows) == 1
