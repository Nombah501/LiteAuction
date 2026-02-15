from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class UserRiskSnapshot:
    score: int
    level: str
    reasons: tuple[str, ...]


def format_risk_reason_label(reason_code: str) -> str:
    labels = {
        "ACTIVE_BLACKLIST": "Активный бан",
        "OPEN_FRAUD_SIGNAL": "Есть открытые фрод-сигналы",
        "COMPLAINTS_AGAINST_3PLUS": "3+ жалобы на пользователя",
        "COMPLAINTS_AGAINST": "Есть жалобы на пользователя",
        "REMOVED_BIDS_3PLUS": "3+ снятые ставки",
        "REMOVED_BIDS": "Есть снятые ставки",
    }
    return labels.get(reason_code, reason_code)


def evaluate_user_risk_snapshot(
    *,
    complaints_against: int,
    open_fraud_signals: int,
    has_active_blacklist: bool,
    removed_bids: int,
    is_verified_user: bool = False,
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

    if is_verified_user and not has_active_blacklist and open_fraud_signals == 0:
        score = max(score - 10, 0)

    score = min(score, 100)

    if score >= 70:
        level = "HIGH"
    elif score >= 35:
        level = "MEDIUM"
    else:
        level = "LOW"

    return UserRiskSnapshot(score=score, level=level, reasons=tuple(reasons))
