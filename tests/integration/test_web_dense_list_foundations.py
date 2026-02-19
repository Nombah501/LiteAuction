from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import AdminListPreference
from app.services.admin_list_preferences_service import (
    load_admin_list_preference,
    save_admin_list_preference,
)
from app.web.auth import AdminAuthContext


def _telegram_auth(tg_user_id: int) -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="telegram",
        role="owner",
        can_manage=True,
        scopes=frozenset({"user:ban"}),
        tg_user_id=tg_user_id,
    )


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
