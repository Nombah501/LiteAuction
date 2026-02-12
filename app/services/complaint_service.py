from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Auction, Bid, Complaint, User


@dataclass(slots=True)
class ComplaintCreateResult:
    ok: bool
    message: str
    complaint: Complaint | None = None


@dataclass(slots=True)
class ComplaintView:
    complaint: Complaint
    auction: Auction
    reporter: User
    target_user: User | None
    target_bid: Bid | None


async def create_complaint(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID,
    reporter_user_id: int,
    reason: str,
) -> ComplaintCreateResult:
    auction = await session.scalar(select(Auction).where(Auction.id == auction_id))
    if auction is None:
        return ComplaintCreateResult(False, "Аукцион не найден")

    duplicate = await session.scalar(
        select(Complaint.id).where(
            Complaint.auction_id == auction_id,
            Complaint.reporter_user_id == reporter_user_id,
            Complaint.status == "OPEN",
        )
    )
    if duplicate is not None:
        return ComplaintCreateResult(False, "У вас уже есть открытая жалоба по этому аукциону")

    target_bid = await session.scalar(
        select(Bid)
        .where(Bid.auction_id == auction_id, Bid.is_removed.is_(False))
        .order_by(Bid.amount.desc(), Bid.created_at.asc())
        .limit(1)
    )

    complaint = Complaint(
        auction_id=auction_id,
        reporter_user_id=reporter_user_id,
        target_bid_id=target_bid.id if target_bid else None,
        target_user_id=target_bid.user_id if target_bid else None,
        reason=reason,
        status="OPEN",
    )
    session.add(complaint)
    await session.flush()
    return ComplaintCreateResult(True, "Жалоба отправлена модераторам", complaint=complaint)


async def load_complaint_view(
    session: AsyncSession,
    complaint_id: int,
    *,
    for_update: bool = False,
) -> ComplaintView | None:
    stmt = select(Complaint).where(Complaint.id == complaint_id)
    if for_update:
        stmt = stmt.with_for_update()
    complaint = await session.scalar(stmt)
    if complaint is None:
        return None

    auction = await session.scalar(select(Auction).where(Auction.id == complaint.auction_id))
    reporter = await session.scalar(select(User).where(User.id == complaint.reporter_user_id))
    if auction is None or reporter is None:
        return None

    target_user = None
    if complaint.target_user_id is not None:
        target_user = await session.scalar(select(User).where(User.id == complaint.target_user_id))

    target_bid = None
    if complaint.target_bid_id is not None:
        target_bid = await session.scalar(select(Bid).where(Bid.id == complaint.target_bid_id))

    return ComplaintView(
        complaint=complaint,
        auction=auction,
        reporter=reporter,
        target_user=target_user,
        target_bid=target_bid,
    )


def render_complaint_text(view: ComplaintView) -> str:
    reporter = f"@{view.reporter.username}" if view.reporter.username else str(view.reporter.tg_user_id)
    target = "не определен"
    if view.target_user is not None:
        target = f"@{view.target_user.username}" if view.target_user.username else str(view.target_user.tg_user_id)

    top_bid = "-"
    if view.target_bid is not None:
        top_bid = f"{view.target_bid.id} (${view.target_bid.amount})"

    return (
        f"Жалоба #{view.complaint.id}\n"
        f"Статус: {view.complaint.status}\n"
        f"Аукцион: {view.auction.id}\n"
        f"Репортер: {reporter}\n"
        f"Подозреваемый: {target}\n"
        f"Ставка: {top_bid}\n"
        f"Причина: {view.complaint.reason}"
    )


async def set_complaint_queue_message(
    session: AsyncSession,
    *,
    complaint_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    complaint = await session.scalar(select(Complaint).where(Complaint.id == complaint_id).with_for_update())
    if complaint is None:
        return
    complaint.queue_chat_id = chat_id
    complaint.queue_message_id = message_id


async def resolve_complaint(
    session: AsyncSession,
    *,
    complaint_id: int,
    resolver_user_id: int,
    status: str,
    note: str,
) -> Complaint | None:
    complaint = await session.scalar(select(Complaint).where(Complaint.id == complaint_id).with_for_update())
    if complaint is None:
        return None
    if complaint.status != "OPEN":
        return complaint

    complaint.status = status
    complaint.resolved_by_user_id = resolver_user_id
    complaint.resolution_note = note
    complaint.resolved_at = datetime.now(UTC)
    return complaint


async def count_open_complaints_for_auction(session: AsyncSession, auction_id: uuid.UUID) -> int:
    rows = await session.execute(
        select(Complaint.id).where(
            and_(
                Complaint.auction_id == auction_id,
                Complaint.status == "OPEN",
            )
        )
    )
    return len(rows.scalars().all())


async def list_complaints(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID | None,
    status: str | None,
    limit: int = 20,
    offset: int = 0,
) -> list[Complaint]:
    stmt = (
        select(Complaint)
        .order_by(Complaint.created_at.desc())
        .offset(max(offset, 0))
        .limit(max(limit, 1))
    )
    if auction_id is not None:
        stmt = stmt.where(Complaint.auction_id == auction_id)
    if status is not None:
        stmt = stmt.where(Complaint.status == status)
    return (await session.execute(stmt)).scalars().all()
