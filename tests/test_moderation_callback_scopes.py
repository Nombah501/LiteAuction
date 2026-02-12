from __future__ import annotations

from app.bot.handlers.moderation import _complaint_action_required_scope, _risk_action_required_scope
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
