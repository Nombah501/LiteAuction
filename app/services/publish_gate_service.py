from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Bid, BlacklistEntry, Complaint, FraudSignal
from app.services.guarantor_service import has_assigned_guarantor_request
from app.services.risk_eval_service import evaluate_user_risk_snapshot, format_risk_reason_label


@dataclass(slots=True, frozen=True)
class SellerPublishGateResult:
    allowed: bool
    risk_level: str
    risk_score: int
    risk_reasons: tuple[str, ...]
    block_message: str | None = None


def _build_block_message(*, risk_score: int, risk_reasons: tuple[str, ...]) -> str:
    factors = ", ".join(format_risk_reason_label(code) for code in risk_reasons) if risk_reasons else "без детализации"
    return (
        "Публикация лота временно ограничена: высокий риск-профиль продавца "
        f"(score={risk_score}). Факторы: {factors}.\n"
        "Для публикации нужен назначенный гарант. Отправьте /guarant в личном чате с ботом."
    )


async def evaluate_seller_publish_gate(
    session: AsyncSession,
    *,
    seller_user_id: int,
) -> SellerPublishGateResult:
    complaints_against = int(
        await session.scalar(select(func.count(Complaint.id)).where(Complaint.target_user_id == seller_user_id))
        or 0
    )
    open_fraud_signals = int(
        await session.scalar(
            select(func.count(FraudSignal.id)).where(
                FraudSignal.user_id == seller_user_id,
                FraudSignal.status == "OPEN",
            )
        )
        or 0
    )
    has_active_blacklist = (
        await session.scalar(
            select(BlacklistEntry.id).where(
                BlacklistEntry.user_id == seller_user_id,
                BlacklistEntry.is_active.is_(True),
            )
        )
        is not None
    )
    removed_bids = int(
        await session.scalar(
            select(func.count(Bid.id)).where(
                Bid.user_id == seller_user_id,
                Bid.is_removed.is_(True),
            )
        )
        or 0
    )

    risk = evaluate_user_risk_snapshot(
        complaints_against=complaints_against,
        open_fraud_signals=open_fraud_signals,
        has_active_blacklist=has_active_blacklist,
        removed_bids=removed_bids,
    )

    if not settings.publish_high_risk_requires_guarantor:
        return SellerPublishGateResult(
            allowed=True,
            risk_level=risk.level,
            risk_score=risk.score,
            risk_reasons=risk.reasons,
        )

    if risk.level != "HIGH":
        return SellerPublishGateResult(
            allowed=True,
            risk_level=risk.level,
            risk_score=risk.score,
            risk_reasons=risk.reasons,
        )

    has_assigned = await has_assigned_guarantor_request(
        session,
        submitter_user_id=seller_user_id,
        max_age_days=max(settings.publish_guarantor_assignment_max_age_days, 0),
    )
    if has_assigned:
        return SellerPublishGateResult(
            allowed=True,
            risk_level=risk.level,
            risk_score=risk.score,
            risk_reasons=risk.reasons,
        )

    return SellerPublishGateResult(
        allowed=False,
        risk_level=risk.level,
        risk_score=risk.score,
        risk_reasons=risk.reasons,
        block_message=_build_block_message(risk_score=risk.score, risk_reasons=risk.reasons),
    )
