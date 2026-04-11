from __future__ import annotations

from app.bot.handlers.my_reputation import format_tier_badge, build_reputation_text
from app.db.enums import ReputationTier


def test_format_tier_badge_new() -> None:
    assert format_tier_badge(ReputationTier.NEW) == ""


def test_format_tier_badge_bronze() -> None:
    assert "🥉" in format_tier_badge(ReputationTier.BRONZE)


def test_format_tier_badge_silver() -> None:
    assert "🥈" in format_tier_badge(ReputationTier.SILVER)


def test_format_tier_badge_gold() -> None:
    assert "🥇" in format_tier_badge(ReputationTier.GOLD)


def test_format_tier_badge_platinum() -> None:
    assert "💎" in format_tier_badge(ReputationTier.PLATINUM)


def test_build_reputation_text_shows_score() -> None:
    text = build_reputation_text(
        tier=ReputationTier.GOLD,
        score=1250,
        deals_count=18,
        feedback_received=15,
        avg_rating=4.7,
        feedback_given=12,
        next_tier=ReputationTier.PLATINUM,
        next_tier_threshold=3000,
    )
    assert "1250" in text
    assert "GOLD" in text
    assert "18" in text
    assert "1750" in text


def test_build_reputation_text_max_tier() -> None:
    text = build_reputation_text(
        tier=ReputationTier.PLATINUM,
        score=5000,
        deals_count=100,
        feedback_received=90,
        avg_rating=4.9,
        feedback_given=80,
        next_tier=None,
        next_tier_threshold=None,
    )
    assert "PLATINUM" in text
    assert "Максимальный" in text or "максимальн" in text.lower()
