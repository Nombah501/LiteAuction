from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.enums import AuctionStatus, ModerationAction, UserRole
from app.db.models import Auction, Bid, BlacklistEntry, ModerationLog, User, UserRoleAssignment
from app.services.rbac_service import (
    resolve_allowlist_role,
    resolve_tg_user_scopes,
)


@dataclass(slots=True)
class ModerationResult:
    ok: bool
    message: str
    auction_id: uuid.UUID | None = None
    seller_tg_user_id: int | None = None
    winner_tg_user_id: int | None = None
    target_tg_user_id: int | None = None


@dataclass(slots=True)
class BidListItem:
    bid_id: uuid.UUID
    amount: int
    created_at: datetime
    tg_user_id: int
    username: str | None
    is_removed: bool


@dataclass(slots=True)
class RoleUpdateResult:
    ok: bool
    message: str
    target_tg_user_id: int | None = None


MODERATION_ROLES: tuple[UserRole, ...] = (UserRole.OWNER, UserRole.ADMIN, UserRole.MODERATOR)


def is_moderator_tg_user(tg_user_id: int) -> bool:
    return tg_user_id in settings.parsed_admin_user_ids()


async def has_moderator_access(session: AsyncSession, tg_user_id: int) -> bool:
    scopes = await resolve_tg_user_scopes(session, tg_user_id)
    return bool(scopes)


async def get_moderation_scopes(session: AsyncSession, tg_user_id: int) -> frozenset[str]:
    return await resolve_tg_user_scopes(session, tg_user_id)


async def has_moderation_scope(session: AsyncSession, tg_user_id: int, scope: str) -> bool:
    scopes = await get_moderation_scopes(session, tg_user_id)
    return scope in scopes


def allowlist_role_and_scopes(tg_user_id: int | None, *, via_token: bool = False) -> tuple[str, frozenset[str]]:
    return resolve_allowlist_role(tg_user_id, via_token=via_token)


async def list_user_roles(session: AsyncSession, user_id: int) -> set[UserRole]:
    rows = (
        await session.execute(
            select(UserRoleAssignment.role).where(UserRoleAssignment.user_id == user_id)
        )
    ).scalars().all()
    return set(rows)


async def list_tg_user_roles(session: AsyncSession, tg_user_id: int) -> set[UserRole]:
    user = await session.scalar(select(User).where(User.tg_user_id == tg_user_id))
    if user is None:
        return set()
    return await list_user_roles(session, user.id)


async def _get_or_create_user_by_tg_id(session: AsyncSession, tg_user_id: int) -> User:
    user = await session.scalar(select(User).where(User.tg_user_id == tg_user_id))
    if user is not None:
        return user

    user = User(tg_user_id=tg_user_id)
    session.add(user)
    await session.flush()
    return user


async def grant_moderator_role(
    session: AsyncSession,
    *,
    target_tg_user_id: int,
) -> RoleUpdateResult:
    if is_moderator_tg_user(target_tg_user_id):
        return RoleUpdateResult(
            False,
            "Пользователь уже имеет права модератора через allowlist",
            target_tg_user_id=target_tg_user_id,
        )

    target_user = await _get_or_create_user_by_tg_id(session, target_tg_user_id)
    existing = await session.scalar(
        select(UserRoleAssignment)
        .where(
            UserRoleAssignment.user_id == target_user.id,
            UserRoleAssignment.role == UserRole.MODERATOR,
        )
        .with_for_update()
    )
    if existing is not None:
        return RoleUpdateResult(
            False,
            "Пользователь уже модератор",
            target_tg_user_id=target_tg_user_id,
        )

    session.add(UserRoleAssignment(user_id=target_user.id, role=UserRole.MODERATOR))
    return RoleUpdateResult(
        True,
        "Права модератора выданы",
        target_tg_user_id=target_tg_user_id,
    )


async def revoke_moderator_role(
    session: AsyncSession,
    *,
    target_tg_user_id: int,
) -> RoleUpdateResult:
    if is_moderator_tg_user(target_tg_user_id):
        return RoleUpdateResult(
            False,
            "Нельзя снять модерацию с пользователя из ADMIN_USER_IDS",
            target_tg_user_id=target_tg_user_id,
        )

    user = await session.scalar(select(User).where(User.tg_user_id == target_tg_user_id))
    if user is None:
        return RoleUpdateResult(False, "Пользователь не найден", target_tg_user_id=target_tg_user_id)

    role_row = await session.scalar(
        select(UserRoleAssignment)
        .where(
            UserRoleAssignment.user_id == user.id,
            UserRoleAssignment.role == UserRole.MODERATOR,
        )
        .with_for_update()
    )
    if role_row is None:
        return RoleUpdateResult(
            False,
            "У пользователя нет роли MODERATOR",
            target_tg_user_id=target_tg_user_id,
        )

    await session.delete(role_row)
    return RoleUpdateResult(
        True,
        "Права модератора сняты",
        target_tg_user_id=target_tg_user_id,
    )


async def is_tg_user_blacklisted(session: AsyncSession, tg_user_id: int) -> bool:
    now = datetime.now(UTC)
    stmt = (
        select(BlacklistEntry.id)
        .join(User, User.id == BlacklistEntry.user_id)
        .where(
            User.tg_user_id == tg_user_id,
            BlacklistEntry.is_active.is_(True),
            (BlacklistEntry.expires_at.is_(None) | (BlacklistEntry.expires_at > now)),
        )
        .limit(1)
    )
    return (await session.scalar(stmt)) is not None


async def is_user_blacklisted(session: AsyncSession, user_id: int) -> bool:
    now = datetime.now(UTC)
    stmt = select(BlacklistEntry.id).where(
        BlacklistEntry.user_id == user_id,
        BlacklistEntry.is_active.is_(True),
        (BlacklistEntry.expires_at.is_(None) | (BlacklistEntry.expires_at > now)),
    )
    return (await session.scalar(stmt)) is not None


async def _get_auction_for_update(session: AsyncSession, auction_id: uuid.UUID) -> Auction | None:
    stmt: Select[tuple[Auction]] = select(Auction).where(Auction.id == auction_id).with_for_update()
    return await session.scalar(stmt)


async def _top_bid(session: AsyncSession, auction_id: uuid.UUID) -> Bid | None:
    return await session.scalar(
        select(Bid)
        .where(Bid.auction_id == auction_id, Bid.is_removed.is_(False))
        .order_by(Bid.amount.desc(), Bid.created_at.asc())
        .limit(1)
    )


async def _log_action(
    session: AsyncSession,
    *,
    actor_user_id: int,
    action: ModerationAction,
    reason: str,
    target_user_id: int | None = None,
    auction_id: uuid.UUID | None = None,
    bid_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> None:
    session.add(
        ModerationLog(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            auction_id=auction_id,
            bid_id=bid_id,
            action=action,
            reason=reason,
            payload=payload,
        )
    )


async def freeze_auction(
    session: AsyncSession,
    *,
    actor_user_id: int,
    auction_id: uuid.UUID,
    reason: str,
) -> ModerationResult:
    auction = await _get_auction_for_update(session, auction_id)
    if auction is None:
        return ModerationResult(False, "Аукцион не найден")

    if auction.status != AuctionStatus.ACTIVE:
        return ModerationResult(False, "Заморозить можно только активный аукцион", auction_id=auction.id)

    auction.status = AuctionStatus.FROZEN
    auction.updated_at = datetime.now(UTC)

    seller = await session.scalar(select(User).where(User.id == auction.seller_user_id))
    await _log_action(
        session,
        actor_user_id=actor_user_id,
        action=ModerationAction.FREEZE_AUCTION,
        reason=reason,
        auction_id=auction.id,
    )
    return ModerationResult(
        True,
        "Аукцион заморожен",
        auction_id=auction.id,
        seller_tg_user_id=seller.tg_user_id if seller else None,
    )


async def unfreeze_auction(
    session: AsyncSession,
    *,
    actor_user_id: int,
    auction_id: uuid.UUID,
    reason: str,
) -> ModerationResult:
    auction = await _get_auction_for_update(session, auction_id)
    if auction is None:
        return ModerationResult(False, "Аукцион не найден")

    if auction.status != AuctionStatus.FROZEN:
        return ModerationResult(False, "Разморозить можно только замороженный аукцион", auction_id=auction.id)

    auction.status = AuctionStatus.ACTIVE
    auction.updated_at = datetime.now(UTC)

    seller = await session.scalar(select(User).where(User.id == auction.seller_user_id))
    await _log_action(
        session,
        actor_user_id=actor_user_id,
        action=ModerationAction.UNFREEZE_AUCTION,
        reason=reason,
        auction_id=auction.id,
    )
    return ModerationResult(
        True,
        "Аукцион разморожен",
        auction_id=auction.id,
        seller_tg_user_id=seller.tg_user_id if seller else None,
    )


async def end_auction(
    session: AsyncSession,
    *,
    actor_user_id: int,
    auction_id: uuid.UUID,
    reason: str,
) -> ModerationResult:
    auction = await _get_auction_for_update(session, auction_id)
    if auction is None:
        return ModerationResult(False, "Аукцион не найден")

    if auction.status not in {AuctionStatus.ACTIVE, AuctionStatus.FROZEN}:
        return ModerationResult(False, "Завершить можно только активный/замороженный аукцион")

    top_bid = await _top_bid(session, auction.id)
    winner_user: User | None = None
    if top_bid is not None:
        auction.winner_user_id = top_bid.user_id
        winner_user = await session.scalar(select(User).where(User.id == top_bid.user_id))
    else:
        auction.winner_user_id = None

    now = datetime.now(UTC)
    auction.status = AuctionStatus.ENDED
    auction.ends_at = now
    auction.updated_at = now

    seller = await session.scalar(select(User).where(User.id == auction.seller_user_id))
    await _log_action(
        session,
        actor_user_id=actor_user_id,
        action=ModerationAction.END_AUCTION,
        reason=reason,
        auction_id=auction.id,
    )

    return ModerationResult(
        True,
        "Аукцион завершен модератором",
        auction_id=auction.id,
        seller_tg_user_id=seller.tg_user_id if seller else None,
        winner_tg_user_id=winner_user.tg_user_id if winner_user else None,
    )


async def remove_bid(
    session: AsyncSession,
    *,
    actor_user_id: int,
    bid_id: uuid.UUID,
    reason: str,
) -> ModerationResult:
    bid = await session.scalar(select(Bid).where(Bid.id == bid_id).with_for_update())
    if bid is None:
        return ModerationResult(False, "Ставка не найдена")

    if bid.is_removed:
        return ModerationResult(False, "Ставка уже удалена")

    bid.is_removed = True
    bid.removed_reason = reason
    bid.removed_by_user_id = actor_user_id

    auction = await _get_auction_for_update(session, bid.auction_id)
    if auction is None:
        return ModerationResult(False, "Аукцион по ставке не найден")

    top_bid = await _top_bid(session, auction.id)
    winner_user: User | None = None

    if auction.status in {AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}:
        if top_bid is None:
            auction.winner_user_id = None
        else:
            auction.winner_user_id = top_bid.user_id
            winner_user = await session.scalar(select(User).where(User.id == top_bid.user_id))

    auction.updated_at = datetime.now(UTC)

    await _log_action(
        session,
        actor_user_id=actor_user_id,
        action=ModerationAction.REMOVE_BID,
        reason=reason,
        auction_id=auction.id,
        bid_id=bid.id,
        target_user_id=bid.user_id,
        payload={"amount": bid.amount},
    )

    target_user = await session.scalar(select(User).where(User.id == bid.user_id))
    return ModerationResult(
        True,
        "Ставка удалена",
        auction_id=auction.id,
        winner_tg_user_id=winner_user.tg_user_id if winner_user else None,
        target_tg_user_id=target_user.tg_user_id if target_user else None,
    )


async def ban_user(
    session: AsyncSession,
    *,
    actor_user_id: int,
    target_tg_user_id: int,
    reason: str,
) -> ModerationResult:
    target_user = await session.scalar(select(User).where(User.tg_user_id == target_tg_user_id))
    if target_user is None:
        target_user = User(tg_user_id=target_tg_user_id)
        session.add(target_user)
        await session.flush()

    now = datetime.now(UTC)
    entry = await session.scalar(
        select(BlacklistEntry).where(BlacklistEntry.user_id == target_user.id).with_for_update()
    )

    if entry is None:
        entry = BlacklistEntry(
            user_id=target_user.id,
            reason=reason,
            created_by_user_id=actor_user_id,
            is_active=True,
        )
        session.add(entry)
    else:
        if entry.is_active and (entry.expires_at is None or entry.expires_at > now):
            return ModerationResult(False, "Пользователь уже в бане", target_tg_user_id=target_tg_user_id)
        entry.reason = reason
        entry.created_by_user_id = actor_user_id
        entry.created_at = now
        entry.expires_at = None
        entry.is_active = True

    await _log_action(
        session,
        actor_user_id=actor_user_id,
        action=ModerationAction.BAN_USER,
        reason=reason,
        target_user_id=target_user.id,
    )
    return ModerationResult(True, "Пользователь заблокирован", target_tg_user_id=target_tg_user_id)


async def unban_user(
    session: AsyncSession,
    *,
    actor_user_id: int,
    target_tg_user_id: int,
    reason: str,
) -> ModerationResult:
    target_user = await session.scalar(select(User).where(User.tg_user_id == target_tg_user_id))
    if target_user is None:
        return ModerationResult(False, "Пользователь не найден")

    entry = await session.scalar(
        select(BlacklistEntry).where(BlacklistEntry.user_id == target_user.id).with_for_update()
    )
    if entry is None or not entry.is_active:
        return ModerationResult(False, "Пользователь не находится в бане", target_tg_user_id=target_tg_user_id)

    entry.is_active = False

    await _log_action(
        session,
        actor_user_id=actor_user_id,
        action=ModerationAction.UNBAN_USER,
        reason=reason,
        target_user_id=target_user.id,
    )
    return ModerationResult(True, "Пользователь разблокирован", target_tg_user_id=target_tg_user_id)


async def list_recent_bids(session: AsyncSession, auction_id: uuid.UUID, limit: int = 10) -> list[BidListItem]:
    rows = (
        await session.execute(
            select(Bid, User)
            .join(User, User.id == Bid.user_id)
            .where(Bid.auction_id == auction_id)
            .order_by(Bid.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        BidListItem(
            bid_id=bid.id,
            amount=bid.amount,
            created_at=bid.created_at,
            tg_user_id=user.tg_user_id,
            username=user.username,
            is_removed=bid.is_removed,
        )
        for bid, user in rows
    ]


async def list_moderation_logs(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID | None,
    limit: int = 15,
) -> list[ModerationLog]:
    stmt = select(ModerationLog).order_by(ModerationLog.created_at.desc()).limit(limit)
    if auction_id is not None:
        stmt = stmt.where(ModerationLog.auction_id == auction_id)
    return (await session.execute(stmt)).scalars().all()
