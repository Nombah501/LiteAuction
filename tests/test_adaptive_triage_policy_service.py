from __future__ import annotations

from app.services.adaptive_triage_policy_service import (
    AdaptiveDepthReasonCode,
    AdaptiveDetailDepth,
    TriagePriorityLevel,
    TriageRiskLevel,
    adaptive_queue_depth_policies,
    decide_adaptive_detail_depth,
)


def test_policy_surface_is_bounded_to_inline_summary_and_full() -> None:
    assert sorted(member.value for member in AdaptiveDetailDepth) == [
        "inline_full",
        "inline_summary",
    ]


def test_operator_override_wins_over_risk_and_priority_rules() -> None:
    decision = decide_adaptive_detail_depth(
        queue_key="complaints",
        risk_level=TriageRiskLevel.CRITICAL,
        priority_level=TriagePriorityLevel.URGENT,
        operator_override="summary",
    )

    assert decision.depth == AdaptiveDetailDepth.INLINE_SUMMARY
    assert decision.reason_code == AdaptiveDepthReasonCode.OPERATOR_OVERRIDE
    assert decision.fallback_applied is False


def test_risk_rule_auto_expands_for_complaints_queue() -> None:
    decision = decide_adaptive_detail_depth(
        queue_key="complaints",
        risk_level="high",
        priority_level="normal",
    )

    assert decision.depth == AdaptiveDetailDepth.INLINE_FULL
    assert decision.reason_code == AdaptiveDepthReasonCode.RISK_AUTO_EXPAND
    assert decision.fallback_applied is False


def test_priority_rule_auto_expands_for_complaints_queue() -> None:
    decision = decide_adaptive_detail_depth(
        queue_key="complaints",
        risk_level="low",
        priority_level="urgent",
    )

    assert decision.depth == AdaptiveDetailDepth.INLINE_FULL
    assert decision.reason_code == AdaptiveDepthReasonCode.PRIORITY_AUTO_EXPAND


def test_risk_and_priority_rule_reports_combined_reason() -> None:
    decision = decide_adaptive_detail_depth(
        queue_key="signals",
        risk_level="medium",
        priority_level="high",
    )

    assert decision.depth == AdaptiveDetailDepth.INLINE_FULL
    assert decision.reason_code == AdaptiveDepthReasonCode.RISK_AND_PRIORITY_AUTO_EXPAND


def test_default_collapsed_for_low_priority_low_risk() -> None:
    decision = decide_adaptive_detail_depth(
        queue_key="appeals",
        risk_level="low",
        priority_level="normal",
    )

    assert decision.depth == AdaptiveDetailDepth.INLINE_SUMMARY
    assert decision.reason_code == AdaptiveDepthReasonCode.DEFAULT_COLLAPSED
    assert decision.fallback_applied is False


def test_unknown_queue_uses_fallback_default_reason() -> None:
    decision = decide_adaptive_detail_depth(
        queue_key="not-a-real-queue",
        risk_level="critical",
        priority_level="urgent",
    )

    assert decision.depth == AdaptiveDetailDepth.INLINE_SUMMARY
    assert decision.reason_code == AdaptiveDepthReasonCode.FALLBACK_DEFAULT
    assert decision.fallback_applied is True
    assert "unknown_queue" in decision.fallback_notes


def test_invalid_tokens_record_fallback_notes() -> None:
    decision = decide_adaptive_detail_depth(
        queue_key="complaints",
        risk_level="weird",
        priority_level="fastest",
        operator_override="unknown",
    )

    assert decision.depth == AdaptiveDetailDepth.INLINE_SUMMARY
    assert decision.reason_code == AdaptiveDepthReasonCode.FALLBACK_DEFAULT
    assert decision.fallback_applied is True
    assert set(decision.fallback_notes) == {
        "invalid_override",
        "invalid_risk",
        "invalid_priority",
    }


def test_trade_feedback_requires_critical_risk_or_urgent_priority_for_auto_expand() -> None:
    medium_risk = decide_adaptive_detail_depth(
        queue_key="trade_feedback",
        risk_level="medium",
        priority_level="normal",
    )
    critical_risk = decide_adaptive_detail_depth(
        queue_key="trade_feedback",
        risk_level="critical",
        priority_level="normal",
    )

    assert medium_risk.depth == AdaptiveDetailDepth.INLINE_SUMMARY
    assert medium_risk.reason_code == AdaptiveDepthReasonCode.DEFAULT_COLLAPSED
    assert critical_risk.depth == AdaptiveDetailDepth.INLINE_FULL
    assert critical_risk.reason_code == AdaptiveDepthReasonCode.RISK_AUTO_EXPAND


def test_policy_registry_exposes_known_moderation_queues() -> None:
    registry = adaptive_queue_depth_policies()

    assert sorted(registry) == ["appeals", "complaints", "signals", "trade_feedback"]
