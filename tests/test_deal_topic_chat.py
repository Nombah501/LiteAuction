from __future__ import annotations

import uuid

from app.bot.handlers.deal_topic_chat import parse_close_payload


def test_parse_close_payload_valid() -> None:
    auction_id = uuid.uuid4()
    result = parse_close_payload(f"deal:close:{auction_id}")
    assert result == auction_id


def test_parse_close_payload_invalid() -> None:
    result = parse_close_payload("deal:close:not-a-uuid")
    assert result is None


def test_parse_close_payload_wrong_prefix() -> None:
    result = parse_close_payload("deal:write:abc")
    assert result is None
