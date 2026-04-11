from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import AuctionStatus, ReputationTier
from app.db.models import Auction
from app.services.guarantor_service import has_assigned_guarantor_request
from app.services.reputation_service import get_or_create_reputation
from app.services.runtime_settings_service import resolve_runtime_setting_value


@dataclass(slots=True, frozen=True)
class SellerPublishGateResult:
    allowed: bool
    risk_level: str
    risk_score: int
    risk_reasons: tuple[str, ...]
    block_message: str | None = None


async def evaluate_seller_publish_gate(
    session: AsyncSession,
    *,
    seller_user_id: int,
) -> SellerPublishGateResult:
    reputation = await get_or_create_reputation(session, seller_user_id)
    tier = reputation.tier
    score = reputation.score

    if tier in {ReputationTier.PLATINUM, ReputationTier.GOLD, ReputationTier.SILVER}:
        return SellerPublishGateResult(
            allowed=True,
            risk_level=tier,
            risk_score=score,
            risk_reasons=(),
        )

    if tier == ReputationTier.BRONZE:
        has_assigned = await has_assigned_guarantor_request(
            session,
            submitter_user_id=seller_user_id,
            max_age_days=max(
                int(await resolve_runtime_setting_value(session, "publish_guarantor_assignment_max_age_days")),
                0,
            ),
        )
        if has_assigned:
            return SellerPublishGateResult(
                allowed=True,
                risk_level=tier,
                risk_score=score,
                risk_reasons=(),
            )
        return SellerPublishGateResult(
            allowed=False,
            risk_level=tier,
            risk_score=score,
            risk_reasons=("no_guarantor",),
            block_message=(
                f"Публикация ограничена. Ваш уровень: BRONZE (score: {score}).\n"
                "Для публикации нужен назначенный гарант → /guarant\n"
                "Повысить уровень: завершите сделки без жалоб."
            ),
        )

    has_assigned = await has_assigned_guarantor_request(
        session,
        submitter_user_id=seller_user_id,
        max_age_days=365,
    )
    completed_statuses = (AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT)
    completed_count = int(
        await session.scalar(
            select(func.count(Auction.id)).where(
                Auction.seller_user_id == seller_user_id,
                Auction.status.in_(completed_statuses),
            )
        )
        or 0
    )
    won_count = int(
        await session.scalar(
            select(func.count(Auction.id)).where(
                Auction.winner_user_id == seller_user_id,
                Auction.status.in_(completed_statuses),
            )
        )
        or 0
    )
    total_deals = completed_count + won_count

    if has_assigned and total_deals >= 1:
        return SellerPublishGateResult(
            allowed=True,
            risk_level=tier,
            risk_score=score,
            risk_reasons=(),
        )

    parts = [f"Публикация ограничена. Ваш уровень: NEW (score: {score})."]
    if not has_assigned:
        parts.append("Для публикации нужен гарант → /guarant")
    if total_deals < 1:
        parts.append("Нужна хотя бы 1 завершённая сделка (проданный или выигранный аукцион).")
    parts.append("Повысить уровень: завершите сделки без жалоб.")

    return SellerPublishGateResult(
        allowed=False,
        risk_level=tier,
        risk_score=score,
        risk_reasons=("new_tier",),
        block_message="\n".join(parts),
    )
