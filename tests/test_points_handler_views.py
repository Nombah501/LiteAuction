from __future__ import annotations

from app.bot.handlers.points import (
    POINTS_VIEW_COMPACT,
    POINTS_VIEW_DETAILED,
    _parse_points_options,
    _render_points_compact_text,
    _render_points_detailed_text,
)
from app.services.appeal_service import AppealPriorityBoostPolicy
from app.services.feedback_service import FeedbackPriorityBoostPolicy
from app.services.guarantor_service import GuarantorPriorityBoostPolicy
from app.services.points_service import UserPointsSummary


def _seed_policies() -> tuple[
    FeedbackPriorityBoostPolicy,
    GuarantorPriorityBoostPolicy,
    AppealPriorityBoostPolicy,
]:
    return (
        FeedbackPriorityBoostPolicy(
            enabled=True,
            cost_points=20,
            daily_limit=2,
            used_today=1,
            remaining_today=1,
            cooldown_seconds=60,
            cooldown_remaining_seconds=0,
        ),
        GuarantorPriorityBoostPolicy(
            enabled=True,
            cost_points=30,
            daily_limit=2,
            used_today=0,
            remaining_today=2,
            cooldown_seconds=120,
            cooldown_remaining_seconds=0,
        ),
        AppealPriorityBoostPolicy(
            enabled=False,
            cost_points=15,
            daily_limit=1,
            used_today=0,
            remaining_today=1,
            cooldown_seconds=30,
            cooldown_remaining_seconds=15,
        ),
    )


def test_parse_points_options_supports_modes_and_limit() -> None:
    assert _parse_points_options("/points") == (POINTS_VIEW_COMPACT, 5)
    assert _parse_points_options("/points detailed") == (POINTS_VIEW_DETAILED, 5)
    assert _parse_points_options("/points detailed 2") == (POINTS_VIEW_DETAILED, 2)
    assert _parse_points_options("/points 2 detailed") == (POINTS_VIEW_DETAILED, 2)
    assert _parse_points_options("/points compact 3") == (POINTS_VIEW_COMPACT, 3)

    assert _parse_points_options("/points detailed compact") is None
    assert _parse_points_options("/points 0") is None
    assert _parse_points_options("/points 1 2") is None


def test_render_points_compact_text_is_actionable(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "points_redemption_enabled", True)
    monkeypatch.setattr(settings, "points_redemption_daily_limit", 3)
    monkeypatch.setattr(settings, "points_redemption_weekly_limit", 5)
    monkeypatch.setattr(settings, "points_redemption_daily_spend_cap", 100)
    monkeypatch.setattr(settings, "points_redemption_weekly_spend_cap", 200)
    monkeypatch.setattr(settings, "points_redemption_monthly_spend_cap", 400)
    monkeypatch.setattr(settings, "points_redemption_min_balance", 10)
    monkeypatch.setattr(settings, "points_redemption_min_earned_points", 20)

    feedback_policy, guarantor_policy, appeal_policy = _seed_policies()
    text = _render_points_compact_text(
        summary=UserPointsSummary(balance=42, total_earned=80, total_spent=38, operations_count=5),
        entries=[],
        shown_limit=5,
        feedback_boost_policy=feedback_policy,
        guarantor_boost_policy=guarantor_policy,
        appeal_boost_policy=appeal_policy,
        redemptions_used_today=1,
        redemptions_used_this_week=2,
        redemptions_spent_today=20,
        redemptions_spent_this_week=35,
        redemptions_spent_this_month=70,
        cooldown_remaining_seconds=0,
        account_age_remaining_seconds=0,
    )

    assert "Баланс: 42 points" in text
    assert "Быстрые действия:" in text
    assert "Подробный режим: /points detailed" in text
    assert "Глобальный лимит бустов в день:" not in text


def test_render_points_detailed_text_keeps_policy_diagnostics(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "points_redemption_enabled", False)
    monkeypatch.setattr(settings, "points_redemption_daily_limit", 2)
    monkeypatch.setattr(settings, "points_redemption_weekly_limit", 4)
    monkeypatch.setattr(settings, "points_redemption_daily_spend_cap", 60)
    monkeypatch.setattr(settings, "points_redemption_weekly_spend_cap", 120)
    monkeypatch.setattr(settings, "points_redemption_monthly_spend_cap", 180)
    monkeypatch.setattr(settings, "points_redemption_min_balance", 15)
    monkeypatch.setattr(settings, "points_redemption_min_account_age_seconds", 3600)
    monkeypatch.setattr(settings, "points_redemption_min_earned_points", 40)
    monkeypatch.setattr(settings, "points_redemption_cooldown_seconds", 900)

    feedback_policy, guarantor_policy, appeal_policy = _seed_policies()
    text = _render_points_detailed_text(
        summary=UserPointsSummary(balance=18, total_earned=30, total_spent=12, operations_count=3),
        entries=[],
        shown_limit=5,
        feedback_boost_policy=feedback_policy,
        guarantor_boost_policy=guarantor_policy,
        appeal_boost_policy=appeal_policy,
        redemptions_used_today=1,
        redemptions_used_this_week=2,
        redemptions_spent_today=20,
        redemptions_spent_this_week=40,
        redemptions_spent_this_month=80,
        cooldown_remaining_seconds=120,
        account_age_remaining_seconds=180,
    )

    assert "Ваш баланс: 18 points" in text
    assert "Глобальный лимит бустов в день: 1/2 (осталось 1)" in text
    assert "Глобальный статус редимпшенов: временно отключены" in text
    assert "Минимум заработанных points для буста: 40 points" in text
    assert "До следующего буста: 120 сек" in text
