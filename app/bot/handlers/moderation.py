from __future__ import annotations

import uuid

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.bot.keyboards.moderation import (
    complaint_actions_keyboard,
    fraud_actions_keyboard,
    moderation_frozen_actions_keyboard,
    moderation_frozen_list_keyboard,
    moderation_complaints_list_keyboard,
    moderation_panel_keyboard,
    moderation_signals_list_keyboard,
)
from app.config import settings
from app.db.enums import AuctionStatus
from app.db.models import Auction
from app.db.session import SessionFactory
from app.services.auction_service import refresh_auction_posts
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
    list_tg_user_roles,
    list_moderation_logs,
    list_recent_bids,
    remove_bid,
    revoke_moderator_role,
    unban_user,
    unfreeze_auction,
)
from app.services.moderation_dashboard_service import get_moderation_dashboard_snapshot
from app.services.rbac_service import (
    SCOPE_AUCTION_MANAGE,
    SCOPE_BID_MANAGE,
    SCOPE_ROLE_MANAGE,
    SCOPE_USER_BAN,
)
from app.services.user_service import upsert_user

router = Router(name="moderation")
PANEL_PAGE_SIZE = 5


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


def _split_args(text: str) -> tuple[str, str] | None:
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        return None
    return parts[1], parts[2]


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


async def _render_mod_panel_home_text() -> str:
    async with SessionFactory() as session:
        snapshot = await get_moderation_dashboard_snapshot(session)

    return (
        "Мод-панель\n"
        f"- Открытые жалобы: {snapshot.open_complaints}\n"
        f"- Открытые фрод-сигналы: {snapshot.open_signals}\n"
        f"- Активные аукционы: {snapshot.active_auctions}\n"
        f"- Замороженные аукционы: {snapshot.frozen_auctions}\n\n"
        "Используйте кнопки ниже для просмотра очередей."
    )


async def _render_mod_stats_text() -> str:
    async with SessionFactory() as session:
        snapshot = await get_moderation_dashboard_snapshot(session)

    engaged_with_private = max(
        snapshot.users_with_engagement - snapshot.users_engaged_without_private_start,
        0,
    )
    hint_conv = "0.0%"
    if snapshot.users_with_soft_gate_hint > 0:
        hint_conv = f"{(snapshot.users_converted_after_hint / snapshot.users_with_soft_gate_hint) * 100:.1f}%"

    return (
        "Статистика модерации\n"
        f"- Открытые жалобы: {snapshot.open_complaints}\n"
        f"- Открытые фрод-сигналы: {snapshot.open_signals}\n"
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
        f"- Вовлеченные с private /start: {engaged_with_private}"
    )


def _parse_page(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < 0:
        return None
    return value


@router.message(Command("mod"), F.chat.type == ChatType.PRIVATE)
async def mod_help(message: Message) -> None:
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
            ]
        )

    await message.answer("Команды модерации:\n" + "\n".join(commands))


@router.message(Command("modstats"), F.chat.type == ChatType.PRIVATE)
async def mod_stats(message: Message) -> None:
    if not await _require_moderator(message):
        return
    await message.answer(await _render_mod_stats_text())


@router.message(Command("role"), F.chat.type == ChatType.PRIVATE)
async def mod_role_manage(message: Message) -> None:
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


@router.message(Command("modpanel"), F.chat.type == ChatType.PRIVATE)
async def mod_panel(message: Message) -> None:
    if not await _require_moderator(message):
        return

    text = await _render_mod_panel_home_text()
    await message.answer(text, reply_markup=moderation_panel_keyboard())


@router.callback_query(F.data == "mod:panel")
async def mod_panel_from_button(callback: CallbackQuery) -> None:
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
        try:
            await bot.send_message(result.seller_tg_user_id, f"Аукцион #{str(auction_id)[:8]} заморожен модератором")
        except TelegramForbiddenError:
            pass


@router.message(Command("unfreeze"), F.chat.type == ChatType.PRIVATE)
async def mod_unfreeze(message: Message, bot: Bot) -> None:
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
        try:
            await bot.send_message(
                result.seller_tg_user_id,
                f"Аукцион #{str(auction_id)[:8]} разморожен модератором",
            )
        except TelegramForbiddenError:
            pass


@router.message(Command("end"), F.chat.type == ChatType.PRIVATE)
async def mod_end(message: Message, bot: Bot) -> None:
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

    if result.seller_tg_user_id:
        try:
            await bot.send_message(result.seller_tg_user_id, f"Аукцион #{str(auction_id)[:8]} завершен модератором")
        except TelegramForbiddenError:
            pass
    if result.winner_tg_user_id:
        try:
            await bot.send_message(result.winner_tg_user_id, f"Вы победили в аукционе #{str(auction_id)[:8]}")
        except TelegramForbiddenError:
            pass


@router.message(Command("bids"), F.chat.type == ChatType.PRIVATE)
async def mod_bids(message: Message) -> None:
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
        try:
            await bot.send_message(result.target_tg_user_id, "Ваша ставка была снята модератором")
        except TelegramForbiddenError:
            pass


@router.message(Command("ban"), F.chat.type == ChatType.PRIVATE)
async def mod_ban(message: Message) -> None:
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
async def mod_unban(message: Message) -> None:
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
async def mod_audit(message: Message) -> None:
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


@router.message(Command("risk"), F.chat.type == ChatType.PRIVATE)
async def mod_risk(message: Message) -> None:
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

    if not signals:
        await message.answer("Открытые фрод-сигналы не найдены")
        return

    lines = ["Открытые фрод-сигналы:"]
    for signal in signals:
        lines.append(
            f"- #{signal.id} | auc={str(signal.auction_id)[:8]} | user={signal.user_id} | score={signal.score}"
        )
    await message.answer("\n".join(lines[:30]))


@router.callback_query(F.data.startswith("modui:"))
async def mod_panel_callbacks(callback: CallbackQuery, bot: Bot) -> None:
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

        has_next = len(signals) > PANEL_PAGE_SIZE
        visible = signals[:PANEL_PAGE_SIZE]
        items = [
            (signal.id, f"Сигнал #{signal.id} | score {signal.score}")
            for signal in visible
        ]
        text_lines = [f"Открытые фрод-сигналы, стр. {page + 1}"]
        for signal in visible:
            text_lines.append(
                f"- #{signal.id} | auc={str(signal.auction_id)[:8]} | user={signal.user_id} | score={signal.score}"
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
            try:
                await bot.send_message(
                    seller_tg_user_id,
                    f"Аукцион #{str(auction_id)[:8]} разморожен модератором",
                )
            except TelegramForbiddenError:
                pass

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
        if view is None:
            await callback.answer("Жалоба не найдена", show_alert=True)
            return

        await callback.message.edit_text(
            render_complaint_text(view),
            reply_markup=complaint_actions_keyboard(
                complaint_id,
                back_callback=f"modui:complaints:{page}",
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
        if view is None:
            await callback.answer("Сигнал не найден", show_alert=True)
            return

        await callback.message.edit_text(
            render_fraud_signal_text(view),
            reply_markup=fraud_actions_keyboard(
                signal_id,
                back_callback=f"modui:signals:{page}",
            ),
        )
        await callback.answer()
        return

    await callback.answer("Неизвестный раздел", show_alert=True)


@router.callback_query(F.data.startswith("modrep:"))
async def mod_report_action(callback: CallbackQuery, bot: Bot) -> None:
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
                updated_text = render_complaint_text(refreshed_view)

    if auction_id is not None:
        await refresh_auction_posts(bot, auction_id)

    if notify_target_tg_user_id is not None:
        try:
            sanction_label = sanction_note or "Применены санкции"
            appeal_note, appeal_keyboard = _build_appeal_cta(f"complaint_{complaint_id}")
            await bot.send_message(
                notify_target_tg_user_id,
                (
                    f"По жалобе #{complaint_id} модератор применил санкции: {sanction_label}.\n"
                    f"{appeal_note}"
                ),
                reply_markup=appeal_keyboard,
            )
        except TelegramForbiddenError:
            pass

    if updated_text is not None and callback.message is not None:
        try:
            await callback.message.edit_text(updated_text, reply_markup=moderation_panel_keyboard())
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

    await callback.answer(callback_message)


@router.callback_query(F.data.startswith("modrisk:"))
async def mod_risk_action(callback: CallbackQuery, bot: Bot) -> None:
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
                updated_text = render_fraud_signal_text(refreshed)

    if auction_id is not None:
        await refresh_auction_posts(bot, auction_id)

    if banned_user_tg is not None:
        try:
            sanction_label = sanction_note or "Применены санкции"
            appeal_note, appeal_keyboard = _build_appeal_cta(f"risk_{signal_id}")
            await bot.send_message(
                banned_user_tg,
                (
                    f"Ваш аккаунт получил санкции по фрод-сигналу #{signal_id}: {sanction_label}.\n"
                    f"{appeal_note}"
                ),
                reply_markup=appeal_keyboard,
            )
        except TelegramForbiddenError:
            pass

    if updated_text is not None and callback.message is not None:
        try:
            await callback.message.edit_text(updated_text, reply_markup=moderation_panel_keyboard())
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

    await callback.answer(callback_message)
