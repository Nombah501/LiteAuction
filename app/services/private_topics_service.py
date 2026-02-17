from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User, UserPrivateTopic
from app.db.session import SessionFactory
from app.services.moderation_service import has_moderator_access, is_moderator_tg_user
from app.services.notification_policy_service import (
    NotificationEventType,
    default_auction_snooze_minutes,
    is_notification_allowed,
    notification_snooze_callback_data,
    notification_event_action_key,
)

logger = logging.getLogger(__name__)
_TOPICS_CAPABILITY_CACHE: dict[int, bool] = {}
_TOPIC_MUTATION_POLICY_CACHE: dict[int, bool] = {}
_BOT_TOPICS_CAPABILITY: bool | None = None
_BOT_TOPIC_MUTATION_ALLOWED: bool | None = None


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
    missing: list[PrivateTopicPurpose] = field(default_factory=list)
    mutation_blocked: bool = False


PURPOSE_ORDER: tuple[PrivateTopicPurpose, ...] = (
    PrivateTopicPurpose.AUCTIONS,
    PrivateTopicPurpose.SUPPORT,
    PrivateTopicPurpose.MODERATION,
)


def _canonical_purpose(purpose: PrivateTopicPurpose) -> PrivateTopicPurpose:
    if purpose in {PrivateTopicPurpose.SUPPORT, PrivateTopicPurpose.POINTS, PrivateTopicPurpose.TRADES}:
        return PrivateTopicPurpose.SUPPORT
    return purpose


def _required_purposes(*, include_moderation: bool) -> tuple[PrivateTopicPurpose, ...]:
    if include_moderation:
        return PURPOSE_ORDER
    return (
        PrivateTopicPurpose.AUCTIONS,
        PrivateTopicPurpose.SUPPORT,
    )


def topic_title(purpose: PrivateTopicPurpose) -> str:
    if purpose == PrivateTopicPurpose.AUCTIONS:
        return settings.private_topic_title_auctions.strip() or "Аукционы"
    if purpose in {PrivateTopicPurpose.SUPPORT, PrivateTopicPurpose.POINTS, PrivateTopicPurpose.TRADES}:
        return settings.private_topic_title_support.strip() or "Уведомления"
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


def _resolve_cached_topic_mutation_policy(
    *,
    tg_user_id: int,
    telegram_user: TgUser | None,
) -> bool | None:
    allows_users_to_create_topics = getattr(telegram_user, "allows_users_to_create_topics", None)
    if isinstance(allows_users_to_create_topics, bool):
        _TOPIC_MUTATION_POLICY_CACHE[tg_user_id] = allows_users_to_create_topics
        return allows_users_to_create_topics
    return _TOPIC_MUTATION_POLICY_CACHE.get(tg_user_id)


def _normalize_topic_policy_mode() -> str:
    mode = settings.private_topics_user_topic_policy.strip().lower()
    if mode in {"auto", "allow", "block"}:
        return mode
    return "auto"


async def _resolve_bot_topic_settings(bot: Bot | None) -> tuple[bool | None, bool | None]:
    global _BOT_TOPICS_CAPABILITY, _BOT_TOPIC_MUTATION_ALLOWED

    if bot is None:
        return _BOT_TOPICS_CAPABILITY, _BOT_TOPIC_MUTATION_ALLOWED
    if _BOT_TOPICS_CAPABILITY is not None and _BOT_TOPIC_MUTATION_ALLOWED is not None:
        return _BOT_TOPICS_CAPABILITY, _BOT_TOPIC_MUTATION_ALLOWED

    try:
        me = await bot.get_me()
    except Exception:
        return _BOT_TOPICS_CAPABILITY, _BOT_TOPIC_MUTATION_ALLOWED

    has_topics_enabled = getattr(me, "has_topics_enabled", None)
    if isinstance(has_topics_enabled, bool):
        _BOT_TOPICS_CAPABILITY = has_topics_enabled

    allows_users_to_create_topics = getattr(me, "allows_users_to_create_topics", None)
    if isinstance(allows_users_to_create_topics, bool):
        _BOT_TOPIC_MUTATION_ALLOWED = allows_users_to_create_topics

    return _BOT_TOPICS_CAPABILITY, _BOT_TOPIC_MUTATION_ALLOWED


def _is_topic_mutation_blocked_error(exc: Exception) -> bool:
    if not isinstance(exc, TelegramBadRequest):
        return False
    text = str(exc).lower()
    return "topic" in text and ("forbidden" in text or "not allowed" in text)


def _topic_mutation_blocked_hint(purpose: PrivateTopicPurpose, command_hint: str | None = None) -> str:
    text = (
        "Не могу создать новый личный раздел: политика тем ограничивает изменения. "
        f"Используйте уже существующий раздел «{topic_title(purpose)}» или отключите строгий режим."
    )
    if command_hint:
        return f"{text} Команда: <code>{command_hint}</code>."
    return text


async def _load_user_topics_map(session: AsyncSession, user_id: int) -> dict[PrivateTopicPurpose, int]:
    rows = await _load_active_user_topic_rows(session, user_id)

    mapping: dict[PrivateTopicPurpose, int] = {}
    for row in rows:
        try:
            purpose = PrivateTopicPurpose(row.purpose)
        except ValueError:
            continue
        mapping[purpose] = row.thread_id
    return mapping


async def _load_active_user_topic_rows(session: AsyncSession, user_id: int) -> list[UserPrivateTopic]:
    rows = (
        await session.execute(
            select(UserPrivateTopic).where(
                UserPrivateTopic.user_id == user_id,
                UserPrivateTopic.is_active.is_(True),
            )
        )
    ).scalars().all()
    return list(rows)


def _normalize_topics_map(mapping: dict[PrivateTopicPurpose, int]) -> dict[PrivateTopicPurpose, int]:
    normalized = dict(mapping)

    notifications_thread_id: int | None = None
    for candidate in (
        PrivateTopicPurpose.SUPPORT,
        PrivateTopicPurpose.POINTS,
        PrivateTopicPurpose.TRADES,
    ):
        thread_id = normalized.get(candidate)
        if thread_id is None:
            continue
        notifications_thread_id = thread_id
        break

    if notifications_thread_id is not None:
        normalized[PrivateTopicPurpose.SUPPORT] = notifications_thread_id
        normalized[PrivateTopicPurpose.POINTS] = notifications_thread_id
        normalized[PrivateTopicPurpose.TRADES] = notifications_thread_id

    return normalized


async def _can_user_access_moderation_topics(session: AsyncSession, user: User) -> bool:
    if is_moderator_tg_user(user.tg_user_id):
        return True
    if session is None:  # type: ignore[redundant-expr]
        return False
    return await has_moderator_access(session, user.tg_user_id)


def _redundant_private_topic_thread_ids(
    rows: list[UserPrivateTopic],
    *,
    mapping: dict[PrivateTopicPurpose, int],
    include_moderation: bool,
) -> set[int]:
    keep_thread_ids: set[int] = set()

    auctions_thread_id = mapping.get(PrivateTopicPurpose.AUCTIONS)
    if auctions_thread_id is not None:
        keep_thread_ids.add(auctions_thread_id)

    notifications_thread_id = mapping.get(PrivateTopicPurpose.SUPPORT)
    if notifications_thread_id is not None:
        keep_thread_ids.add(notifications_thread_id)

    moderation_thread_id = mapping.get(PrivateTopicPurpose.MODERATION)
    if include_moderation and moderation_thread_id is not None:
        keep_thread_ids.add(moderation_thread_id)

    redundant: set[int] = set()
    for row in rows:
        if row.thread_id in keep_thread_ids:
            continue
        redundant.add(row.thread_id)

    return redundant


async def _prune_legacy_private_topics(
    session: AsyncSession,
    bot: Bot | None,
    *,
    user: User,
    mapping: dict[PrivateTopicPurpose, int],
    include_moderation: bool,
    mutation_allowed: bool,
) -> None:
    if bot is None or not mutation_allowed:
        return

    rows = await _load_active_user_topic_rows(session, user.id)
    redundant_thread_ids = _redundant_private_topic_thread_ids(
        rows,
        mapping=mapping,
        include_moderation=include_moderation,
    )
    if not redundant_thread_ids:
        return

    changed = False
    now_utc = datetime.now(timezone.utc)
    for thread_id in sorted(redundant_thread_ids):
        try:
            await bot.delete_forum_topic(chat_id=user.tg_user_id, message_thread_id=thread_id)
        except Exception as exc:
            logger.warning(
                "Failed to delete legacy private topic for user %s thread %s: %s",
                user.tg_user_id,
                thread_id,
                exc,
            )
            continue

        for row in rows:
            if row.thread_id != thread_id or not row.is_active:
                continue
            row.is_active = False
            row.updated_at = now_utc
            changed = True

    if changed:
        await session.flush()


async def ensure_user_private_topics(
    session: AsyncSession,
    bot: Bot | None,
    *,
    user: User,
    telegram_user: TgUser | None = None,
) -> EnsureTopicsResult:
    existing = _normalize_topics_map(await _load_user_topics_map(session, user.id))
    bot_capability, bot_mutation_policy = await _resolve_bot_topic_settings(bot)
    can_access_moderation = await _can_user_access_moderation_topics(session, user)
    required_purposes = _required_purposes(include_moderation=can_access_moderation)

    capability = _resolve_cached_topics_capability(tg_user_id=user.tg_user_id, telegram_user=telegram_user)
    if capability is None:
        capability = bot_capability
    if capability is False:
        return EnsureTopicsResult(mapping={}, created=[])

    mode = _normalize_topic_policy_mode()
    mutation_allowed: bool | None
    if mode == "allow":
        mutation_allowed = True
    elif mode == "block":
        mutation_allowed = False
    else:
        mutation_allowed = _resolve_cached_topic_mutation_policy(
            tg_user_id=user.tg_user_id,
            telegram_user=telegram_user,
        )
        if mutation_allowed is None:
            mutation_allowed = bot_mutation_policy
        if mutation_allowed is None:
            mutation_allowed = True

    if not settings.private_topics_enabled or bot is None:
        return EnsureTopicsResult(mapping=existing, created=[])

    missing = [purpose for purpose in required_purposes if purpose not in existing]
    if missing and not mutation_allowed:
        return EnsureTopicsResult(mapping=existing, created=[], missing=missing, mutation_blocked=True)

    created: list[PrivateTopicPurpose] = []
    for purpose in required_purposes:
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
            if _is_topic_mutation_blocked_error(exc):
                missing = [item for item in required_purposes if item not in existing]
                return EnsureTopicsResult(
                    mapping=existing,
                    created=created,
                    missing=missing,
                    mutation_blocked=True,
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

    normalized = _normalize_topics_map(existing)

    await _prune_legacy_private_topics(
        session,
        bot,
        user=user,
        mapping=normalized,
        include_moderation=can_access_moderation,
        mutation_allowed=bool(mutation_allowed),
    )

    missing = [purpose for purpose in required_purposes if purpose not in normalized]
    return EnsureTopicsResult(mapping=normalized, created=created, missing=missing)


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

    result = await ensure_user_private_topics(
        session,
        bot,
        user=user,
        telegram_user=message.from_user,
    )
    target_thread_id = result.mapping.get(purpose)
    if target_thread_id is None and settings.private_topics_strict_routing and result.mutation_blocked:
        await message.answer(_topic_mutation_blocked_hint(purpose, command_hint))
        return False
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

    result = await ensure_user_private_topics(
        session,
        bot,
        user=user,
        telegram_user=callback.from_user,
    )
    target_thread_id = result.mapping.get(purpose)
    if target_thread_id is None and settings.private_topics_strict_routing and result.mutation_blocked:
        await callback.answer(_topic_mutation_blocked_hint(purpose, command_hint), show_alert=True)
        return False
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


def _notification_reply_markup(
    *,
    reply_markup: InlineKeyboardMarkup | None,
    notification_event: NotificationEventType | None,
    auction_id: uuid.UUID | None,
) -> InlineKeyboardMarkup | None:
    if notification_event is None:
        return reply_markup

    rows: list[list[InlineKeyboardButton]] = []
    if reply_markup is not None:
        rows.extend(reply_markup.inline_keyboard)

    if notification_event in {
        NotificationEventType.AUCTION_OUTBID,
        NotificationEventType.AUCTION_FINISH,
        NotificationEventType.AUCTION_WIN,
        NotificationEventType.AUCTION_MOD_ACTION,
    } and auction_id is not None:
        snooze_minutes = default_auction_snooze_minutes()
        snooze_callback = notification_snooze_callback_data(
            auction_id=auction_id,
            duration_minutes=snooze_minutes,
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Пауза по лоту на {snooze_minutes // 60}ч",
                    callback_data=snooze_callback,
                )
            ]
        )

    mute_callback = f"notif:mute:{notification_event_action_key(notification_event)}"
    rows.append([InlineKeyboardButton(text="Отключить этот тип", callback_data=mute_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_user_topic_message(
    bot: Bot,
    *,
    tg_user_id: int,
    purpose: PrivateTopicPurpose,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    message_effect_id: str | None = None,
    notification_event: NotificationEventType | None = None,
    auction_id: uuid.UUID | None = None,
) -> bool:
    if notification_event is not None:
        async with SessionFactory() as session:
            allowed = await is_notification_allowed(
                session,
                tg_user_id=tg_user_id,
                event_type=notification_event,
                auction_id=auction_id,
            )
        if not allowed:
            return False

    effective_reply_markup = _notification_reply_markup(
        reply_markup=reply_markup,
        notification_event=notification_event,
        auction_id=auction_id,
    )

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

    normalized_effect_id = message_effect_id.strip() if message_effect_id else ""
    attempts: list[tuple[bool, bool]] = [
        (thread_id is not None, bool(normalized_effect_id)),
    ]
    if normalized_effect_id:
        attempts.append((thread_id is not None, False))
    if thread_id is not None:
        attempts.append((False, bool(normalized_effect_id)))
        attempts.append((False, False))

    deduplicated_attempts: list[tuple[bool, bool]] = []
    for attempt in attempts:
        if attempt not in deduplicated_attempts:
            deduplicated_attempts.append(attempt)

    last_bad_request: TelegramBadRequest | None = None
    for use_thread, use_effect in deduplicated_attempts:
        try:
            if use_thread and thread_id is not None and use_effect and normalized_effect_id:
                await bot.send_message(
                    chat_id=tg_user_id,
                    text=text,
                    reply_markup=effective_reply_markup,
                    message_thread_id=thread_id,
                    message_effect_id=normalized_effect_id,
                )
            elif use_thread and thread_id is not None:
                await bot.send_message(
                    chat_id=tg_user_id,
                    text=text,
                    reply_markup=effective_reply_markup,
                    message_thread_id=thread_id,
                )
            elif use_effect and normalized_effect_id:
                await bot.send_message(
                    chat_id=tg_user_id,
                    text=text,
                    reply_markup=effective_reply_markup,
                    message_effect_id=normalized_effect_id,
                )
            else:
                await bot.send_message(
                    chat_id=tg_user_id,
                    text=text,
                    reply_markup=effective_reply_markup,
                )
            return True
        except TelegramBadRequest as exc:
            last_bad_request = exc
            continue
        except TelegramForbiddenError as exc:
            logger.warning(
                "Failed to deliver user message (tg_user_id=%s, purpose=%s): %s",
                tg_user_id,
                purpose,
                exc,
            )
            return False
        except TelegramAPIError as exc:
            logger.warning(
                "Telegram API error while delivering user message (tg_user_id=%s, purpose=%s): %s",
                tg_user_id,
                purpose,
                exc,
            )
            return False
        except Exception as exc:
            logger.warning(
                "Unexpected error while delivering user message (tg_user_id=%s, purpose=%s): %s",
                tg_user_id,
                purpose,
                exc,
            )
            return False

    if last_bad_request is not None:
        logger.warning(
            "Bad request while delivering user message (tg_user_id=%s, purpose=%s): %s",
            tg_user_id,
            purpose,
            last_bad_request,
        )
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
    if result.mutation_blocked and result.missing:
        missing_titles = ", ".join(topic_title(purpose) for purpose in result.missing)
        return (
            "Создание новых разделов ограничено политикой тем. "
            f"Недоступно для автосоздания: {missing_titles}."
        )
    if not result.mapping:
        return (
            "Разделы в личке пока недоступны для этого диалога. "
            "Остальные команды продолжают работать в обычном режиме."
        )

    can_access_moderation = await _can_user_access_moderation_topics(session, user)
    lines = ["Личные разделы:"]
    for purpose in _required_purposes(include_moderation=can_access_moderation):
        thread_id = result.mapping.get(purpose)
        if thread_id is None:
            continue
        marker = " (создан)" if purpose in result.created else ""
        lines.append(f"- {topic_title(purpose)}{marker}")

    if settings.private_topics_strict_routing:
        lines.append("")
        lines.append("Включен строгий режим: команды работают только в своем разделе.")

    return "\n".join(lines)
