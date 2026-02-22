from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from app.bot.keyboards.auction import open_auction_post_keyboard
from app.bot.keyboards.moderation import complaint_actions_keyboard, fraud_actions_keyboard
from app.config import settings
from app.db.session import SessionFactory
from app.services.anti_fool_service import (
    acquire_bid_cooldown,
    acquire_complaint_cooldown,
    acquire_outbid_notification_debounce,
    arm_or_confirm_action,
)
from app.services.auction_service import (
    load_auction_photo_ids,
    resolve_auction_post_link,
    resolve_auction_post_url,
    load_auction_view,
    process_bid_action,
    refresh_auction_posts,
)
from app.services.bot_funnel_metrics_service import (
    BotFunnelActorRole,
    BotFunnelJourney,
    BotFunnelStep,
    record_bot_funnel_event,
)
from app.services.complaint_service import (
    create_complaint,
    load_complaint_view,
    render_complaint_text,
    set_complaint_queue_message,
)
from app.services.moderation_checklist_service import ensure_checklist, render_checklist_block
from app.services.fraud_service import (
    load_fraud_signal_view,
    render_fraud_signal_text,
    set_fraud_signal_queue_message,
)
from app.services.message_effects_service import (
    AuctionMessageEffectEvent,
    resolve_auction_message_effect_id,
)
from app.services.moderation_topic_router import ModerationTopicSection, send_section_message
from app.services.private_topics_service import PrivateTopicPurpose, send_user_topic_message
from app.services.notification_policy_service import (
    NotificationEventType,
    should_apply_notification_debounce,
    should_include_notification_in_digest,
)
from app.services.notification_digest_service import register_outbid_notification_suppression
from app.services.notification_metrics_service import (
    record_notification_aggregated,
    record_notification_suppressed,
)
from app.services.notification_copy_service import (
    auction_buyout_finished_text,
    auction_buyout_winner_text,
    outbid_digest_text,
    outbid_notification_text,
)
from app.services.user_service import upsert_user

router = Router(name="bid_actions")


async def _record_bid_funnel(
    *,
    journey: BotFunnelJourney,
    step: BotFunnelStep,
    context_key: str,
    failure_reason: str | None = None,
) -> None:
    await record_bot_funnel_event(
        journey=journey,
        step=step,
        actor_role=BotFunnelActorRole.BIDDER,
        context_key=context_key,
        failure_reason=failure_reason,
    )


def _soft_gate_alert_text() -> str:
    username = settings.bot_username.strip()
    if username:
        return (
            f"Сначала откройте @{username} в личке и нажмите /start. "
            "Можно через кнопку 'Поддержка' внизу поста."
        )
    return "Сначала откройте бота в личке и нажмите /start"


def _soft_gate_hint_text(action_done_text: str) -> str:
    username = settings.bot_username.strip()
    if username:
        return f"{action_done_text}. Для уведомлений откройте @{username} в личке и нажмите /start"
    return f"{action_done_text}. Для уведомлений откройте бота в личке и нажмите /start"


def _extract_amount_from_alert_text(alert_text: str) -> int | None:
    match = re.search(r"\$(\d+)", alert_text)
    if match is None:
        return None
    return int(match.group(1))


def _compose_bid_success_alert(*, alert_text: str, placed_bid_amount: int | None, include_soft_gate_hint: bool) -> str:
    amount = placed_bid_amount
    if amount is None:
        amount = _extract_amount_from_alert_text(alert_text)

    if amount is not None:
        if "выкуп" in alert_text.lower():
            base = f"✅ Выкуп оформлен: ${amount}"
        else:
            base = f"✅ Ставка зафиксирована: ${amount}"
    else:
        base = alert_text

    if include_soft_gate_hint:
        return _soft_gate_hint_text(base)
    return base


def _soft_gate_decision(*, private_started: bool) -> tuple[bool, bool]:
    if private_started or not settings.soft_gate_require_private_start:
        return False, False

    mode = settings.soft_gate_mode.strip().lower()
    if mode == "off":
        return False, False
    if mode == "strict":
        return True, False
    return False, True


def _format_digest_window(window_seconds: int) -> str:
    if window_seconds >= 3600:
        return f"{window_seconds // 3600} ч"
    if window_seconds >= 60:
        return f"{window_seconds // 60} мин"
    return f"{window_seconds} сек"


def _should_emit_soft_gate_hint(last_sent_at: datetime | None) -> tuple[bool, datetime]:
    now_utc = datetime.now(timezone.utc)
    interval_hours = max(settings.soft_gate_hint_interval_hours, 1)
    if last_sent_at is None:
        return True, now_utc
    if now_utc - last_sent_at >= timedelta(hours=interval_hours):
        return True, now_utc
    return False, now_utc


def _parse_bid_payload(data: str) -> tuple[uuid.UUID, int] | None:
    parts = data.split(":")
    if len(parts) != 3:
        return None
    _, auction_raw, multiplier_raw = parts
    try:
        auction_id = uuid.UUID(auction_raw)
    except ValueError:
        return None
    if multiplier_raw not in {"1", "3", "5"}:
        return None
    return auction_id, int(multiplier_raw)


def _parse_buy_payload(data: str) -> uuid.UUID | None:
    parts = data.split(":")
    if len(parts) != 2:
        return None
    _, auction_raw = parts
    try:
        return uuid.UUID(auction_raw)
    except ValueError:
        return None


def _parse_report_payload(data: str) -> uuid.UUID | None:
    parts = data.split(":")
    if len(parts) != 2:
        return None
    _, auction_raw = parts
    try:
        return uuid.UUID(auction_raw)
    except ValueError:
        return None


def _parse_gallery_payload(data: str) -> uuid.UUID | None:
    parts = data.split(":")
    if len(parts) != 2:
        return None
    _, auction_raw = parts
    try:
        return uuid.UUID(auction_raw)
    except ValueError:
        return None


@router.callback_query(F.data.startswith("gallery:"))
async def handle_gallery_action(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None or callback.data is None:
        return

    auction_id = _parse_gallery_payload(callback.data)
    if auction_id is None:
        await callback.answer("Некорректная галерея", show_alert=True)
        return

    async with SessionFactory() as session:
        view = await load_auction_view(session, auction_id)
        if view is None:
            await callback.answer("Лот не найден", show_alert=True)
            return
        photo_ids = await load_auction_photo_ids(session, auction_id)

    if not photo_ids:
        photo_ids = [view.auction.photo_file_id]

    caption = f"Фото лота #{str(auction_id)[:8]}"
    try:
        if len(photo_ids) == 1:
            await bot.send_photo(callback.from_user.id, photo=photo_ids[0], caption=caption)
        else:
            for chunk_start in range(0, len(photo_ids), 10):
                chunk = photo_ids[chunk_start : chunk_start + 10]
                media = [
                    InputMediaPhoto(
                        media=file_id,
                        caption=caption if chunk_start == 0 and idx == 0 else None,
                    )
                    for idx, file_id in enumerate(chunk)
                ]
                await bot.send_media_group(chat_id=callback.from_user.id, media=media)
    except TelegramForbiddenError:
        await callback.answer(_soft_gate_alert_text(), show_alert=True)
        return
    except TelegramBadRequest:
        await callback.answer("Не удалось отправить фото. Попробуйте еще раз.", show_alert=True)
        return

    await callback.answer("Отправил фото в личку")


async def _notify_moderators_about_complaint(
    bot: Bot,
    *,
    complaint_id: int,
    text: str,
) -> tuple[int, int] | None:
    keyboard = complaint_actions_keyboard(complaint_id)
    return await send_section_message(
        bot,
        section=ModerationTopicSection.COMPLAINTS,
        text=text,
        reply_markup=keyboard,
    )


async def _notify_moderators_about_fraud(
    bot: Bot,
    *,
    signal_id: int,
    text: str,
) -> tuple[int, int] | None:
    keyboard = fraud_actions_keyboard(signal_id)
    return await send_section_message(
        bot,
        section=ModerationTopicSection.BUGS,
        text=text,
        reply_markup=keyboard,
    )


async def _maybe_send_fraud_alert(bot: Bot, signal_id: int) -> None:
    async with SessionFactory() as session:
        async with session.begin():
            view = await load_fraud_signal_view(session, signal_id)
            if view is None:
                return
            text = render_fraud_signal_text(view)

    queue_message = await _notify_moderators_about_fraud(bot, signal_id=signal_id, text=text)
    if queue_message is None:
        return

    async with SessionFactory() as session:
        async with session.begin():
            await set_fraud_signal_queue_message(
                session,
                signal_id=signal_id,
                chat_id=queue_message[0],
                message_id=queue_message[1],
            )


def _callback_post_url(callback: CallbackQuery) -> str | None:
    if callback.message is None or not isinstance(callback.message, Message):
        return None
    chat = callback.message.chat
    if chat is None or not isinstance(chat.id, int):
        return None
    username = chat.username if isinstance(chat.username, str) else None
    return resolve_auction_post_link(
        chat_id=chat.id,
        message_id=callback.message.message_id,
        username=username,
    )


async def _notify_outbid(
    bot: Bot,
    outbid_user_tg_id: int | None,
    actor_tg_id: int,
    *,
    auction_id: uuid.UUID,
    post_url: str | None,
) -> None:
    if outbid_user_tg_id is None or outbid_user_tg_id == actor_tg_id:
        return

    if should_apply_notification_debounce(NotificationEventType.AUCTION_OUTBID):
        if not await acquire_outbid_notification_debounce(auction_id, outbid_user_tg_id):
            await record_notification_suppressed(
                event_type=NotificationEventType.AUCTION_OUTBID,
                reason="debounce_gate",
            )
            await record_notification_aggregated(
                event_type=NotificationEventType.AUCTION_OUTBID,
                reason="debounce_gate",
            )

            if should_include_notification_in_digest(NotificationEventType.AUCTION_OUTBID):
                digest = await register_outbid_notification_suppression(
                    tg_user_id=outbid_user_tg_id,
                    auction_id=auction_id,
                )
                if digest.should_emit_digest:
                    resolved_post_url = post_url or await resolve_auction_post_url(
                        bot,
                        auction_id=auction_id,
                    )
                    reply_markup = (
                        open_auction_post_keyboard(resolved_post_url) if resolved_post_url else None
                    )
                    window_label = _format_digest_window(digest.window_seconds)
                    await send_user_topic_message(
                        bot,
                        tg_user_id=outbid_user_tg_id,
                        purpose=PrivateTopicPurpose.AUCTIONS,
                        text=outbid_digest_text(
                            auction_id=auction_id,
                            suppressed_count=digest.suppressed_count,
                            window_label=window_label,
                        ),
                        reply_markup=reply_markup,
                        notification_event=NotificationEventType.AUCTION_OUTBID,
                        auction_id=auction_id,
                    )
            return

    resolved_post_url = post_url or await resolve_auction_post_url(bot, auction_id=auction_id)
    reply_markup = open_auction_post_keyboard(resolved_post_url) if resolved_post_url else None

    await send_user_topic_message(
        bot,
        tg_user_id=outbid_user_tg_id,
        purpose=PrivateTopicPurpose.AUCTIONS,
        text=outbid_notification_text(auction_id),
        reply_markup=reply_markup,
        message_effect_id=resolve_auction_message_effect_id(AuctionMessageEffectEvent.OUTBID),
        notification_event=NotificationEventType.AUCTION_OUTBID,
        auction_id=auction_id,
    )


async def _notify_auction_finish(
    bot: Bot,
    *,
    winner_tg_id: int | None,
    seller_tg_id: int | None,
    auction_id: uuid.UUID,
    post_url: str | None,
) -> None:
    resolved_post_url = post_url or await resolve_auction_post_url(bot, auction_id=auction_id)
    reply_markup = open_auction_post_keyboard(resolved_post_url) if resolved_post_url else None

    if seller_tg_id is not None:
        await send_user_topic_message(
            bot,
            tg_user_id=seller_tg_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=auction_buyout_finished_text(auction_id),
            reply_markup=reply_markup,
            message_effect_id=resolve_auction_message_effect_id(
                AuctionMessageEffectEvent.BUYOUT_SELLER
            ),
            notification_event=NotificationEventType.AUCTION_FINISH,
            auction_id=auction_id,
        )
    if winner_tg_id is not None:
        await send_user_topic_message(
            bot,
            tg_user_id=winner_tg_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=auction_buyout_winner_text(auction_id),
            reply_markup=reply_markup,
            message_effect_id=resolve_auction_message_effect_id(
                AuctionMessageEffectEvent.BUYOUT_WINNER
            ),
            notification_event=NotificationEventType.AUCTION_WIN,
            auction_id=auction_id,
        )


@router.callback_query(F.data.startswith("bid:"))
async def handle_bid_action(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None or callback.data is None:
        return

    payload = _parse_bid_payload(callback.data)
    if payload is None:
        await _record_bid_funnel(
            journey=BotFunnelJourney.BID,
            step=BotFunnelStep.FAIL,
            context_key="callback_bid",
            failure_reason="invalid_payload",
        )
        await callback.answer("Некорректная ставка", show_alert=True)
        return

    auction_id, multiplier = payload
    bid_context = f"callback_bid_x{multiplier}"
    await _record_bid_funnel(
        journey=BotFunnelJourney.BID,
        step=BotFunnelStep.START,
        context_key=bid_context,
    )

    if not await acquire_bid_cooldown(auction_id, callback.from_user.id):
        await _record_bid_funnel(
            journey=BotFunnelJourney.BID,
            step=BotFunnelStep.FAIL,
            context_key=bid_context,
            failure_reason="cooldown",
        )
        await callback.answer(
            f"Слишком часто. Подождите {settings.bid_cooldown_seconds} сек.",
            show_alert=True,
        )
        return

    blocked_by_soft_gate = False
    soft_gate_hint = False
    show_soft_gate_hint = False
    result = None
    async with SessionFactory() as session:
        async with session.begin():
            bidder = await upsert_user(session, callback.from_user)
            blocked_by_soft_gate, soft_gate_hint = _soft_gate_decision(
                private_started=bidder.private_started_at is not None
            )
            if not blocked_by_soft_gate:
                result = await process_bid_action(
                    session,
                    auction_id=auction_id,
                    bidder_user_id=bidder.id,
                    multiplier=multiplier,
                    is_buyout=False,
                )
                if soft_gate_hint and result.success:
                    show_soft_gate_hint, hint_ts = _should_emit_soft_gate_hint(bidder.soft_gate_hint_sent_at)
                    if show_soft_gate_hint:
                        bidder.soft_gate_hint_sent_at = hint_ts

    if blocked_by_soft_gate:
        await _record_bid_funnel(
            journey=BotFunnelJourney.BID,
            step=BotFunnelStep.FAIL,
            context_key=bid_context,
            failure_reason="soft_gate",
        )
        await callback.answer(_soft_gate_alert_text(), show_alert=True)
        return
    if result is None:
        await _record_bid_funnel(
            journey=BotFunnelJourney.BID,
            step=BotFunnelStep.FAIL,
            context_key=bid_context,
            failure_reason="service_error",
        )
        await callback.answer("Не удалось обработать ставку", show_alert=True)
        return

    if result.success:
        await _record_bid_funnel(
            journey=BotFunnelJourney.BID,
            step=BotFunnelStep.COMPLETE,
            context_key=bid_context,
        )
        await callback.answer(
            _compose_bid_success_alert(
                alert_text=result.alert_text,
                placed_bid_amount=result.placed_bid_amount,
                include_soft_gate_hint=show_soft_gate_hint,
            ),
            show_alert=True,
        )
    else:
        await _record_bid_funnel(
            journey=BotFunnelJourney.BID,
            step=BotFunnelStep.FAIL,
            context_key=bid_context,
            failure_reason="action_rejected",
        )
        await callback.answer(result.alert_text, show_alert=True)

    if result.should_refresh:
        await refresh_auction_posts(bot, auction_id)

    post_url = _callback_post_url(callback)
    await _notify_outbid(
        bot,
        result.outbid_tg_user_id,
        callback.from_user.id,
        auction_id=auction_id,
        post_url=post_url,
    )

    if result.fraud_signal_id is not None:
        await _maybe_send_fraud_alert(bot, result.fraud_signal_id)


@router.callback_query(F.data.startswith("buy:"))
async def handle_buyout_action(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None or callback.data is None:
        return

    auction_id = _parse_buy_payload(callback.data)
    if auction_id is None:
        await _record_bid_funnel(
            journey=BotFunnelJourney.BUYOUT,
            step=BotFunnelStep.FAIL,
            context_key="callback_buyout",
            failure_reason="invalid_payload",
        )
        await callback.answer("Некорректный выкуп", show_alert=True)
        return

    needs_confirm = await arm_or_confirm_action(
        auction_id,
        callback.from_user.id,
        action="buyout",
    )
    if needs_confirm:
        await callback.answer(
            f"Подтвердите выкуп: нажмите кнопку еще раз в течение {settings.confirmation_ttl_seconds} сек.",
            show_alert=True,
        )
        return

    await _record_bid_funnel(
        journey=BotFunnelJourney.BUYOUT,
        step=BotFunnelStep.START,
        context_key="callback_buyout",
    )

    if not await acquire_bid_cooldown(auction_id, callback.from_user.id):
        await _record_bid_funnel(
            journey=BotFunnelJourney.BUYOUT,
            step=BotFunnelStep.FAIL,
            context_key="callback_buyout",
            failure_reason="cooldown",
        )
        await callback.answer(
            f"Слишком часто. Подождите {settings.bid_cooldown_seconds} сек.",
            show_alert=True,
        )
        return

    blocked_by_soft_gate = False
    soft_gate_hint = False
    show_soft_gate_hint = False
    result = None
    async with SessionFactory() as session:
        async with session.begin():
            bidder = await upsert_user(session, callback.from_user)
            blocked_by_soft_gate, soft_gate_hint = _soft_gate_decision(
                private_started=bidder.private_started_at is not None
            )
            if not blocked_by_soft_gate:
                result = await process_bid_action(
                    session,
                    auction_id=auction_id,
                    bidder_user_id=bidder.id,
                    multiplier=1,
                    is_buyout=True,
                )
                if soft_gate_hint and result.success:
                    show_soft_gate_hint, hint_ts = _should_emit_soft_gate_hint(bidder.soft_gate_hint_sent_at)
                    if show_soft_gate_hint:
                        bidder.soft_gate_hint_sent_at = hint_ts

    if blocked_by_soft_gate:
        await _record_bid_funnel(
            journey=BotFunnelJourney.BUYOUT,
            step=BotFunnelStep.FAIL,
            context_key="callback_buyout",
            failure_reason="soft_gate",
        )
        await callback.answer(_soft_gate_alert_text(), show_alert=True)
        return
    if result is None:
        await _record_bid_funnel(
            journey=BotFunnelJourney.BUYOUT,
            step=BotFunnelStep.FAIL,
            context_key="callback_buyout",
            failure_reason="service_error",
        )
        await callback.answer("Не удалось обработать выкуп", show_alert=True)
        return

    if result.success:
        await _record_bid_funnel(
            journey=BotFunnelJourney.BUYOUT,
            step=BotFunnelStep.COMPLETE,
            context_key="callback_buyout",
        )
    else:
        await _record_bid_funnel(
            journey=BotFunnelJourney.BUYOUT,
            step=BotFunnelStep.FAIL,
            context_key="callback_buyout",
            failure_reason="action_rejected",
        )

    if result.success and show_soft_gate_hint:
        await callback.answer(_soft_gate_hint_text("Выкуп принят"), show_alert=True)
    else:
        await callback.answer(result.alert_text, show_alert=not result.success)

    if result.should_refresh:
        await refresh_auction_posts(bot, auction_id)

    post_url = _callback_post_url(callback)
    await _notify_outbid(
        bot,
        result.outbid_tg_user_id,
        callback.from_user.id,
        auction_id=auction_id,
        post_url=post_url,
    )

    if result.fraud_signal_id is not None:
        await _maybe_send_fraud_alert(bot, result.fraud_signal_id)

    if result.auction_finished:
        await _notify_auction_finish(
            bot,
            winner_tg_id=result.winner_tg_user_id,
            seller_tg_id=result.seller_tg_user_id,
            auction_id=auction_id,
            post_url=post_url,
        )


@router.callback_query(F.data.startswith("report:"))
async def handle_report_action(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None or callback.data is None:
        return

    auction_id = _parse_report_payload(callback.data)
    if auction_id is None:
        await _record_bid_funnel(
            journey=BotFunnelJourney.COMPLAINT,
            step=BotFunnelStep.FAIL,
            context_key="callback_report",
            failure_reason="invalid_payload",
        )
        await callback.answer("Некорректная жалоба", show_alert=True)
        return

    needs_confirm = await arm_or_confirm_action(
        auction_id,
        callback.from_user.id,
        action="report",
    )
    if needs_confirm:
        await callback.answer(
            f"Подтвердите жалобу: нажмите кнопку еще раз в течение {settings.confirmation_ttl_seconds} сек.",
            show_alert=True,
        )
        return

    await _record_bid_funnel(
        journey=BotFunnelJourney.COMPLAINT,
        step=BotFunnelStep.START,
        context_key="callback_report",
    )

    if not await acquire_complaint_cooldown(auction_id, callback.from_user.id):
        await _record_bid_funnel(
            journey=BotFunnelJourney.COMPLAINT,
            step=BotFunnelStep.FAIL,
            context_key="callback_report",
            failure_reason="cooldown",
        )
        await callback.answer(
            f"Слишком часто. Повторить можно через {settings.complaint_cooldown_seconds} сек.",
            show_alert=True,
        )
        return

    complaint_id: int | None = None
    complaint_text: str | None = None
    blocked_by_soft_gate = False
    soft_gate_hint = False
    show_soft_gate_hint = False

    async with SessionFactory() as session:
        async with session.begin():
            reporter = await upsert_user(session, callback.from_user)
            blocked_by_soft_gate, soft_gate_hint = _soft_gate_decision(
                private_started=reporter.private_started_at is not None
            )
            if not blocked_by_soft_gate:
                created = await create_complaint(
                    session,
                    auction_id=auction_id,
                    reporter_user_id=reporter.id,
                    reason="Жалоба из аукционного поста",
                )
                if not created.ok or created.complaint is None:
                    await _record_bid_funnel(
                        journey=BotFunnelJourney.COMPLAINT,
                        step=BotFunnelStep.FAIL,
                        context_key="callback_report",
                        failure_reason="create_rejected",
                    )
                    await callback.answer(created.message, show_alert=True)
                    return

                view = await load_complaint_view(session, created.complaint.id)
                if view is None:
                    await _record_bid_funnel(
                        journey=BotFunnelJourney.COMPLAINT,
                        step=BotFunnelStep.FAIL,
                        context_key="callback_report",
                        failure_reason="view_unavailable",
                    )
                    await callback.answer("Не удалось сформировать жалобу", show_alert=True)
                    return

                complaint_id = created.complaint.id
                checklist_items = await ensure_checklist(
                    session,
                    entity_type="complaint",
                    entity_id=created.complaint.id,
                )
                complaint_text = (
                    f"{render_complaint_text(view)}\n\n{render_checklist_block(checklist_items)}"
                    if checklist_items
                    else render_complaint_text(view)
                )
                if soft_gate_hint:
                    show_soft_gate_hint, hint_ts = _should_emit_soft_gate_hint(
                        reporter.soft_gate_hint_sent_at
                    )
                    if show_soft_gate_hint:
                        reporter.soft_gate_hint_sent_at = hint_ts

    if blocked_by_soft_gate:
        await _record_bid_funnel(
            journey=BotFunnelJourney.COMPLAINT,
            step=BotFunnelStep.FAIL,
            context_key="callback_report",
            failure_reason="soft_gate",
        )
        await callback.answer(_soft_gate_alert_text(), show_alert=True)
        return

    if complaint_id is None or complaint_text is None:
        await _record_bid_funnel(
            journey=BotFunnelJourney.COMPLAINT,
            step=BotFunnelStep.FAIL,
            context_key="callback_report",
            failure_reason="service_error",
        )
        await callback.answer("Не удалось отправить жалобу", show_alert=True)
        return

    queue_message = await _notify_moderators_about_complaint(
        bot,
        complaint_id=complaint_id,
        text=complaint_text,
    )

    if queue_message is not None:
        async with SessionFactory() as session:
            async with session.begin():
                await set_complaint_queue_message(
                    session,
                    complaint_id=complaint_id,
                    chat_id=queue_message[0],
                    message_id=queue_message[1],
                )

    await refresh_auction_posts(bot, auction_id)
    if queue_message is None:
        success_text = "Жалоба создана, но очередь модерации не настроена"
    else:
        success_text = "Жалоба отправлена модераторам"

    if show_soft_gate_hint:
        success_text = _soft_gate_hint_text(success_text)

    await _record_bid_funnel(
        journey=BotFunnelJourney.COMPLAINT,
        step=BotFunnelStep.COMPLETE,
        context_key="callback_report_queue_unavailable" if queue_message is None else "callback_report",
    )

    await callback.answer(success_text, show_alert=True)
