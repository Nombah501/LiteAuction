from __future__ import annotations

import uuid

from app.bot.handlers.post_auction import parse_deal_callback


def test_parse_deal_callback_write() -> None:
    action, auction_id = parse_deal_callback("deal:write:a1b2c3d4-5678-9012-abcd-ef1234567890")
    assert action == "write"
    assert auction_id == uuid.UUID("a1b2c3d4-5678-9012-abcd-ef1234567890")


def test_parse_deal_callback_guarant() -> None:
    action, auction_id = parse_deal_callback("deal:guarant:a1b2c3d4-5678-9012-abcd-ef1234567890")
    assert action == "guarant"
    assert auction_id == uuid.UUID("a1b2c3d4-5678-9012-abcd-ef1234567890")


def test_parse_deal_callback_feedback() -> None:
    action, auction_id = parse_deal_callback("deal:feedback:a1b2c3d4-5678-9012-abcd-ef1234567890")
    assert action == "feedback"
    assert auction_id == uuid.UUID("a1b2c3d4-5678-9012-abcd-ef1234567890")


def test_parse_deal_callback_republish() -> None:
    action, auction_id = parse_deal_callback("deal:republish:a1b2c3d4-5678-9012-abcd-ef1234567890")
    assert action == "republish"
    assert auction_id == uuid.UUID("a1b2c3d4-5678-9012-abcd-ef1234567890")
