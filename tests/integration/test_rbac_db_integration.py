from __future__ import annotations

import random

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import UserRole
from app.db.models import User, UserRoleAssignment
from app.services.moderation_service import (
    grant_moderator_role,
    has_moderation_scope,
    revoke_moderator_role,
)
from app.services.rbac_service import ALL_MANAGE_SCOPES, OPERATOR_SCOPES, SCOPE_AUCTION_MANAGE, SCOPE_USER_BAN, VIEWER_SCOPES, resolve_tg_user_scopes


def _test_tg_user_id() -> int:
    return random.randint(10_000_000, 999_999_999)


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
