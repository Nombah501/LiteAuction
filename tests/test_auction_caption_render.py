from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.db.enums import AuctionStatus
from app.services.auction_service import AuctionView, TopBidView, render_auction_caption


def _build_view(*, top_bids: list[TopBidView]) -> AuctionView:
    now = datetime.now(UTC)
    auction = SimpleNamespace(
        id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        status=AuctionStatus.ACTIVE,
        description="Легендарный нож в отличном состоянии",
        start_price=50,
        buyout_price=150,
        min_step=5,
        anti_sniper_enabled=True,
        anti_sniper_max_extensions=3,
        anti_sniper_extensions_used=1,
        ends_at=now + timedelta(hours=2),
    )
    seller = SimpleNamespace(username="sellername", first_name="Seller", tg_user_id=101)

    return AuctionView(
        auction=auction,
        seller=seller,
        winner=None,
        top_bids=top_bids,
        current_price=95,
        minimum_next_bid=100,
        open_complaints=2,
        photo_count=4,
    )


def test_render_auction_caption_contains_compact_emotional_sections() -> None:
    view = _build_view(
        top_bids=[
            TopBidView(
                amount=95,
                user_id=1,
                tg_user_id=11,
                username="anna",
                first_name="Anna",
                created_at=datetime.now(UTC),
            ),
            TopBidView(
                amount=90,
                user_id=2,
                tg_user_id=22,
                username=None,
                first_name="Vlad",
                created_at=datetime.now(UTC),
            ),
        ]
    )

    caption = render_auction_caption(view)

    assert "🔥 Аукцион #12345678" in caption
    assert "⚡ Торги в разгаре" in caption
    assert "💸 Текущая ставка: <b>$95</b>" in caption
    assert "⏭ Следующая ставка: <b>$100</b>" in caption
    assert "🖼 Фото: 4 | 🚨 Жалобы: 2" in caption
    assert "🏆 <b>Топ-3 ставок</b>" in caption
    assert "🥇 $95 — @anna" in caption
    assert "🥈 $90" in caption
    assert "🥉 —" in caption


def test_render_auction_caption_shows_empty_top_with_medals() -> None:
    view = _build_view(top_bids=[])

    caption = render_auction_caption(view)

    assert "🥇 —" in caption
    assert "🥈 —" in caption
    assert "🥉 —" in caption
