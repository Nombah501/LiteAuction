from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from aiogram.types import CallbackQuery

from app.bot.handlers.start import (
    _render_my_auctions_text,
    callback_dashboard_balance,
    callback_dashboard_settings,
)
from app.db.enums import AuctionStatus


class _DummyCallback:
    def __init__(self) -> None:
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str = "", show_alert: bool = False, **_kwargs) -> None:
        self.answers.append((text, show_alert))


def test_render_my_auctions_text_empty_state() -> None:
    text = _render_my_auctions_text([])

    assert "пока нет аукционов" in text
    assert "Создать аукцион" in text


def test_render_my_auctions_text_with_rows() -> None:
    rows = [
        (UUID("12345678-1234-5678-1234-567812345678"), AuctionStatus.ACTIVE, 50, 95),
        (UUID("87654321-4321-8765-4321-876543218765"), AuctionStatus.DRAFT, 40, None),
    ]

    text = _render_my_auctions_text(rows)

    assert "Мои аукционы (последние 10):" in text
    assert "#12345678 | Активен | текущая цена: $95" in text
    assert "#87654321 | Черновик | текущая цена: $40" in text


@pytest.mark.asyncio
async def test_dashboard_settings_callback_returns_in_development_alert() -> None:
    callback = _DummyCallback()

    await callback_dashboard_settings(cast(CallbackQuery, callback))

    assert callback.answers == [("Раздел «Настройки» в разработке.", True)]


@pytest.mark.asyncio
async def test_dashboard_balance_callback_returns_in_development_alert() -> None:
    callback = _DummyCallback()

    await callback_dashboard_balance(cast(CallbackQuery, callback))

    assert callback.answers == [("Раздел «Баланс» в разработке.", True)]
