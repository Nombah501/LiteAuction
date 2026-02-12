from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from app.db.session import SessionFactory
from app.services.moderation_service import has_moderator_access, is_moderator_tg_user

router = Router(name="emoji_tools")


_UI_KEYS = [
    "UI_EMOJI_CREATE_AUCTION_ID",
    "UI_EMOJI_PUBLISH_ID",
    "UI_EMOJI_BID_ID",
    "UI_EMOJI_BUYOUT_ID",
    "UI_EMOJI_REPORT_ID",
    "UI_EMOJI_MOD_PANEL_ID",
]


def _collect_custom_emoji_ids(message: Message) -> list[str]:
    ids: list[str] = []
    for group in (message.entities, message.caption_entities):
        if not group:
            continue
        for entity in group:
            if str(entity.type) == "custom_emoji" and entity.custom_emoji_id:
                ids.append(entity.custom_emoji_id)

    unique: list[str] = []
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _env_template(ids: list[str]) -> str:
    if not ids:
        return "\n".join(f"{key}=" for key in _UI_KEYS)

    lines: list[str] = []
    for idx, key in enumerate(_UI_KEYS):
        value = ids[idx] if idx < len(ids) else ids[-1]
        lines.append(f"{key}={value}")
    return "\n".join(lines)


@router.message(Command("emojiid"), F.chat.type == ChatType.PRIVATE)
async def emoji_id(message: Message) -> None:
    if message.from_user is None:
        return

    allowed = is_moderator_tg_user(message.from_user.id)
    if not allowed:
        async with SessionFactory() as session:
            allowed = await has_moderator_access(session, message.from_user.id)

    if not allowed:
        await message.answer("Недостаточно прав")
        return

    source = message.reply_to_message or message
    ids = _collect_custom_emoji_ids(source)

    if not ids:
        await message.answer(
            "Не нашел custom emoji.\n"
            "Сделайте reply командой <code>/emojiid</code> на сообщение, где есть premium/custom emoji."
        )
        return

    id_lines = "\n".join(f"- <code>{item}</code>" for item in ids)
    env_lines = _env_template(ids)

    await message.answer(
        "Найдены custom_emoji_id:\n"
        f"{id_lines}\n\n"
        "Скопируйте в <code>.env</code> (можно отредактировать вручную):\n"
        f"<code>{env_lines}</code>\n\n"
        "После обновления .env перезапустите бот: <code>docker compose up -d --build bot</code>"
    )
