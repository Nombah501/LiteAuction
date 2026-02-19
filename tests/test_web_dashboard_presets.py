from __future__ import annotations

import pytest
from starlette.requests import Request

from app.services.moderation_dashboard_service import ModerationDashboardSnapshot
from app.web.auth import AdminAuthContext
from app.web.main import dashboard


def _make_request(path: str = "/", query: str = "") -> Request:
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
        scopes=frozenset({"auction:manage", "bid:manage", "user:ban", "role:manage"}),
        tg_user_id=None,
    )


def _snapshot() -> ModerationDashboardSnapshot:
    return ModerationDashboardSnapshot(
        open_complaints=1,
        open_signals=2,
        active_auctions=3,
        frozen_auctions=1,
        bids_last_hour=4,
        bids_last_24h=20,
        active_blacklist_entries=1,
        total_users=10,
        users_private_started=8,
        users_with_bid_activity=6,
        users_with_report_activity=2,
        users_with_engagement=7,
        users_engaged_without_private_start=1,
        users_with_soft_gate_hint=5,
        users_soft_gate_hint_last_24h=2,
        users_converted_after_hint=3,
        users_pending_after_hint=1,
        points_active_users_7d=3,
        points_users_with_positive_balance=4,
        points_redeemers_7d=2,
        points_feedback_boost_redeemers_7d=1,
        points_guarantor_boost_redeemers_7d=1,
        points_appeal_boost_redeemers_7d=0,
        points_earned_24h=30,
        points_spent_24h=12,
        feedback_boost_redeems_24h=1,
        guarantor_boost_redeems_24h=1,
        appeal_boost_redeems_24h=0,
    )


class _DummySessionCtx:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> bool:
        return False


def _stub_session_factory() -> _DummySessionCtx:
    return _DummySessionCtx()


@pytest.mark.asyncio
async def test_dashboard_default_uses_incident_preset(monkeypatch) -> None:
    async def _snapshot_fetcher(_session: object) -> ModerationDashboardSnapshot:
        return _snapshot()

    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)
    monkeypatch.setattr("app.web.main.get_moderation_dashboard_snapshot", _snapshot_fetcher)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))

    response = await dashboard(_make_request("/"))
    body = bytes(response.body).decode("utf-8")

    assert response.status_code == 200
    assert "class='chip chip-active' data-preset='incident'" in body
    assert "<details class='details'><summary>Развернуть воронку онбординга" in body
    assert "<details class='details' open><summary>Развернуть воронку онбординга" not in body
    assert "<details class='details'><summary>Развернуть policy и лимиты rewards</summary>" in body
    assert "la_dashboard_preset" in body


@pytest.mark.asyncio
async def test_dashboard_routine_preset_opens_routine_sections(monkeypatch) -> None:
    async def _snapshot_fetcher(_session: object) -> ModerationDashboardSnapshot:
        return _snapshot()

    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)
    monkeypatch.setattr("app.web.main.get_moderation_dashboard_snapshot", _snapshot_fetcher)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))

    response = await dashboard(_make_request("/", query="preset=routine"))
    body = bytes(response.body).decode("utf-8")

    assert response.status_code == 200
    assert "class='chip chip-active' data-preset='routine'" in body
    assert "<details class='details' open><summary>Развернуть воронку онбординга" in body
    assert "<details class='details' open><summary>Развернуть метрики активности" in body
    assert "<details class='details' open><summary>Развернуть weekly rewards метрики</summary>" in body


@pytest.mark.asyncio
async def test_dashboard_rewards_preset_opens_rewards_sections(monkeypatch) -> None:
    async def _snapshot_fetcher(_session: object) -> ModerationDashboardSnapshot:
        return _snapshot()

    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)
    monkeypatch.setattr("app.web.main.get_moderation_dashboard_snapshot", _snapshot_fetcher)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))

    response = await dashboard(_make_request("/", query="preset=rewards"))
    body = bytes(response.body).decode("utf-8")

    assert response.status_code == 200
    assert "class='chip chip-active' data-preset='rewards'" in body
    assert "<details class='details' open><summary>Развернуть weekly rewards метрики</summary>" in body
    assert "<details class='details' open><summary>Развернуть rewards активность за 24ч</summary>" in body
    assert "<details class='details' open><summary>Развернуть policy и лимиты rewards</summary>" in body


@pytest.mark.asyncio
async def test_dashboard_invalid_preset_falls_back_to_incident(monkeypatch) -> None:
    async def _snapshot_fetcher(_session: object) -> ModerationDashboardSnapshot:
        return _snapshot()

    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)
    monkeypatch.setattr("app.web.main.get_moderation_dashboard_snapshot", _snapshot_fetcher)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _stub_auth()))

    response = await dashboard(_make_request("/", query="preset=unknown"))
    body = bytes(response.body).decode("utf-8")

    assert response.status_code == 200
    assert "class='chip chip-active' data-preset='incident'" in body
