from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.auction import draft_publish_keyboard
from app.bot.states.auction_create import AuctionCreateStates
from app.config import settings
from app.db.session import SessionFactory
from app.services.auction_create_wizard_service import (
    WIZARD_STEP_WAITING_ANTI_SNIPER,
    WIZARD_STEP_WAITING_BUYOUT_PRICE,
    WIZARD_STEP_WAITING_DESCRIPTION,
    WIZARD_STEP_WAITING_DURATION,
    WIZARD_STEP_WAITING_MIN_STEP,
    WIZARD_STEP_WAITING_PHOTO,
    WIZARD_STEP_WAITING_START_PRICE,
    delete_numeric_input_message,
    upsert_create_wizard_progress,
)
from app.services.auction_service import create_draft_auction, load_auction_view, render_auction_caption
from app.services.bot_funnel_metrics_service import (
    BotFunnelActorRole,
    BotFunnelJourney,
    BotFunnelStep,
    record_bot_funnel_event,
)
from app.services.channel_dm_intake_service import (
    AuctionIntakeKind,
    extract_direct_messages_topic_id,
    resolve_auction_intake_actor,
    resolve_auction_intake_context,
)
from app.services.message_draft_service import send_progress_draft
from app.services.moderation_service import is_tg_user_blacklisted
from app.services.private_topics_service import (
    PrivateTopicPurpose,
    enforce_callback_topic,
    enforce_message_topic,
)
from app.services.publish_gate_service import evaluate_seller_publish_gate
from app.services.user_service import upsert_user

router = Router(name="create_auction")
MAX_AUCTION_PHOTOS = 10
logger = logging.getLogger(__name__)


async def _record_create_auction_funnel(
    *,
    step: BotFunnelStep,
    context_key: str,
    failure_reason: str | None = None,
) -> None:
    await record_bot_funnel_event(
        journey=BotFunnelJourney.AUCTION_CREATE,
        step=step,
        actor_role=BotFunnelActorRole.SELLER,
        context_key=context_key,
        failure_reason=failure_reason,
    )


def _parse_usd_amount(text: str) -> int | None:
    raw = text.strip().replace("$", "")
    if not raw.isdigit():
        return None
    amount = int(raw)
    if amount < 1:
        return None
    return amount


def _anchor_message(target: Message | CallbackQuery | None) -> Message | None:
    if isinstance(target, Message):
        return target
    if isinstance(target, CallbackQuery) and isinstance(target.message, Message):
        return target.message
    return None


async def _update_wizard(
    *,
    state: FSMContext,
    bot: Bot | None,
    target: Message | CallbackQuery | None,
    step_name: str,
    hint: str,
    error: str | None = None,
    finished: bool = False,
    last_event: str | None = None,
    force_repost: bool = False,
) -> None:
    await upsert_create_wizard_progress(
        state=state,
        bot=bot,
        anchor_message=_anchor_message(target),
        step_name=step_name,
        hint=hint,
        error=error,
        finished=finished,
        last_event=last_event,
        force_repost=force_repost,
    )


async def _append_photo_file_id(state: FSMContext, file_id: str) -> tuple[int, bool, bool]:
    data = await state.get_data()
    photo_ids_raw = data.get("photo_file_ids")
    if isinstance(photo_ids_raw, list):
        photo_file_ids = [str(item) for item in photo_ids_raw if str(item)]
    else:
        fallback = data.get("photo_file_id")
        photo_file_ids = [str(fallback)] if isinstance(fallback, str) and fallback else []

    if file_id in photo_file_ids:
        return len(photo_file_ids), False, False
    if len(photo_file_ids) >= MAX_AUCTION_PHOTOS:
        return len(photo_file_ids), False, True

    photo_file_ids.append(file_id)
    await state.update_data(photo_file_id=photo_file_ids[0], photo_file_ids=photo_file_ids)
    return len(photo_file_ids), True, False


async def _mark_media_group_seen(state: FSMContext, media_group_id: str | None) -> bool:
    if not media_group_id:
        return False

    data = await state.get_data()
    seen_raw = data.get("photo_media_group_ids")
    seen_group_ids = [str(item) for item in seen_raw if str(item)] if isinstance(seen_raw, list) else []
    if media_group_id in seen_group_ids:
        return False

    seen_group_ids.append(media_group_id)
    await state.update_data(photo_media_group_ids=seen_group_ids[-20:])
    return True


async def _store_expected_intake_scope(state: FSMContext, message: Message) -> None:
    context = resolve_auction_intake_context(message)
    thread_id = getattr(message, "message_thread_id", None)
    expected_thread_id: int | None = None
    expected_direct_messages_topic_id: int | None = None
    if context.kind == AuctionIntakeKind.PRIVATE and isinstance(thread_id, int):
        expected_thread_id = thread_id
    if context.kind == AuctionIntakeKind.CHANNEL_DM and isinstance(context.direct_messages_topic_id, int):
        expected_direct_messages_topic_id = context.direct_messages_topic_id
    await state.update_data(
        expected_thread_id=expected_thread_id,
        expected_direct_messages_topic_id=expected_direct_messages_topic_id,
    )


async def _handle_unsupported_newauction_context(message: Message) -> bool:
    context = resolve_auction_intake_context(message)
    if context.kind != AuctionIntakeKind.UNSUPPORTED:
        return False

    if context.reason == "channel_dm_disabled":
        await message.answer("Создание лота через DM канала временно отключено.")
    elif context.reason == "channel_dm_chat_not_allowed":
        await message.answer("Этот DM-чат канала не подключен для создания лотов.")
    elif context.reason == "missing_direct_topic_id":
        await message.answer("Не удалось определить тему DM канала. Откройте нужную тему и повторите /newauction.")
    elif context.reason == "unsupported_chat_type":
        await message.answer("Команда доступна в личке бота или в теме DM канала.")
    return True


async def _handle_unsupported_newauction_callback(callback: CallbackQuery) -> bool:
    message = callback.message
    context = resolve_auction_intake_context(message if isinstance(message, Message) else None)
    if context.kind != AuctionIntakeKind.UNSUPPORTED:
        return False

    if context.reason == "channel_dm_disabled":
        await callback.answer("DM-режим канала выключен", show_alert=True)
    elif context.reason == "channel_dm_chat_not_allowed":
        await callback.answer("Этот DM-чат не подключен", show_alert=True)
    elif context.reason == "missing_direct_topic_id":
        await callback.answer("Откройте нужную тему DM канала", show_alert=True)
    elif context.reason == "unsupported_chat_type":
        await callback.answer("Откройте личку бота или DM тему", show_alert=True)
    return True


async def _ensure_auction_state_message(
    message: Message,
    state: FSMContext,
    bot: Bot | None,
) -> bool:
    data = await state.get_data()
    expected_dm_topic_id = data.get("expected_direct_messages_topic_id")
    if isinstance(expected_dm_topic_id, int):
        current_dm_topic_id = extract_direct_messages_topic_id(message)
        if current_dm_topic_id == expected_dm_topic_id:
            return True

        await message.answer("Продолжайте создание лота в исходной теме DM канала.")
        chat_id = getattr(getattr(message, "chat", None), "id", None)
        if bot is not None and isinstance(chat_id, int):
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    direct_messages_topic_id=expected_dm_topic_id,
                    text="Продолжим создание лота здесь.",
                )
            except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError) as exc:
                logger.warning(
                    "newauction_redirect_dm_failed chat_id=%s topic_id=%s error=%s",
                    chat_id,
                    expected_dm_topic_id,
                    exc,
                )
        return False

    if not settings.private_topics_enabled or not settings.private_topics_strict_routing:
        return True

    expected_thread_id = data.get("expected_thread_id")
    if not isinstance(expected_thread_id, int):
        return True
    if getattr(message, "message_thread_id", None) == expected_thread_id:
        return True

    await message.answer("Продолжайте создание лота в разделе «Лоты».")
    if bot is not None and message.chat is not None:
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                message_thread_id=expected_thread_id,
                text="Продолжим создание лота здесь.",
            )
        except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError) as exc:
            logger.warning(
                "newauction_redirect_topic_failed chat_id=%s thread_id=%s error=%s",
                message.chat.id,
                expected_thread_id,
                exc,
            )
    return False


async def _ensure_auction_state_callback(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot | None,
) -> bool:
    message = callback.message
    if message is None or not isinstance(message, Message):
        return True

    data = await state.get_data()
    expected_dm_topic_id = data.get("expected_direct_messages_topic_id")
    if isinstance(expected_dm_topic_id, int):
        current_dm_topic_id = extract_direct_messages_topic_id(message)
        if current_dm_topic_id == expected_dm_topic_id:
            return True

        await callback.answer("Откройте исходную тему DM канала", show_alert=True)
        chat_id = getattr(getattr(message, "chat", None), "id", None)
        if bot is not None and isinstance(chat_id, int):
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    direct_messages_topic_id=expected_dm_topic_id,
                    text="Продолжим создание лота здесь.",
                )
            except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError) as exc:
                logger.warning(
                    "newauction_callback_redirect_dm_failed chat_id=%s topic_id=%s error=%s",
                    chat_id,
                    expected_dm_topic_id,
                    exc,
                )
        return False

    if not settings.private_topics_enabled or not settings.private_topics_strict_routing:
        return True

    expected_thread_id = data.get("expected_thread_id")
    if not isinstance(expected_thread_id, int):
        return True
    if getattr(message, "message_thread_id", None) == expected_thread_id:
        return True

    await callback.answer("Откройте раздел «Лоты»", show_alert=True)
    if bot is not None and message.chat is not None:
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                message_thread_id=expected_thread_id,
                text="Продолжим создание лота здесь.",
            )
        except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError) as exc:
            logger.warning(
                "newauction_callback_redirect_topic_failed chat_id=%s thread_id=%s error=%s",
                message.chat.id,
                expected_thread_id,
                exc,
            )
    return False


@router.message(Command("newauction"), F.chat.type.in_({ChatType.PRIVATE, ChatType.SUPERGROUP}))
async def command_new_auction(message: Message, state: FSMContext, bot: Bot) -> None:
    if await _handle_unsupported_newauction_context(message):
        await _record_create_auction_funnel(
            step=BotFunnelStep.FAIL,
            context_key="command_newauction",
            failure_reason="unsupported_context",
        )
        return

    actor = resolve_auction_intake_actor(message)
    if actor is None:
        await _record_create_auction_funnel(
            step=BotFunnelStep.FAIL,
            context_key="command_newauction",
            failure_reason="actor_missing",
        )
        await message.answer("Не удалось определить отправителя. Попробуйте снова из личного диалога.")
        return

    intake_context = resolve_auction_intake_context(message)
    async with SessionFactory() as session:
        async with session.begin():
            seller = await upsert_user(session, actor, mark_private_started=True)
            if intake_context.kind == AuctionIntakeKind.PRIVATE and not await enforce_message_topic(
                message,
                bot=bot,
                session=session,
                user=seller,
                purpose=PrivateTopicPurpose.AUCTIONS,
                command_hint="/newauction",
            ):
                await _record_create_auction_funnel(
                    step=BotFunnelStep.FAIL,
                    context_key="command_newauction",
                    failure_reason="topic_routing",
                )
                return
            if await is_tg_user_blacklisted(session, actor.id):
                await _record_create_auction_funnel(
                    step=BotFunnelStep.FAIL,
                    context_key="command_newauction",
                    failure_reason="blacklisted",
                )
                await message.answer("Вы в черном списке и не можете создавать аукционы")
                return

    await state.clear()
    await state.set_state(AuctionCreateStates.waiting_photo)
    await _store_expected_intake_scope(state, message)
    await _record_create_auction_funnel(
        step=BotFunnelStep.START,
        context_key="command_newauction",
    )
    await _update_wizard(
        state=state,
        bot=bot,
        target=message,
        step_name=WIZARD_STEP_WAITING_PHOTO,
        hint="Отправьте фото лота (до 10 штук). Когда закончите, нажмите 'Готово'.",
        last_event="Старт мастера: шаг 1 из 7.",
    )


@router.callback_query(F.data == "create:new")
async def callback_new_auction(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if await _handle_unsupported_newauction_callback(callback):
        await _record_create_auction_funnel(
            step=BotFunnelStep.FAIL,
            context_key="callback_create_new",
            failure_reason="unsupported_context",
        )
        return

    actor = callback.from_user
    if actor is None and isinstance(callback.message, Message):
        actor = resolve_auction_intake_actor(callback.message)
    if actor is None:
        await _record_create_auction_funnel(
            step=BotFunnelStep.FAIL,
            context_key="callback_create_new",
            failure_reason="actor_missing",
        )
        await callback.answer("Не удалось определить отправителя", show_alert=True)
        return

    intake_context = resolve_auction_intake_context(callback.message if isinstance(callback.message, Message) else None)
    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, actor, mark_private_started=True)
            if intake_context.kind == AuctionIntakeKind.PRIVATE and not await enforce_callback_topic(
                callback,
                bot=bot,
                session=session,
                user=user,
                purpose=PrivateTopicPurpose.AUCTIONS,
                command_hint="/newauction",
            ):
                await _record_create_auction_funnel(
                    step=BotFunnelStep.FAIL,
                    context_key="callback_create_new",
                    failure_reason="topic_routing",
                )
                return
            if await is_tg_user_blacklisted(session, actor.id):
                await _record_create_auction_funnel(
                    step=BotFunnelStep.FAIL,
                    context_key="callback_create_new",
                    failure_reason="blacklisted",
                )
                await callback.answer("Вы в черном списке", show_alert=True)
                return

    await state.clear()
    await state.set_state(AuctionCreateStates.waiting_photo)
    if isinstance(callback.message, Message):
        await _store_expected_intake_scope(state, callback.message)
    await _record_create_auction_funnel(
        step=BotFunnelStep.START,
        context_key="callback_create_new",
    )
    await callback.answer()
    if callback.message:
        await _update_wizard(
            state=state,
            bot=bot,
            target=callback,
            step_name=WIZARD_STEP_WAITING_PHOTO,
            hint="Отправьте фото лота (до 10 штук). Когда закончите, нажмите 'Готово'.",
            last_event="Старт мастера: шаг 1 из 7.",
        )


@router.message(Command("cancel"), F.chat.type.in_({ChatType.PRIVATE, ChatType.SUPERGROUP}))
async def cancel_creation(message: Message, state: FSMContext) -> None:
    context = resolve_auction_intake_context(message)
    if context.kind == AuctionIntakeKind.UNSUPPORTED:
        return
    await state.clear()
    await message.answer("Создание аукциона отменено. Для нового лота нажмите /newauction")


@router.message(AuctionCreateStates.waiting_photo, F.photo)
async def create_photo_step(message: Message, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_message(message, state, bot):
        return
    if not message.photo:
        return
    photo = message.photo[-1]
    count, added, max_reached = await _append_photo_file_id(state, photo.file_id)
    if max_reached:
        await _update_wizard(
            state=state,
            bot=bot,
            target=message,
            step_name=WIZARD_STEP_WAITING_PHOTO,
            hint="Нажмите 'Готово' или удалите лишние фото.",
            error=f"Можно добавить максимум {MAX_AUCTION_PHOTOS} фото.",
            last_event=f"Лимит фото достигнут: {MAX_AUCTION_PHOTOS}/{MAX_AUCTION_PHOTOS}.",
        )
        return

    if not added:
        return

    if message.media_group_id is None:
        await _update_wizard(
            state=state,
            bot=bot,
            target=message,
            step_name=WIZARD_STEP_WAITING_PHOTO,
            hint=f"Фото добавлено ({count}/{MAX_AUCTION_PHOTOS}). Отправьте еще или нажмите 'Готово'.",
            last_event=f"Добавлено фото: {count}/{MAX_AUCTION_PHOTOS}.",
            force_repost=True,
        )
        return

    if await _mark_media_group_seen(state, message.media_group_id):
        await _update_wizard(
            state=state,
            bot=bot,
            target=message,
            step_name=WIZARD_STEP_WAITING_PHOTO,
            hint="Альбом принят. После отправки всех фото нажмите 'Готово'.",
            last_event=f"Принят альбом. Фото сейчас: {count}/{MAX_AUCTION_PHOTOS}.",
            force_repost=True,
        )


@router.callback_query(AuctionCreateStates.waiting_photo, F.data == "create:photos:done")
async def create_photos_done(callback: CallbackQuery, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_callback(callback, state, bot):
        return
    data = await state.get_data()
    photo_ids_raw = data.get("photo_file_ids")
    photo_file_ids = photo_ids_raw if isinstance(photo_ids_raw, list) else []
    if not photo_file_ids:
        await callback.answer("Сначала добавьте хотя бы одно фото", show_alert=True)
        return

    await state.set_state(AuctionCreateStates.waiting_description)
    await callback.answer()
    if callback.message is not None:
        await _update_wizard(
            state=state,
            bot=bot,
            target=callback,
            step_name=WIZARD_STEP_WAITING_DESCRIPTION,
            hint="Отлично. Теперь отправьте описание лота.",
            last_event="Фото сохранены. Переход к описанию.",
        )


@router.message(AuctionCreateStates.waiting_photo)
async def create_photo_step_invalid(message: Message, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_message(message, state, bot):
        return
    await _update_wizard(
        state=state,
        bot=bot,
        target=message,
        step_name=WIZARD_STEP_WAITING_PHOTO,
        hint="Отправьте фото лота (можно несколько), затем нажмите 'Готово'.",
    )


@router.message(AuctionCreateStates.waiting_description, F.text)
async def create_description_step(message: Message, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_message(message, state, bot):
        return
    description = (message.text or "").strip()
    if len(description) < 3:
        await _update_wizard(
            state=state,
            bot=bot,
            target=message,
            step_name=WIZARD_STEP_WAITING_DESCRIPTION,
            hint="Пришлите описание лота текстом.",
            error="Описание слишком короткое. Добавьте больше деталей.",
        )
        return

    await state.update_data(description=description)
    await state.set_state(AuctionCreateStates.waiting_start_price)
    await _update_wizard(
        state=state,
        bot=bot,
        target=message,
        step_name=WIZARD_STEP_WAITING_START_PRICE,
        hint="Укажите начальную цену в USD (целое число, минимум 1).",
        last_event="Описание сохранено.",
    )


@router.message(AuctionCreateStates.waiting_description, F.photo)
async def create_description_collect_photo(message: Message, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_message(message, state, bot):
        return
    if not message.photo:
        return
    photo = message.photo[-1]
    count, added, max_reached = await _append_photo_file_id(state, photo.file_id)
    if max_reached:
        await _update_wizard(
            state=state,
            bot=bot,
            target=message,
            step_name=WIZARD_STEP_WAITING_DESCRIPTION,
            hint="Пришлите описание лота текстом.",
            error=f"Можно добавить максимум {MAX_AUCTION_PHOTOS} фото.",
            last_event=f"Лимит фото достигнут: {MAX_AUCTION_PHOTOS}/{MAX_AUCTION_PHOTOS}.",
        )
        return

    if not added:
        return

    if message.media_group_id is None:
        await _update_wizard(
            state=state,
            bot=bot,
            target=message,
            step_name=WIZARD_STEP_WAITING_DESCRIPTION,
            hint=f"Фото добавлено ({count}/{MAX_AUCTION_PHOTOS}). Теперь пришлите описание текстом.",
            last_event=f"Добавлено фото: {count}/{MAX_AUCTION_PHOTOS}.",
            force_repost=True,
        )
        return

    if await _mark_media_group_seen(state, message.media_group_id):
        await _update_wizard(
            state=state,
            bot=bot,
            target=message,
            step_name=WIZARD_STEP_WAITING_DESCRIPTION,
            hint="Альбом принят. После отправки всех фото пришлите описание текстом.",
            last_event=f"Принят альбом. Фото сейчас: {count}/{MAX_AUCTION_PHOTOS}.",
            force_repost=True,
        )


@router.message(AuctionCreateStates.waiting_description)
async def create_description_step_invalid(message: Message, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_message(message, state, bot):
        return
    await _update_wizard(
        state=state,
        bot=bot,
        target=message,
        step_name=WIZARD_STEP_WAITING_DESCRIPTION,
        hint="Пришлите описание лота текстом.",
    )


@router.message(AuctionCreateStates.waiting_start_price, F.text)
async def create_start_price_step(message: Message, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_message(message, state, bot):
        return
    amount = _parse_usd_amount(message.text or "")
    if amount is None:
        await _update_wizard(
            state=state,
            bot=bot,
            target=message,
            step_name=WIZARD_STEP_WAITING_START_PRICE,
            hint="Введите стартовую цену текстом (например: 100).",
            error="Некорректная цена. Введите целое число USD, минимум 1.",
        )
        return

    await state.update_data(start_price=amount)
    await delete_numeric_input_message(message)
    await state.set_state(AuctionCreateStates.waiting_buyout_price)
    await _update_wizard(
        state=state,
        bot=bot,
        target=message,
        step_name=WIZARD_STEP_WAITING_BUYOUT_PRICE,
        hint="Укажите цену выкупа в USD или нажмите 'Пропустить'. Цена выкупа не может быть ниже стартовой.",
        last_event=f"Стартовая цена сохранена: ${amount}.",
    )


@router.message(AuctionCreateStates.waiting_start_price)
async def create_start_price_step_invalid(message: Message, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_message(message, state, bot):
        return
    await _update_wizard(
        state=state,
        bot=bot,
        target=message,
        step_name=WIZARD_STEP_WAITING_START_PRICE,
        hint="Введите стартовую цену текстом (например: 100).",
    )


@router.callback_query(AuctionCreateStates.waiting_buyout_price, F.data == "create:buyout:skip")
async def create_buyout_skip(callback: CallbackQuery, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_callback(callback, state, bot):
        return
    await state.update_data(buyout_price=None)
    await state.set_state(AuctionCreateStates.waiting_min_step)
    await callback.answer("Выкуп пропущен")
    await _update_wizard(
        state=state,
        bot=bot,
        target=callback,
        step_name=WIZARD_STEP_WAITING_MIN_STEP,
        hint="Укажите минимальный шаг ставки в USD (например: 1 или 5).",
        last_event="Цена выкупа пропущена.",
    )


@router.message(AuctionCreateStates.waiting_buyout_price, F.text)
async def create_buyout_step(message: Message, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_message(message, state, bot):
        return
    buyout_price = _parse_usd_amount(message.text or "")
    if buyout_price is None:
        await _update_wizard(
            state=state,
            bot=bot,
            target=message,
            step_name=WIZARD_STEP_WAITING_BUYOUT_PRICE,
            hint="Введите цену выкупа текстом или нажмите 'Пропустить'.",
            error="Некорректная цена выкупа. Введите целое число или нажмите 'Пропустить'.",
        )
        return

    data = await state.get_data()
    start_price = int(data["start_price"])
    if buyout_price < start_price:
        await _update_wizard(
            state=state,
            bot=bot,
            target=message,
            step_name=WIZARD_STEP_WAITING_BUYOUT_PRICE,
            hint="Введите цену выкупа или нажмите 'Пропустить'.",
            error="Цена выкупа не может быть ниже начальной цены.",
        )
        return

    await state.update_data(buyout_price=buyout_price)
    await delete_numeric_input_message(message)
    await state.set_state(AuctionCreateStates.waiting_min_step)
    await _update_wizard(
        state=state,
        bot=bot,
        target=message,
        step_name=WIZARD_STEP_WAITING_MIN_STEP,
        hint="Укажите минимальный шаг ставки в USD (например: 1 или 5).",
        last_event=f"Цена выкупа сохранена: ${buyout_price}.",
    )


@router.message(AuctionCreateStates.waiting_buyout_price)
async def create_buyout_step_invalid(message: Message, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_message(message, state, bot):
        return
    await _update_wizard(
        state=state,
        bot=bot,
        target=message,
        step_name=WIZARD_STEP_WAITING_BUYOUT_PRICE,
        hint="Введите цену выкупа текстом или нажмите 'Пропустить'.",
    )


@router.message(AuctionCreateStates.waiting_min_step, F.text)
async def create_min_step_step(message: Message, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_message(message, state, bot):
        return
    min_step = _parse_usd_amount(message.text or "")
    if min_step is None:
        await _update_wizard(
            state=state,
            bot=bot,
            target=message,
            step_name=WIZARD_STEP_WAITING_MIN_STEP,
            hint="Введите минимальный шаг текстом (например: 1).",
            error="Некорректный шаг. Введите целое число USD, минимум 1.",
        )
        return

    await state.update_data(min_step=min_step)
    await delete_numeric_input_message(message)
    await state.set_state(AuctionCreateStates.waiting_duration)
    await _update_wizard(
        state=state,
        bot=bot,
        target=message,
        step_name=WIZARD_STEP_WAITING_DURATION,
        hint="Выберите длительность аукциона.",
        last_event=f"Минимальный шаг сохранен: ${min_step}.",
    )


@router.message(AuctionCreateStates.waiting_min_step)
async def create_min_step_step_invalid(message: Message, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_message(message, state, bot):
        return
    await _update_wizard(
        state=state,
        bot=bot,
        target=message,
        step_name=WIZARD_STEP_WAITING_MIN_STEP,
        hint="Введите минимальный шаг текстом (например: 1).",
    )


@router.callback_query(AuctionCreateStates.waiting_duration, F.data.startswith("create:duration:"))
async def create_duration_step(callback: CallbackQuery, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_callback(callback, state, bot):
        return
    if callback.data is None:
        await callback.answer("Некорректная длительность", show_alert=True)
        return
    duration_raw = callback.data.split(":")[-1]
    if duration_raw not in {"6", "12", "18", "24"}:
        await callback.answer("Некорректная длительность", show_alert=True)
        return

    await state.update_data(duration_hours=int(duration_raw))
    await state.set_state(AuctionCreateStates.waiting_anti_sniper)
    await callback.answer()
    if callback.message:
        await _update_wizard(
            state=state,
            bot=bot,
            target=callback,
            step_name=WIZARD_STEP_WAITING_ANTI_SNIPER,
            hint=(
                "Антиснайпер включить? Если ставка в последние 2 минуты, дедлайн "
                "продлится на 3 минуты, максимум 3 раза."
            ),
            last_event=f"Длительность выбрана: {duration_raw} ч.",
        )


@router.callback_query(AuctionCreateStates.waiting_anti_sniper, F.data.startswith("create:antisniper:"))
async def create_anti_sniper_step(callback: CallbackQuery, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_callback(callback, state, bot):
        return
    if callback.from_user is None or callback.message is None or callback.data is None:
        return

    async with SessionFactory() as session:
        if await is_tg_user_blacklisted(session, callback.from_user.id):
            await _record_create_auction_funnel(
                step=BotFunnelStep.FAIL,
                context_key="wizard_finalize",
                failure_reason="blacklisted",
            )
            await callback.answer("Вы в черном списке", show_alert=True)
            await state.clear()
            return

    anti_sniper = callback.data.endswith(":1")
    await state.update_data(anti_sniper_enabled=anti_sniper)
    data = await state.get_data()

    await _update_wizard(
        state=state,
        bot=bot,
        target=callback,
        step_name=WIZARD_STEP_WAITING_ANTI_SNIPER,
        hint="Собираю черновик и проверяю условия публикации...",
        last_event="Антиснайпер включен." if anti_sniper else "Антиснайпер выключен.",
    )

    if isinstance(callback.message, Message):
        await send_progress_draft(
            bot,
            callback.message,
            text="Собираю черновик и проверяю условия публикации...",
            scope_key="newauction-finalize",
        )

    async with SessionFactory() as session:
        publish_gate = None
        seller = await upsert_user(session, callback.from_user, mark_private_started=True)
        auction = await create_draft_auction(
            session,
            seller_user_id=seller.id,
            photo_file_id=data["photo_file_id"],
            photo_file_ids=data.get("photo_file_ids"),
            description=data["description"],
            start_price=int(data["start_price"]),
            buyout_price=data.get("buyout_price"),
            min_step=int(data["min_step"]),
            duration_hours=int(data["duration_hours"]),
            anti_sniper_enabled=anti_sniper,
        )
        view = await load_auction_view(session, auction.id)
        publish_gate = await evaluate_seller_publish_gate(session, seller_user_id=seller.id)
        await session.commit()

    if view is None:
        await _record_create_auction_funnel(
            step=BotFunnelStep.FAIL,
            context_key="wizard_finalize",
            failure_reason="preview_unavailable",
        )
        await _update_wizard(
            state=state,
            bot=bot,
            target=callback,
            step_name=WIZARD_STEP_WAITING_ANTI_SNIPER,
            hint="Не удалось завершить шаг. Запустите /newauction еще раз.",
            error="Не удалось собрать предпросмотр. Попробуйте снова.",
        )
        await state.clear()
        await callback.message.answer("Не удалось собрать предпросмотр. Попробуйте снова.")
        return

    publish_blocked = publish_gate is not None and not publish_gate.allowed
    final_hint = (
        "Черновик создан. Публикация пока недоступна, но данные сохранены."
        if publish_blocked
        else "Черновик создан. Ниже карточка предпросмотра и кнопки публикации."
    )
    await _update_wizard(
        state=state,
        bot=bot,
        target=callback,
        step_name=WIZARD_STEP_WAITING_ANTI_SNIPER,
        hint=final_hint,
        finished=True,
        last_event="Черновик успешно создан.",
    )
    await state.clear()
    await _record_create_auction_funnel(
        step=BotFunnelStep.COMPLETE,
        context_key="wizard_finalize_publish_blocked" if publish_blocked else "wizard_finalize",
    )
    await callback.answer("Черновик создан")

    caption = render_auction_caption(view, publish_pending=not publish_blocked)
    await callback.message.answer_photo(
        photo=view.auction.photo_file_id,
        caption=caption,
        reply_markup=(
            None
            if publish_blocked
            else draft_publish_keyboard(str(view.auction.id), photo_count=view.photo_count)
        ),
    )
    if publish_blocked:
        await callback.message.answer(publish_gate.block_message or "Публикация временно ограничена")
        return

    await callback.message.answer(
        "Для публикации в нужный раздел откройте этот раздел и отправьте команду "
        f"<code>/publish {view.auction.id}</code>.\n"
        "Можно нажать кнопку 'Скопировать /publish'.\n\n"
        "Кнопка 'Опубликовать в чате/канале' доступна как fallback через inline mode."
    )


@router.callback_query(AuctionCreateStates.waiting_duration)
async def create_duration_invalid(callback: CallbackQuery, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_callback(callback, state, bot):
        return
    await callback.answer("Выберите одну из кнопок длительности", show_alert=True)
    await _update_wizard(
        state=state,
        bot=bot,
        target=callback,
        step_name=WIZARD_STEP_WAITING_DURATION,
        hint="Выберите одну из кнопок длительности.",
        error="Некорректная длительность.",
    )


@router.callback_query(AuctionCreateStates.waiting_anti_sniper)
async def create_anti_sniper_invalid(callback: CallbackQuery, state: FSMContext, bot: Bot | None = None) -> None:
    if not await _ensure_auction_state_callback(callback, state, bot):
        return
    await callback.answer("Выберите: включить или выключить", show_alert=True)
    await _update_wizard(
        state=state,
        bot=bot,
        target=callback,
        step_name=WIZARD_STEP_WAITING_ANTI_SNIPER,
        hint="Выберите: включить или выключить антиснайпер.",
        error="Некорректный выбор режима антиснайпера.",
    )
