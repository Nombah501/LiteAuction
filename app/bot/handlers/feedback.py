from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.moderation import feedback_actions_keyboard
from app.bot.states.feedback_intake import FeedbackIntakeStates
from app.db.enums import FeedbackStatus, FeedbackType, ModerationAction
from app.db.session import SessionFactory
from app.services.feedback_service import (
    approve_feedback,
    create_feedback,
    load_feedback_view,
    redeem_feedback_priority_boost,
    reject_feedback,
    render_feedback_text,
    set_feedback_queue_message,
    take_feedback_in_review,
)
from app.services.moderation_service import has_moderator_access, log_moderation_action
from app.services.moderation_topic_router import ModerationTopicSection, send_section_message
from app.services.user_service import upsert_user

router = Router(name="feedback")


def _extract_payload(message: Message) -> str | None:
    text = (message.text or "").strip()
    if not text:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    payload = parts[1].strip()
    return payload or None


def _feedback_label(feedback_type: FeedbackType) -> str:
    return "баг" if feedback_type == FeedbackType.BUG else "предложение"


def _feedback_section(feedback_type: FeedbackType) -> ModerationTopicSection:
    if feedback_type == FeedbackType.BUG:
        return ModerationTopicSection.BUGS
    return ModerationTopicSection.SUGGESTIONS


async def _create_feedback_item(
    *,
    message: Message,
    state: FSMContext,
    bot: Bot,
    feedback_type: FeedbackType,
    content: str,
) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        async with session.begin():
            submitter = await upsert_user(session, message.from_user, mark_private_started=True)
            created = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=feedback_type,
                content=content,
            )
            if not created.ok or created.item is None:
                await message.answer(created.message)
                return

            view = await load_feedback_view(session, created.item.id)
            if view is None:
                await message.answer("Не удалось сохранить сообщение")
                return

            queue_text = render_feedback_text(view)
            queue_status = FeedbackStatus(view.item.status)
            feedback_id = view.item.id

    queue_message = await send_section_message(
        bot,
        section=_feedback_section(feedback_type),
        text=queue_text,
        reply_markup=feedback_actions_keyboard(feedback_id=feedback_id, status=queue_status),
    )

    if queue_message is not None:
        async with SessionFactory() as session:
            async with session.begin():
                await set_feedback_queue_message(
                    session,
                    feedback_id=feedback_id,
                    chat_id=queue_message[0],
                    message_id=queue_message[1],
                )

    await state.clear()
    created_label = _feedback_label(feedback_type)
    if queue_message is None:
        await message.answer(f"Ваше {created_label} сохранено, но очередь модерации недоступна")
        return
    await message.answer(f"Ваше {created_label} отправлено модераторам")


@router.message(Command("bug"), F.chat.type == ChatType.PRIVATE)
async def command_bug(message: Message, state: FSMContext, bot: Bot) -> None:
    payload = _extract_payload(message)
    if payload is not None:
        await _create_feedback_item(
            message=message,
            state=state,
            bot=bot,
            feedback_type=FeedbackType.BUG,
            content=payload,
        )
        return

    await state.set_state(FeedbackIntakeStates.waiting_bug_text)
    await message.answer("Опишите баг в одном сообщении. Для отмены используйте /cancel")


@router.message(Command("suggest"), F.chat.type == ChatType.PRIVATE)
async def command_suggest(message: Message, state: FSMContext, bot: Bot) -> None:
    payload = _extract_payload(message)
    if payload is not None:
        await _create_feedback_item(
            message=message,
            state=state,
            bot=bot,
            feedback_type=FeedbackType.SUGGESTION,
            content=payload,
        )
        return

    await state.set_state(FeedbackIntakeStates.waiting_suggestion_text)
    await message.answer("Опишите предложение в одном сообщении. Для отмены используйте /cancel")


@router.message(Command("boostfeedback"), F.chat.type == ChatType.PRIVATE)
async def command_boost_feedback(message: Message, bot: Bot) -> None:
    if message.from_user is None or message.text is None:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /boostfeedback <feedback_id>")
        return

    feedback_id = int(parts[1])
    queue_chat_id: int | None = None
    queue_message_id: int | None = None
    queue_text = ""
    queue_status: FeedbackStatus | None = None
    result_message = ""
    result_changed = False
    async with SessionFactory() as session:
        async with session.begin():
            submitter = await upsert_user(session, message.from_user, mark_private_started=True)
            result = await redeem_feedback_priority_boost(
                session,
                feedback_id=feedback_id,
                submitter_user_id=submitter.id,
            )
            if not result.ok or result.item is None:
                await message.answer(result.message)
                return
            result_message = result.message
            result_changed = result.changed

            view = await load_feedback_view(session, feedback_id)
            if view is not None:
                queue_chat_id = view.item.queue_chat_id
                queue_message_id = view.item.queue_message_id
                queue_text = render_feedback_text(view)
                queue_status = FeedbackStatus(view.item.status)

    if queue_chat_id is not None and queue_message_id is not None and queue_status is not None:
        try:
            await bot.edit_message_text(
                chat_id=queue_chat_id,
                message_id=queue_message_id,
                text=queue_text,
                reply_markup=feedback_actions_keyboard(feedback_id=feedback_id, status=queue_status),
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

    if result_changed:
        await message.answer(f"{result_message}. Модераторы получат обновленную карточку.")
        return

    await message.answer(result_message)


@router.message(FeedbackIntakeStates.waiting_bug_text, F.text)
async def waiting_bug_text(message: Message, state: FSMContext, bot: Bot) -> None:
    await _create_feedback_item(
        message=message,
        state=state,
        bot=bot,
        feedback_type=FeedbackType.BUG,
        content=message.text or "",
    )


@router.message(FeedbackIntakeStates.waiting_suggestion_text, F.text)
async def waiting_suggestion_text(message: Message, state: FSMContext, bot: Bot) -> None:
    await _create_feedback_item(
        message=message,
        state=state,
        bot=bot,
        feedback_type=FeedbackType.SUGGESTION,
        content=message.text or "",
    )


@router.message(FeedbackIntakeStates.waiting_bug_text)
async def waiting_bug_text_invalid(message: Message) -> None:
    await message.answer("Нужен текст. Опишите баг в одном сообщении")


@router.message(FeedbackIntakeStates.waiting_suggestion_text)
async def waiting_suggestion_text_invalid(message: Message) -> None:
    await message.answer("Нужен текст. Опишите предложение в одном сообщении")


async def _notify_submitter_decision(
    bot: Bot,
    *,
    submitter_tg_user_id: int,
    feedback_type: FeedbackType,
    approved: bool,
    reward_points: int,
) -> None:
    label = "баг" if feedback_type == FeedbackType.BUG else "предложение"
    if approved:
        text = f"Ваше {label} одобрено модерацией. Начислено +{reward_points} points."
    else:
        text = f"Ваше {label} отклонено модерацией. Спасибо за участие."

    try:
        await bot.send_message(submitter_tg_user_id, text)
    except TelegramForbiddenError:
        return


@router.callback_query(F.data.startswith("modfb:"))
async def feedback_callbacks(callback: CallbackQuery, bot: Bot) -> None:
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
    feedback_id = int(parts[2])

    submitter_tg_user_id: int | None = None
    notify_feedback_type: FeedbackType | None = None
    notify_approved = False
    notify_reward_points = 0

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, callback.from_user)

            if action == "take":
                result = await take_feedback_in_review(
                    session,
                    feedback_id=feedback_id,
                    moderator_user_id=actor.id,
                    note="Взято в работу модератором",
                )
                audit_action = ModerationAction.TAKE_FEEDBACK
            elif action == "approve":
                result = await approve_feedback(
                    session,
                    feedback_id=feedback_id,
                    moderator_user_id=actor.id,
                    note="Одобрено модератором",
                )
                audit_action = ModerationAction.APPROVE_FEEDBACK
            elif action == "reject":
                result = await reject_feedback(
                    session,
                    feedback_id=feedback_id,
                    moderator_user_id=actor.id,
                    note="Отклонено модератором",
                )
                audit_action = ModerationAction.REJECT_FEEDBACK
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
                        "feedback_id": result.item.id,
                        "feedback_type": str(result.item.type),
                        "status": str(result.item.status),
                        "reward_points": result.item.reward_points,
                    },
                )

            if not result.ok or result.item is None:
                await callback.answer(result.message, show_alert=True)
                return

            view = await load_feedback_view(session, feedback_id)
            if view is None:
                await callback.answer("Запись не найдена", show_alert=True)
                return

            if result.changed and action in {"approve", "reject"} and view.submitter is not None:
                submitter_tg_user_id = view.submitter.tg_user_id
                notify_feedback_type = FeedbackType(view.item.type)
                notify_approved = action == "approve"
                notify_reward_points = view.item.reward_points

            updated_text = render_feedback_text(view)
            updated_keyboard = feedback_actions_keyboard(
                feedback_id=feedback_id,
                status=FeedbackStatus(view.item.status),
            )

    if callback.message is not None:
        try:
            await callback.message.edit_text(updated_text, reply_markup=updated_keyboard)
        except TelegramBadRequest:
            pass

    if submitter_tg_user_id is not None and notify_feedback_type is not None:
        await _notify_submitter_decision(
            bot,
            submitter_tg_user_id=submitter_tg_user_id,
            feedback_type=notify_feedback_type,
            approved=notify_approved,
            reward_points=notify_reward_points,
        )

    await callback.answer(result.message)
