from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class UserRiskSnapshot:
    score: int
    level: str
    reasons: tuple[str, ...]


def evaluate_user_risk_snapshot(
    *,
    complaints_against: int,
    open_fraud_signals: int,
    has_active_blacklist: bool,
    removed_bids: int,
) -> UserRiskSnapshot:
    score = 0
    reasons: list[str] = []

    if has_active_blacklist:
        score += 60
        reasons.append("ACTIVE_BLACKLIST")

    if open_fraud_signals > 0:
        score += 40
        reasons.append("OPEN_FRAUD_SIGNAL")

    if complaints_against >= 3:
        score += 30
        reasons.append("COMPLAINTS_AGAINST_3PLUS")
    elif complaints_against >= 1:
        score += 15
        reasons.append("COMPLAINTS_AGAINST")

    if removed_bids >= 3:
        score += 15
        reasons.append("REMOVED_BIDS_3PLUS")
    elif removed_bids >= 1:
        score += 8
        reasons.append("REMOVED_BIDS")

    score = min(score, 100)

    if score >= 70:
        level = "HIGH"
    elif score >= 35:
        level = "MEDIUM"
    else:
        level = "LOW"

    return UserRiskSnapshot(score=score, level=level, reasons=tuple(reasons))
