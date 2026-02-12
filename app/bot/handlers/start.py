from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.keyboards.auction import start_private_keyboard
from app.config import settings
from app.db.session import SessionFactory
from app.services.appeal_service import create_appeal_from_ref
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


def _appeal_acceptance_text(appeal_id: int) -> str:
    return (
        f"Апелляция #{appeal_id} принята. "
        "Мы передали запрос модераторам и вернемся с ответом."
    )


async def _notify_moderators_about_appeal(
    bot: Bot,
    message: Message,
    appeal_ref: str,
    *,
    appeal_id: int,
) -> None:
    if message.from_user is None:
        return

    username = f"@{message.from_user.username}" if message.from_user.username else "-"
    text = (
        "Новая апелляция\n"
        f"ID апелляции: {appeal_id}\n"
        f"Референс: {appeal_ref}\n"
        f"TG user id: {message.from_user.id}\n"
        f"Юзернейм: {username}"
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

    payload = _extract_start_payload(message)
    appeal_id: int | None = None

    async with SessionFactory() as session:
        user = await upsert_user(session, message.from_user, mark_private_started=True)
        if payload is not None and payload.startswith("appeal_"):
            appeal_ref = payload[len("appeal_") :] or "manual"
            appeal = await create_appeal_from_ref(
                session,
                appellant_user_id=user.id,
                appeal_ref=appeal_ref,
            )
            appeal_id = appeal.id
        await session.commit()

    if payload is not None and payload.startswith("appeal_") and appeal_id is not None:
        appeal_ref = payload[len("appeal_") :] or "manual"
        await _notify_moderators_about_appeal(
            bot,
            message,
            appeal_ref,
            appeal_id=appeal_id,
        )
        await message.answer(
            _appeal_acceptance_text(appeal_id),
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
