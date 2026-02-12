from __future__ import annotations

import os
import random

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.enums import UserRole
from app.db.models import User, UserRoleAssignment
from app.services.moderation_service import (
    grant_moderator_role,
    has_moderation_scope,
    revoke_moderator_role,
)
from app.services.rbac_service import (
    ALL_MANAGE_SCOPES,
    OPERATOR_SCOPES,
    SCOPE_AUCTION_MANAGE,
    SCOPE_USER_BAN,
    VIEWER_SCOPES,
    resolve_tg_user_scopes,
)

if os.getenv("RUN_INTEGRATION_TESTS") != "1":
    pytest.skip("Integration tests are disabled (set RUN_INTEGRATION_TESTS=1)", allow_module_level=True)


def _test_tg_user_id() -> int:
    return random.randint(10_000_000, 999_999_999)


@pytest_asyncio.fixture
async def integration_engine():
    db_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        pytest.skip("No TEST_DATABASE_URL or DATABASE_URL set")

    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # pragma: no cover
        await engine.dispose()
        pytest.skip(f"Integration database is unavailable: {exc}")

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(integration_engine) -> AsyncSession:
    session_factory = async_sessionmaker(
        bind=integration_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_dynamic_moderator_grant_and_revoke_changes_scopes(monkeypatch, db_session: AsyncSession) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_user_ids", "")
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    tg_user_id = _test_tg_user_id()

    async with db_session.begin():
        result = await grant_moderator_role(db_session, target_tg_user_id=tg_user_id)
    assert result.ok is True

    scopes_after_grant = await resolve_tg_user_scopes(db_session, tg_user_id)
    assert scopes_after_grant == OPERATOR_SCOPES
    assert await has_moderation_scope(db_session, tg_user_id, SCOPE_AUCTION_MANAGE) is True
    assert await has_moderation_scope(db_session, tg_user_id, SCOPE_USER_BAN) is False

    revoke_result = await revoke_moderator_role(db_session, target_tg_user_id=tg_user_id)
    await db_session.flush()
    assert revoke_result.ok is True

    scopes_after_revoke = await resolve_tg_user_scopes(db_session, tg_user_id)
    assert scopes_after_revoke == VIEWER_SCOPES


@pytest.mark.asyncio
async def test_dynamic_admin_role_grants_full_scopes(monkeypatch, db_session: AsyncSession) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_user_ids", "")
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    tg_user_id = _test_tg_user_id()
    async with db_session.begin():
        user = User(tg_user_id=tg_user_id)
        db_session.add(user)
        await db_session.flush()
        db_session.add(UserRoleAssignment(user_id=user.id, role=UserRole.ADMIN))

    scopes = await resolve_tg_user_scopes(db_session, tg_user_id)
    assert scopes == ALL_MANAGE_SCOPES
    assert await has_moderation_scope(db_session, tg_user_id, SCOPE_USER_BAN) is True


@pytest.mark.asyncio
async def test_revoke_is_blocked_for_allowlist_user(monkeypatch, db_session: AsyncSession) -> None:
    from app.config import settings

    tg_user_id = _test_tg_user_id()
    monkeypatch.setattr(settings, "admin_user_ids", f"1,{tg_user_id}")
    monkeypatch.setattr(settings, "admin_operator_user_ids", str(tg_user_id))

    async with db_session.begin():
        result = await revoke_moderator_role(db_session, target_tg_user_id=tg_user_id)

    assert result.ok is False
    assert "ADMIN_USER_IDS" in result.message
