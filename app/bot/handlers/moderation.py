from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from app.bot.keyboards.moderation import (
    moderation_appeal_back_keyboard,
    complaint_actions_keyboard,
    fraud_actions_keyboard,
    moderation_appeal_actions_keyboard,
    moderation_appeals_list_keyboard,
    moderation_checklist_keyboard,
    moderation_frozen_actions_keyboard,
    moderation_frozen_list_keyboard,
    moderation_complaints_list_keyboard,
    moderation_panel_keyboard,
    moderation_signals_list_keyboard,
)
from app.bot.keyboards.auction import open_auction_post_keyboard
from app.config import settings
from app.db.enums import AppealSourceType, AppealStatus, AuctionStatus, ModerationAction, PointsEventType
from app.db.models import Appeal, Auction, User
from app.db.session import SessionFactory
from app.services.bot_profile_photo_service import (
    apply_bot_profile_photo_preset,
    list_bot_profile_photo_presets,
    rollback_bot_profile_photo,
)
from app.services.appeal_service import (
    mark_appeal_in_review,
    reject_appeal,
    resolve_appeal,
    resolve_appeal_auction_id,
)
from app.services.auction_service import refresh_auction_posts, resolve_auction_post_url
from app.services.complaint_service import (
    list_complaints,
    load_complaint_view,
    render_complaint_text,
    resolve_complaint,
)
from app.services.fraud_service import (
    list_fraud_signals,
    load_fraud_signal_view,
    render_fraud_signal_text,
    resolve_fraud_signal,
)
from app.services.moderation_service import (
    allowlist_role_and_scopes,
    ban_user,
    end_auction,
    freeze_auction,
    get_moderation_scopes,
    grant_moderator_role,
    has_moderation_scope,
    has_moderator_access,
    log_moderation_action,
    list_tg_user_roles,
    list_moderation_logs,
    list_recent_bids,
    remove_bid,
    revoke_moderator_role,
    unban_user,
    unfreeze_auction,
)
from app.services.moderation_dashboard_service import get_moderation_dashboard_snapshot
from app.services.message_draft_service import send_progress_draft
from app.services.chat_owner_guard_service import confirm_chat_owner_events
from app.services.moderation_checklist_service import (
    ENTITY_APPEAL,
    ENTITY_COMPLAINT,
    ENTITY_GUARANTOR,
    add_checklist_reply,
    ensure_checklist,
    list_checklist_replies,
    render_checklist_block,
    toggle_checklist_item,
)
from app.services.points_service import (
    UserPointsSummary,
    count_user_points_entries,
    get_user_points_summary,
    grant_points,
    list_user_points_entries,
)
from app.services.private_topics_service import (
    PrivateTopicPurpose,
    enforce_callback_topic,
    enforce_message_topic,
    send_user_topic_message,
)
from app.services.notification_policy_service import NotificationEventType
from app.services.notification_copy_service import (
    moderation_bid_removed_text,
    moderation_ended_text,
    moderation_frozen_text,
    moderation_unfrozen_text,
    moderation_winner_text,
)
from app.services.rbac_service import (
    SCOPE_AUCTION_MANAGE,
    SCOPE_BID_MANAGE,
    SCOPE_DIRECT_MESSAGES_MANAGE,
    SCOPE_ROLE_MANAGE,
    SCOPE_TRUST_MANAGE,
    SCOPE_USER_BAN,
)
from app.services.user_service import upsert_user
from app.services.verification_service import (
    get_user_verification_status,
    load_verified_tg_user_ids,
    set_chat_verification,
    set_user_verification,
)

router = Router(name="moderation")
PANEL_PAGE_SIZE = 5
DEFAULT_POINTS_HISTORY_LIMIT = 5
MAX_POINTS_HISTORY_LIMIT = 20
MODPOINTS_HISTORY_PAGE_SIZE = 10


@dataclass(slots=True)
class _PendingChecklistReply:
    entity_type: str
    entity_id: int
    item_code: str
    page: int


_PENDING_CHECKLIST_REPLIES: dict[int, _PendingChecklistReply] = {}


def _appeal_deep_link(appeal_ref: str) -> str | None:
    username = settings.bot_username.strip()
    if not username:
        return None
    return f"https://t.me/{username}?start=appeal_{appeal_ref}"


def _appeal_keyboard(appeal_ref: str) -> InlineKeyboardMarkup | None:
    url = _appeal_deep_link(appeal_ref)
    if url is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Обжаловать решение", url=url)],
        ]
    )


def _build_appeal_cta(appeal_ref: str) -> tuple[str, InlineKeyboardMarkup | None]:
    keyboard = _appeal_keyboard(appeal_ref)
    if keyboard is None:
        return (
            f"Если вы не согласны с решением, отправьте в этот чат команду /start appeal_{appeal_ref}.",
            None,
        )
    return (
        "Если вы не согласны с решением, нажмите кнопку ниже и отправьте апелляцию.",
        keyboard,
    )


async def _auction_post_keyboard(bot: Bot, auction_id: uuid.UUID) -> InlineKeyboardMarkup | None:
    post_url = await resolve_auction_post_url(bot, auction_id=auction_id)
    if post_url is None:
        return None
    return open_auction_post_keyboard(post_url)


def _format_user_label(user: User | None) -> str:
    if user is None:
        return "-"
    if user.username:
        return f"@{user.username}"
    return str(user.tg_user_id)


def _format_appeal_source(appeal: Appeal) -> str:
    source_type = AppealSourceType(appeal.source_type)
    if source_type == AppealSourceType.COMPLAINT and appeal.source_id is not None:
        return f"Жалоба #{appeal.source_id}"
    if source_type == AppealSourceType.RISK and appeal.source_id is not None:
        return f"Фрод-сигнал #{appeal.source_id}"
    return "Ручная апелляция"


def _render_appeal_text(
    appeal: Appeal,
    *,
    appellant: User | None,
    resolver: User | None,
) -> str:
    now = datetime.now(UTC)
    is_overdue = _appeal_is_overdue(appeal, now=now)

    lines = [
        f"Апелляция #{appeal.id}",
        f"Статус: {appeal.status}{' ⏰' if is_overdue else ''}",
        f"Источник: {_format_appeal_source(appeal)}",
        f"Референс: {appeal.appeal_ref}",
        f"Подал: {_format_user_label(appellant)}",
        f"Создана: {appeal.created_at}",
    ]
    if appeal.sla_deadline_at is not None:
        lines.append(f"SLA дедлайн: {appeal.sla_deadline_at}")
    if appeal.escalated_at is not None:
        lines.append(f"Эскалирована: {appeal.escalated_at}")
    if appeal.priority_boost_points_spent > 0 and appeal.priority_boosted_at is not None:
        lines.append(f"Приоритет: boosted ({appeal.priority_boost_points_spent} points) at {appeal.priority_boosted_at}")
    if appeal.resolution_note:
        lines.append(f"Решение: {appeal.resolution_note}")
    if resolver is not None:
        lines.append(f"Модератор: {_format_user_label(resolver)}")
    if appeal.resolved_at is not None:
        lines.append(f"Закрыта: {appeal.resolved_at}")
    return "\n".join(lines)


async def _append_checklist_block(
    session,
    *,
    entity_type: str,
    entity_id: int,
    text: str,
) -> str:
    items = await ensure_checklist(session, entity_type=entity_type, entity_id=entity_id)
    if not items:
        return text
    replies_by_item = await list_checklist_replies(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    return f"{text}\n\n{render_checklist_block(items, replies_by_item=replies_by_item)}"


def _checklist_title(*, entity_type: str, entity_id: int) -> str:
    if entity_type == ENTITY_COMPLAINT:
        return f"Чеклист жалобы #{entity_id}"
    if entity_type == ENTITY_APPEAL:
        return f"Чеклист апелляции #{entity_id}"
    if entity_type == ENTITY_GUARANTOR:
        return f"Чеклист гаранта #{entity_id}"
    return f"Чеклист #{entity_id}"


def _checklist_back_callback(*, entity_type: str, entity_id: int, page: int) -> str | None:
    if entity_type == ENTITY_COMPLAINT:
        return f"modui:complaint:{entity_id}:{page}"
    if entity_type == ENTITY_APPEAL:
        return f"modui:appeal:{entity_id}:{page}"
    return None


async def _build_appeals_page(page: int) -> tuple[str, InlineKeyboardMarkup]:
    offset = page * PANEL_PAGE_SIZE
    async with SessionFactory() as session:
        appeals = (
            await session.execute(
                select(Appeal)
                .where(Appeal.status.in_([AppealStatus.OPEN, AppealStatus.IN_REVIEW]))
                .order_by(Appeal.priority_boosted_at.desc().nullslast(), Appeal.created_at.desc(), Appeal.id.desc())
                .offset(offset)
                .limit(PANEL_PAGE_SIZE + 1)
            )
        ).scalars().all()

    has_next = len(appeals) > PANEL_PAGE_SIZE
    visible = appeals[:PANEL_PAGE_SIZE]
    now = datetime.now(UTC)
    items = []
    for item in visible:
        boost_prefix = "⚡ " if item.priority_boosted_at is not None else ""
        overdue_prefix = "⏰ " if _appeal_is_overdue(item, now=now) else ""
        items.append((item.id, f"{boost_prefix}{overdue_prefix}Апелляция #{item.id} | {item.status} | {item.appeal_ref}"))

    text_lines = [f"Активные апелляции, стр. {page + 1}"]
    for item in visible:
        boost_suffix = " | boosted" if item.priority_boosted_at is not None else ""
        overdue_suffix = " | overdue" if _appeal_is_overdue(item, now=now) else ""
        text_lines.append(f"- #{item.id} | status={item.status} | ref={item.appeal_ref}{boost_suffix}{overdue_suffix}")
    if not visible:
        text_lines.append("- нет записей")

    return "\n".join(text_lines), moderation_appeals_list_keyboard(items=items, page=page, has_next=has_next)


async def _build_frozen_auctions_page(page: int) -> tuple[str, InlineKeyboardMarkup]:
    offset = page * PANEL_PAGE_SIZE
    async with SessionFactory() as session:
        auctions = (
            await session.execute(
                select(Auction)
                .where(Auction.status == AuctionStatus.FROZEN)
                .order_by(Auction.updated_at.desc(), Auction.created_at.desc())
                .offset(offset)
                .limit(PANEL_PAGE_SIZE + 1)
            )
        ).scalars().all()

    has_next = len(auctions) > PANEL_PAGE_SIZE
    visible = auctions[:PANEL_PAGE_SIZE]
    items = [(str(item.id), f"Аукцион {str(item.id)[:8]} | seller {item.seller_user_id}") for item in visible]
    text_lines = [f"Замороженные аукционы, стр. {page + 1}"]
    for item in visible:
        text_lines.append(f"- auc={str(item.id)[:8]} | seller={item.seller_user_id} | ends={item.ends_at}")
    if not visible:
        text_lines.append("- нет записей")

    return "\n".join(text_lines), moderation_frozen_list_keyboard(items=items, page=page, has_next=has_next)


def _parse_uuid(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _appeal_is_overdue(appeal: Appeal, *, now: datetime | None = None) -> bool:
    status = AppealStatus(appeal.status)
    if status not in {AppealStatus.OPEN, AppealStatus.IN_REVIEW}:
        return False
    if appeal.sla_deadline_at is None:
        return False
    current_time = now or datetime.now(UTC)
    return appeal.sla_deadline_at <= current_time


def _split_args(text: str) -> tuple[str, str] | None:
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        return None
    return parts[1], parts[2]


def _parse_signed_int(raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_positive_int(raw: str, *, minimum: int, maximum: int) -> int | None:
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < minimum or value > maximum:
        return None
    return value


def _event_label(event_type: PointsEventType) -> str:
    if event_type == PointsEventType.FEEDBACK_APPROVED:
        return "Награда за фидбек"
    if event_type == PointsEventType.FEEDBACK_PRIORITY_BOOST:
        return "Списание за приоритет фидбека"
    if event_type == PointsEventType.GUARANTOR_PRIORITY_BOOST:
        return "Списание за приоритет гаранта"
    if event_type == PointsEventType.APPEAL_PRIORITY_BOOST:
        return "Списание за приоритет апелляции"
    return "Ручная корректировка"


def _parse_points_filter(raw: str | None) -> PointsEventType | None | Literal["invalid"]:
    if raw is None:
        return None
    lowered = raw.strip().lower()
    if lowered in {"", "all", "all_types"}:
        return None
    if lowered in {"feedback", "feedback_approved"}:
        return PointsEventType.FEEDBACK_APPROVED
    if lowered in {"manual", "manual_adjustment"}:
        return PointsEventType.MANUAL_ADJUSTMENT
    if lowered in {"boost", "priority", "feedback_priority_boost"}:
        return PointsEventType.FEEDBACK_PRIORITY_BOOST
    if lowered in {"gboost", "guarantor_priority_boost", "guarant_boost"}:
        return PointsEventType.GUARANTOR_PRIORITY_BOOST
    if lowered in {"aboost", "appeal_priority_boost", "appeal_boost"}:
        return PointsEventType.APPEAL_PRIORITY_BOOST
    return "invalid"


def _points_filter_label(event_type: PointsEventType | None) -> str:
    if event_type is None:
        return "all"
    if event_type == PointsEventType.FEEDBACK_APPROVED:
        return "feedback"
    if event_type == PointsEventType.FEEDBACK_PRIORITY_BOOST:
        return "boost"
    if event_type == PointsEventType.GUARANTOR_PRIORITY_BOOST:
        return "gboost"
    if event_type == PointsEventType.APPEAL_PRIORITY_BOOST:
        return "aboost"
    return "manual"


def _render_modpoints_history(
    *,
    target_tg_user_id: int,
    entries: list,
    page: int,
    total_pages: int,
    total_items: int,
    filter_event_type: PointsEventType | None,
) -> str:
    lines = [
        (
            f"История points пользователя {target_tg_user_id} | "
            f"фильтр: {_points_filter_label(filter_event_type)} | "
            f"стр. {page}/{total_pages}"
        ),
        f"Всего записей: {total_items}",
    ]
    if not entries:
        lines.append("Операции не найдены")
        return "\n".join(lines)

    lines.append("")
    for entry in entries:
        created_at = entry.created_at.astimezone().strftime("%d.%m %H:%M")
        amount_text = f"+{entry.amount}" if entry.amount > 0 else str(entry.amount)
        lines.append(f"- {created_at} | {amount_text} | {_event_label(PointsEventType(entry.event_type))} | {entry.reason}")
    return "\n".join(lines)


def _render_points_snapshot(
    *,
    target_tg_user_id: int,
    summary: UserPointsSummary,
    entries: list,
    shown_limit: int,
) -> str:
    lines = [
        f"Баланс пользователя {target_tg_user_id}: {summary.balance} points",
        f"Всего начислено: +{summary.total_earned}",
        f"Всего списано: -{summary.total_spent}",
    ]
    if not entries:
        lines.append("Операций пока нет")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"Последние операции (до {shown_limit}):")
    for entry in entries:
        created_at = entry.created_at.astimezone().strftime("%d.%m %H:%M")
        amount_text = f"+{entry.amount}" if entry.amount > 0 else str(entry.amount)
        lines.append(f"- {created_at} | {amount_text} | {_event_label(PointsEventType(entry.event_type))}")
    return "\n".join(lines)


async def _require_moderator(message: Message) -> bool:
    if message.from_user is None:
        return False

    async with SessionFactory() as session:
        allowed = await has_moderator_access(session, message.from_user.id)

    if not allowed:
        await message.answer("Недостаточно прав")
        return False
    return True


async def _require_moderator_callback(callback: CallbackQuery) -> bool:
    if callback.from_user is None:
        return False

    async with SessionFactory() as session:
        allowed = await has_moderator_access(session, callback.from_user.id)

    if not allowed:
        await callback.answer("Недостаточно прав", show_alert=True)
        return False
    return True


def _scope_title(scope: str) -> str:
    if scope == SCOPE_AUCTION_MANAGE:
        return "управление аукционами"
    if scope == SCOPE_BID_MANAGE:
        return "управление ставками"
    if scope == SCOPE_USER_BAN:
        return "бан/разбан пользователей"
    if scope == SCOPE_ROLE_MANAGE:
        return "управление ролями"
    if scope == SCOPE_TRUST_MANAGE:
        return "управление верификацией"
    return scope


def _complaint_action_required_scope(action: str) -> str | None:
    if action == "freeze":
        return SCOPE_AUCTION_MANAGE
    if action in {"dismiss", "rm_top"}:
        return SCOPE_BID_MANAGE
    if action == "ban_top":
        return SCOPE_USER_BAN
    return None


def _risk_action_required_scope(action: str) -> str | None:
    if action == "freeze":
        return SCOPE_AUCTION_MANAGE
    if action == "ban":
        return SCOPE_USER_BAN
    if action == "ignore":
        return SCOPE_BID_MANAGE
    return None


async def _require_scope_message(message: Message, scope: str) -> bool:
    if message.from_user is None:
        return False
    async with SessionFactory() as session:
        allowed = await has_moderation_scope(session, message.from_user.id, scope)
    if not allowed:
        await message.answer(f"Недостаточно прав: нужно право '{_scope_title(scope)}'")
        return False
    return True


async def _require_scope_callback(callback: CallbackQuery, scope: str) -> bool:
    if callback.from_user is None:
        return False
    async with SessionFactory() as session:
        allowed = await has_moderation_scope(session, callback.from_user.id, scope)
    if not allowed:
        await callback.answer(f"Недостаточно прав: нужно право '{_scope_title(scope)}'", show_alert=True)
        return False
    return True


async def _ensure_moderation_topic(message: Message, bot: Bot | None, command_hint: str) -> bool:
    if message.from_user is None:
        return False
    if bot is None:
        return True

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, message.from_user, mark_private_started=True)
            return await enforce_message_topic(
                message,
                bot=bot,
                session=session,
                user=user,
                purpose=PrivateTopicPurpose.MODERATION,
                command_hint=command_hint,
            )


async def _ensure_moderation_callback_topic(callback: CallbackQuery, bot: Bot) -> bool:
    if callback.from_user is None:
        return False

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            return await enforce_callback_topic(
                callback,
                bot=bot,
                session=session,
                user=user,
                purpose=PrivateTopicPurpose.MODERATION,
                command_hint="/modpanel",
            )


async def _render_mod_panel_home_text() -> str:
    async with SessionFactory() as session:
        snapshot = await get_moderation_dashboard_snapshot(session)
        active_appeals = (
            await session.scalar(
                select(func.count(Appeal.id)).where(Appeal.status.in_([AppealStatus.OPEN, AppealStatus.IN_REVIEW]))
            )
        ) or 0

    return (
        "Мод-панель\n"
        f"- Открытые жалобы: {snapshot.open_complaints}\n"
        f"- Открытые фрод-сигналы: {snapshot.open_signals}\n"
        f"- Активные апелляции: {active_appeals}\n"
        f"- Активные аукционы: {snapshot.active_auctions}\n"
        f"- Замороженные аукционы: {snapshot.frozen_auctions}\n\n"
        "Используйте кнопки ниже для просмотра очередей."
    )


async def _render_mod_stats_text() -> str:
    async with SessionFactory() as session:
        snapshot = await get_moderation_dashboard_snapshot(session)
        active_appeals = (
            await session.scalar(
                select(func.count(Appeal.id)).where(Appeal.status.in_([AppealStatus.OPEN, AppealStatus.IN_REVIEW]))
            )
        ) or 0

    engaged_with_private = max(
        snapshot.users_with_engagement - snapshot.users_engaged_without_private_start,
        0,
    )
    hint_conv = "0.0%"
    if snapshot.users_with_soft_gate_hint > 0:
        hint_conv = f"{(snapshot.users_converted_after_hint / snapshot.users_with_soft_gate_hint) * 100:.1f}%"
    points_redeem_conv = "0.0%"
    if snapshot.points_users_with_positive_balance > 0:
        points_redeem_conv = (
            f"{(snapshot.points_redeemers_7d / snapshot.points_users_with_positive_balance) * 100:.1f}%"
        )
    global_daily_limit_text = "- global daily limit: unlimited\n"
    if settings.points_redemption_daily_limit > 0:
        global_daily_limit_text = f"- global daily limit: {settings.points_redemption_daily_limit}/day\n"
    global_weekly_limit_text = "- global weekly limit: unlimited\n"
    if settings.points_redemption_weekly_limit > 0:
        global_weekly_limit_text = f"- global weekly limit: {settings.points_redemption_weekly_limit}/week\n"
    global_daily_spend_cap_text = "- global daily spend cap: unlimited\n"
    if settings.points_redemption_daily_spend_cap > 0:
        global_daily_spend_cap_text = (
            f"- global daily spend cap: {settings.points_redemption_daily_spend_cap} points/day\n"
        )
    global_weekly_spend_cap_text = "- global weekly spend cap: unlimited\n"
    if settings.points_redemption_weekly_spend_cap > 0:
        global_weekly_spend_cap_text = (
            f"- global weekly spend cap: {settings.points_redemption_weekly_spend_cap} points/week\n"
        )
    global_monthly_spend_cap_text = "- global monthly spend cap: unlimited\n"
    if settings.points_redemption_monthly_spend_cap > 0:
        global_monthly_spend_cap_text = (
            f"- global monthly spend cap: {settings.points_redemption_monthly_spend_cap} points/month\n"
        )

    return (
        "Статистика модерации\n"
        f"- Открытые жалобы: {snapshot.open_complaints}\n"
        f"- Открытые фрод-сигналы: {snapshot.open_signals}\n"
        f"- Активные апелляции: {active_appeals}\n"
        f"- Активные аукционы: {snapshot.active_auctions}\n"
        f"- Замороженные аукционы: {snapshot.frozen_auctions}\n"
        f"- Ставок за 1 час: {snapshot.bids_last_hour}\n"
        f"- Ставок за 24 часа: {snapshot.bids_last_24h}\n"
        f"- Активных банов: {snapshot.active_blacklist_entries}\n"
        "\n"
        "Онбординг / soft-gate\n"
        f"- Пользователей всего: {snapshot.total_users}\n"
        f"- Private /start: {snapshot.users_private_started}\n"
        f"- С hint: {snapshot.users_with_soft_gate_hint}\n"
        f"- Конверсия после hint: {snapshot.users_converted_after_hint} ({hint_conv})\n"
        f"- Вовлеченные без private /start: {snapshot.users_engaged_without_private_start}\n"
        f"- Вовлеченные с private /start: {engaged_with_private}\n"
        "\n"
        "Points utility\n"
        f"- Активные points-пользователи (7д): {snapshot.points_active_users_7d}\n"
        f"- Пользователи с положительным балансом: {snapshot.points_users_with_positive_balance}\n"
        f"- Редимеры points (7д): {snapshot.points_redeemers_7d} ({points_redeem_conv})\n"
        f"- Редимеры фидбек-буста (7д): {snapshot.points_feedback_boost_redeemers_7d}\n"
        f"- Редимеры буста гаранта (7д): {snapshot.points_guarantor_boost_redeemers_7d}\n"
        f"- Редимеры буста апелляции (7д): {snapshot.points_appeal_boost_redeemers_7d}\n"
        f"- Points начислено (24ч): +{snapshot.points_earned_24h}\n"
        f"- Points списано (24ч): -{snapshot.points_spent_24h}\n"
        f"- Бустов фидбека (24ч): {snapshot.feedback_boost_redeems_24h}\n"
        f"- Бустов гаранта (24ч): {snapshot.guarantor_boost_redeems_24h}\n"
        f"- Бустов апелляций (24ч): {snapshot.appeal_boost_redeems_24h}\n"
        "\n"
        "Points policy\n"
        f"- redemptions: {'on' if settings.points_redemption_enabled else 'off'}\n"
        f"- feedback: {'on' if settings.feedback_priority_boost_enabled else 'off'} | "
        f"cost {settings.feedback_priority_boost_cost_points} | "
        f"limit {settings.feedback_priority_boost_daily_limit}/day | "
        f"cooldown {max(settings.feedback_priority_boost_cooldown_seconds, 0)}s\n"
        f"- guarantor: {'on' if settings.guarantor_priority_boost_enabled else 'off'} | "
        f"cost {settings.guarantor_priority_boost_cost_points} | "
        f"limit {settings.guarantor_priority_boost_daily_limit}/day | "
        f"cooldown {max(settings.guarantor_priority_boost_cooldown_seconds, 0)}s\n"
        f"- appeal: {'on' if settings.appeal_priority_boost_enabled else 'off'} | "
        f"cost {settings.appeal_priority_boost_cost_points} | "
        f"limit {settings.appeal_priority_boost_daily_limit}/day | "
        f"cooldown {max(settings.appeal_priority_boost_cooldown_seconds, 0)}s\n"
        f"{global_daily_limit_text}"
        f"{global_weekly_limit_text}"
        f"{global_daily_spend_cap_text}"
        f"{global_weekly_spend_cap_text}"
        f"{global_monthly_spend_cap_text}"
        f"- min balance after redemption: {max(settings.points_redemption_min_balance, 0)} points\n"
        f"- min account age for redemption: {max(settings.points_redemption_min_account_age_seconds, 0)}s\n"
        f"- min earned points for redemption: {max(settings.points_redemption_min_earned_points, 0)} points\n"
        f"- global cooldown: {max(settings.points_redemption_cooldown_seconds, 0)}s"
    )


def _parse_page(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < 0:
        return None
    return value


def _parse_tg_user_and_description(text: str) -> tuple[int, str | None] | None:
    parts = text.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    tg_user_id = int(parts[1])
    description = parts[2].strip() if len(parts) > 2 else None
    if description == "":
        description = None
    return tg_user_id, description


def _parse_chat_and_description(text: str) -> tuple[int, str | None] | None:
    parts = text.split(maxsplit=2)
    if len(parts) < 2:
        return None
    try:
        chat_id = int(parts[1])
    except ValueError:
        return None

    description = parts[2].strip() if len(parts) > 2 else None
    if description == "":
        description = None
    return chat_id, description


@router.message(Command("mod"), F.chat.type == ChatType.PRIVATE)
async def mod_help(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/mod"):
        return
    if not await _require_moderator(message) or message.from_user is None:
        return

    async with SessionFactory() as session:
        scopes = await get_moderation_scopes(session, message.from_user.id)

    commands = [
        "/mod",
        "/modpanel",
        "/modstats",
        "/audit [auction_uuid]",
        "/risk [auction_uuid]",
    ]
    if SCOPE_AUCTION_MANAGE in scopes:
        commands.extend(
            [
                "/freeze <auction_uuid> <reason>",
                "/unfreeze <auction_uuid> <reason>",
                "/end <auction_uuid> <reason>",
                "/botphoto list",
                "/botphoto set <preset>",
                "/botphoto reset",
            ]
        )
    if SCOPE_BID_MANAGE in scopes:
        commands.extend(
            [
                "/bids <auction_uuid>",
                "/rm_bid <bid_uuid> <reason>",
            ]
        )
    if SCOPE_USER_BAN in scopes:
        commands.extend(
            [
                "/ban <tg_user_id> <reason>",
                "/unban <tg_user_id> <reason>",
            ]
        )
    if SCOPE_ROLE_MANAGE in scopes:
        commands.extend(
            [
                "/role list <tg_user_id>",
                "/role grant <tg_user_id> moderator",
                "/role revoke <tg_user_id> moderator",
                "/modpoints <tg_user_id>",
                "/modpoints <tg_user_id> <limit>",
                "/modpoints <tg_user_id> <amount> <reason>",
                "/modpoints_history <tg_user_id> [page] [all|feedback|manual|boost|gboost|aboost]",
            ]
        )
    if SCOPE_TRUST_MANAGE in scopes:
        commands.extend(
            [
                "/verifyuser <tg_user_id> [description]",
                "/unverifyuser <tg_user_id>",
                "/verifychat <chat_id> [description]",
                "/unverifychat <chat_id>",
            ]
        )
    if SCOPE_DIRECT_MESSAGES_MANAGE in scopes:
        commands.append("/confirmowner <chat_id>")

    await message.answer("Команды модерации:\n" + "\n".join(commands))


@router.message(Command("modstats"), F.chat.type == ChatType.PRIVATE)
async def mod_stats(message: Message, bot: Bot | None = None) -> None:
    if not await _ensure_moderation_topic(message, bot, "/modstats"):
        return
    if not await _require_moderator(message):
        return

    await send_progress_draft(
        bot,
        message,
        text="Собираю модераторскую статистику...",
        scope_key="modstats",
    )
    await message.answer(await _render_mod_stats_text())


@router.message(Command("botphoto"), F.chat.type == ChatType.PRIVATE)
async def mod_botphoto(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/botphoto"):
        return
    if (
        not await _require_scope_message(message, SCOPE_AUCTION_MANAGE)
        or message.from_user is None
        or message.text is None
    ):
        return

    parts = message.text.split(maxsplit=2)
    action = "list" if len(parts) == 1 else parts[1].strip().lower()

    if action == "list":
        presets = list_bot_profile_photo_presets()
        if not presets:
            await message.answer(
                "Preset-ы не настроены. Добавьте BOT_PROFILE_PHOTO_PRESETS в env "
                "(пример: default=file_id,campaign=file_id)."
            )
            return

        default_preset = settings.parsed_bot_profile_photo_default_preset()
        lines = ["Доступные preset-ы фото бота:"]
        for preset in presets:
            suffix = " (default)" if default_preset == preset else ""
            lines.append(f"- {preset}{suffix}")
        lines.append("")
        lines.append("Команды: /botphoto set <preset> | /botphoto reset")
        await message.answer("\n".join(lines))
        return

    if action == "set":
        if len(parts) != 3:
            await message.answer("Формат: /botphoto set <preset>")
            return
        result = await apply_bot_profile_photo_preset(bot, preset=parts[2])
    elif action == "reset":
        if len(parts) != 2:
            await message.answer("Формат: /botphoto reset")
            return
        result = await rollback_bot_profile_photo(bot)
    else:
        await message.answer(
            "Формат:\n"
            "/botphoto list\n"
            "/botphoto set <preset>\n"
            "/botphoto reset"
        )
        return

    if not result.ok:
        await message.answer(result.message)
        return

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, message.from_user)
            if result.action is not None and result.reason is not None:
                await log_moderation_action(
                    session,
                    actor_user_id=actor.id,
                    action=result.action,
                    reason=result.reason,
                    payload=result.payload,
                )

    await message.answer(result.message)


@router.message(Command("role"), F.chat.type == ChatType.PRIVATE)
async def mod_role_manage(message: Message, bot: Bot | None = None) -> None:
    if not await _ensure_moderation_topic(message, bot, "/role"):
        return
    if not await _require_scope_message(message, SCOPE_ROLE_MANAGE) or message.text is None:
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Формат:\n"
            "/role list <tg_user_id>\n"
            "/role grant <tg_user_id> moderator\n"
            "/role revoke <tg_user_id> moderator"
        )
        return

    action = parts[1].lower()
    if action == "list":
        if len(parts) != 3 or not parts[2].isdigit():
            await message.answer("Формат: /role list <tg_user_id>")
            return
        target_tg_user_id = int(parts[2])
        async with SessionFactory() as session:
            roles = await list_tg_user_roles(session, target_tg_user_id)
            scopes = await get_moderation_scopes(session, target_tg_user_id)

        allowlist_role, _ = allowlist_role_and_scopes(target_tg_user_id, via_token=False)
        role_label = allowlist_role if allowlist_role != "viewer" else "none"
        dynamic_roles = ", ".join(sorted(role.value for role in roles)) if roles else "none"
        scopes_label = ", ".join(sorted(scopes)) if scopes else "read-only"
        await message.answer(
            f"TG user: {target_tg_user_id}\n"
            f"Allowlist role: {role_label}\n"
            f"DB roles: {dynamic_roles}\n"
            f"Scopes: {scopes_label}"
        )
        return

    if action in {"grant", "revoke"}:
        if len(parts) != 4 or not parts[2].isdigit():
            await message.answer(f"Формат: /role {action} <tg_user_id> moderator")
            return

        target_tg_user_id = int(parts[2])
        role_raw = parts[3].lower()
        if role_raw not in {"moderator", "mod"}:
            await message.answer("Сейчас поддерживается только роль moderator")
            return

        async with SessionFactory() as session:
            async with session.begin():
                if action == "grant":
                    result = await grant_moderator_role(session, target_tg_user_id=target_tg_user_id)
                else:
                    result = await revoke_moderator_role(session, target_tg_user_id=target_tg_user_id)

        await message.answer(result.message)
        return

    await message.answer("Неизвестная команда. Используйте /role list|grant|revoke ...")


@router.message(Command("modpoints"), F.chat.type == ChatType.PRIVATE)
async def mod_points(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/modpoints"):
        return
    if (
        not await _require_scope_message(message, SCOPE_ROLE_MANAGE)
        or message.from_user is None
        or message.text is None
    ):
        return

    usage_text = (
        "Формат:\n"
        "/modpoints <tg_user_id>\n"
        f"/modpoints <tg_user_id> <1..{MAX_POINTS_HISTORY_LIMIT}>\n"
        "/modpoints <tg_user_id> <amount> <reason>"
    )

    parts = message.text.split(maxsplit=3)
    if len(parts) in {2, 3}:
        if not parts[1].isdigit():
            await message.answer(usage_text)
            return

        target_tg_user_id = int(parts[1])
        limit = DEFAULT_POINTS_HISTORY_LIMIT
        if len(parts) == 3:
            parsed_limit = _parse_positive_int(parts[2], minimum=1, maximum=MAX_POINTS_HISTORY_LIMIT)
            if parsed_limit is None:
                await message.answer(usage_text)
                return
            limit = parsed_limit

        async with SessionFactory() as session:
            target = await session.scalar(select(User).where(User.tg_user_id == target_tg_user_id))
            if target is None:
                await message.answer("Пользователь не найден")
                return

            summary = await get_user_points_summary(session, user_id=target.id)
            entries = await list_user_points_entries(session, user_id=target.id, limit=limit)

        await message.answer(
            _render_points_snapshot(
                target_tg_user_id=target_tg_user_id,
                summary=summary,
                entries=entries,
                shown_limit=limit,
            )
        )
        return

    if len(parts) < 4 or not parts[1].isdigit():
        await message.answer(usage_text)
        return

    target_tg_user_id = int(parts[1])
    amount = _parse_signed_int(parts[2])
    reason = parts[3].strip()
    if amount is None:
        await message.answer("amount должен быть целым числом (например: 20 или -5)")
        return
    if amount == 0:
        await message.answer("amount не может быть 0")
        return
    if not reason:
        await message.answer("Укажите причину изменения")
        return

    chat_id = message.chat.id if message.chat is not None else 0
    changed = False
    summary: UserPointsSummary | None = None
    entries = []
    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, message.from_user)
            target = await session.scalar(select(User).where(User.tg_user_id == target_tg_user_id).with_for_update())
            if target is None:
                await message.answer("Пользователь не найден")
                return

            dedupe_key = f"modpoints:{actor.id}:{target.id}:{chat_id}:{message.message_id}"
            grant_result = await grant_points(
                session,
                user_id=target.id,
                amount=amount,
                event_type=PointsEventType.MANUAL_ADJUSTMENT,
                dedupe_key=dedupe_key,
                reason=reason,
                payload={
                    "actor_tg_user_id": message.from_user.id,
                    "target_tg_user_id": target_tg_user_id,
                },
            )
            changed = grant_result.changed

            if changed:
                await log_moderation_action(
                    session,
                    actor_user_id=actor.id,
                    action=ModerationAction.ADJUST_USER_POINTS,
                    reason=reason,
                    target_user_id=target.id,
                    payload={
                        "amount": amount,
                        "dedupe_key": dedupe_key,
                    },
                )

            summary = await get_user_points_summary(session, user_id=target.id)
            entries = await list_user_points_entries(session, user_id=target.id, limit=DEFAULT_POINTS_HISTORY_LIMIT)

    if summary is None:
        await message.answer("Не удалось рассчитать баланс")
        return

    snapshot = _render_points_snapshot(
        target_tg_user_id=target_tg_user_id,
        summary=summary,
        entries=entries,
        shown_limit=DEFAULT_POINTS_HISTORY_LIMIT,
    )
    if changed:
        delta_text = f"+{amount}" if amount > 0 else str(amount)
        await message.answer(f"Изменение применено: {delta_text} points\n\n{snapshot}")
        notify_label = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
        await send_user_topic_message(
            bot,
            tg_user_id=target_tg_user_id,
            purpose=PrivateTopicPurpose.POINTS,
            text=(
                f"Ваш баланс скорректирован модератором {notify_label}: {delta_text} points.\n"
                f"Причина: {reason}"
            ),
            notification_event=NotificationEventType.POINTS,
        )
        return

    await message.answer(f"Команда уже обработана ранее\n\n{snapshot}")


@router.message(Command("modpoints_history"), F.chat.type == ChatType.PRIVATE)
async def mod_points_history(message: Message, bot: Bot | None = None) -> None:
    if not await _ensure_moderation_topic(message, bot, "/modpoints_history"):
        return
    if not await _require_scope_message(message, SCOPE_ROLE_MANAGE) or message.text is None:
        return

    usage_text = (
        "Формат:\n"
        "/modpoints_history <tg_user_id>\n"
        "/modpoints_history <tg_user_id> <page>\n"
        "/modpoints_history <tg_user_id> <all|feedback|manual|boost|gboost|aboost>\n"
        "/modpoints_history <tg_user_id> <page> <all|feedback|manual|boost|gboost|aboost>"
    )

    parts = message.text.split()
    if len(parts) < 2 or len(parts) > 4 or not parts[1].isdigit():
        await message.answer(usage_text)
        return

    target_tg_user_id = int(parts[1])
    page_raw: str | None = None
    filter_raw: str | None = None
    if len(parts) == 3:
        token = parts[2]
        if token.isdigit():
            page_raw = token
        else:
            filter_raw = token
    elif len(parts) == 4:
        if parts[2].isdigit():
            page_raw = parts[2]
            filter_raw = parts[3]
        elif parts[3].isdigit():
            filter_raw = parts[2]
            page_raw = parts[3]
        else:
            await message.answer(usage_text)
            return

    page = 1
    if page_raw is not None:
        parsed_page = _parse_positive_int(page_raw, minimum=1, maximum=1000)
        if parsed_page is None:
            await message.answer("Некорректная страница. Допустимо: 1..1000")
            return
        page = parsed_page

    filter_value = _parse_points_filter(filter_raw)
    if filter_value == "invalid":
        await message.answer(usage_text)
        return
    filter_event_type = cast(PointsEventType | None, filter_value)

    async with SessionFactory() as session:
        target = await session.scalar(select(User).where(User.tg_user_id == target_tg_user_id))
        if target is None:
            await message.answer("Пользователь не найден")
            return

        total_items = await count_user_points_entries(
            session,
            user_id=target.id,
            event_type=filter_event_type,
        )
        total_pages = max((total_items + MODPOINTS_HISTORY_PAGE_SIZE - 1) // MODPOINTS_HISTORY_PAGE_SIZE, 1)
        if total_items > 0 and page > total_pages:
            await message.answer(f"Страница вне диапазона. Доступно: 1..{total_pages}")
            return

        offset = (page - 1) * MODPOINTS_HISTORY_PAGE_SIZE
        entries = await list_user_points_entries(
            session,
            user_id=target.id,
            limit=MODPOINTS_HISTORY_PAGE_SIZE,
            offset=offset,
            event_type=filter_event_type,
        )

    await message.answer(
        _render_modpoints_history(
            target_tg_user_id=target_tg_user_id,
            entries=entries,
            page=page,
            total_pages=total_pages,
            total_items=total_items,
            filter_event_type=filter_event_type,
        )
    )


@router.message(Command("modpanel"), F.chat.type == ChatType.PRIVATE)
async def mod_panel(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/modpanel"):
        return
    if not await _require_moderator(message):
        return

    text = await _render_mod_panel_home_text()
    await message.answer(text, reply_markup=moderation_panel_keyboard())


@router.callback_query(F.data == "mod:panel")
async def mod_panel_from_button(callback: CallbackQuery, bot: Bot) -> None:
    if not await _ensure_moderation_callback_topic(callback, bot):
        return
    if not await _require_moderator_callback(callback):
        return
    if callback.message is None:
        return

    await callback.answer()
    await callback.message.answer(
        await _render_mod_panel_home_text(),
        reply_markup=moderation_panel_keyboard(),
    )


@router.message(Command("freeze"), F.chat.type == ChatType.PRIVATE)
async def mod_freeze(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/freeze"):
        return
    if (
        not await _require_scope_message(message, SCOPE_AUCTION_MANAGE)
        or message.from_user is None
        or message.text is None
    ):
        return

    parsed = _split_args(message.text)
    if parsed is None:
        await message.answer("Формат: /freeze <auction_uuid> <reason>")
        return

    auction_raw, reason = parsed
    auction_id = _parse_uuid(auction_raw)
    if auction_id is None:
        await message.answer("Некорректный auction_uuid")
        return

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, message.from_user)
            result = await freeze_auction(
                session,
                actor_user_id=actor.id,
                auction_id=auction_id,
                reason=reason,
            )

    if not result.ok:
        await message.answer(result.message)
        return

    await refresh_auction_posts(bot, auction_id)
    await message.answer(result.message)

    if result.seller_tg_user_id:
        reply_markup = await _auction_post_keyboard(bot, auction_id)
        await send_user_topic_message(
            bot,
            tg_user_id=result.seller_tg_user_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=moderation_frozen_text(auction_id),
            reply_markup=reply_markup,
            notification_event=NotificationEventType.AUCTION_MOD_ACTION,
            auction_id=auction_id,
        )


@router.message(Command("unfreeze"), F.chat.type == ChatType.PRIVATE)
async def mod_unfreeze(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/unfreeze"):
        return
    if (
        not await _require_scope_message(message, SCOPE_AUCTION_MANAGE)
        or message.from_user is None
        or message.text is None
    ):
        return

    parsed = _split_args(message.text)
    if parsed is None:
        await message.answer("Формат: /unfreeze <auction_uuid> <reason>")
        return

    auction_raw, reason = parsed
    auction_id = _parse_uuid(auction_raw)
    if auction_id is None:
        await message.answer("Некорректный auction_uuid")
        return

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, message.from_user)
            result = await unfreeze_auction(
                session,
                actor_user_id=actor.id,
                auction_id=auction_id,
                reason=reason,
            )

    if not result.ok:
        await message.answer(result.message)
        return

    await refresh_auction_posts(bot, auction_id)
    await message.answer(result.message)

    if result.seller_tg_user_id:
        reply_markup = await _auction_post_keyboard(bot, auction_id)
        await send_user_topic_message(
            bot,
            tg_user_id=result.seller_tg_user_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=moderation_unfrozen_text(auction_id),
            reply_markup=reply_markup,
            notification_event=NotificationEventType.AUCTION_MOD_ACTION,
            auction_id=auction_id,
        )


@router.message(Command("end"), F.chat.type == ChatType.PRIVATE)
async def mod_end(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/end"):
        return
    if (
        not await _require_scope_message(message, SCOPE_AUCTION_MANAGE)
        or message.from_user is None
        or message.text is None
    ):
        return

    parsed = _split_args(message.text)
    if parsed is None:
        await message.answer("Формат: /end <auction_uuid> <reason>")
        return

    auction_raw, reason = parsed
    auction_id = _parse_uuid(auction_raw)
    if auction_id is None:
        await message.answer("Некорректный auction_uuid")
        return

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, message.from_user)
            result = await end_auction(
                session,
                actor_user_id=actor.id,
                auction_id=auction_id,
                reason=reason,
            )

    if not result.ok:
        await message.answer(result.message)
        return

    await refresh_auction_posts(bot, auction_id)
    await message.answer(result.message)

    reply_markup = await _auction_post_keyboard(bot, auction_id)
    if result.seller_tg_user_id:
        await send_user_topic_message(
            bot,
            tg_user_id=result.seller_tg_user_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=moderation_ended_text(auction_id),
            reply_markup=reply_markup,
            notification_event=NotificationEventType.AUCTION_MOD_ACTION,
            auction_id=auction_id,
        )
    if result.winner_tg_user_id:
        await send_user_topic_message(
            bot,
            tg_user_id=result.winner_tg_user_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=moderation_winner_text(auction_id),
            reply_markup=reply_markup,
            notification_event=NotificationEventType.AUCTION_MOD_ACTION,
            auction_id=auction_id,
        )


@router.message(Command("bids"), F.chat.type == ChatType.PRIVATE)
async def mod_bids(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/bids"):
        return
    if not await _require_scope_message(message, SCOPE_BID_MANAGE) or message.text is None:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /bids <auction_uuid>")
        return

    auction_id = _parse_uuid(parts[1])
    if auction_id is None:
        await message.answer("Некорректный auction_uuid")
        return

    async with SessionFactory() as session:
        items = await list_recent_bids(session, auction_id)

    if not items:
        await message.answer("Ставки не найдены")
        return

    lines = [f"Последние ставки по аукциону #{str(auction_id)[:8]}:"]
    for item in items:
        actor = f"@{item.username}" if item.username else str(item.tg_user_id)
        marker = "(удалена)" if item.is_removed else ""
        lines.append(f"- {item.bid_id} | ${item.amount} | {actor} {marker}")
    await message.answer("\n".join(lines))


@router.message(Command("rm_bid"), F.chat.type == ChatType.PRIVATE)
async def mod_remove_bid(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/rm_bid"):
        return
    if (
        not await _require_scope_message(message, SCOPE_BID_MANAGE)
        or message.from_user is None
        or message.text is None
    ):
        return

    parsed = _split_args(message.text)
    if parsed is None:
        await message.answer("Формат: /rm_bid <bid_uuid> <reason>")
        return

    bid_raw, reason = parsed
    bid_id = _parse_uuid(bid_raw)
    if bid_id is None:
        await message.answer("Некорректный bid_uuid")
        return

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, message.from_user)
            result = await remove_bid(
                session,
                actor_user_id=actor.id,
                bid_id=bid_id,
                reason=reason,
            )

    if not result.ok:
        await message.answer(result.message)
        return

    if result.auction_id is not None:
        await refresh_auction_posts(bot, result.auction_id)
    await message.answer(result.message)

    if result.target_tg_user_id:
        reply_markup = None
        if result.auction_id is not None:
            reply_markup = await _auction_post_keyboard(bot, result.auction_id)
        await send_user_topic_message(
            bot,
            tg_user_id=result.target_tg_user_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=moderation_bid_removed_text(),
            reply_markup=reply_markup,
            notification_event=NotificationEventType.AUCTION_MOD_ACTION,
            auction_id=result.auction_id,
        )


@router.message(Command("ban"), F.chat.type == ChatType.PRIVATE)
async def mod_ban(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/ban"):
        return
    if (
        not await _require_scope_message(message, SCOPE_USER_BAN)
        or message.from_user is None
        or message.text is None
    ):
        return

    parsed = _split_args(message.text)
    if parsed is None:
        await message.answer("Формат: /ban <tg_user_id> <reason>")
        return

    tg_user_raw, reason = parsed
    if not tg_user_raw.isdigit():
        await message.answer("tg_user_id должен быть числом")
        return

    target_tg_user_id = int(tg_user_raw)
    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, message.from_user)
            result = await ban_user(
                session,
                actor_user_id=actor.id,
                target_tg_user_id=target_tg_user_id,
                reason=reason,
            )

    await message.answer(result.message)


@router.message(Command("unban"), F.chat.type == ChatType.PRIVATE)
async def mod_unban(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/unban"):
        return
    if (
        not await _require_scope_message(message, SCOPE_USER_BAN)
        or message.from_user is None
        or message.text is None
    ):
        return

    parsed = _split_args(message.text)
    if parsed is None:
        await message.answer("Формат: /unban <tg_user_id> <reason>")
        return

    tg_user_raw, reason = parsed
    if not tg_user_raw.isdigit():
        await message.answer("tg_user_id должен быть числом")
        return

    target_tg_user_id = int(tg_user_raw)
    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, message.from_user)
            result = await unban_user(
                session,
                actor_user_id=actor.id,
                target_tg_user_id=target_tg_user_id,
                reason=reason,
            )

    await message.answer(result.message)


@router.message(Command("audit"), F.chat.type == ChatType.PRIVATE)
async def mod_audit(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/audit"):
        return
    if not await _require_moderator(message) or message.text is None:
        return

    parts = message.text.split(maxsplit=1)
    auction_id: uuid.UUID | None = None
    if len(parts) > 1:
        auction_id = _parse_uuid(parts[1])
        if auction_id is None:
            await message.answer("Некорректный auction_uuid")
            return

    async with SessionFactory() as session:
        logs = await list_moderation_logs(session, auction_id=auction_id)

    if not logs:
        await message.answer("Логи модерации не найдены")
        return

    lines = ["Последние мод-действия:"]
    for log in logs:
        target = f"auc={str(log.auction_id)[:8]}" if log.auction_id else "auc=-"
        lines.append(
            f"- {log.created_at.strftime('%d.%m %H:%M')} | {log.action} | {target} | reason: {log.reason}"
        )
    await message.answer("\n".join(lines[:30]))


@router.message(Command("confirmowner"), F.chat.type == ChatType.PRIVATE)
async def mod_confirm_owner(message: Message, bot: Bot | None = None) -> None:
    if not await _ensure_moderation_topic(message, bot, "/confirmowner"):
        return
    if (
        not await _require_scope_message(message, SCOPE_DIRECT_MESSAGES_MANAGE)
        or message.from_user is None
        or message.text is None
    ):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /confirmowner <chat_id>")
        return

    try:
        chat_id = int(parts[1].strip())
    except ValueError:
        await message.answer("chat_id должен быть числом")
        return

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, message.from_user)
            resolved = await confirm_chat_owner_events(
                session,
                chat_id=chat_id,
                actor_user_id=actor.id,
            )

    if resolved == 0:
        await message.answer("Нет неподтвержденных owner-событий для этого DM-чата.")
        return

    await message.answer(
        f"Подтверждено событий: {resolved}. Пауза автообработки для чата <code>{chat_id}</code> снята."
    )


@router.message(Command("risk"), F.chat.type == ChatType.PRIVATE)
async def mod_risk(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/risk"):
        return
    if not await _require_moderator(message) or message.text is None:
        return

    parts = message.text.split(maxsplit=1)
    auction_id: uuid.UUID | None = None
    if len(parts) > 1:
        auction_id = _parse_uuid(parts[1])
        if auction_id is None:
            await message.answer("Некорректный auction_uuid")
            return

    async with SessionFactory() as session:
        signals = await list_fraud_signals(session, auction_id=auction_id, status="OPEN")
        user_map: dict[int, User] = {}
        verified_tg_ids: set[int] = set()
        if signals:
            user_ids = sorted({signal.user_id for signal in signals})
            users = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
            user_map = {user.id: user for user in users}
            verified_tg_ids = await load_verified_tg_user_ids(
                session,
                tg_user_ids=[user.tg_user_id for user in users],
            )

    if not signals:
        await message.answer("Открытые фрод-сигналы не найдены")
        return

    lines = ["Открытые фрод-сигналы:"]
    for signal in signals:
        user = user_map.get(signal.user_id)
        tg_user_id = user.tg_user_id if user is not None else signal.user_id
        verified_marker = " [verified]" if tg_user_id in verified_tg_ids else ""
        lines.append(
            f"- #{signal.id} | auc={str(signal.auction_id)[:8]} | user={tg_user_id}{verified_marker} | score={signal.score}"
        )
    await message.answer("\n".join(lines[:30]))


@router.message(Command("verifyuser"), F.chat.type == ChatType.PRIVATE)
async def mod_verify_user(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/verifyuser"):
        return
    if not await _require_scope_message(message, SCOPE_TRUST_MANAGE) or message.text is None:
        return
    actor = message.from_user
    if actor is None:
        return

    parsed = _parse_tg_user_and_description(message.text)
    if parsed is None:
        await message.answer("Формат: /verifyuser <tg_user_id> [description]")
        return

    tg_user_id, description = parsed
    async with SessionFactory() as session:
        async with session.begin():
            actor_user = await upsert_user(session, actor)
            result = await set_user_verification(
                session,
                bot,
                actor_user_id=actor_user.id,
                target_tg_user_id=tg_user_id,
                verify=True,
                custom_description=description,
            )

    await message.answer(result.message)


@router.message(Command("unverifyuser"), F.chat.type == ChatType.PRIVATE)
async def mod_unverify_user(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/unverifyuser"):
        return
    if not await _require_scope_message(message, SCOPE_TRUST_MANAGE) or message.text is None:
        return
    actor = message.from_user
    if actor is None:
        return

    parsed = _parse_tg_user_and_description(message.text)
    if parsed is None:
        await message.answer("Формат: /unverifyuser <tg_user_id>")
        return

    tg_user_id, _ = parsed
    async with SessionFactory() as session:
        async with session.begin():
            actor_user = await upsert_user(session, actor)
            result = await set_user_verification(
                session,
                bot,
                actor_user_id=actor_user.id,
                target_tg_user_id=tg_user_id,
                verify=False,
            )

    await message.answer(result.message)


@router.message(Command("verifychat"), F.chat.type == ChatType.PRIVATE)
async def mod_verify_chat(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/verifychat"):
        return
    if not await _require_scope_message(message, SCOPE_TRUST_MANAGE) or message.text is None:
        return
    actor = message.from_user
    if actor is None:
        return

    parsed = _parse_chat_and_description(message.text)
    if parsed is None:
        await message.answer("Формат: /verifychat <chat_id> [description]")
        return

    chat_id, description = parsed
    async with SessionFactory() as session:
        async with session.begin():
            actor_user = await upsert_user(session, actor)
            result = await set_chat_verification(
                session,
                bot,
                actor_user_id=actor_user.id,
                chat_id=chat_id,
                verify=True,
                custom_description=description,
            )

    await message.answer(result.message)


@router.message(Command("unverifychat"), F.chat.type == ChatType.PRIVATE)
async def mod_unverify_chat(message: Message, bot: Bot) -> None:
    if not await _ensure_moderation_topic(message, bot, "/unverifychat"):
        return
    if not await _require_scope_message(message, SCOPE_TRUST_MANAGE) or message.text is None:
        return
    actor = message.from_user
    if actor is None:
        return

    parsed = _parse_chat_and_description(message.text)
    if parsed is None:
        await message.answer("Формат: /unverifychat <chat_id>")
        return

    chat_id, _ = parsed
    async with SessionFactory() as session:
        async with session.begin():
            actor_user = await upsert_user(session, actor)
            result = await set_chat_verification(
                session,
                bot,
                actor_user_id=actor_user.id,
                chat_id=chat_id,
                verify=False,
            )

    await message.answer(result.message)


@router.callback_query(F.data.startswith("modui:"))
async def mod_panel_callbacks(callback: CallbackQuery, bot: Bot) -> None:
    if not await _ensure_moderation_callback_topic(callback, bot):
        return
    if not await _require_moderator_callback(callback):
        return
    if callback.data is None or callback.message is None:
        return

    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("Некорректная команда", show_alert=True)
        return

    section = parts[1]

    if section == "home":
        text = await _render_mod_panel_home_text()
        await callback.message.edit_text(text, reply_markup=moderation_panel_keyboard())
        await callback.answer()
        return

    if section == "stats":
        await callback.message.edit_text(
            await _render_mod_stats_text(),
            reply_markup=moderation_panel_keyboard(),
        )
        await callback.answer()
        return

    if section == "appeals":
        if len(parts) != 3:
            await callback.answer("Некорректная пагинация", show_alert=True)
            return
        page = _parse_page(parts[2])
        if page is None:
            await callback.answer("Некорректная страница", show_alert=True)
            return
        if not await _require_scope_callback(callback, SCOPE_USER_BAN):
            return

        text, keyboard = await _build_appeals_page(page)
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()
        return

    if section == "appeal":
        if len(parts) != 4 or not parts[2].isdigit():
            await callback.answer("Некорректная апелляция", show_alert=True)
            return
        appeal_id = int(parts[2])
        page = _parse_page(parts[3])
        if page is None:
            await callback.answer("Некорректная страница", show_alert=True)
            return
        if not await _require_scope_callback(callback, SCOPE_USER_BAN):
            return

        async with SessionFactory() as session:
            appeal = await session.scalar(select(Appeal).where(Appeal.id == appeal_id))
            if appeal is None:
                await callback.answer("Апелляция не найдена", show_alert=True)
                return
            appellant = await session.scalar(select(User).where(User.id == appeal.appellant_user_id))
            resolver = None
            if appeal.resolver_user_id is not None:
                resolver = await session.scalar(select(User).where(User.id == appeal.resolver_user_id))
            appeal_text = await _append_checklist_block(
                session,
                entity_type=ENTITY_APPEAL,
                entity_id=appeal_id,
                text=_render_appeal_text(appeal, appellant=appellant, resolver=resolver),
            )

        status = AppealStatus(appeal.status)
        keyboard = moderation_appeal_back_keyboard(appeal_id=appeal.id, page=page)
        if status in {AppealStatus.OPEN, AppealStatus.IN_REVIEW}:
            keyboard = moderation_appeal_actions_keyboard(
                appeal_id=appeal.id,
                page=page,
                show_take=status == AppealStatus.OPEN,
            )
        await callback.message.edit_text(
            appeal_text,
            reply_markup=keyboard,
        )
        await callback.answer()
        return

    if section == "appeal_review":
        if len(parts) != 4 or not parts[2].isdigit():
            await callback.answer("Некорректная апелляция", show_alert=True)
            return
        appeal_id = int(parts[2])
        page = _parse_page(parts[3])
        if page is None:
            await callback.answer("Некорректная страница", show_alert=True)
            return
        if not await _require_scope_callback(callback, SCOPE_USER_BAN):
            return

        async with SessionFactory() as session:
            async with session.begin():
                actor = await upsert_user(session, callback.from_user)
                result = await mark_appeal_in_review(
                    session,
                    appeal_id=appeal_id,
                    reviewer_user_id=actor.id,
                    note="Взята в работу через modpanel",
                )

        if not result.ok:
            await callback.answer(result.message, show_alert=True)
            return

        text, keyboard = await _build_appeals_page(page)
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer(result.message)
        return

    if section in {"appeal_resolve", "appeal_reject"}:
        if len(parts) != 4 or not parts[2].isdigit():
            await callback.answer("Некорректная апелляция", show_alert=True)
            return
        appeal_id = int(parts[2])
        page = _parse_page(parts[3])
        if page is None:
            await callback.answer("Некорректная страница", show_alert=True)
            return
        if not await _require_scope_callback(callback, SCOPE_USER_BAN):
            return

        notify_tg_user_id: int | None = None
        notify_text: str | None = None
        action_message = "Апелляция обработана"

        async with SessionFactory() as session:
            async with session.begin():
                actor = await upsert_user(session, callback.from_user)
                audit_action = ModerationAction.RESOLVE_APPEAL
                if section == "appeal_resolve":
                    result = await resolve_appeal(
                        session,
                        appeal_id=appeal_id,
                        resolver_user_id=actor.id,
                        note="Апелляция удовлетворена",
                    )
                    action_message = "Апелляция удовлетворена"
                else:
                    audit_action = ModerationAction.REJECT_APPEAL
                    result = await reject_appeal(
                        session,
                        appeal_id=appeal_id,
                        resolver_user_id=actor.id,
                        note="Апелляция отклонена",
                    )
                    action_message = "Апелляция отклонена"

                if not result.ok or result.appeal is None:
                    await callback.answer(result.message, show_alert=True)
                    return

                related_auction_id = await resolve_appeal_auction_id(session, result.appeal)
                await log_moderation_action(
                    session,
                    actor_user_id=actor.id,
                    action=audit_action,
                    reason=result.appeal.resolution_note or action_message,
                    target_user_id=result.appeal.appellant_user_id,
                    auction_id=related_auction_id,
                    payload={
                        "appeal_id": result.appeal.id,
                        "appeal_ref": result.appeal.appeal_ref,
                        "source_type": result.appeal.source_type,
                        "source_id": result.appeal.source_id,
                    },
                )

                appellant = await session.scalar(select(User).where(User.id == result.appeal.appellant_user_id))
                if appellant is not None:
                    notify_tg_user_id = appellant.tg_user_id
                    notify_text = (
                        f"По вашей апелляции #{result.appeal.id} принято решение: {result.appeal.resolution_note or action_message}."
                    )

        if notify_tg_user_id is not None and notify_text is not None:
            await send_user_topic_message(
                bot,
                tg_user_id=notify_tg_user_id,
                purpose=PrivateTopicPurpose.SUPPORT,
                text=notify_text,
                notification_event=NotificationEventType.SUPPORT,
            )

        text, keyboard = await _build_appeals_page(page)
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer(action_message)
        return

    if section == "complaints":
        if len(parts) != 3:
            await callback.answer("Некорректная пагинация", show_alert=True)
            return
        page = _parse_page(parts[2])
        if page is None:
            await callback.answer("Некорректная страница", show_alert=True)
            return

        offset = page * PANEL_PAGE_SIZE
        async with SessionFactory() as session:
            complaints = await list_complaints(
                session,
                auction_id=None,
                status="OPEN",
                limit=PANEL_PAGE_SIZE + 1,
                offset=offset,
            )

        has_next = len(complaints) > PANEL_PAGE_SIZE
        visible = complaints[:PANEL_PAGE_SIZE]
        items = [
            (item.id, f"Жалоба #{item.id} | auc {str(item.auction_id)[:8]}")
            for item in visible
        ]
        text_lines = [f"Открытые жалобы, стр. {page + 1}"]
        for item in visible:
            text_lines.append(f"- #{item.id} | auc={str(item.auction_id)[:8]} | reason={item.reason[:40]}")
        if not visible:
            text_lines.append("- нет записей")

        await callback.message.edit_text(
            "\n".join(text_lines),
            reply_markup=moderation_complaints_list_keyboard(
                items=items,
                page=page,
                has_next=has_next,
            ),
        )
        await callback.answer()
        return

    if section == "signals":
        if len(parts) != 3:
            await callback.answer("Некорректная пагинация", show_alert=True)
            return
        page = _parse_page(parts[2])
        if page is None:
            await callback.answer("Некорректная страница", show_alert=True)
            return

        offset = page * PANEL_PAGE_SIZE
        async with SessionFactory() as session:
            signals = await list_fraud_signals(
                session,
                auction_id=None,
                status="OPEN",
                limit=PANEL_PAGE_SIZE + 1,
                offset=offset,
            )
            visible_user_ids = sorted({signal.user_id for signal in signals[:PANEL_PAGE_SIZE]})
            users = (
                (await session.execute(select(User).where(User.id.in_(visible_user_ids)))).scalars().all()
                if visible_user_ids
                else []
            )
            users_by_id = {user.id: user for user in users}
            verified_tg_ids = await load_verified_tg_user_ids(
                session,
                tg_user_ids=[user.tg_user_id for user in users],
            )

        has_next = len(signals) > PANEL_PAGE_SIZE
        visible = signals[:PANEL_PAGE_SIZE]
        items = []
        for signal in visible:
            user = users_by_id.get(signal.user_id)
            tg_user_id = user.tg_user_id if user is not None else signal.user_id
            verified_marker = " ✅" if tg_user_id in verified_tg_ids else ""
            items.append((signal.id, f"Сигнал #{signal.id} | score {signal.score}{verified_marker}"))

        text_lines = [f"Открытые фрод-сигналы, стр. {page + 1}"]
        for signal in visible:
            user = users_by_id.get(signal.user_id)
            tg_user_id = user.tg_user_id if user is not None else signal.user_id
            verified_marker = " [verified]" if tg_user_id in verified_tg_ids else ""
            text_lines.append(
                f"- #{signal.id} | auc={str(signal.auction_id)[:8]} | user={tg_user_id}{verified_marker} | score={signal.score}"
            )
        if not visible:
            text_lines.append("- нет записей")

        await callback.message.edit_text(
            "\n".join(text_lines),
            reply_markup=moderation_signals_list_keyboard(
                items=items,
                page=page,
                has_next=has_next,
            ),
        )
        await callback.answer()
        return

    if section == "frozen":
        if len(parts) != 3:
            await callback.answer("Некорректная пагинация", show_alert=True)
            return
        page = _parse_page(parts[2])
        if page is None:
            await callback.answer("Некорректная страница", show_alert=True)
            return
        if not await _require_scope_callback(callback, SCOPE_AUCTION_MANAGE):
            return

        text, keyboard = await _build_frozen_auctions_page(page)
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()
        return

    if section == "frozen_auction":
        if len(parts) != 4:
            await callback.answer("Некорректный аукцион", show_alert=True)
            return
        auction_id = _parse_uuid(parts[2])
        page = _parse_page(parts[3])
        if auction_id is None or page is None:
            await callback.answer("Некорректные параметры", show_alert=True)
            return
        if not await _require_scope_callback(callback, SCOPE_AUCTION_MANAGE):
            return

        async with SessionFactory() as session:
            auction = await session.scalar(select(Auction).where(Auction.id == auction_id))

        if auction is None:
            await callback.answer("Аукцион не найден", show_alert=True)
            return

        text = (
            f"Аукцион {auction.id}\n"
            f"Статус: {auction.status}\n"
            f"Seller UID: {auction.seller_user_id}\n"
            f"Ends: {auction.ends_at}"
        )
        await callback.message.edit_text(
            text,
            reply_markup=moderation_frozen_actions_keyboard(auction_id=str(auction.id), page=page),
        )
        await callback.answer()
        return

    if section == "unfreeze":
        if len(parts) != 4:
            await callback.answer("Некорректный аукцион", show_alert=True)
            return
        auction_id = _parse_uuid(parts[2])
        page = _parse_page(parts[3])
        if auction_id is None or page is None:
            await callback.answer("Некорректные параметры", show_alert=True)
            return
        if not await _require_scope_callback(callback, SCOPE_AUCTION_MANAGE):
            return

        seller_tg_user_id: int | None = None
        async with SessionFactory() as session:
            async with session.begin():
                actor = await upsert_user(session, callback.from_user)
                result = await unfreeze_auction(
                    session,
                    actor_user_id=actor.id,
                    auction_id=auction_id,
                    reason="Через modpanel",
                )
                seller_tg_user_id = result.seller_tg_user_id

        if not result.ok:
            await callback.answer(result.message, show_alert=True)
            return

        await refresh_auction_posts(bot, auction_id)
        if seller_tg_user_id is not None:
            reply_markup = await _auction_post_keyboard(bot, auction_id)
            await send_user_topic_message(
                bot,
                tg_user_id=seller_tg_user_id,
                purpose=PrivateTopicPurpose.AUCTIONS,
                text=moderation_unfrozen_text(auction_id),
                reply_markup=reply_markup,
                notification_event=NotificationEventType.AUCTION_MOD_ACTION,
                auction_id=auction_id,
            )

        text, keyboard = await _build_frozen_auctions_page(page)
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer(result.message)
        return

    if section == "complaint":
        if len(parts) != 4 or not parts[2].isdigit():
            await callback.answer("Некорректная жалоба", show_alert=True)
            return
        complaint_id = int(parts[2])
        page = _parse_page(parts[3])
        if page is None:
            await callback.answer("Некорректная страница", show_alert=True)
            return

        async with SessionFactory() as session:
            view = await load_complaint_view(session, complaint_id)
            complaint_text = None
            if view is not None:
                complaint_text = await _append_checklist_block(
                    session,
                    entity_type=ENTITY_COMPLAINT,
                    entity_id=complaint_id,
                    text=render_complaint_text(view),
                )
        if view is None:
            await callback.answer("Жалоба не найдена", show_alert=True)
            return

        await callback.message.edit_text(
            complaint_text or render_complaint_text(view),
            reply_markup=complaint_actions_keyboard(
                complaint_id,
                back_callback=f"modui:complaints:{page}",
                checklist_page=page,
            ),
        )
        await callback.answer()
        return

    if section == "signal":
        if len(parts) != 4 or not parts[2].isdigit():
            await callback.answer("Некорректный сигнал", show_alert=True)
            return
        signal_id = int(parts[2])
        page = _parse_page(parts[3])
        if page is None:
            await callback.answer("Некорректная страница", show_alert=True)
            return

        async with SessionFactory() as session:
            view = await load_fraud_signal_view(session, signal_id)
            verification = None
            if view is not None:
                verification = await get_user_verification_status(
                    session,
                    tg_user_id=view.user.tg_user_id,
                )
        if view is None:
            await callback.answer("Сигнал не найден", show_alert=True)
            return

        verification_line = "Верификация пользователя: yes" if verification and verification.is_verified else "Верификация пользователя: no"

        await callback.message.edit_text(
            f"{render_fraud_signal_text(view)}\n{verification_line}",
            reply_markup=fraud_actions_keyboard(
                signal_id,
                back_callback=f"modui:signals:{page}",
            ),
        )
        await callback.answer()
        return

    await callback.answer("Неизвестный раздел", show_alert=True)


@router.callback_query(F.data.startswith("modchk:"))
async def mod_checklist_callbacks(callback: CallbackQuery) -> None:
    if not await _require_moderator_callback(callback):
        return
    if callback.data is None or callback.message is None or callback.from_user is None:
        return

    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer("Некорректный чеклист", show_alert=True)
        return

    _, action, entity_type, entity_id_raw, *rest = parts
    if not entity_id_raw.isdigit():
        await callback.answer("Некорректный id", show_alert=True)
        return
    entity_id = int(entity_id_raw)

    item_code: str | None = None
    if action in {"toggle", "reply"}:
        if len(rest) != 2:
            await callback.answer("Некорректный пункт", show_alert=True)
            return
        item_code = rest[0]
        page = _parse_page(rest[1])
    else:
        if len(rest) != 1:
            await callback.answer("Некорректная страница", show_alert=True)
            return
        page = _parse_page(rest[0])

    if page is None:
        await callback.answer("Некорректная страница", show_alert=True)
        return

    if entity_type not in {ENTITY_COMPLAINT, ENTITY_APPEAL, ENTITY_GUARANTOR}:
        await callback.answer("Неизвестный тип чеклиста", show_alert=True)
        return

    action_message = ""
    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, callback.from_user)
            if action == "toggle":
                toggled = await toggle_checklist_item(
                    session,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    item_code=item_code or "",
                    actor_user_id=actor.id,
                )
                if toggled is None:
                    await callback.answer("Пункт не найден", show_alert=True)
                    return
                await log_moderation_action(
                    session,
                    actor_user_id=actor.id,
                    action=ModerationAction.UPDATE_MODERATION_CHECKLIST,
                    reason=f"Checklist {entity_type}:{entity_id}:{item_code}",
                    payload={
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "item_code": item_code,
                        "is_done": toggled.is_done,
                    },
                )
                action_message = "Чеклист обновлен"

            if action == "reply":
                _PENDING_CHECKLIST_REPLIES[callback.from_user.id] = _PendingChecklistReply(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    item_code=item_code or "",
                    page=page,
                )
                action_message = "Отправьте ответ одним сообщением в чат модерации"

            items = await ensure_checklist(session, entity_type=entity_type, entity_id=entity_id)
            replies_by_item = await list_checklist_replies(
                session,
                entity_type=entity_type,
                entity_id=entity_id,
            )

    back_callback = _checklist_back_callback(entity_type=entity_type, entity_id=entity_id, page=page)
    keyboard = moderation_checklist_keyboard(
        entity_type=entity_type,
        entity_id=entity_id,
        page=page,
        items=items,
        back_callback=back_callback,
    )
    title = _checklist_title(entity_type=entity_type, entity_id=entity_id)
    checklist_text = render_checklist_block(items, replies_by_item=replies_by_item)
    await callback.message.edit_text(f"{title}\n\n{checklist_text}", reply_markup=keyboard)
    await callback.answer(action_message)


@router.message(F.text)
async def mod_checklist_reply_message(message: Message, bot: Bot) -> None:
    if message.from_user is None or message.text is None:
        return

    pending = _PENDING_CHECKLIST_REPLIES.get(message.from_user.id)
    if pending is None:
        return

    if not await _ensure_moderation_topic(message, bot, "/modpanel"):
        return
    if not await _require_moderator(message):
        return

    raw = message.text.strip()
    if raw == "/cancel":
        _PENDING_CHECKLIST_REPLIES.pop(message.from_user.id, None)
        await message.answer("Ответ к чеклисту отменен")
        return
    if len(raw) < 2:
        await message.answer("Ответ слишком короткий. Отправьте текст или /cancel")
        return

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, message.from_user)
            reply = await add_checklist_reply(
                session,
                entity_type=pending.entity_type,
                entity_id=pending.entity_id,
                item_code=pending.item_code,
                actor_user_id=actor.id,
                reply_text=raw,
            )
            if reply is None:
                await message.answer("Не удалось сохранить ответ для этого пункта")
                return

            await log_moderation_action(
                session,
                actor_user_id=actor.id,
                action=ModerationAction.UPDATE_MODERATION_CHECKLIST,
                reason=f"Checklist reply {pending.entity_type}:{pending.entity_id}:{pending.item_code}",
                payload={
                    "entity_type": pending.entity_type,
                    "entity_id": pending.entity_id,
                    "item_code": pending.item_code,
                    "reply_text": raw,
                },
            )

            items = await ensure_checklist(
                session,
                entity_type=pending.entity_type,
                entity_id=pending.entity_id,
            )
            replies_by_item = await list_checklist_replies(
                session,
                entity_type=pending.entity_type,
                entity_id=pending.entity_id,
            )

    _PENDING_CHECKLIST_REPLIES.pop(message.from_user.id, None)
    back_callback = _checklist_back_callback(
        entity_type=pending.entity_type,
        entity_id=pending.entity_id,
        page=pending.page,
    )
    keyboard = moderation_checklist_keyboard(
        entity_type=pending.entity_type,
        entity_id=pending.entity_id,
        page=pending.page,
        items=items,
        back_callback=back_callback,
    )
    title = _checklist_title(entity_type=pending.entity_type, entity_id=pending.entity_id)
    checklist_text = render_checklist_block(items, replies_by_item=replies_by_item)
    await message.answer(f"{title}\n\n{checklist_text}", reply_markup=keyboard)


@router.callback_query(F.data.startswith("modrep:"))
async def mod_report_action(callback: CallbackQuery, bot: Bot) -> None:
    if not await _ensure_moderation_callback_topic(callback, bot):
        return
    if not await _require_moderator_callback(callback):
        return
    if callback.data is None or callback.from_user is None:
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректная команда", show_alert=True)
        return

    _, action, complaint_id_raw = parts
    if not complaint_id_raw.isdigit():
        await callback.answer("Некорректный complaint_id", show_alert=True)
        return

    required_scope = _complaint_action_required_scope(action)
    if required_scope is None:
        await callback.answer("Неизвестное действие", show_alert=True)
        return

    if not await _require_scope_callback(callback, required_scope):
        return

    complaint_id = int(complaint_id_raw)

    auction_id: uuid.UUID | None = None
    notify_target_tg_user_id: int | None = None
    callback_message = "Действие выполнено"
    updated_text: str | None = None
    sanction_note: str | None = None

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, callback.from_user)
            view = await load_complaint_view(session, complaint_id, for_update=True)
            if view is None:
                await callback.answer("Жалоба не найдена", show_alert=True)
                return

            if view.complaint.status != "OPEN":
                await callback.answer(f"Жалоба уже обработана ({view.complaint.status})", show_alert=True)
                return

            auction_id = view.complaint.auction_id

            if action == "dismiss":
                await resolve_complaint(
                    session,
                    complaint_id=complaint_id,
                    resolver_user_id=actor.id,
                    status="DISMISSED",
                    note="Отклонено модератором",
                )
                callback_message = "Жалоба отклонена"

            elif action == "freeze":
                freeze_result = await freeze_auction(
                    session,
                    actor_user_id=actor.id,
                    auction_id=view.auction.id,
                    reason=f"Жалоба #{complaint_id}",
                )
                if not freeze_result.ok:
                    await callback.answer(freeze_result.message, show_alert=True)
                    return

                await resolve_complaint(
                    session,
                    complaint_id=complaint_id,
                    resolver_user_id=actor.id,
                    status="RESOLVED",
                    note="Заморозка аукциона",
                )
                callback_message = "Аукцион заморожен"
                sanction_note = callback_message

            elif action == "rm_top":
                if view.complaint.target_bid_id is None:
                    await callback.answer("В жалобе нет связанной ставки", show_alert=True)
                    return

                rm_result = await remove_bid(
                    session,
                    actor_user_id=actor.id,
                    bid_id=view.complaint.target_bid_id,
                    reason=f"Жалоба #{complaint_id}: снятие топ-ставки",
                )
                if not rm_result.ok:
                    await callback.answer(rm_result.message, show_alert=True)
                    return

                notify_target_tg_user_id = rm_result.target_tg_user_id
                await resolve_complaint(
                    session,
                    complaint_id=complaint_id,
                    resolver_user_id=actor.id,
                    status="RESOLVED",
                    note="Снята топ-ставка",
                )
                callback_message = "Топ-ставка снята"
                sanction_note = callback_message

            elif action == "ban_top":
                if view.target_user is None:
                    await callback.answer("Подозреваемый пользователь не найден", show_alert=True)
                    return

                ban_result = await ban_user(
                    session,
                    actor_user_id=actor.id,
                    target_tg_user_id=view.target_user.tg_user_id,
                    reason=f"Жалоба #{complaint_id}: фрод-ставка",
                    auction_id=view.auction.id,
                )
                if not ban_result.ok:
                    await callback.answer(ban_result.message, show_alert=True)
                    return

                notify_target_tg_user_id = view.target_user.tg_user_id
                if view.complaint.target_bid_id is not None:
                    await remove_bid(
                        session,
                        actor_user_id=actor.id,
                        bid_id=view.complaint.target_bid_id,
                        reason=f"Жалоба #{complaint_id}: бан + снятие ставки",
                    )

                await resolve_complaint(
                    session,
                    complaint_id=complaint_id,
                    resolver_user_id=actor.id,
                    status="RESOLVED",
                    note="Пользователь заблокирован, ставка снята",
                )
                callback_message = "Пользователь заблокирован"
                sanction_note = callback_message

            refreshed_view = await load_complaint_view(session, complaint_id)
            if refreshed_view is not None:
                updated_text = await _append_checklist_block(
                    session,
                    entity_type=ENTITY_COMPLAINT,
                    entity_id=complaint_id,
                    text=render_complaint_text(refreshed_view),
                )

    if auction_id is not None:
        await refresh_auction_posts(bot, auction_id)

    if notify_target_tg_user_id is not None:
        sanction_label = sanction_note or "Применены санкции"
        appeal_note, appeal_keyboard = _build_appeal_cta(f"complaint_{complaint_id}")
        await send_user_topic_message(
            bot,
            tg_user_id=notify_target_tg_user_id,
            purpose=PrivateTopicPurpose.SUPPORT,
            text=(
                f"По жалобе #{complaint_id} модератор применил санкции: {sanction_label}.\n"
                f"{appeal_note}"
            ),
            reply_markup=appeal_keyboard,
            notification_event=NotificationEventType.SUPPORT,
        )

    if updated_text is not None and callback.message is not None:
        try:
            await callback.message.edit_text(updated_text, reply_markup=moderation_panel_keyboard())
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

    await callback.answer(callback_message)


@router.callback_query(F.data.startswith("modrisk:"))
async def mod_risk_action(callback: CallbackQuery, bot: Bot) -> None:
    if not await _ensure_moderation_callback_topic(callback, bot):
        return
    if not await _require_moderator_callback(callback):
        return
    if callback.data is None or callback.from_user is None:
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректная команда", show_alert=True)
        return

    _, action, signal_id_raw = parts
    if not signal_id_raw.isdigit():
        await callback.answer("Некорректный signal_id", show_alert=True)
        return

    required_scope = _risk_action_required_scope(action)
    if required_scope is None:
        await callback.answer("Неизвестное действие", show_alert=True)
        return

    if not await _require_scope_callback(callback, required_scope):
        return

    signal_id = int(signal_id_raw)
    callback_message = "Действие выполнено"
    updated_text: str | None = None
    auction_id: uuid.UUID | None = None
    banned_user_tg: int | None = None
    sanction_note: str | None = None

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, callback.from_user)
            view = await load_fraud_signal_view(session, signal_id, for_update=True)
            if view is None:
                await callback.answer("Сигнал не найден", show_alert=True)
                return

            if view.signal.status != "OPEN":
                await callback.answer(f"Сигнал уже обработан ({view.signal.status})", show_alert=True)
                return

            auction_id = view.signal.auction_id

            if action == "ignore":
                await resolve_fraud_signal(
                    session,
                    signal_id=signal_id,
                    resolver_user_id=actor.id,
                    status="DISMISSED",
                    note="Сигнал отклонен модератором",
                )
                callback_message = "Сигнал отклонен"

            elif action == "freeze":
                freeze_result = await freeze_auction(
                    session,
                    actor_user_id=actor.id,
                    auction_id=view.auction.id,
                    reason=f"Фрод-сигнал #{signal_id}",
                )
                if not freeze_result.ok:
                    await callback.answer(freeze_result.message, show_alert=True)
                    return

                await resolve_fraud_signal(
                    session,
                    signal_id=signal_id,
                    resolver_user_id=actor.id,
                    status="CONFIRMED",
                    note="Аукцион заморожен",
                )
                callback_message = "Аукцион заморожен"
                sanction_note = callback_message

            elif action == "ban":
                ban_result = await ban_user(
                    session,
                    actor_user_id=actor.id,
                    target_tg_user_id=view.user.tg_user_id,
                    reason=f"Фрод-сигнал #{signal_id}",
                    auction_id=view.auction.id,
                )
                if not ban_result.ok:
                    await callback.answer(ban_result.message, show_alert=True)
                    return

                banned_user_tg = view.user.tg_user_id
                await resolve_fraud_signal(
                    session,
                    signal_id=signal_id,
                    resolver_user_id=actor.id,
                    status="CONFIRMED",
                    note="Пользователь заблокирован",
                )
                callback_message = "Пользователь заблокирован"
                sanction_note = callback_message

            refreshed = await load_fraud_signal_view(session, signal_id)
            if refreshed is not None:
                verification = await get_user_verification_status(
                    session,
                    tg_user_id=refreshed.user.tg_user_id,
                )
                verification_line = "Верификация пользователя: yes" if verification.is_verified else "Верификация пользователя: no"
                updated_text = f"{render_fraud_signal_text(refreshed)}\n{verification_line}"

    if auction_id is not None:
        await refresh_auction_posts(bot, auction_id)

    if banned_user_tg is not None:
        sanction_label = sanction_note or "Применены санкции"
        appeal_note, appeal_keyboard = _build_appeal_cta(f"risk_{signal_id}")
        await send_user_topic_message(
            bot,
            tg_user_id=banned_user_tg,
            purpose=PrivateTopicPurpose.SUPPORT,
            text=(
                f"Ваш аккаунт получил санкции по фрод-сигналу #{signal_id}: {sanction_label}.\n"
                f"{appeal_note}"
            ),
            reply_markup=appeal_keyboard,
            notification_event=NotificationEventType.SUPPORT,
        )

    if updated_text is not None and callback.message is not None:
        try:
            await callback.message.edit_text(updated_text, reply_markup=moderation_panel_keyboard())
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

    await callback.answer(callback_message)
