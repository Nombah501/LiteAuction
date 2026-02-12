from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.keyboards.auction import start_private_keyboard
from app.db.session import SessionFactory
from app.services.user_service import upsert_user

router = Router(name="start")


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def handle_start_private(message: Message) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        await upsert_user(session, message.from_user, mark_private_started=True)
        await session.commit()

    await message.answer(
        "Привет! Я LiteAuction bot.\\n"
        "Создавайте аукционы через кнопку ниже.\\n"
        "Для модераторов там же есть вход в панель.\\n\\n"
        "В посте будут live-ставки, топ-3, анти-снайпер и выкуп.",
        reply_markup=start_private_keyboard(),
    )


@router.message(CommandStart())
async def handle_start_non_private(message: Message) -> None:
    await message.answer("Для настройки и уведомлений откройте бота в личных сообщениях.")
