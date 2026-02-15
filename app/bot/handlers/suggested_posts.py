from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.config import settings
from app.db.models import SuggestedPostReview
from app.db.session import SessionFactory
from app.services.channel_dm_intake_service import (
    AuctionIntakeKind,
    resolve_auction_intake_actor,
    resolve_auction_intake_context,
)
from app.services.chat_owner_guard_service import (
    build_chat_owner_guard_alert_text,
    is_chat_owner_confirmation_required,
    parse_chat_owner_service_event,
    record_chat_owner_service_event,
)
from app.services.moderation_topic_router import ModerationTopicSection, send_section_message
from app.services.rbac_service import SCOPE_DIRECT_MESSAGES_MANAGE, resolve_tg_user_scopes
from app.services.user_service import upsert_user

router = Router(name="suggested_posts")

_CALLBACK_PREFIX = "spp"
_CALLBACK_ACTION_APPROVE = "ap"
_CALLBACK_ACTION_DECLINE = "dc"

_DECLINE_REASON_MAP: dict[str, str] = {
    "rules": "Нарушение правил площадки",
    "spam": "Спам или реклама",
}


@dataclass(slots=True)
class SuggestedPostDecision:
    review_id: int
    approve: bool
    decline_reason_code: str | None = None


def _resolve_monitored_channel_dm_chat_id(message: Message) -> int | None:
    chat = getattr(message, "chat", None)
    if chat is None:
        return None
    if getattr(chat, "type", None) != ChatType.SUPERGROUP:
        return None
    if not bool(getattr(chat, "is_direct_messages", False)):
        return None
    if not settings.channel_dm_intake_enabled:
        return None

    chat_id = getattr(chat, "id", None)
    if not isinstance(chat_id, int):
        return None

    allowed_chat_id = int(settings.channel_dm_intake_chat_id)
    if allowed_chat_id and chat_id != allowed_chat_id:
        return None

    return chat_id


def _build_review_keyboard(review_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Одобрить",
                    callback_data=f"{_CALLBACK_PREFIX}:{_CALLBACK_ACTION_APPROVE}:{review_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Отклонить: правила",
                    callback_data=(
                        f"{_CALLBACK_PREFIX}:{_CALLBACK_ACTION_DECLINE}:{review_id}:rules"
                    ),
                ),
                InlineKeyboardButton(
                    text="Отклонить: спам",
                    callback_data=f"{_CALLBACK_PREFIX}:{_CALLBACK_ACTION_DECLINE}:{review_id}:spam",
                ),
            ],
        ]
    )


def _parse_decision_callback(data: str | None) -> SuggestedPostDecision | None:
    if not data:
        return None
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != _CALLBACK_PREFIX:
        return None

    action = parts[1]
    review_raw = parts[2]
    if not review_raw.isdigit():
        return None
    review_id = int(review_raw)

    if action == _CALLBACK_ACTION_APPROVE:
        return SuggestedPostDecision(review_id=review_id, approve=True)

    if action == _CALLBACK_ACTION_DECLINE:
        if len(parts) < 4:
            return None
        decline_reason_code = parts[3].strip().lower()
        if decline_reason_code not in _DECLINE_REASON_MAP:
            return None
        return SuggestedPostDecision(
            review_id=review_id,
            approve=False,
            decline_reason_code=decline_reason_code,
        )

    return None


def _source_author_line(message: Message) -> str:
    actor = resolve_auction_intake_actor(message)
    if actor is None:
        return "Источник: неизвестный пользователь"
    if actor.username:
        return f"Источник: @{actor.username} (tg:{actor.id})"
    return f"Источник: tg:{actor.id}"


def _review_intro_text(*, review_id: int, message: Message) -> str:
    topic_id = getattr(getattr(message, "direct_messages_topic", None), "topic_id", None)
    topic_segment = f" | topic:{topic_id}" if isinstance(topic_id, int) else ""
    return (
        "🧩 Новый suggested post в DM канала\n"
        f"review_id: <code>{review_id}</code>\n"
        f"source_chat: <code>{message.chat.id}</code>\n"
        f"source_message: <code>{message.message_id}</code>{topic_segment}\n"
        f"{_source_author_line(message)}\n"
        "\n"
        "Выберите решение модерации:"
    )


@router.message(F.chat.type == ChatType.SUPERGROUP)
async def capture_chat_owner_service_events(message: Message, bot: Bot) -> None:
    parsed_event = parse_chat_owner_service_event(message)
    if parsed_event is None:
        return

    monitored_chat_id = _resolve_monitored_channel_dm_chat_id(message)
    if monitored_chat_id is None:
        return

    message_id = getattr(message, "message_id", None)
    normalized_message_id = message_id if isinstance(message_id, int) else None

    async with SessionFactory() as session:
        async with session.begin():
            persisted = await record_chat_owner_service_event(
                session,
                chat_id=monitored_chat_id,
                message_id=normalized_message_id,
                event=parsed_event,
            )

    if not persisted.created:
        return

    await send_section_message(
        bot,
        section=ModerationTopicSection.BUGS,
        text=build_chat_owner_guard_alert_text(
            chat_id=monitored_chat_id,
            event=parsed_event,
            audit_id=persisted.audit_id,
        ),
    )


@router.message(F.chat.type == ChatType.SUPERGROUP)
async def capture_suggested_post(message: Message, bot: Bot) -> None:
    if getattr(message, "suggested_post_info", None) is None:
        return

    context = resolve_auction_intake_context(message)
    if context.kind != AuctionIntakeKind.CHANNEL_DM:
        return
    if context.chat_id is None:
        return

    existing_review_id: int | None = None
    actor = resolve_auction_intake_actor(message)
    async with SessionFactory() as session:
        async with session.begin():
            if await is_chat_owner_confirmation_required(session, chat_id=context.chat_id):
                return

            existing = await session.scalar(
                select(SuggestedPostReview).where(
                    SuggestedPostReview.source_chat_id == context.chat_id,
                    SuggestedPostReview.source_message_id == message.message_id,
                )
            )
            if existing is not None and existing.queue_message_id is not None:
                return
            if existing is not None:
                existing_review_id = existing.id
            else:
                submitter_user_id: int | None = None
                submitter_tg_user_id: int | None = None
                if actor is not None:
                    submitter = await upsert_user(session, actor)
                    submitter_user_id = submitter.id
                    submitter_tg_user_id = submitter.tg_user_id

                review = SuggestedPostReview(
                    source_chat_id=context.chat_id,
                    source_message_id=message.message_id,
                    source_direct_messages_topic_id=context.direct_messages_topic_id,
                    submitter_user_id=submitter_user_id,
                    submitter_tg_user_id=submitter_tg_user_id,
                    status="PENDING",
                    payload={
                        "has_caption": bool(message.caption),
                        "has_media": any(
                            getattr(message, key, None) is not None
                            for key in (
                                "photo",
                                "video",
                                "animation",
                                "document",
                            )
                        ),
                    },
                )
                session.add(review)
                await session.flush()
                existing_review_id = review.id

    if existing_review_id is None:
        return

    moderation_ref = await send_section_message(
        bot,
        section=ModerationTopicSection.SUGGESTIONS,
        text=_review_intro_text(review_id=existing_review_id, message=message),
        reply_markup=_build_review_keyboard(existing_review_id),
    )

    if moderation_ref is None:
        return

    async with SessionFactory() as session:
        async with session.begin():
            review = await session.get(SuggestedPostReview, existing_review_id)
            if review is None:
                return
            if review.queue_message_id is None:
                review.queue_chat_id, review.queue_message_id = moderation_ref
                review.updated_at = datetime.now(UTC)


@router.callback_query(F.data.startswith(f"{_CALLBACK_PREFIX}:"))
async def handle_suggested_post_decision(callback: CallbackQuery, bot: Bot) -> None:
    decision = _parse_decision_callback(callback.data)
    if decision is None:
        await callback.answer("Некорректное действие", show_alert=True)
        return

    moderator = callback.from_user
    if moderator is None:
        await callback.answer("Не удалось определить модератора", show_alert=True)
        return

    async with SessionFactory() as session:
        async with session.begin():
            scopes = await resolve_tg_user_scopes(session, moderator.id)
            if SCOPE_DIRECT_MESSAGES_MANAGE not in scopes:
                await callback.answer("Недостаточно прав", show_alert=True)
                return

            moderator_user = await upsert_user(session, moderator)
            review = await session.scalar(
                select(SuggestedPostReview)
                .where(SuggestedPostReview.id == decision.review_id)
                .with_for_update()
            )
            if review is None:
                await callback.answer("review не найден", show_alert=True)
                return

            if review.status != "PENDING":
                await callback.answer("Решение уже принято", show_alert=True)
                return

            if await is_chat_owner_confirmation_required(session, chat_id=review.source_chat_id):
                await callback.answer(
                    f"Автообработка на паузе. Нужна команда /confirmowner {review.source_chat_id}",
                    show_alert=True,
                )
                return

            try:
                if decision.approve:
                    await bot.approve_suggested_post(
                        chat_id=review.source_chat_id,
                        message_id=review.source_message_id,
                    )
                    review.status = "APPROVED"
                    review.decision_note = "Одобрено модерацией"
                    ui_note = "✅ Suggested post одобрен"
                else:
                    decline_reason = _DECLINE_REASON_MAP.get(
                        decision.decline_reason_code or "",
                        "Отклонено модерацией",
                    )
                    await bot.decline_suggested_post(
                        chat_id=review.source_chat_id,
                        message_id=review.source_message_id,
                        comment=decline_reason,
                    )
                    review.status = "DECLINED"
                    review.decision_note = decline_reason
                    ui_note = f"⛔ Suggested post отклонен: {decline_reason}"
            except TelegramAPIError as exc:
                review.status = "FAILED"
                review.decision_note = str(exc)
                review.decided_by_user_id = moderator_user.id
                review.decided_at = datetime.now(UTC)
                review.updated_at = datetime.now(UTC)
                await callback.answer("API ошибка при обработке suggested post", show_alert=True)
                return

            review.decided_by_user_id = moderator_user.id
            review.decided_at = datetime.now(UTC)
            review.updated_at = datetime.now(UTC)

    await callback.answer("Решение сохранено")

    message = callback.message
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    message_id = getattr(message, "message_id", None)
    if isinstance(chat_id, int) and isinstance(message_id, int):
        original_text = getattr(message, "text", None) or "Suggested post review"
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"{original_text}\n\n{ui_note}",
                reply_markup=None,
            )
        except Exception:
            pass
