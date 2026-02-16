from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from app.db.session import SessionFactory
from app.services.private_topics_service import PrivateTopicPurpose, enforce_message_topic
from app.services.moderation_service import has_moderator_access, is_moderator_tg_user
from app.services.user_service import upsert_user

router = Router(name="emoji_tools")
_LAST_EFFECT_ID_BY_USER: dict[int, str] = {}


_UI_KEYS = [
    "UI_EMOJI_CREATE_AUCTION_ID",
    "UI_EMOJI_PUBLISH_ID",
    "UI_EMOJI_BID_ID",
    "UI_EMOJI_BID_X1_ID",
    "UI_EMOJI_BID_X3_ID",
    "UI_EMOJI_BID_X5_ID",
    "UI_EMOJI_BUYOUT_ID",
    "UI_EMOJI_REPORT_ID",
    "UI_EMOJI_COPY_PUBLISH_ID",
    "UI_EMOJI_GALLERY_ID",
    "UI_EMOJI_NEW_LOT_ID",
    "UI_EMOJI_PHOTOS_DONE_ID",
    "UI_EMOJI_MOD_PANEL_ID",
    "UI_EMOJI_MOD_COMPLAINTS_ID",
    "UI_EMOJI_MOD_SIGNALS_ID",
    "UI_EMOJI_MOD_FROZEN_ID",
    "UI_EMOJI_MOD_APPEALS_ID",
    "UI_EMOJI_MOD_STATS_ID",
    "UI_EMOJI_MOD_REFRESH_ID",
    "UI_EMOJI_MOD_FREEZE_ID",
    "UI_EMOJI_MOD_UNFREEZE_ID",
    "UI_EMOJI_MOD_REMOVE_TOP_ID",
    "UI_EMOJI_MOD_BAN_ID",
    "UI_EMOJI_MOD_IGNORE_ID",
    "UI_EMOJI_MOD_TAKE_ID",
    "UI_EMOJI_MOD_APPROVE_ID",
    "UI_EMOJI_MOD_REJECT_ID",
    "UI_EMOJI_MOD_ASSIGN_GUARANTOR_ID",
    "UI_EMOJI_MOD_BACK_ID",
    "UI_EMOJI_MOD_MENU_ID",
    "UI_EMOJI_MOD_PREV_ID",
    "UI_EMOJI_MOD_NEXT_ID",
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


def _collect_message_effect_id(message: Message) -> str | None:
    effect_id = getattr(message, "effect_id", None)
    if not isinstance(effect_id, str):
        return None
    normalized = effect_id.strip()
    return normalized or None


def _cached_effect_id_for_user(tg_user_id: int | None) -> str | None:
    if tg_user_id is None:
        return None
    return _LAST_EFFECT_ID_BY_USER.get(tg_user_id)


def _remember_effect_id_for_user(tg_user_id: int | None, effect_id: str | None) -> None:
    if tg_user_id is None or effect_id is None:
        return
    _LAST_EFFECT_ID_BY_USER[tg_user_id] = effect_id


def _auction_effect_env_template(effect_id: str) -> str:
    return "\n".join(
        [
            "AUCTION_MESSAGE_EFFECTS_ENABLED=true",
            f"AUCTION_EFFECT_DEFAULT_ID={effect_id}",
            "AUCTION_EFFECT_OUTBID_ID=",
            "AUCTION_EFFECT_BUYOUT_SELLER_ID=",
            "AUCTION_EFFECT_BUYOUT_WINNER_ID=",
            "AUCTION_EFFECT_ENDED_SELLER_ID=",
            "AUCTION_EFFECT_ENDED_WINNER_ID=",
        ]
    )


@router.message(Command("emojiid"), F.chat.type == ChatType.PRIVATE)
async def emoji_id(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, message.from_user, mark_private_started=True)
            if not await enforce_message_topic(
                message,
                bot=bot,
                session=session,
                user=user,
                purpose=PrivateTopicPurpose.MODERATION,
                command_hint="/emojiid",
            ):
                return

            allowed = is_moderator_tg_user(message.from_user.id)
            if not allowed:
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


@router.message(Command("effectid"), F.chat.type == ChatType.PRIVATE)
async def effect_id(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, message.from_user, mark_private_started=True)
            if not await enforce_message_topic(
                message,
                bot=bot,
                session=session,
                user=user,
                purpose=PrivateTopicPurpose.MODERATION,
                command_hint="/effectid",
            ):
                return

            allowed = is_moderator_tg_user(message.from_user.id)
            if not allowed:
                allowed = await has_moderator_access(session, message.from_user.id)

    if not allowed:
        await message.answer("Недостаточно прав")
        return

    source = message.reply_to_message or message
    effect_id_value = _collect_message_effect_id(source)
    effect_from_cache = False
    if effect_id_value is None:
        effect_id_value = _cached_effect_id_for_user(message.from_user.id if message.from_user else None)
        effect_from_cache = effect_id_value is not None

    if effect_id_value is None:
        custom_emoji_ids = _collect_custom_emoji_ids(source)
        custom_emoji_hint = ""
        if custom_emoji_ids:
            custom_emoji_hint = (
                "\n\nПохоже, это custom emoji, а не message effect. "
                "Для custom emoji используйте <code>/emojiid</code>."
            )
        await message.answer(
            "Не нашел <code>effect_id</code>.\n"
            "Сделайте reply командой <code>/effectid</code> на сообщение с визуальным эффектом "
            "(эффект отправки, а не просто emoji в тексте)."
            f"{custom_emoji_hint}"
        )
        return

    _remember_effect_id_for_user(message.from_user.id if message.from_user else None, effect_id_value)

    env_lines = _auction_effect_env_template(effect_id_value)
    source_note = " (взято из кэша последнего сообщения с эффектом)" if effect_from_cache else ""
    await message.answer(
        "Найден message effect ID:\n"
        f"- <code>{effect_id_value}</code>{source_note}\n\n"
        "Скопируйте в <code>.env</code> или <code>config/defaults.toml</code>:\n"
        f"<code>{env_lines}</code>\n\n"
        "Дальше можно оставить <code>AUCTION_EFFECT_DEFAULT_ID</code> как есть, "
        "или задать отдельные ID по событиям."
    )


@router.message(F.chat.type == ChatType.PRIVATE, F.effect_id)
async def cache_effect_id(message: Message) -> None:
    effect_id_value = _collect_message_effect_id(message)
    _remember_effect_id_for_user(message.from_user.id if message.from_user else None, effect_id_value)
