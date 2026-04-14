from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ChatType

router = Router(name="help")


def build_help_text() -> str:
    return (
        "📖 <b>Как работает LiteAuction</b>\n\n"
        "🔍 <b>Ставки</b> — нажимайте кнопки +/- под лотом в чате/канале\n"
        "🏆 <b>Победа</b> — если ваша ставка осталась верхней к финишу, вы выиграли\n"
        "💬 <b>Сделка</b> — бот создаст приватный топик для связи с продавцом\n"
        "🛡 <b>Гарант</b> — безопасная сделка через модератора\n"
        "⭐ <b>Отзыв</b> — оцените сделку после завершения\n\n"
        "<b>Команды:</b>\n"
        "/mybids — мои ставки и аукционы\n"
        "/points — баланс и бонусы\n"
        "/myrep — моя репутация\n"
        "/guarant — запросить гаранта\n"
        "/bug /suggest — сообщить о проблеме"
    )


def build_group_help_text() -> str:
    return (
        "📖 Полная справка доступна в личных сообщениях с ботом.\n"
        "Нажмите /help в приватном чате."
    )


@router.message(Command("help"), F.chat.type == ChatType.PRIVATE)
async def handle_help_private(message: Message) -> None:
    await message.answer(build_help_text(), parse_mode="HTML")


@router.message(Command("help"))
async def handle_help_group(message: Message) -> None:
    await message.answer(build_group_help_text())
