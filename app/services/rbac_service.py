from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.enums import UserRole
from app.db.models import User, UserRoleAssignment

SCOPE_AUCTION_MANAGE = "auction:manage"
SCOPE_BID_MANAGE = "bid:manage"
SCOPE_USER_BAN = "user:ban"
SCOPE_ROLE_MANAGE = "role:manage"
SCOPE_DIRECT_MESSAGES_MANAGE = "direct-messages:manage"

ALL_MANAGE_SCOPES = frozenset(
    {
        SCOPE_AUCTION_MANAGE,
        SCOPE_BID_MANAGE,
        SCOPE_USER_BAN,
        SCOPE_ROLE_MANAGE,
        SCOPE_DIRECT_MESSAGES_MANAGE,
    }
)

OPERATOR_SCOPES = frozenset({SCOPE_AUCTION_MANAGE, SCOPE_BID_MANAGE, SCOPE_DIRECT_MESSAGES_MANAGE})
VIEWER_SCOPES = frozenset()


def resolve_allowlist_role(tg_user_id: int | None, *, via_token: bool) -> tuple[str, frozenset[str]]:
    if via_token:
        return "owner", ALL_MANAGE_SCOPES

    admin_ids = settings.parsed_admin_user_ids()
    operator_ids = set(settings.parsed_admin_operator_user_ids())
    if tg_user_id is None or tg_user_id not in admin_ids:
        return "viewer", VIEWER_SCOPES

    if admin_ids and tg_user_id == admin_ids[0]:
        return "owner", ALL_MANAGE_SCOPES
    if tg_user_id in operator_ids:
        return "operator", OPERATOR_SCOPES
    return "viewer", VIEWER_SCOPES


async def resolve_tg_user_scopes(session: AsyncSession, tg_user_id: int) -> frozenset[str]:
    _, allowlist_scopes = resolve_allowlist_role(tg_user_id, via_token=False)
    if allowlist_scopes:
        return allowlist_scopes

    user = await session.scalar(select(User).where(User.tg_user_id == tg_user_id))
    if user is None:
        return VIEWER_SCOPES

    roles = (
        await session.execute(
            select(UserRoleAssignment.role).where(UserRoleAssignment.user_id == user.id)
        )
    ).scalars().all()
    role_set = set(roles)

    if UserRole.OWNER in role_set or UserRole.ADMIN in role_set:
        return ALL_MANAGE_SCOPES
    if UserRole.MODERATOR in role_set:
        return OPERATOR_SCOPES
    return VIEWER_SCOPES
