from __future__ import annotations

from app.bot.handlers.moderation import (
    _build_appeal_cta,
    _complaint_action_required_scope,
    _risk_action_required_scope,
)
from app.services.rbac_service import SCOPE_AUCTION_MANAGE, SCOPE_BID_MANAGE, SCOPE_USER_BAN


def test_complaint_callback_scope_mapping() -> None:
    assert _complaint_action_required_scope("freeze") == SCOPE_AUCTION_MANAGE
    assert _complaint_action_required_scope("dismiss") == SCOPE_BID_MANAGE
    assert _complaint_action_required_scope("rm_top") == SCOPE_BID_MANAGE
    assert _complaint_action_required_scope("ban_top") == SCOPE_USER_BAN
    assert _complaint_action_required_scope("unknown") is None


def test_risk_callback_scope_mapping() -> None:
    assert _risk_action_required_scope("freeze") == SCOPE_AUCTION_MANAGE
    assert _risk_action_required_scope("ignore") == SCOPE_BID_MANAGE
    assert _risk_action_required_scope("ban") == SCOPE_USER_BAN
    assert _risk_action_required_scope("unknown") is None


def test_appeal_cta_with_bot_username(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "bot_username", "liteauction_bot")

    cta_text, markup = _build_appeal_cta("risk_10")

    assert "кнопку ниже" in cta_text
    assert markup is not None
    assert markup.inline_keyboard[0][0].url == "https://t.me/liteauction_bot?start=appeal_risk_10"


def test_appeal_cta_without_bot_username(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "bot_username", "")

    cta_text, markup = _build_appeal_cta("complaint_20")

    assert "/start appeal_complaint_20" in cta_text
    assert markup is None
