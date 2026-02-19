from __future__ import annotations

import json
import os
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
import pytest_asyncio
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import AdminListPreference
from app.services.admin_list_preferences_service import (
    load_admin_list_preference,
    save_admin_list_preference,
)
from app.web.auth import AdminAuthContext
from app.web.main import action_save_dense_list_preferences, appeals, complaints, violators


def _telegram_auth(tg_user_id: int) -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="telegram",
        role="owner",
        can_manage=True,
        scopes=frozenset({"user:ban"}),
        tg_user_id=tg_user_id,
    )


def _make_request(path: str = "/") -> Request:
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


def _make_json_request(path: str, payload: dict[str, object]) -> Request:
    body = json.dumps(payload).encode("utf-8")
    state = {"sent": False}
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive() -> dict[str, object]:
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def _token_auth() -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="token",
        role="owner",
        can_manage=True,
        scopes=frozenset({"user:ban"}),
        tg_user_id=None,
    )


class _EmptyRows:
    def all(self) -> list[tuple[object, ...]]:
        return []


class _SessionStub:
    async def scalar(self, _stmt):
        return None

    async def execute(self, _stmt) -> _EmptyRows:
        return _EmptyRows()


class _SessionFactoryCtx:
    async def __aenter__(self) -> _SessionStub:
        return _SessionStub()

    async def __aexit__(self, *_args: object) -> bool:
        return False


def _stub_session_factory() -> _SessionFactoryCtx:
    return _SessionFactoryCtx()


@pytest_asyncio.fixture
async def preference_session_factory():
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled (set RUN_INTEGRATION_TESTS=1)")

    db_url = (os.getenv("TEST_DATABASE_URL") or "").strip()
    if not db_url:
        pytest.skip("No TEST_DATABASE_URL set")

    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(AdminListPreference.__table__.drop, checkfirst=True)
            await conn.run_sync(AdminListPreference.__table__.create)
    except Exception as exc:  # pragma: no cover
        await engine.dispose()
        pytest.skip(f"Integration database is unavailable: {exc}")

    yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(AdminListPreference.__table__.drop, checkfirst=True)
    await engine.dispose()


@pytest.mark.asyncio
async def test_preferences_persist_for_same_subject_and_queue(preference_session_factory) -> None:
    session_factory = preference_session_factory
    allowed_columns = ["status", "auction", "updated_at"]

    async with session_factory() as session:
        async with session.begin():
            await save_admin_list_preference(
                session,
                auth=_telegram_auth(777001),
                queue_key="complaints",
                density="compact",
                columns_payload={
                    "visible": ["status", "updated_at"],
                    "order": ["status", "auction", "updated_at"],
                    "pinned": ["status"],
                },
                allowed_columns=allowed_columns,
            )

    async with session_factory() as session:
        restored = await load_admin_list_preference(
            session,
            auth=_telegram_auth(777001),
            queue_key="complaints",
            allowed_columns=allowed_columns,
        )

    assert restored == {
        "density": "compact",
        "columns": {
            "visible": ["status", "updated_at"],
            "order": ["status", "auction", "updated_at"],
            "pinned": ["status"],
        },
    }


@pytest.mark.asyncio
async def test_preferences_are_isolated_by_subject(preference_session_factory) -> None:
    session_factory = preference_session_factory
    allowed_columns = ["status", "auction", "updated_at"]

    async with session_factory() as session:
        async with session.begin():
            await save_admin_list_preference(
                session,
                auth=_telegram_auth(777101),
                queue_key="complaints",
                density="comfortable",
                columns_payload={
                    "visible": ["auction", "updated_at"],
                    "order": ["auction", "status", "updated_at"],
                    "pinned": [],
                },
                allowed_columns=allowed_columns,
            )
            await save_admin_list_preference(
                session,
                auth=_telegram_auth(777102),
                queue_key="complaints",
                density="compact",
                columns_payload={
                    "visible": ["status"],
                    "order": ["status", "auction", "updated_at"],
                    "pinned": ["status"],
                },
                allowed_columns=allowed_columns,
            )

    async with session_factory() as session:
        subject_a = await load_admin_list_preference(
            session,
            auth=_telegram_auth(777101),
            queue_key="complaints",
            allowed_columns=allowed_columns,
        )
        subject_b = await load_admin_list_preference(
            session,
            auth=_telegram_auth(777102),
            queue_key="complaints",
            allowed_columns=allowed_columns,
        )

    assert subject_a["density"] == "comfortable"
    assert subject_b["density"] == "compact"
    assert subject_a["columns"]["visible"] == ["auction", "updated_at"]
    assert subject_b["columns"]["visible"] == ["status"]


@pytest.mark.asyncio
async def test_complaints_restores_saved_density_and_layout(preference_session_factory, monkeypatch) -> None:
    session_factory = preference_session_factory
    allowed_columns = ["id", "auction", "reporter", "status", "reason", "created"]

    async with session_factory() as session:
        async with session.begin():
            await save_admin_list_preference(
                session,
                auth=_telegram_auth(777777),
                queue_key="complaints",
                density="comfortable",
                columns_payload={
                    "visible": ["id", "status", "reason"],
                    "order": ["id", "auction", "reporter", "status", "reason", "created"],
                    "pinned": ["id", "status"],
                },
                allowed_columns=allowed_columns,
            )

    async def _list_complaints(_session, **_kwargs):
        return [
            SimpleNamespace(
                id=55,
                auction_id=91,
                reporter_user_id=10001,
                status="OPEN",
                reason="layout restore",
                created_at=None,
            )
        ]

    monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
    monkeypatch.setattr("app.web.main.list_complaints", _list_complaints)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _telegram_auth(777777)))

    response = await complaints(_make_request("/complaints"), status="OPEN", page=0, density=None)
    body = bytes(response.body).decode("utf-8")

    assert response.status_code == 200
    assert "data-density='comfortable'" in body
    assert "data-columns-visible='id,status,reason'" in body
    assert "data-columns-pinned='id,status'" in body
    assert "<th data-col='id'>ID</th>" in body
    assert "<td data-col='reason'>layout restore</td>" in body


@pytest.mark.asyncio
async def test_preferences_reject_invalid_density_payload(preference_session_factory) -> None:
    session_factory = preference_session_factory

    async with session_factory() as session:
        async with session.begin():
            with pytest.raises(ValueError, match="Invalid density value"):
                await save_admin_list_preference(
                    session,
                    auth=_telegram_auth(777888),
                    queue_key="complaints",
                    density="super-dense",
                    columns_payload={
                        "visible": ["status"],
                        "order": ["status"],
                        "pinned": [],
                    },
                    allowed_columns=["status"],
                )


@pytest.mark.asyncio
async def test_preferences_reject_unknown_columns_payload(preference_session_factory) -> None:
    session_factory = preference_session_factory

    async with session_factory() as session:
        async with session.begin():
            with pytest.raises(ValueError, match="Unknown columns in order"):
                await save_admin_list_preference(
                    session,
                    auth=_telegram_auth(777889),
                    queue_key="complaints",
                    density="standard",
                    columns_payload={
                        "visible": ["status"],
                        "order": ["status", "unknown"],
                        "pinned": [],
                    },
                    allowed_columns=["status"],
                )


@pytest.mark.asyncio
async def test_complaints_density_and_quick_filter_markup(monkeypatch) -> None:
    async def _list_complaints(_session, **_kwargs):
        return [
            SimpleNamespace(
                id=44,
                auction_id=901,
                reporter_user_id=777,
                status="OPEN",
                reason="duplicate payment",
                created_at=None,
            )
        ]

    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)
    monkeypatch.setattr("app.web.main.list_complaints", _list_complaints)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _telegram_auth(555001)))

    response = await complaints(_make_request("/complaints"), status="OPEN", page=0, density="compact")
    body = bytes(response.body).decode("utf-8")

    assert response.status_code == 200
    assert "data-density-option='compact'" in body
    assert "data-quick-filter='complaints-table'" in body
    assert "data-dense-list='complaints-table'" in body
    assert "data-row='44 901 777 OPEN duplicate payment'" in body


@pytest.mark.asyncio
async def test_appeals_filter_links_keep_qualifiers_with_density(monkeypatch) -> None:
    async def _risk_map(_session, *, user_ids, now=None):
        return {}

    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)
    monkeypatch.setattr("app.web.main._load_user_risk_snapshot_map", _risk_map)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _telegram_auth(555002)))

    response = await appeals(
        _make_request("/appeals"),
        status="open",
        source="manual",
        overdue="only",
        escalated="none",
        page=0,
        q="case42",
        density="compact",
    )
    body = bytes(response.body).decode("utf-8")

    assert response.status_code == 200
    assert "data-density-option='compact'" in body
    assert (
        "/appeals?status=open&amp;source=manual&amp;overdue=only&amp;escalated=none"
        "&amp;q=case42&amp;density=compact&amp;page=0"
    ) in body


@pytest.mark.asyncio
async def test_violators_invalid_filter_stays_server_validated(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _token_auth()))

    with pytest.raises(HTTPException) as exc:
        await violators(
            _make_request("/violators"),
            status="active",
            page=0,
            q="",
            by="",
            created_from="2026-02-01",
            created_to="broken-date",
            density="comfortable",
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_dense_list_preferences_endpoint_rejects_invalid_density(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _telegram_auth(100500))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _token: True)

    request = _make_json_request(
        "/actions/dense-list/preferences",
        {
            "queue_key": "complaints",
            "density": "invalid",
            "columns": {
                "visible": ["id"],
                "order": ["id", "auction", "reporter", "status", "reason", "created"],
                "pinned": [],
            },
            "csrf_token": "ok",
        },
    )

    with pytest.raises(HTTPException) as exc:
        await action_save_dense_list_preferences(request)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid density value"


@pytest.mark.asyncio
async def test_dense_list_preferences_endpoint_rejects_unknown_column(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _telegram_auth(100501))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _token: True)

    request = _make_json_request(
        "/actions/dense-list/preferences",
        {
            "queue_key": "complaints",
            "density": "standard",
            "columns": {
                "visible": ["id"],
                "order": ["id", "auction", "reporter", "status", "reason", "created", "not_allowed"],
                "pinned": [],
            },
            "csrf_token": "ok",
        },
    )

    with pytest.raises(HTTPException) as exc:
        await action_save_dense_list_preferences(request)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Unknown columns in order"
