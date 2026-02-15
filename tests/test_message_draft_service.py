from __future__ import annotations

from aiogram.enums import ChatType
import pytest

from app.services.message_draft_service import send_progress_draft


class _DummyChat:
    def __init__(self, *, chat_type: ChatType, chat_id: int) -> None:
        self.type = chat_type
        self.id = chat_id


class _DummyMessage:
    def __init__(
        self,
        *,
        chat: _DummyChat,
        message_thread_id: int | None = None,
    ) -> None:
        self.chat = chat
        self.message_thread_id = message_thread_id


class _DummyBot:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send_message_draft(
        self,
        *,
        chat_id: int,
        draft_id: int,
        text: str,
        message_thread_id: int | None,
    ) -> bool:
        self.calls.append(
            {
                "chat_id": chat_id,
                "draft_id": draft_id,
                "text": text,
                "message_thread_id": message_thread_id,
            }
        )
        return True


@pytest.mark.asyncio
async def test_send_progress_draft_uses_private_thread(monkeypatch) -> None:
    async def _runtime_enabled(_key: str) -> bool:
        return True

    monkeypatch.setattr("app.services.message_draft_service.get_runtime_setting_value", _runtime_enabled)
    bot = _DummyBot()
    message = _DummyMessage(
        chat=_DummyChat(chat_type=ChatType.PRIVATE, chat_id=100),
        message_thread_id=42,
    )

    result = await send_progress_draft(
        bot,
        message,
        text="loading",
        scope_key="modstats",
    )

    assert result is True
    assert len(bot.calls) == 1
    assert bot.calls[0]["message_thread_id"] == 42


@pytest.mark.asyncio
async def test_send_progress_draft_skips_when_disabled(monkeypatch) -> None:
    async def _runtime_disabled(_key: str) -> bool:
        return False

    monkeypatch.setattr("app.services.message_draft_service.get_runtime_setting_value", _runtime_disabled)
    bot = _DummyBot()
    message = _DummyMessage(chat=_DummyChat(chat_type=ChatType.PRIVATE, chat_id=100))

    result = await send_progress_draft(bot, message, text="loading", scope_key="modstats")

    assert result is False
    assert bot.calls == []
