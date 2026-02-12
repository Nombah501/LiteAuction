from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.keyboards.auction import start_private_keyboard
from app.config import settings
from app.db.session import SessionFactory
from app.services.user_service import upsert_user

router = Router(name="start")


def _extract_start_payload(message: Message) -> str | None:
    text = (message.text or "").strip()
    if not text:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    payload = parts[1].strip()
    return payload or None


async def _notify_moderators_about_appeal(bot: Bot, message: Message, appeal_ref: str) -> None:
    if message.from_user is None:
        return

    username = f"@{message.from_user.username}" if message.from_user.username else "-"
    text = (
        "Новая апелляция\n"
        f"Референс: {appeal_ref}\n"
        f"TG user id: {message.from_user.id}\n"
        f"Username: {username}"
    )

    moderation_chat_id = settings.parsed_moderation_chat_id()
    moderation_thread_id = settings.parsed_moderation_thread_id()
    if moderation_chat_id is not None:
        try:
            if moderation_thread_id is not None:
                await bot.send_message(
                    chat_id=moderation_chat_id,
                    text=text,
                    message_thread_id=moderation_thread_id,
                )
            else:
                await bot.send_message(chat_id=moderation_chat_id, text=text)
            return
        except TelegramForbiddenError:
            pass

    for admin_tg_id in settings.parsed_admin_user_ids():
        try:
            await bot.send_message(chat_id=admin_tg_id, text=text)
        except TelegramForbiddenError:
            continue


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def handle_start_private(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        await upsert_user(session, message.from_user, mark_private_started=True)
        await session.commit()

    payload = _extract_start_payload(message)
    if payload is not None and payload.startswith("appeal_"):
        appeal_ref = payload[len("appeal_") :] or "-"
        await _notify_moderators_about_appeal(bot, message, appeal_ref)
        await message.answer(
            "Апелляция принята. Мы передали запрос модераторам и вернемся с ответом.",
            reply_markup=start_private_keyboard(),
        )
        return

    await message.answer(
        "Привет! Я LiteAuction bot.\n"
        "Создавайте аукционы через кнопку ниже.\n"
        "Для модераторов там же есть вход в панель.\n\n"
        "В посте будут live-ставки, топ-3, анти-снайпер и выкуп.",
        reply_markup=start_private_keyboard(),
    )


@router.message(CommandStart())
async def handle_start_non_private(message: Message) -> None:
    await message.answer("Для настройки и уведомлений откройте бота в личных сообщениях.")
