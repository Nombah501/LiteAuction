from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.web.auth import AdminAuthContext
from app.web.main import (
    action_workflow_presets,
    action_workflow_presets_telemetry,
    appeals,
    complaints,
    signals,
    trade_feedback,
)


def _telegram_auth(*, tg_user_id: int, role: str = "owner") -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="telegram",
        role=role,
        can_manage=True,
        scopes=frozenset({"user:ban"}),
        tg_user_id=tg_user_id,
    )


def _unauthorized_auth() -> AdminAuthContext:
    return AdminAuthContext(
        authorized=False,
        via="none",
        role="viewer",
        can_manage=False,
        scopes=frozenset(),
        tg_user_id=None,
    )


class _SessionStub:
    class _BeginCtx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    async def scalar(self, _stmt):
        return None

    async def execute(self, _stmt):
        class _Rows:
            def all(self):
                return []

            def scalars(self):
                return self

        return _Rows()

    def begin(self):
        return self._BeginCtx()

    async def flush(self):
        return None


class _SessionFactoryCtx:
    async def __aenter__(self):
        return _SessionStub()

    async def __aexit__(self, *_args):
        return False


def _stub_session_factory() -> _SessionFactoryCtx:
    return _SessionFactoryCtx()


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

    async def receive():
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

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


@pytest.mark.asyncio
async def test_queue_routes_render_preset_controls_for_required_contexts(monkeypatch) -> None:
    async def _resolve(_session, **_kwargs):
        return {
            "source": "first_entry_default",
            "state": {
                "density": "compact",
                "columns": {"visible": ["id"], "order": ["id"], "pinned": []},
                "filters": {},
                "sort": {},
            },
            "active_preset": {"id": 91, "name": "Default", "owner_subject_key": "tg:5"},
            "presets": [{"id": 91, "name": "Default", "queue_context": "moderation", "updated_at": ""}],
            "notice": "stale skipped",
        }

    async def _list_complaints(_session, **_kwargs):
        return [SimpleNamespace(id=1, auction_id=2, reporter_user_id=3, status="OPEN", reason="r", created_at=None)]

    async def _list_signals(_session, **_kwargs):
        return [SimpleNamespace(id=1, auction_id=2, user_id=3, score=9, status="OPEN", created_at=None)]

    monkeypatch.setattr("app.web.main.resolve_queue_preset_state", _resolve)
    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)
    monkeypatch.setattr("app.web.main._auth_context_or_unauthorized", lambda _req: (None, _telegram_auth(tg_user_id=1)))
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _telegram_auth(tg_user_id=1)))
    monkeypatch.setattr("app.web.main.list_complaints", _list_complaints)
    monkeypatch.setattr("app.web.main.list_fraud_signals", _list_signals)
    async def _risk_map(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("app.web.main._load_user_risk_snapshot_map", _risk_map)

    complaints_body = bytes((await complaints(_make_request("/complaints"))).body).decode("utf-8")
    signals_body = bytes((await signals(_make_request("/signals"))).body).decode("utf-8")
    feedback_body = bytes((await trade_feedback(_make_request("/trade-feedback"))).body).decode("utf-8")
    appeals_body = bytes((await appeals(_make_request("/appeals"))).body).decode("utf-8")

    assert "data-preset-controls='complaints-table'" in complaints_body
    assert "data-preset-controls='signals-table'" in signals_body
    assert "data-preset-controls='trade-feedback-table'" in feedback_body
    assert "data-preset-controls='appeals-table'" in appeals_body
    assert "Preset telemetry insights (7d)" in complaints_body
    assert "Telemetry is advisory only and does not automate moderation decisions." in complaints_body


@pytest.mark.asyncio
async def test_trade_feedback_telemetry_filter_preserves_queue_context(monkeypatch) -> None:
    async def _resolve(_session, **_kwargs):
        return {
            "source": "none",
            "state": {
                "density": "compact",
                "columns": {"visible": ["id"], "order": ["id"], "pinned": []},
                "filters": {},
                "sort": {},
            },
            "active_preset": None,
            "presets": [],
            "notice": None,
        }

    async def _segments(_session, **_kwargs):
        return [
            {
                "queue_context": "feedback",
                "queue_key": "trade_feedback",
                "preset_id": 5,
                "events_total": 12,
                "avg_time_to_action_ms": 810.0,
                "avg_filter_churn_count": 2.0,
                "reopen_total": 3,
                "reopen_rate": 0.25,
            }
        ]

    monkeypatch.setattr("app.web.main.resolve_queue_preset_state", _resolve)
    monkeypatch.setattr("app.web.main.load_workflow_preset_telemetry_segments", _segments)
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _telegram_auth(tg_user_id=2)))
    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)

    body = bytes(
        (
            await trade_feedback(
                _make_request("/trade-feedback"),
                status="visible",
                moderated="all",
                page=1,
                q="",
                telemetry_preset_id=5,
            )
        ).body
    ).decode("utf-8")

    assert "name='telemetry_preset_id' value='5'" in body
    assert "telemetry_preset_id=5" in body
    assert "Preset filter:" in body
    assert "preset #5" in body


@pytest.mark.asyncio
async def test_workflow_presets_action_returns_conflict_metadata(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _telegram_auth(tg_user_id=7))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _token: True)
    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)

    called = {"telemetry": False}

    async def _save(_session, **_kwargs):
        return {"ok": False, "conflict": True, "preset": {"id": 3, "name": "Incident"}}

    async def _capture(**_kwargs):
        called["telemetry"] = True

    monkeypatch.setattr("app.web.main.save_preset", _save)
    monkeypatch.setattr("app.web.main._record_workflow_preset_telemetry_safe", _capture)

    response = await action_workflow_presets(
        _make_json_request(
            "/actions/workflow-presets",
            {
                "action": "save",
                "queue_context": "moderation",
                "name": "Incident",
                "density": "compact",
                "columns": {
                    "visible": ["id", "auction", "reporter", "status", "reason", "created"],
                    "order": ["id", "auction", "reporter", "status", "reason", "created"],
                    "pinned": [],
                },
                "csrf_token": "ok",
            },
        )
    )

    assert isinstance(response, dict)
    assert response.get("ok") is True
    assert response.get("telemetry_recorded") is False
    result = response.get("result")
    assert isinstance(result, dict)
    assert result.get("conflict") is True
    preset = result.get("preset")
    assert isinstance(preset, dict)
    assert preset.get("id") == 3
    assert called["telemetry"] is False


@pytest.mark.asyncio
async def test_workflow_presets_action_rejects_non_admin_default_update(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _telegram_auth(tg_user_id=8, role="moderator"))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _token: True)
    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)

    async def _set_default(_session, **_kwargs):
        raise PermissionError("Forbidden")

    monkeypatch.setattr("app.web.main.set_admin_default", _set_default)

    with pytest.raises(HTTPException) as exc:
        await action_workflow_presets(
            _make_json_request(
                "/actions/workflow-presets",
                {
                    "action": "set_default",
                    "queue_context": "moderation",
                    "preset_id": 11,
                    "csrf_token": "ok",
                },
            )
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_workflow_presets_action_records_telemetry_for_successful_mutation(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _telegram_auth(tg_user_id=9))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _token: True)
    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)

    async def _save(_session, **_kwargs):
        return {"ok": True, "preset": {"id": 14, "name": "Escalations"}}

    captured: dict[str, object] = {}

    async def _capture(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.web.main.save_preset", _save)
    monkeypatch.setattr("app.web.main._record_workflow_preset_telemetry_safe", _capture)

    response = await action_workflow_presets(
        _make_json_request(
            "/actions/workflow-presets",
            {
                "action": "save",
                "queue_context": "moderation",
                "name": "Escalations",
                "density": "compact",
                "columns": {
                    "visible": ["id", "auction", "reporter", "status", "reason", "created"],
                    "order": ["id", "auction", "reporter", "status", "reason", "created"],
                    "pinned": [],
                },
                "telemetry": {
                    "time_to_action_ms": 1850,
                    "reopen_signal": True,
                    "filter_churn_count": 3,
                },
                "csrf_token": "ok",
            },
        )
    )

    assert isinstance(response, dict)
    assert response.get("ok") is True
    assert response.get("telemetry_recorded") is True
    assert captured["queue_context"] == "moderation"
    assert captured["action"] == "save"
    assert captured["preset_id"] == 14
    assert captured["telemetry_payload"] == {
        "time_to_action_ms": 1850,
        "reopen_signal": True,
        "filter_churn_count": 3,
    }


@pytest.mark.asyncio
async def test_workflow_presets_action_does_not_record_telemetry_on_invalid_action(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _telegram_auth(tg_user_id=10))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _token: True)

    called = {"telemetry": False}

    async def _capture(**_kwargs):
        called["telemetry"] = True

    monkeypatch.setattr("app.web.main._record_workflow_preset_telemetry_safe", _capture)

    with pytest.raises(HTTPException) as exc:
        await action_workflow_presets(
            _make_json_request(
                "/actions/workflow-presets",
                {
                    "action": "unknown",
                    "queue_context": "moderation",
                    "csrf_token": "ok",
                },
            )
        )

    assert exc.value.status_code == 400
    assert called["telemetry"] is False


@pytest.mark.asyncio
async def test_workflow_presets_action_rejects_unauthorized_before_telemetry(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _unauthorized_auth())

    called = {"telemetry": False}

    async def _capture(**_kwargs):
        called["telemetry"] = True

    monkeypatch.setattr("app.web.main._record_workflow_preset_telemetry_safe", _capture)

    with pytest.raises(HTTPException) as exc:
        await action_workflow_presets(
            _make_json_request(
                "/actions/workflow-presets",
                {
                    "action": "save",
                    "queue_context": "moderation",
                    "csrf_token": "ok",
                },
            )
        )

    assert exc.value.status_code == 401
    assert called["telemetry"] is False


@pytest.mark.asyncio
async def test_workflow_presets_action_rejects_csrf_before_telemetry(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main.get_admin_auth_context", lambda _req: _telegram_auth(tg_user_id=12))
    monkeypatch.setattr("app.web.main._validate_csrf_token", lambda _req, _auth, _token: False)

    called = {"telemetry": False}

    async def _capture(**_kwargs):
        called["telemetry"] = True

    monkeypatch.setattr("app.web.main._record_workflow_preset_telemetry_safe", _capture)

    with pytest.raises(HTTPException) as exc:
        await action_workflow_presets(
            _make_json_request(
                "/actions/workflow-presets",
                {
                    "action": "save",
                    "queue_context": "moderation",
                    "csrf_token": "bad",
                },
            )
        )

    assert exc.value.status_code == 403
    assert called["telemetry"] is False


@pytest.mark.asyncio
async def test_workflow_presets_telemetry_endpoint_returns_segments(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _telegram_auth(tg_user_id=11)))

    async def _segments(_session, **_kwargs):
        return [
            {
                "queue_context": "moderation",
                "queue_key": "complaints",
                "preset_id": 5,
                "events_total": 9,
                "avg_time_to_action_ms": 730.0,
                "avg_filter_churn_count": 2.0,
                "reopen_total": 2,
                "reopen_rate": 2 / 9,
            }
        ]

    monkeypatch.setattr("app.web.main.load_workflow_preset_telemetry_segments", _segments)
    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)

    payload = await action_workflow_presets_telemetry(
        _make_request("/actions/workflow-presets/telemetry"),
        queue_context="moderation",
        lookback_hours=48,
    )

    assert isinstance(payload, dict)
    assert payload.get("ok") is True
    assert payload.get("queue_context") == "moderation"
    assert payload.get("lookback_hours") == 48
    assert payload.get("segment_count") == 1
    segments = payload.get("segments")
    assert isinstance(segments, list)
    assert isinstance(segments[0], dict)
    assert segments[0].get("queue_key") == "complaints"


@pytest.mark.asyncio
async def test_workflow_presets_telemetry_endpoint_rejects_invalid_context(monkeypatch) -> None:
    monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _telegram_auth(tg_user_id=13)))
    monkeypatch.setattr("app.web.main.SessionFactory", _stub_session_factory)

    async def _segments(_session, **_kwargs):
        raise ValueError("Unknown queue context")

    monkeypatch.setattr("app.web.main.load_workflow_preset_telemetry_segments", _segments)

    with pytest.raises(HTTPException) as exc:
        await action_workflow_presets_telemetry(
            _make_request("/actions/workflow-presets/telemetry"),
            queue_context="invalid",
            lookback_hours=48,
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_workflow_presets_telemetry_endpoint_requires_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.web.main._require_scope_permission",
        lambda _req, _scope: (
            SimpleNamespace(status_code=403),
            _telegram_auth(tg_user_id=14, role="moderator"),
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await action_workflow_presets_telemetry(
            _make_request("/actions/workflow-presets/telemetry"),
            queue_context="moderation",
            lookback_hours=48,
        )

    assert exc.value.status_code == 403
