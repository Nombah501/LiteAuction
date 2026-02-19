from __future__ import annotations

import hashlib

import pytest

from app.services.admin_list_preferences_service import (
    load_admin_list_preference,
    save_admin_list_preference,
)
from app.web.auth import AdminAuthContext


def _telegram_auth() -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="telegram",
        role="owner",
        can_manage=True,
        scopes=frozenset({"user:ban"}),
        tg_user_id=1001,
    )


def _token_auth() -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="token",
        role="owner",
        can_manage=True,
        scopes=frozenset({"user:ban"}),
        tg_user_id=None,
    )


class _DummySession:
    def __init__(self, *, scalar_result=None) -> None:
        self.scalar_result = scalar_result
        self.executed_statement = None
        self.flush_called = False

    async def scalar(self, _statement):
        return self.scalar_result

    async def execute(self, statement) -> None:
        self.executed_statement = statement

    async def flush(self) -> None:
        self.flush_called = True


@pytest.mark.asyncio
async def test_load_returns_defaults_when_preference_missing() -> None:
    session = _DummySession(scalar_result=None)

    result = await load_admin_list_preference(
        session,
        auth=_telegram_auth(),
        queue_key="Complaints",
        allowed_columns=["status", "auction", "updated_at"],
    )

    assert result["density"] == "standard"
    assert result["columns"] == {
        "visible": ["status", "auction", "updated_at"],
        "order": ["status", "auction", "updated_at"],
        "pinned": [],
    }


@pytest.mark.asyncio
async def test_save_upserts_valid_preference_payload() -> None:
    session = _DummySession()

    result = await save_admin_list_preference(
        session,
        auth=_telegram_auth(),
        queue_key="complaints",
        density="compact",
        columns_payload={
            "visible": ["status", "updated_at"],
            "order": ["status", "auction", "updated_at"],
            "pinned": ["status"],
        },
        allowed_columns=["status", "auction", "updated_at"],
    )

    assert result == {
        "density": "compact",
        "columns": {
            "visible": ["status", "updated_at"],
            "order": ["status", "auction", "updated_at"],
            "pinned": ["status"],
        },
    }
    assert session.executed_statement is not None
    assert session.flush_called is True


@pytest.mark.asyncio
async def test_save_rejects_invalid_density() -> None:
    session = _DummySession()

    with pytest.raises(ValueError, match="Invalid density value"):
        await save_admin_list_preference(
            session,
            auth=_telegram_auth(),
            queue_key="complaints",
            density="dense",
            columns_payload={
                "visible": ["status"],
                "order": ["status"],
                "pinned": [],
            },
            allowed_columns=["status"],
        )


@pytest.mark.asyncio
async def test_save_rejects_unknown_column_keys() -> None:
    session = _DummySession()

    with pytest.raises(ValueError, match="Unknown columns in visible"):
        await save_admin_list_preference(
            session,
            auth=_telegram_auth(),
            queue_key="complaints",
            density="standard",
            columns_payload={
                "visible": ["status", "unknown"],
                "order": ["status"],
                "pinned": [],
            },
            allowed_columns=["status"],
        )


@pytest.mark.asyncio
async def test_save_rejects_order_missing_allowed_columns() -> None:
    session = _DummySession()

    with pytest.raises(ValueError, match="order must include each allowed column exactly once"):
        await save_admin_list_preference(
            session,
            auth=_telegram_auth(),
            queue_key="complaints",
            density="standard",
            columns_payload={
                "visible": ["status"],
                "order": ["status"],
                "pinned": [],
            },
            allowed_columns=["status", "auction"],
        )


@pytest.mark.asyncio
async def test_save_rejects_pinned_columns_that_are_not_visible() -> None:
    session = _DummySession()

    with pytest.raises(ValueError, match="pinned columns must be visible"):
        await save_admin_list_preference(
            session,
            auth=_telegram_auth(),
            queue_key="complaints",
            density="standard",
            columns_payload={
                "visible": ["status"],
                "order": ["status", "auction"],
                "pinned": ["auction"],
            },
            allowed_columns=["status", "auction"],
        )


@pytest.mark.asyncio
async def test_save_requires_token_for_token_auth_context() -> None:
    session = _DummySession()

    with pytest.raises(ValueError, match="Token-auth context requires admin token"):
        await save_admin_list_preference(
            session,
            auth=_token_auth(),
            queue_key="complaints",
            density="standard",
            columns_payload={
                "visible": ["status"],
                "order": ["status"],
                "pinned": [],
            },
            allowed_columns=["status"],
        )


@pytest.mark.asyncio
async def test_save_accepts_hashed_token_subject() -> None:
    session = _DummySession()
    token = "very-secret"
    expected_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()

    await save_admin_list_preference(
        session,
        auth=_token_auth(),
        queue_key="complaints",
        density="standard",
        columns_payload={
            "visible": ["status"],
            "order": ["status"],
            "pinned": [],
        },
        allowed_columns=["status"],
        admin_token=token,
    )

    assert session.executed_statement is not None
    assert session.executed_statement.compile().params["subject_key"] == f"tok:{expected_digest}"
