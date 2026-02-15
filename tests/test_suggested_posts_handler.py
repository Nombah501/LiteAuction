from __future__ import annotations

from app.bot.handlers.suggested_posts import _parse_decision_callback


def test_parse_suggested_post_approve_callback() -> None:
    parsed = _parse_decision_callback("spp:ap:42")

    assert parsed is not None
    assert parsed.review_id == 42
    assert parsed.approve is True
    assert parsed.decline_reason_code is None


def test_parse_suggested_post_decline_callback() -> None:
    parsed = _parse_decision_callback("spp:dc:11:rules")

    assert parsed is not None
    assert parsed.review_id == 11
    assert parsed.approve is False
    assert parsed.decline_reason_code == "rules"


def test_parse_suggested_post_callback_rejects_malformed_payload() -> None:
    assert _parse_decision_callback(None) is None
    assert _parse_decision_callback("spp:dc:11") is None
    assert _parse_decision_callback("spp:dc:abc:rules") is None
    assert _parse_decision_callback("spp:dc:11:unknown") is None
