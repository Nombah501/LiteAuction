from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.web.auth import AdminAuthContext
from app.web.dense_list import DenseListConfig
from app.web.main import (
    action_triage_bulk,
    action_triage_detail_section,
    appeals,
    complaints,
    signals,
    trade_feedback,
)


def _telegram_auth() -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="telegram",
        role="owner",
        can_manage=True,
        scopes=frozenset({"user:ban"}),
        tg_user_id=900001,
    )


def _telegram_auth_forbidden() -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="telegram",
        role="moderator",
        can_manage=False,
        scopes=frozenset(),
        tg_user_id=900002,
    )


def _unauthorized_auth() -> AdminAuthContext:
    return AdminAuthContext(
        authorized=False,
        via="telegram",
        role="viewer",
        can_manage=False,
        scopes=frozenset(),
        tg_user_id=None,
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


class _SessionStub:
    class _BeginCtx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    async def execute(self, _stmt):
        stmt_text = str(_stmt)

        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return list(self._rows)

        if "trade_feedback" in stmt_text:
            feedback = SimpleNamespace(
                id=66,
                status="VISIBLE",
                rating=5,
                comment="great",
                moderation_note="",
                created_at=None,
                moderated_at=None,
            )
            auction = SimpleNamespace(id="00000000-0000-0000-0000-000000000066")
            author = SimpleNamespace(tg_user_id=6001, username="alpha")
            target = SimpleNamespace(tg_user_id=6002, username="beta")
            return _Rows([(feedback, auction, author, target, None)])

        if "appeals" in stmt_text:
            appeal = SimpleNamespace(
                id=77,
                appeal_ref="risk_77",
                source_type="risk",
                source_id=9,
                status="OPEN",
                resolution_note="",
                resolver_user_id=None,
                created_at=None,
                resolved_at=None,
                sla_deadline_at=None,
                escalated_at=None,
                escalation_level=0,
                priority_boosted_at=None,
            )
            appellant = SimpleNamespace(id=5, tg_user_id=5005, username="delta")
            return _Rows([(appeal, appellant, None)])

        return _Rows([])

    async def scalar(self, _stmt):
        return None

    def begin(self):
        return self._BeginCtx()


class _SessionFactoryCtx:
    async def __aenter__(self):
        return _SessionStub()

    async def __aexit__(self, *_args):
        return False


def _stub_session_factory() -> _SessionFactoryCtx:
    return _SessionFactoryCtx()


def _dense_config(queue_key: str, table_id: str) -> DenseListConfig:
    return DenseListConfig(
        queue_key=queue_key,
        density="compact",
        table_id=table_id,
        quick_filter_placeholder="filter",
        columns_order=("id",),
        columns_visible=("id",),
        columns_pinned=(),
        csrf_token="csrf",
    )


@pytest.mark.asyncio
async def test_triage_markup_renders_for_primary_queues(monkeypatch) -> None:
    async def _list_complaints(_session, **_kwargs):
        return [
            SimpleNamespace(
                id=11,
                auction_id=91,
                reporter_user_id=333,
                status="OPEN",
                reason="duplicate",
                created_at=None,
            )
        ]

    async def _list_signals(_session, **_kwargs):
        return [
            SimpleNamespace(
                id=12,
                auction_id=92,
                user_id=444,
                score=8,
                status="OPEN",
                created_at=None,
            )
        ]

    async def _dense(_session, *, queue_key, **_kwargs):
        table = "complaints-table" if queue_key == "complaints" else "signals-table"
        return _dense_config(queue_key, table)

    async def _risk_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _telegram_auth()))
    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)
    monkeypatch.setattr("app.web.main.list_complaints", _list_complaints)
    monkeypatch.setattr("app.web.main.list_fraud_signals", _list_signals)
    monkeypatch.setattr("app.web.main._load_dense_list_config", _dense)
    monkeypatch.setattr("app.web.main._load_user_risk_snapshot_map", _risk_map)

    complaints_body = bytes((await complaints(_make_request("/complaints"))).body).decode("utf-8")
    signals_body = bytes((await signals(_make_request("/signals"))).body).decode("utf-8")

    assert "data-triage-row='1'" in complaints_body
    assert "data-triage-detail='11'" in complaints_body
    assert "data-bulk-controls='complaints-table'" in complaints_body
    assert "data-shortcut='row-next'" in complaints_body

    assert "data-triage-row='1'" in signals_body
    assert "data-triage-detail='12'" in signals_body
    assert "data-bulk-controls='signals-table'" in signals_body


@pytest.mark.asyncio
async def test_triage_markup_renders_for_trade_feedback_and_appeals(monkeypatch) -> None:
    async def _dense(_session, *, queue_key, **_kwargs):
        table = "trade-feedback-table" if queue_key == "trade_feedback" else "appeals-table"
        return _dense_config(queue_key, table)

    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _telegram_auth()))
    monkeypatch.setattr("app.web.main._load_dense_list_config", _dense)
    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)
    async def _risk_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("app.web.main._load_user_risk_snapshot_map", _risk_map)

    feedback_body = bytes((await trade_feedback(_make_request("/trade-feedback"))).body).decode("utf-8")
    appeals_body = bytes((await appeals(_make_request("/appeals"))).body).decode("utf-8")

    assert "data-triage-row='1'" in feedback_body
    assert "data-bulk-controls='trade-feedback-table'" in feedback_body
    assert "data-triage-row='1'" in appeals_body
    assert "data-bulk-controls='appeals-table'" in appeals_body


@pytest.mark.asyncio
async def test_triage_markup_includes_keyboard_focus_and_scroll_hooks(monkeypatch) -> None:
    async def _list_complaints(_session, **_kwargs):
        return [
            SimpleNamespace(
                id=21,
                auction_id=91,
                reporter_user_id=333,
                status="OPEN",
                reason="duplicate",
                created_at=None,
            )
        ]

    async def _dense(_session, *, queue_key, **_kwargs):
        assert queue_key == "complaints"
        return _dense_config(queue_key, "complaints-table")

    async def _risk_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _telegram_auth()))
    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)
    monkeypatch.setattr("app.web.main.list_complaints", _list_complaints)
    monkeypatch.setattr("app.web.main._load_dense_list_config", _dense)
    monkeypatch.setattr("app.web.main._load_user_risk_snapshot_map", _risk_map)

    body = bytes((await complaints(_make_request("/complaints"))).body).decode("utf-8")

    assert "moveFocusedRow(1)" in body
    assert "moveFocusedRow(-1)" in body
    assert "toggleFocusedRowDetail()" in body
    assert "window.scrollTo({ top: closeContext.scrollY, behavior: 'auto' });" in body
    assert "closeContext.invoker.focus({ preventScroll: true });" in body


@pytest.mark.asyncio
async def test_triage_detail_section_contract(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _telegram_auth()))

    payload = await action_triage_detail_section(
        _make_request("/actions/triage/detail-section"),
        queue_key="complaints",
        row_id=20,
        section="primary",
    )
    assert payload["ok"] is True
    assert "complaints #20" in payload["html"]

    with pytest.raises(HTTPException):
        await action_triage_detail_section(
            _make_request("/actions/triage/detail-section"),
            queue_key="unknown",
            row_id=20,
            section="primary",
        )


@pytest.mark.asyncio
async def test_bulk_endpoint_requires_confirmation_for_destructive(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _telegram_auth())
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _token: True)

    with pytest.raises(HTTPException) as exc:
        await action_triage_bulk(
            _make_json_request(
                "/actions/triage/bulk",
                {
                    "queue_key": "trade_feedback",
                    "bulk_action": "hide",
                    "selected_ids": [1, 2],
                    "csrf_token": "ok",
                    "confirm_text": "WRONG",
                },
            )
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_bulk_endpoint_returns_mixed_results(monkeypatch) -> None:
    class _Result:
        def __init__(self, *, ok: bool, message: str):
            self.ok = ok
            self.message = message

    class _BulkSessionFactoryCtx:
        async def __aenter__(self):
            return _SessionStub()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _telegram_auth())
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _token: True)
    async def _actor_id(_auth):
        return 1

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _actor_id)
    monkeypatch.setattr("app.web.main.SessionFactory", lambda: _BulkSessionFactoryCtx())

    async def _set_feedback_visibility(_session, *, feedback_id, **_kwargs):
        if feedback_id == 1:
            return _Result(ok=True, message="ok")
        return _Result(ok=False, message="already hidden")

    monkeypatch.setattr("app.web.main.set_trade_feedback_visibility", _set_feedback_visibility)

    payload = await action_triage_bulk(
        _make_json_request(
            "/actions/triage/bulk",
            {
                "queue_key": "trade_feedback",
                "bulk_action": "hide",
                "selected_ids": [1, 2],
                "csrf_token": "ok",
                "confirm_text": "CONFIRM",
                "reason": "bulk",
            },
        )
    )

    assert payload["ok"] is True
    assert payload["results"][0]["ok"] is True
    assert payload["results"][1]["ok"] is False


@pytest.mark.asyncio
async def test_bulk_endpoint_rejects_unauthorized_actor_without_mutation(monkeypatch) -> None:
    called = {"actor": False}

    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _unauthorized_auth())

    async def _actor_id(_auth):
        called["actor"] = True
        return 1

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _actor_id)

    with pytest.raises(HTTPException) as exc:
        await action_triage_bulk(
            _make_json_request(
                "/actions/triage/bulk",
                {
                    "queue_key": "trade_feedback",
                    "bulk_action": "hide",
                    "selected_ids": [1],
                    "csrf_token": "ok",
                    "confirm_text": "CONFIRM",
                },
            )
        )

    assert exc.value.status_code == 401
    assert called["actor"] is False


@pytest.mark.asyncio
async def test_bulk_endpoint_rejects_forbidden_scope_without_mutation(monkeypatch) -> None:
    called = {"actor": False}

    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _telegram_auth_forbidden())
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _token: True)

    async def _actor_id(_auth):
        called["actor"] = True
        return 1

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _actor_id)

    with pytest.raises(HTTPException) as exc:
        await action_triage_bulk(
            _make_json_request(
                "/actions/triage/bulk",
                {
                    "queue_key": "trade_feedback",
                    "bulk_action": "hide",
                    "selected_ids": [1],
                    "csrf_token": "ok",
                    "confirm_text": "CONFIRM",
                },
            )
        )

    assert exc.value.status_code == 403
    assert called["actor"] is False


@pytest.mark.asyncio
async def test_bulk_endpoint_rejects_csrf_without_mutation(monkeypatch) -> None:
    called = {"actor": False}

    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _telegram_auth())
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _token: False)

    async def _actor_id(_auth):
        called["actor"] = True
        return 1

    monkeypatch.setattr("app.web.main._resolve_actor_user_id", _actor_id)

    with pytest.raises(HTTPException) as exc:
        await action_triage_bulk(
            _make_json_request(
                "/actions/triage/bulk",
                {
                    "queue_key": "complaints",
                    "bulk_action": "resolve",
                    "selected_ids": [1],
                    "csrf_token": "bad",
                },
            )
        )

    assert exc.value.status_code == 403
    assert called["actor"] is False
