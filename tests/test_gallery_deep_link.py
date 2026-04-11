from __future__ import annotations

import uuid

from app.bot.handlers.start import _extract_gallery_auction_id


def test_extract_gallery_auction_id_valid() -> None:
    uid = uuid.uuid4()
    result = _extract_gallery_auction_id(f"gallery_{uid}")
    assert result == uid


def test_extract_gallery_auction_id_invalid() -> None:
    result = _extract_gallery_auction_id("gallery_not-a-uuid")
    assert result is None


def test_extract_gallery_auction_id_wrong_prefix() -> None:
    result = _extract_gallery_auction_id("report_abc")
    assert result is None


def test_extract_gallery_auction_id_none() -> None:
    result = _extract_gallery_auction_id(None)
    assert result is None
