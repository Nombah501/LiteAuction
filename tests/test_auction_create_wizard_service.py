from __future__ import annotations

from typing import Any, cast

import pytest

from app.services.auction_create_wizard_service import (
    WIZARD_STEP_WAITING_ANTI_SNIPER,
    WIZARD_STEP_WAITING_BUYOUT_PRICE,
    WIZARD_STEP_WAITING_PHOTO,
    render_create_wizard_text,
    upsert_create_wizard_progress,
)


class _DummyState:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    async def update_data(self, data: dict[str, object] | None = None, **kwargs) -> None:
        if isinstance(data, dict):
            self.data.update(data)
        self.data.update(kwargs)

    async def get_data(self) -> dict[str, object]:
        return dict(self.data)


class _DummyChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class _SentMessage:
    def __init__(self, *, chat_id: int, message_id: int) -> None:
        self.chat = _DummyChat(chat_id)
        self.message_id = message_id


class _DummyMessage:
    def __init__(self) -> None:
        self.sent_texts: list[str] = []

    async def answer(self, text: str, **_kwargs):
        self.sent_texts.append(text)
        return _SentMessage(chat_id=77, message_id=100 + len(self.sent_texts))


def test_render_create_wizard_text_shows_progress_and_skip() -> None:
    text = render_create_wizard_text(
        data={
            "photo_file_ids": ["1", "2"],
            "description": "  Редкий лот   с длинным    описанием  ",
            "start_price": 100,
            "buyout_price": None,
        },
        step_name=WIZARD_STEP_WAITING_BUYOUT_PRICE,
        hint="Введите цену выкупа",
        error="Неверный формат",
    )

    assert "Создание лота · шаг 4/7" in text
    assert "57%" in text
    assert "[x] Фото" in text
    assert "[>] Выкуп" in text
    assert "Пропущен" in text
    assert "<b>Ошибка:</b> Неверный формат" in text
    assert "<b>Сейчас:</b> Введите цену выкупа" in text


def test_render_create_wizard_text_finished_mode() -> None:
    text = render_create_wizard_text(
        data={
            "photo_file_ids": ["1"],
            "description": "Test",
            "start_price": 10,
            "min_step": 1,
            "duration_hours": 12,
            "anti_sniper_enabled": True,
        },
        step_name=WIZARD_STEP_WAITING_ANTI_SNIPER,
        hint="Черновик создан",
        finished=True,
    )

    assert "Создание лота завершено" in text
    assert "шаг 7/7" in text
    assert "100%" in text
    assert "[x] Антиснайпер" in text
    assert "Включен" in text


@pytest.mark.asyncio
async def test_upsert_create_wizard_progress_skips_duplicate_payload() -> None:
    state = _DummyState()
    message = _DummyMessage()

    await upsert_create_wizard_progress(
        state=cast(Any, state),
        bot=None,
        anchor_message=cast(Any, message),
        step_name=WIZARD_STEP_WAITING_PHOTO,
        hint="Добавьте фото",
    )
    await upsert_create_wizard_progress(
        state=cast(Any, state),
        bot=None,
        anchor_message=cast(Any, message),
        step_name=WIZARD_STEP_WAITING_PHOTO,
        hint="Добавьте фото",
    )

    assert len(message.sent_texts) == 1
