from __future__ import annotations

from datetime import datetime, timezone, timedelta

from app.bot.handlers.my_bids import parse_mybids_page_payload, format_time_left


def test_parse_mybids_page_payload_valid() -> None:
    result = parse_mybids_page_payload("mybids:page:3")
    assert result == 3


def test_parse_mybids_page_payload_default() -> None:
    result = parse_mybids_page_payload("mybids:page:0")
    assert result == 0


def test_parse_mybids_page_payload_invalid() -> None:
    result = parse_mybids_page_payload("mybids:page:abc")
    assert result == 0


def test_format_time_left_hours() -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=2, minutes=30, seconds=5)
    result = format_time_left(future)
    assert "2ч" in result
    assert "30м" in result


def test_format_time_left_minutes_only() -> None:
    future = datetime.now(timezone.utc) + timedelta(minutes=45, seconds=5)
    result = format_time_left(future)
    assert "45м" in result
