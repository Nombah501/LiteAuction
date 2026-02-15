from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.types import Message

from app.config import settings


def _draft_id_for_scope(*, chat_id: int, scope_key: str) -> int:
    normalized_scope = scope_key.strip() or "default"
    draft_id = abs(hash(f"{chat_id}:{normalized_scope}")) % 2_000_000_000
    if draft_id == 0:
        return 1
    return draft_id


async def send_progress_draft(
    bot: Bot | None,
    message: Message,
    *,
    text: str,
    scope_key: str,
) -> bool:
    if bot is None or not settings.message_drafts_enabled:
        return False

    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if not isinstance(chat_id, int):
        return False

    message_thread_id: int | None = None
    if getattr(chat, "type", None) == ChatType.PRIVATE:
        thread_id = getattr(message, "message_thread_id", None)
        if isinstance(thread_id, int):
            message_thread_id = thread_id

    try:
        await bot.send_message_draft(
            chat_id=chat_id,
            draft_id=_draft_id_for_scope(chat_id=chat_id, scope_key=scope_key),
            text=text,
            message_thread_id=message_thread_id,
        )
        return True
    except Exception:
        return False
