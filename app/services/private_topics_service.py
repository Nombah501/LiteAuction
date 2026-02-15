from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User, UserPrivateTopic
from app.db.session import SessionFactory

logger = logging.getLogger(__name__)
_TOPICS_CAPABILITY_CACHE: dict[int, bool] = {}


class PrivateTopicPurpose(StrEnum):
    AUCTIONS = "auctions"
    SUPPORT = "support"
    POINTS = "points"
    TRADES = "trades"
    MODERATION = "moderation"


@dataclass(slots=True)
class EnsureTopicsResult:
    mapping: dict[PrivateTopicPurpose, int]
    created: list[PrivateTopicPurpose]


PURPOSE_ORDER: tuple[PrivateTopicPurpose, ...] = (
    PrivateTopicPurpose.AUCTIONS,
    PrivateTopicPurpose.SUPPORT,
    PrivateTopicPurpose.POINTS,
    PrivateTopicPurpose.TRADES,
    PrivateTopicPurpose.MODERATION,
)


def topic_title(purpose: PrivateTopicPurpose) -> str:
    if purpose == PrivateTopicPurpose.AUCTIONS:
        return settings.private_topic_title_auctions.strip() or "Лоты"
    if purpose == PrivateTopicPurpose.SUPPORT:
        return settings.private_topic_title_support.strip() or "Поддержка"
    if purpose == PrivateTopicPurpose.POINTS:
        return settings.private_topic_title_points.strip() or "Баллы"
    if purpose == PrivateTopicPurpose.TRADES:
        return settings.private_topic_title_trades.strip() or "Сделки"
    return settings.private_topic_title_moderation.strip() or "Модерация"


def _resolve_cached_topics_capability(
    *,
    tg_user_id: int,
    telegram_user: TgUser | None,
) -> bool | None:
    has_topics_enabled = getattr(telegram_user, "has_topics_enabled", None)
    if isinstance(has_topics_enabled, bool):
        _TOPICS_CAPABILITY_CACHE[tg_user_id] = has_topics_enabled
        return has_topics_enabled
    return _TOPICS_CAPABILITY_CACHE.get(tg_user_id)


async def _load_user_topics_map(session: AsyncSession, user_id: int) -> dict[PrivateTopicPurpose, int]:
    rows = (
        await session.execute(
            select(UserPrivateTopic).where(
                UserPrivateTopic.user_id == user_id,
                UserPrivateTopic.is_active.is_(True),
            )
        )
    ).scalars().all()

    mapping: dict[PrivateTopicPurpose, int] = {}
    for row in rows:
        try:
            purpose = PrivateTopicPurpose(row.purpose)
        except ValueError:
            continue
        mapping[purpose] = row.thread_id
    return mapping


async def ensure_user_private_topics(
    session: AsyncSession,
    bot: Bot | None,
    *,
    user: User,
    telegram_user: TgUser | None = None,
) -> EnsureTopicsResult:
    existing = await _load_user_topics_map(session, user.id)
    capability = _resolve_cached_topics_capability(tg_user_id=user.tg_user_id, telegram_user=telegram_user)
    if capability is False:
        return EnsureTopicsResult(mapping=existing, created=[])
    if not settings.private_topics_enabled or bot is None:
        return EnsureTopicsResult(mapping=existing, created=[])

    created: list[PrivateTopicPurpose] = []
    for purpose in PURPOSE_ORDER:
        if purpose in existing:
            continue
        title = topic_title(purpose)
        try:
            forum_topic = await bot.create_forum_topic(chat_id=user.tg_user_id, name=title)
        except Exception as exc:
            logger.warning(
                "Failed to create private topic for user %s purpose %s: %s",
                user.tg_user_id,
                purpose,
                exc,
            )
            break

        thread_id = getattr(forum_topic, "message_thread_id", None)
        if thread_id is None:
            logger.warning("Forum topic has no thread id for user %s purpose %s", user.tg_user_id, purpose)
            continue

        now_utc = datetime.now(timezone.utc)
        session.add(
            UserPrivateTopic(
                user_id=user.id,
                purpose=purpose.value,
                thread_id=thread_id,
                title=title,
                is_active=True,
                created_at=now_utc,
                updated_at=now_utc,
            )
        )
        await session.flush()
        existing[purpose] = thread_id
        created.append(purpose)

    return EnsureTopicsResult(mapping=existing, created=created)


async def resolve_user_topic_thread_id(
    session: AsyncSession,
    bot: Bot | None,
    *,
    user: User,
    purpose: PrivateTopicPurpose,
    telegram_user: TgUser | None = None,
) -> int | None:
    result = await ensure_user_private_topics(session, bot, user=user, telegram_user=telegram_user)
    return result.mapping.get(purpose)


async def enforce_message_topic(
    message: Message,
    *,
    bot: Bot | None,
    session: AsyncSession,
    user: User,
    purpose: PrivateTopicPurpose,
    command_hint: str | None = None,
) -> bool:
    if not settings.private_topics_enabled:
        return True
    chat = getattr(message, "chat", None)
    if chat is None or getattr(chat, "type", None) != ChatType.PRIVATE:
        return True

    capability = _resolve_cached_topics_capability(tg_user_id=user.tg_user_id, telegram_user=message.from_user)
    if capability is False:
        return True

    target_thread_id = await resolve_user_topic_thread_id(
        session,
        bot,
        user=user,
        purpose=purpose,
        telegram_user=message.from_user,
    )
    if target_thread_id is None or not settings.private_topics_strict_routing:
        return True
    if getattr(message, "message_thread_id", None) == target_thread_id:
        return True

    hint = f"Эта команда доступна в разделе «{topic_title(purpose)}»."
    if command_hint:
        hint = f"{hint} Повторите там: <code>{command_hint}</code>."
    await message.answer(hint)

    chat_id = getattr(chat, "id", None)
    if bot is not None and isinstance(chat_id, int):
        try:
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=target_thread_id,
                text=(
                    f"Раздел «{topic_title(purpose)}» готов. "
                    f"Повторите здесь: <code>{command_hint}</code>."
                    if command_hint
                    else f"Раздел «{topic_title(purpose)}» готов. Продолжайте здесь."
                ),
            )
        except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError):
            pass
    return False


async def enforce_callback_topic(
    callback: CallbackQuery,
    *,
    bot: Bot | None,
    session: AsyncSession,
    user: User,
    purpose: PrivateTopicPurpose,
    command_hint: str | None = None,
) -> bool:
    message = callback.message
    chat = getattr(message, "chat", None) if message is not None else None
    if not settings.private_topics_enabled or message is None or chat is None:
        return True
    if getattr(chat, "type", None) != ChatType.PRIVATE:
        return True

    capability = _resolve_cached_topics_capability(tg_user_id=user.tg_user_id, telegram_user=callback.from_user)
    if capability is False:
        return True

    target_thread_id = await resolve_user_topic_thread_id(
        session,
        bot,
        user=user,
        purpose=purpose,
        telegram_user=callback.from_user,
    )
    if target_thread_id is None or not settings.private_topics_strict_routing:
        return True
    if getattr(message, "message_thread_id", None) == target_thread_id:
        return True

    await callback.answer(f"Откройте раздел «{topic_title(purpose)}»", show_alert=True)
    chat_id = getattr(chat, "id", None)
    if bot is not None and isinstance(chat_id, int):
        try:
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=target_thread_id,
                text=(
                    f"Раздел «{topic_title(purpose)}»: повторите <code>{command_hint}</code>."
                    if command_hint
                    else f"Раздел «{topic_title(purpose)}»: продолжайте здесь."
                ),
            )
        except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError):
            pass
    return False


async def send_user_topic_message(
    bot: Bot,
    *,
    tg_user_id: int,
    purpose: PrivateTopicPurpose,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    thread_id: int | None = None

    if settings.private_topics_enabled:
        if _TOPICS_CAPABILITY_CACHE.get(tg_user_id) is False:
            thread_id = None
        else:
            try:
                async with SessionFactory() as session:
                    async with session.begin():
                        user = await session.scalar(select(User).where(User.tg_user_id == tg_user_id))
                        if user is not None:
                            thread_id = await resolve_user_topic_thread_id(session, bot, user=user, purpose=purpose)
            except Exception:
                thread_id = None

    try:
        if thread_id is not None:
            await bot.send_message(
                chat_id=tg_user_id,
                message_thread_id=thread_id,
                text=text,
                reply_markup=reply_markup,
            )
        else:
            await bot.send_message(chat_id=tg_user_id, text=text, reply_markup=reply_markup)
        return True
    except Exception:
        return False


async def render_user_topics_overview(
    session: AsyncSession,
    bot: Bot | None,
    *,
    user: User,
    telegram_user: TgUser | None = None,
) -> str:
    capability = _resolve_cached_topics_capability(tg_user_id=user.tg_user_id, telegram_user=telegram_user)
    if capability is False:
        return (
            "Разделы-топики отключены для этого диалога в настройках Telegram. "
            "Команды продолжают работать в обычном режиме лички."
        )

    result = await ensure_user_private_topics(session, bot, user=user, telegram_user=telegram_user)
    if not result.mapping:
        return (
            "Разделы в личке пока недоступны для этого диалога. "
            "Остальные команды продолжают работать в обычном режиме."
        )

    lines = ["Личные разделы:"]
    for purpose in PURPOSE_ORDER:
        thread_id = result.mapping.get(purpose)
        if thread_id is None:
            continue
        marker = " (создан)" if purpose in result.created else ""
        lines.append(f"- {topic_title(purpose)}: thread {thread_id}{marker}")

    if settings.private_topics_strict_routing:
        lines.append("")
        lines.append("Включен строгий режим: команды работают только в своем разделе.")

    return "\n".join(lines)
