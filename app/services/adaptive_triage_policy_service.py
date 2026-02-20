from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AdaptiveDetailDepth(StrEnum):
    INLINE_SUMMARY = "inline_summary"
    INLINE_FULL = "inline_full"


class AdaptiveDepthReasonCode(StrEnum):
    OPERATOR_OVERRIDE = "operator_override"
    RISK_AUTO_EXPAND = "risk_auto_expand"
    PRIORITY_AUTO_EXPAND = "priority_auto_expand"
    RISK_AND_PRIORITY_AUTO_EXPAND = "risk_and_priority_auto_expand"
    DEFAULT_COLLAPSED = "default_collapsed"
    FALLBACK_DEFAULT = "fallback_default"


class TriageRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TriagePriorityLevel(StrEnum):
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


@dataclass(frozen=True, slots=True)
class AdaptiveQueueDepthPolicy:
    queue_key: str
    default_depth: AdaptiveDetailDepth
    auto_expand_risk_levels: frozenset[TriageRiskLevel]
    auto_expand_priority_levels: frozenset[TriagePriorityLevel]


@dataclass(frozen=True, slots=True)
class AdaptiveDetailDepthDecision:
    depth: AdaptiveDetailDepth
    reason_code: AdaptiveDepthReasonCode
    queue_key: str
    fallback_applied: bool
    fallback_notes: tuple[str, ...]


_GLOBAL_DEFAULT_DEPTH = AdaptiveDetailDepth.INLINE_SUMMARY

_QUEUE_POLICIES: dict[str, AdaptiveQueueDepthPolicy] = {
    "complaints": AdaptiveQueueDepthPolicy(
        queue_key="complaints",
        default_depth=_GLOBAL_DEFAULT_DEPTH,
        auto_expand_risk_levels=frozenset({TriageRiskLevel.HIGH, TriageRiskLevel.CRITICAL}),
        auto_expand_priority_levels=frozenset(
            {TriagePriorityLevel.HIGH, TriagePriorityLevel.URGENT}
        ),
    ),
    "signals": AdaptiveQueueDepthPolicy(
        queue_key="signals",
        default_depth=_GLOBAL_DEFAULT_DEPTH,
        auto_expand_risk_levels=frozenset(
            {TriageRiskLevel.MEDIUM, TriageRiskLevel.HIGH, TriageRiskLevel.CRITICAL}
        ),
        auto_expand_priority_levels=frozenset(
            {TriagePriorityLevel.HIGH, TriagePriorityLevel.URGENT}
        ),
    ),
    "trade_feedback": AdaptiveQueueDepthPolicy(
        queue_key="trade_feedback",
        default_depth=_GLOBAL_DEFAULT_DEPTH,
        auto_expand_risk_levels=frozenset({TriageRiskLevel.CRITICAL}),
        auto_expand_priority_levels=frozenset({TriagePriorityLevel.URGENT}),
    ),
    "appeals": AdaptiveQueueDepthPolicy(
        queue_key="appeals",
        default_depth=_GLOBAL_DEFAULT_DEPTH,
        auto_expand_risk_levels=frozenset({TriageRiskLevel.HIGH, TriageRiskLevel.CRITICAL}),
        auto_expand_priority_levels=frozenset({TriagePriorityLevel.URGENT}),
    ),
}


def adaptive_queue_depth_policies() -> dict[str, AdaptiveQueueDepthPolicy]:
    return dict(_QUEUE_POLICIES)


def decide_adaptive_detail_depth(
    *,
    queue_key: str | None,
    risk_level: str | TriageRiskLevel | None,
    priority_level: str | TriagePriorityLevel | None,
    operator_override: str | AdaptiveDetailDepth | None = None,
) -> AdaptiveDetailDepthDecision:
    fallback_notes: list[str] = []
    normalized_queue = _normalize_queue_key(queue_key)
    if normalized_queue is None:
        fallback_notes.append("unknown_queue")
        normalized_queue = "default"
    elif normalized_queue not in _QUEUE_POLICIES:
        fallback_notes.append("unknown_queue")

    policy = _QUEUE_POLICIES.get(normalized_queue)
    if policy is None:
        policy = AdaptiveQueueDepthPolicy(
            queue_key=normalized_queue,
            default_depth=_GLOBAL_DEFAULT_DEPTH,
            auto_expand_risk_levels=frozenset(),
            auto_expand_priority_levels=frozenset(),
        )

    override = _normalize_override(operator_override)
    if operator_override is not None and override is None:
        fallback_notes.append("invalid_override")
    if override is not None:
        return AdaptiveDetailDepthDecision(
            depth=override,
            reason_code=AdaptiveDepthReasonCode.OPERATOR_OVERRIDE,
            queue_key=policy.queue_key,
            fallback_applied=bool(fallback_notes),
            fallback_notes=tuple(fallback_notes),
        )

    risk = _normalize_risk_level(risk_level)
    if risk_level is not None and risk is None:
        fallback_notes.append("invalid_risk")
    priority = _normalize_priority_level(priority_level)
    if priority_level is not None and priority is None:
        fallback_notes.append("invalid_priority")

    risk_hit = risk in policy.auto_expand_risk_levels if risk is not None else False
    priority_hit = (
        priority in policy.auto_expand_priority_levels if priority is not None else False
    )

    if risk_hit and priority_hit:
        return AdaptiveDetailDepthDecision(
            depth=AdaptiveDetailDepth.INLINE_FULL,
            reason_code=AdaptiveDepthReasonCode.RISK_AND_PRIORITY_AUTO_EXPAND,
            queue_key=policy.queue_key,
            fallback_applied=bool(fallback_notes),
            fallback_notes=tuple(fallback_notes),
        )

    if risk_hit:
        return AdaptiveDetailDepthDecision(
            depth=AdaptiveDetailDepth.INLINE_FULL,
            reason_code=AdaptiveDepthReasonCode.RISK_AUTO_EXPAND,
            queue_key=policy.queue_key,
            fallback_applied=bool(fallback_notes),
            fallback_notes=tuple(fallback_notes),
        )

    if priority_hit:
        return AdaptiveDetailDepthDecision(
            depth=AdaptiveDetailDepth.INLINE_FULL,
            reason_code=AdaptiveDepthReasonCode.PRIORITY_AUTO_EXPAND,
            queue_key=policy.queue_key,
            fallback_applied=bool(fallback_notes),
            fallback_notes=tuple(fallback_notes),
        )

    reason_code = AdaptiveDepthReasonCode.DEFAULT_COLLAPSED
    if fallback_notes:
        reason_code = AdaptiveDepthReasonCode.FALLBACK_DEFAULT
    return AdaptiveDetailDepthDecision(
        depth=policy.default_depth,
        reason_code=reason_code,
        queue_key=policy.queue_key,
        fallback_applied=bool(fallback_notes),
        fallback_notes=tuple(fallback_notes),
    )


def _normalize_queue_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = str(raw).strip().lower()
    if not key:
        return None
    return key


def _normalize_risk_level(raw: str | TriageRiskLevel | None) -> TriageRiskLevel | None:
    if raw is None:
        return None
    if isinstance(raw, TriageRiskLevel):
        return raw
    token = str(raw).strip().lower()
    if token in {"critical", "crit", "p0"}:
        return TriageRiskLevel.CRITICAL
    if token in {"high", "p1"}:
        return TriageRiskLevel.HIGH
    if token in {"medium", "med", "p2"}:
        return TriageRiskLevel.MEDIUM
    if token in {"low", "p3"}:
        return TriageRiskLevel.LOW
    return None


def _normalize_priority_level(
    raw: str | TriagePriorityLevel | None,
) -> TriagePriorityLevel | None:
    if raw is None:
        return None
    if isinstance(raw, TriagePriorityLevel):
        return raw
    token = str(raw).strip().lower()
    if token in {"urgent", "critical", "p0"}:
        return TriagePriorityLevel.URGENT
    if token in {"high", "p1"}:
        return TriagePriorityLevel.HIGH
    if token in {"normal", "medium", "med", "default", "p2", "p3"}:
        return TriagePriorityLevel.NORMAL
    return None


def _normalize_override(
    raw: str | AdaptiveDetailDepth | None,
) -> AdaptiveDetailDepth | None:
    if raw is None:
        return None
    if isinstance(raw, AdaptiveDetailDepth):
        return raw
    token = str(raw).strip().lower()
    if token in {"inline_full", "full", "expanded"}:
        return AdaptiveDetailDepth.INLINE_FULL
    if token in {"inline_summary", "summary", "compact"}:
        return AdaptiveDetailDepth.INLINE_SUMMARY
    return None
