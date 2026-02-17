from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.moderation import guarantor_actions_keyboard
from app.bot.states.guarantor_intake import GuarantorIntakeStates
from app.config import settings
from app.db.enums import GuarantorRequestStatus, ModerationAction
from app.db.session import SessionFactory
from app.services.guarantor_service import (
    assign_guarantor_request,
    create_guarantor_request,
    load_guarantor_request_view,
    redeem_guarantor_priority_boost,
    reject_guarantor_request,
    render_guarantor_request_text,
    set_guarantor_request_queue_message,
)
from app.services.moderation_checklist_service import ensure_checklist, list_checklist_replies, render_checklist_block
from app.services.moderation_service import has_moderator_access, log_moderation_action
from app.services.moderation_topic_router import ModerationTopicSection, send_section_message
from app.services.private_topics_service import (
    PrivateTopicPurpose,
    enforce_message_topic,
    send_user_topic_message,
)
from app.services.notification_policy_service import NotificationEventType
from app.services.user_service import upsert_user

router = Router(name="guarantor")


def _extract_payload(message: Message) -> str | None:
    text = (message.text or "").strip()
    if not text:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    payload = parts[1].strip()
    return payload or None


def _user_label(tg_user_id: int, username: str | None) -> str:
    if username:
        return f"@{username} ({tg_user_id})"
    return str(tg_user_id)


async def _ensure_guarantor_state_thread(message: Message, state: FSMContext, bot: Bot | None) -> bool:
    if not settings.private_topics_enabled or not settings.private_topics_strict_routing:
        return True

    data = await state.get_data()
    expected_thread_id = data.get("expected_thread_id")
    if not isinstance(expected_thread_id, int):
        return True
    if message.message_thread_id == expected_thread_id:
        return True

    await message.answer("Этот шаг доступен в разделе «Поддержка».")
    if bot is not None and message.chat is not None:
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                message_thread_id=expected_thread_id,
                text="Продолжим в разделе «Поддержка».",
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
    return False


async def _create_request_item(*, message: Message, state: FSMContext, bot: Bot, details: str) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        async with session.begin():
            submitter = await upsert_user(session, message.from_user, mark_private_started=True)
            created = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details=details,
            )
            if not created.ok or created.item is None:
                await message.answer(created.message)
                return

            view = await load_guarantor_request_view(session, created.item.id)
            if view is None:
                await message.answer("Не удалось сохранить запрос")
                return

            checklist_items = await ensure_checklist(
                session,
                entity_type="guarantor",
                entity_id=view.item.id,
            )
            checklist_replies = await list_checklist_replies(
                session,
                entity_type="guarantor",
                entity_id=view.item.id,
            )
            queue_text = (
                f"{render_guarantor_request_text(view)}\n\n"
                f"{render_checklist_block(checklist_items, replies_by_item=checklist_replies)}"
                if checklist_items
                else render_guarantor_request_text(view)
            )
            queue_status = GuarantorRequestStatus(view.item.status)
            request_id = view.item.id

    queue_message = await send_section_message(
        bot,
        section=ModerationTopicSection.GUARANTORS,
        text=queue_text,
        reply_markup=guarantor_actions_keyboard(request_id=request_id, status=queue_status),
    )

    if queue_message is not None:
        async with SessionFactory() as session:
            async with session.begin():
                await set_guarantor_request_queue_message(
                    session,
                    request_id=request_id,
                    chat_id=queue_message[0],
                    message_id=queue_message[1],
                )

    await state.clear()
    if queue_message is None:
        await message.answer("Запрос гаранта сохранен, но очередь модерации недоступна")
        return
    await message.answer("Запрос гаранта отправлен модераторам")


@router.message(Command("guarant"), F.chat.type == ChatType.PRIVATE)
async def command_guarant(message: Message, state: FSMContext, bot: Bot) -> None:
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
                purpose=PrivateTopicPurpose.SUPPORT,
                command_hint="/guarant",
            ):
                return

    payload = _extract_payload(message)
    if payload is not None:
        await _create_request_item(message=message, state=state, bot=bot, details=payload)
        return

    await state.set_state(GuarantorIntakeStates.waiting_request_text)
    if isinstance(message.message_thread_id, int):
        await state.update_data(expected_thread_id=message.message_thread_id)
    await message.answer("Опишите запрос на гаранта одним сообщением. Для отмены используйте /cancel")


@router.message(Command("boostguarant"), F.chat.type == ChatType.PRIVATE)
async def command_boost_guarant(message: Message, bot: Bot) -> None:
    if message.from_user is None or message.text is None:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /boostguarant <request_id>")
        return

    request_id = int(parts[1])
    queue_chat_id: int | None = None
    queue_message_id: int | None = None
    queue_text = ""
    queue_status: GuarantorRequestStatus | None = None
    result_message = ""
    result_changed = False

    async with SessionFactory() as session:
        async with session.begin():
            submitter = await upsert_user(session, message.from_user, mark_private_started=True)
            if not await enforce_message_topic(
                message,
                bot=bot,
                session=session,
                user=submitter,
                purpose=PrivateTopicPurpose.POINTS,
                command_hint=f"/boostguarant {request_id}",
            ):
                return
            result = await redeem_guarantor_priority_boost(
                session,
                request_id=request_id,
                submitter_user_id=submitter.id,
            )
            if not result.ok or result.item is None:
                await message.answer(result.message)
                return

            result_message = result.message
            result_changed = result.changed
            view = await load_guarantor_request_view(session, request_id)
            if view is not None:
                queue_chat_id = view.item.queue_chat_id
                queue_message_id = view.item.queue_message_id
                checklist_items = await ensure_checklist(
                    session,
                    entity_type="guarantor",
                    entity_id=view.item.id,
                )
                checklist_replies = await list_checklist_replies(
                    session,
                    entity_type="guarantor",
                    entity_id=view.item.id,
                )
                queue_text = (
                    f"{render_guarantor_request_text(view)}\n\n"
                    f"{render_checklist_block(checklist_items, replies_by_item=checklist_replies)}"
                    if checklist_items
                    else render_guarantor_request_text(view)
                )
                queue_status = GuarantorRequestStatus(view.item.status)

    if queue_chat_id is not None and queue_message_id is not None and queue_status is not None:
        try:
            await bot.edit_message_text(
                chat_id=queue_chat_id,
                message_id=queue_message_id,
                text=queue_text,
                reply_markup=guarantor_actions_keyboard(request_id=request_id, status=queue_status),
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

    if result_changed:
        await message.answer(f"{result_message}. Модераторы получат обновленную карточку.")
        return

    await message.answer(result_message)


@router.message(GuarantorIntakeStates.waiting_request_text, F.text)
async def waiting_guarantor_text(message: Message, state: FSMContext, bot: Bot) -> None:
    if not await _ensure_guarantor_state_thread(message, state, bot):
        return
    await _create_request_item(message=message, state=state, bot=bot, details=message.text or "")


@router.message(GuarantorIntakeStates.waiting_request_text)
async def waiting_guarantor_text_invalid(
    message: Message,
    state: FSMContext,
    bot: Bot | None = None,
) -> None:
    if not await _ensure_guarantor_state_thread(message, state, bot):
        return
    await message.answer("Нужен текст. Опишите запрос на гаранта одним сообщением")


async def _notify_submitter_decision(
    bot: Bot,
    *,
    submitter_tg_user_id: int,
    assigned: bool,
    moderator_tg_user_id: int | None,
    moderator_username: str | None,
) -> None:
    if assigned:
        if moderator_tg_user_id is not None:
            moderator_label = _user_label(moderator_tg_user_id, moderator_username)
            text = f"Ваш запрос на гаранта принят. Ответственный модератор: {moderator_label}."
        else:
            text = "Ваш запрос на гаранта принят. С вами свяжется модератор."
    else:
        text = "Ваш запрос на гаранта отклонен модерацией."

    await send_user_topic_message(
        bot,
        tg_user_id=submitter_tg_user_id,
        purpose=PrivateTopicPurpose.SUPPORT,
        text=text,
        notification_event=NotificationEventType.SUPPORT,
    )


@router.callback_query(F.data.startswith("modgr:"))
async def guarantor_callbacks(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None or callback.data is None:
        return

    async with SessionFactory() as session:
        if not await has_moderator_access(session, callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return

    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer("Некорректные данные", show_alert=True)
        return

    action = parts[1]
    request_id = int(parts[2])

    submitter_tg_user_id: int | None = None
    notify_assigned = False
    notify_moderator_tg_user_id: int | None = None
    notify_moderator_username: str | None = None

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, callback.from_user)

            if action == "assign":
                result = await assign_guarantor_request(
                    session,
                    request_id=request_id,
                    moderator_user_id=actor.id,
                    note="Назначено модератором как гарант",
                )
                audit_action = ModerationAction.ASSIGN_GUARANTOR_REQUEST
            elif action == "reject":
                result = await reject_guarantor_request(
                    session,
                    request_id=request_id,
                    moderator_user_id=actor.id,
                    note="Отклонено модератором",
                )
                audit_action = ModerationAction.REJECT_GUARANTOR_REQUEST
            else:
                await callback.answer("Некорректное действие", show_alert=True)
                return

            if result.item is not None and result.ok:
                await log_moderation_action(
                    session,
                    actor_user_id=actor.id,
                    action=audit_action,
                    reason=result.message,
                    target_user_id=result.item.submitter_user_id,
                    payload={
                        "guarantor_request_id": result.item.id,
                        "status": str(result.item.status),
                    },
                )

            if not result.ok or result.item is None:
                await callback.answer(result.message, show_alert=True)
                return

            view = await load_guarantor_request_view(session, request_id)
            if view is None:
                await callback.answer("Запрос не найден", show_alert=True)
                return

            if result.changed and view.submitter is not None:
                submitter_tg_user_id = view.submitter.tg_user_id
                notify_assigned = action == "assign"
                if notify_assigned and view.moderator is not None:
                    notify_moderator_tg_user_id = view.moderator.tg_user_id
                    notify_moderator_username = view.moderator.username

            checklist_items = await ensure_checklist(
                session,
                entity_type="guarantor",
                entity_id=view.item.id,
            )
            checklist_replies = await list_checklist_replies(
                session,
                entity_type="guarantor",
                entity_id=view.item.id,
            )
            updated_text = (
                f"{render_guarantor_request_text(view)}\n\n"
                f"{render_checklist_block(checklist_items, replies_by_item=checklist_replies)}"
                if checklist_items
                else render_guarantor_request_text(view)
            )
            updated_keyboard = guarantor_actions_keyboard(
                request_id=request_id,
                status=GuarantorRequestStatus(view.item.status),
            )

    if callback.message is not None:
        try:
            await callback.message.edit_text(updated_text, reply_markup=updated_keyboard)
        except TelegramBadRequest:
            pass

    if submitter_tg_user_id is not None:
        await _notify_submitter_decision(
            bot,
            submitter_tg_user_id=submitter_tg_user_id,
            assigned=notify_assigned,
            moderator_tg_user_id=notify_moderator_tg_user_id,
            moderator_username=notify_moderator_username,
        )

    await callback.answer(result.message)
