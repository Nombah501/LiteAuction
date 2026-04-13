from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from html import escape
from urllib.parse import urlencode

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select

from app.config import settings
from app.db.enums import ModerationAction, PointsEventType, UserRole
from app.db.models import (
    Appeal,
    Bid,
    BlacklistEntry,
    Complaint,
    FeedbackItem,
    FraudSignal,
    GuarantorRequest,
    User,
    UserRoleAssignment,
)
from app.db.session import SessionFactory
from app.services.moderation_service import (
    ban_user,
    is_moderator_tg_user,
    list_user_roles,
    log_moderation_action,
    unban_user,
)
from app.services.points_service import (
    count_user_points_entries,
    get_points_redemption_account_age_remaining_seconds,
    get_points_redemptions_spent_today,
    get_points_redemptions_spent_this_month,
    get_points_redemptions_spent_this_week,
    get_points_redemptions_used_today,
    get_points_redemptions_used_this_week,
    get_user_points_summary,
    grant_points,
    list_user_points_entries,
)
from app.services.rbac_service import (
    SCOPE_ROLE_MANAGE,
    SCOPE_TRUST_MANAGE,
    SCOPE_USER_BAN,
)
from app.services.risk_eval_service import (
    UserRiskSnapshot,
    evaluate_user_risk_snapshot,
    format_risk_reason_label,
)
from app.services.trade_feedback_service import (
    get_trade_feedback_summary,
    list_received_trade_feedback,
)
from app.services.verification_service import (
    get_user_verification_status,
    load_verified_user_ids,
    set_user_verification,
)
from app.web.components import (
    _action_error_page,
    _fmt_ts,
    _kpi_card,
    _kpi_grid,
    _load_dense_list_config,
    _pager_html,
    _render_app_header,
    _render_confirmation_page,
    _render_page,
    _risk_snapshot_inline_html,
    _safe_return_to,
)
from app.web.dense_list import render_dense_list_script, render_dense_list_toolbar
from app.web.deps import (
    _auth_context_or_unauthorized,
    _csrf_failed_response,
    _csrf_hidden_input,
    _is_confirmed,
    _path_with_auth,
    _require_scope_permission,
    _validate_csrf_token,
)
from app.web.filters import (
    _normalize_points_filter_query,
    _parse_signed_int,
    _points_event_label,
    _points_filter_query_value,
)

router = APIRouter()
logger = logging.getLogger(__name__)


async def _load_user_risk_snapshot_map(
    session,
    *,
    user_ids: list[int],
    now: datetime | None = None,
) -> dict[int, UserRiskSnapshot]:
    unique_user_ids = sorted(set(user_ids))
    if not unique_user_ids:
        return {}

    current_time = now or datetime.now(UTC)

    complaint_counts = {
        int(user_id): int(total)
        for user_id, total in (
            await session.execute(
                select(Complaint.target_user_id, func.count(Complaint.id))
                .where(Complaint.target_user_id.in_(unique_user_ids))
                .group_by(Complaint.target_user_id)
            )
        ).all()
    }
    open_fraud_counts = {
        int(user_id): int(total)
        for user_id, total in (
            await session.execute(
                select(FraudSignal.user_id, func.count(FraudSignal.id))
                .where(
                    FraudSignal.user_id.in_(unique_user_ids),
                    FraudSignal.status == "OPEN",
                )
                .group_by(FraudSignal.user_id)
            )
        ).all()
    }
    removed_bid_counts = {
        int(user_id): int(total)
        for user_id, total in (
            await session.execute(
                select(Bid.user_id, func.count(Bid.id))
                .where(
                    Bid.user_id.in_(unique_user_ids),
                    Bid.is_removed.is_(True),
                )
                .group_by(Bid.user_id)
            )
        ).all()
    }
    active_blacklist_user_ids = set(
        (
            await session.execute(
                select(BlacklistEntry.user_id).where(
                    BlacklistEntry.user_id.in_(unique_user_ids),
                    BlacklistEntry.is_active.is_(True),
                    (
                        BlacklistEntry.expires_at.is_(None)
                        | (BlacklistEntry.expires_at > current_time)
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    verified_user_ids = await load_verified_user_ids(session, user_ids=unique_user_ids)

    risk_map = {}
    for user_id in unique_user_ids:
        risk_map[user_id] = evaluate_user_risk_snapshot(
            complaints_against=complaint_counts.get(user_id, 0),
            open_fraud_signals=open_fraud_counts.get(user_id, 0),
            has_active_blacklist=user_id in active_blacklist_user_ids,
            removed_bids=removed_bid_counts.get(user_id, 0),
            is_verified_user=user_id in verified_user_ids,
        )
    return risk_map


async def _resolve_actor_user_id(auth) -> int:
    tg_user_id = auth.tg_user_id
    if tg_user_id is None:
        admin_ids = settings.parsed_admin_user_ids()
        if not admin_ids:
            raise HTTPException(
                status_code=500, detail="ADMIN_USER_IDS is required for web actions"
            )
        tg_user_id = admin_ids[0]

    async with SessionFactory() as session:
        existing = await session.scalar(select(User).where(User.tg_user_id == tg_user_id))
        if existing is not None:
            return existing.id

        user = User(tg_user_id=tg_user_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


@router.get("/manage/user/{user_id}", response_class=HTMLResponse)
async def manage_user(
    request: Request,
    user_id: int,
    points_page: int = 1,
    points_filter: str = "all",
) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response

    now = datetime.now(UTC)
    points_page_size = 10
    points_page = max(points_page, 1)
    points_filter_value = _normalize_points_filter_query(points_filter)
    points_filter_query = _points_filter_query_value(points_filter_value)
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        user_roles = await list_user_roles(session, user.id)
        has_dynamic_mod = bool({UserRole.OWNER, UserRole.ADMIN, UserRole.MODERATOR} & user_roles)
        has_allowlist_mod = is_moderator_tg_user(user.tg_user_id)
        has_moderator_access = has_allowlist_mod or has_dynamic_mod

        active_blacklist_entry = await session.scalar(
            select(BlacklistEntry).where(
                BlacklistEntry.user_id == user.id,
                BlacklistEntry.is_active.is_(True),
                (BlacklistEntry.expires_at.is_(None) | (BlacklistEntry.expires_at > now)),
            )
        )

        bids_total = int(
            await session.scalar(select(func.count(Bid.id)).where(Bid.user_id == user.id)) or 0
        )
        bids_removed = int(
            await session.scalar(
                select(func.count(Bid.id)).where(
                    Bid.user_id == user.id,
                    Bid.is_removed.is_(True),
                )
            )
            or 0
        )
        complaints_created = int(
            await session.scalar(
                select(func.count(Complaint.id)).where(Complaint.reporter_user_id == user.id)
            )
            or 0
        )
        complaints_against = int(
            await session.scalar(
                select(func.count(Complaint.id)).where(Complaint.target_user_id == user.id)
            )
            or 0
        )
        fraud_total = int(
            await session.scalar(
                select(func.count(FraudSignal.id)).where(FraudSignal.user_id == user.id)
            )
            or 0
        )
        fraud_open = int(
            await session.scalar(
                select(func.count(FraudSignal.id)).where(
                    FraudSignal.user_id == user.id,
                    FraudSignal.status == "OPEN",
                )
            )
            or 0
        )

        recent_complaints_against = (
            (
                await session.execute(
                    select(Complaint)
                    .where(Complaint.target_user_id == user.id)
                    .order_by(Complaint.created_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        recent_fraud_signals = (
            (
                await session.execute(
                    select(FraudSignal)
                    .where(FraudSignal.user_id == user.id)
                    .order_by(FraudSignal.created_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )

        points_summary = await get_user_points_summary(session, user_id=user.id)
        points_total_items = await count_user_points_entries(
            session,
            user_id=user.id,
            event_type=points_filter_value,
        )
        points_total_pages = max((points_total_items + points_page_size - 1) // points_page_size, 1)
        if points_total_items > 0 and points_page > points_total_pages:
            points_page = points_total_pages

        points_entries = await list_user_points_entries(
            session,
            user_id=user.id,
            limit=points_page_size,
            offset=(points_page - 1) * points_page_size,
            event_type=points_filter_value,
        )
        boost_feedback_count = int(
            await session.scalar(
                select(func.count(FeedbackItem.id)).where(
                    FeedbackItem.submitter_user_id == user.id,
                    FeedbackItem.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_feedback_points_spent_total = int(
            await session.scalar(
                select(func.coalesce(func.sum(FeedbackItem.priority_boost_points_spent), 0)).where(
                    FeedbackItem.submitter_user_id == user.id,
                    FeedbackItem.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_guarantor_count = int(
            await session.scalar(
                select(func.count(GuarantorRequest.id)).where(
                    GuarantorRequest.submitter_user_id == user.id,
                    GuarantorRequest.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_guarantor_points_spent_total = int(
            await session.scalar(
                select(
                    func.coalesce(func.sum(GuarantorRequest.priority_boost_points_spent), 0)
                ).where(
                    GuarantorRequest.submitter_user_id == user.id,
                    GuarantorRequest.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_appeal_count = int(
            await session.scalar(
                select(func.count(Appeal.id)).where(
                    Appeal.appellant_user_id == user.id,
                    Appeal.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_appeal_points_spent_total = int(
            await session.scalar(
                select(func.coalesce(func.sum(Appeal.priority_boost_points_spent), 0)).where(
                    Appeal.appellant_user_id == user.id,
                    Appeal.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_points_spent_total = (
            boost_feedback_points_spent_total
            + boost_guarantor_points_spent_total
            + boost_appeal_points_spent_total
        )
        redemptions_used_today = await get_points_redemptions_used_today(
            session,
            user_id=user.id,
            now=now,
        )
        redemptions_used_this_week = await get_points_redemptions_used_this_week(
            session,
            user_id=user.id,
            now=now,
        )
        redemptions_spent_today = await get_points_redemptions_spent_today(
            session,
            user_id=user.id,
            now=now,
        )
        redemptions_spent_this_week = await get_points_redemptions_spent_this_week(
            session,
            user_id=user.id,
            now=now,
        )
        redemptions_spent_this_month = await get_points_redemptions_spent_this_month(
            session,
            user_id=user.id,
            now=now,
        )
        min_account_age_remaining = await get_points_redemption_account_age_remaining_seconds(
            session,
            user_id=user.id,
            min_account_age_seconds=settings.points_redemption_min_account_age_seconds,
            now=now,
        )

        trade_feedback_summary = await get_trade_feedback_summary(session, target_user_id=user.id)
        trade_feedback_received = await list_received_trade_feedback(
            session, target_user_id=user.id, limit=10
        )
        verification_status = await get_user_verification_status(
            session, tg_user_id=user.tg_user_id
        )

    can_ban_users = auth.can(SCOPE_USER_BAN)
    can_manage_roles = auth.can(SCOPE_ROLE_MANAGE)
    can_manage_trust = auth.can(SCOPE_TRUST_MANAGE)
    csrf_input = _csrf_hidden_input(request, auth)

    controls_blocks: list[str] = []

    if can_ban_users:
        controls_blocks.append(
            f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/ban'))}'>"
            f"<input type='hidden' name='target_tg_user_id' value='{user.tg_user_id}'>"
            f"<input type='hidden' name='return_to' value='{escape(f'/manage/user/{user.id}')}'>"
            f"{csrf_input}"
            "<input name='reason' placeholder='Причина бана' style='width:360px' required>"
            "<button type='submit'>Ban</button></form><br>"
            f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/unban'))}'>"
            f"<input type='hidden' name='target_tg_user_id' value='{user.tg_user_id}'>"
            f"<input type='hidden' name='return_to' value='{escape(f'/manage/user/{user.id}')}'>"
            f"{csrf_input}"
            "<input name='reason' placeholder='Причина разбана' style='width:360px' required>"
            "<button type='submit'>Unban</button></form>"
        )

    if can_manage_roles:
        role_block = ""
        if has_allowlist_mod:
            role_block = "<p><b>MODERATOR:</b> через ADMIN_USER_IDS (из UI не снимается)</p>"
        elif has_dynamic_mod:
            role_block = (
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/moderator/revoke'))}'>"
                f"<input type='hidden' name='target_tg_user_id' value='{user.tg_user_id}'>"
                f"<input type='hidden' name='return_to' value='{escape(f'/manage/user/{user.id}')}'>"
                f"{csrf_input}"
                "<input name='reason' placeholder='Причина снятия роли' style='width:360px' required>"
                "<button type='submit'>Revoke MODERATOR</button></form>"
            )
        else:
            role_block = (
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/moderator/grant'))}'>"
                f"<input type='hidden' name='target_tg_user_id' value='{user.tg_user_id}'>"
                f"<input type='hidden' name='return_to' value='{escape(f'/manage/user/{user.id}')}'>"
                f"{csrf_input}"
                "<input name='reason' placeholder='Причина выдачи роли' style='width:360px' required>"
                "<button type='submit'>Grant MODERATOR</button></form>"
            )
        controls_blocks.append(role_block)

    if can_manage_trust:
        if verification_status.is_verified:
            controls_blocks.append(
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/unverify'))}'>"
                f"<input type='hidden' name='target_tg_user_id' value='{user.tg_user_id}'>"
                f"<input type='hidden' name='return_to' value='{escape(f'/manage/user/{user.id}')}'>"
                f"{csrf_input}"
                "<button type='submit'>Снять верификацию</button></form>"
            )
        else:
            controls_blocks.append(
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/verify'))}'>"
                f"<input type='hidden' name='target_tg_user_id' value='{user.tg_user_id}'>"
                f"<input type='hidden' name='return_to' value='{escape(f'/manage/user/{user.id}')}'>"
                f"{csrf_input}"
                "<input name='custom_description' placeholder='Описание для Telegram (необязательно)' style='width:360px'>"
                "<button type='submit'>Подтвердить верификацию</button></form>"
            )

    controls = "<p><i>Только просмотр (нет прав на управление пользователями).</i></p>"
    if controls_blocks:
        controls = "<br>".join(controls_blocks)

    risk_snapshot = evaluate_user_risk_snapshot(
        complaints_against=complaints_against,
        open_fraud_signals=fraud_open,
        has_active_blacklist=active_blacklist_entry is not None,
        removed_bids=bids_removed,
        is_verified_user=verification_status.is_verified,
    )
    risk_reasons_text = "-"
    if risk_snapshot.reasons:
        risk_reasons_text = ", ".join(
            format_risk_reason_label(code) for code in risk_snapshot.reasons
        )

    complaints_rows = "".join(
        "<tr>"
        f"<td>{item.id}</td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.auction_id}'))}'>{escape(str(item.auction_id))}</a></td>"
        f"<td>{escape(item.status)}</td>"
        f"<td>{escape(item.reason[:120])}</td>"
        f"<td>{escape(_fmt_ts(item.created_at))}</td>"
        "</tr>"
        for item in recent_complaints_against
    )
    if not complaints_rows:
        complaints_rows = (
            "<tr><td colspan='5'><span class='empty-state'>Нет записей</span></td></tr>"
        )

    signal_rows = "".join(
        "<tr>"
        f"<td>{item.id}</td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.auction_id}'))}'>{escape(str(item.auction_id))}</a></td>"
        f"<td>{item.score}</td>"
        f"<td>{escape(item.status)}</td>"
        f"<td>{escape(_fmt_ts(item.created_at))}</td>"
        "</tr>"
        for item in recent_fraud_signals
    )
    if not signal_rows:
        signal_rows = "<tr><td colspan='5'><span class='empty-state'>Нет записей</span></td></tr>"

    points_rows = "".join(
        "<tr>"
        f"<td>{escape(_fmt_ts(item.created_at))}</td>"
        f"<td>{'+{}'.format(item.amount) if item.amount > 0 else item.amount}</td>"
        f"<td>{escape(_points_event_label(PointsEventType(item.event_type)))}</td>"
        f"<td>{escape(item.reason[:200])}</td>"
        "</tr>"
        for item in points_entries
    )
    if not points_rows:
        points_rows = "<tr><td colspan='4'><span class='empty-state'>Нет операций</span></td></tr>"

    trade_feedback_rows = "".join(
        "<tr>"
        f"<td>{view.item.id}</td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/timeline/auction/{view.auction.id}'))}'>{escape(str(view.auction.id))}</a></td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/manage/user/{view.author.id}'))}'>{escape(f'@{view.author.username}' if view.author.username else str(view.author.tg_user_id))}</a></td>"
        f"<td>{view.item.rating}/5</td>"
        f"<td>{'Виден' if view.item.status == 'VISIBLE' else 'Скрыт'}</td>"
        f"<td>{escape((view.item.comment or '-')[:180])}</td>"
        f"<td>{escape(_fmt_ts(view.item.created_at))}</td>"
        "</tr>"
        for view in trade_feedback_received
    )
    if not trade_feedback_rows:
        trade_feedback_rows = (
            "<tr><td colspan='7'><span class='empty-state'>Нет отзывов</span></td></tr>"
        )

    average_trade_rating_text = "-"
    if trade_feedback_summary.average_visible_rating is not None:
        average_trade_rating_text = f"{trade_feedback_summary.average_visible_rating:.1f}"

    def _points_manage_path(target_page: int, filter_query: str) -> str:
        query = urlencode({"points_page": str(target_page), "points_filter": filter_query})
        return f"/manage/user/{user.id}?{query}"

    points_manage_return_to = _points_manage_path(points_page, points_filter_query)

    points_prev_link = ""
    if points_page > 1:
        points_prev_link = (
            f"<a href='{escape(_path_with_auth(request, _points_manage_path(points_page - 1, points_filter_query)))}'>"
            "← Предыдущая страница</a>"
        )
    points_next_link = ""
    if points_page < points_total_pages:
        points_next_link = (
            f"<a href='{escape(_path_with_auth(request, _points_manage_path(points_page + 1, points_filter_query)))}'>"
            "Следующая страница →</a>"
        )
    points_pager = ""
    if points_prev_link or points_next_link:
        points_pager = f"<p>{points_prev_link} {' | ' if points_prev_link and points_next_link else ''}{points_next_link}</p>"

    points_filter_links = " ".join(
        (
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'all')))}'>all</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'feedback')))}'>feedback</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'manual')))}'>manual</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'boost')))}'>boost</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'gboost')))}'>gboost</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'aboost')))}'>aboost</a>",
        )
    )

    points_adjust_form = ""
    if can_manage_roles:
        points_action_id = secrets.token_hex(12)
        points_adjust_form = (
            "<div class='card'><h3>Ручная корректировка points</h3>"
            f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/points/adjust'))}'>"
            f"<input type='hidden' name='target_tg_user_id' value='{user.tg_user_id}'>"
            f"<input type='hidden' name='return_to' value='{escape(points_manage_return_to)}'>"
            f"<input type='hidden' name='action_id' value='{points_action_id}'>"
            f"{csrf_input}"
            "<input name='amount' placeholder='+10 или -5' style='width:140px' required>"
            "<input name='reason' placeholder='Причина корректировки' style='width:320px' required>"
            "<button type='submit'>Применить</button>"
            "</form></div>"
        )

    roles_text = ", ".join(sorted(role.value for role in user_roles)) if user_roles else "-"
    moderator_text = "yes" if has_moderator_access else "no"
    blacklist_status = "active" if active_blacklist_entry is not None else "no"
    verification_text = "verified" if verification_status.is_verified else "no"
    verification_desc = verification_status.custom_description or "-"
    feedback_boost_policy_status = "on" if settings.feedback_priority_boost_enabled else "off"
    guarantor_boost_policy_status = "on" if settings.guarantor_priority_boost_enabled else "off"
    appeal_boost_policy_status = "on" if settings.appeal_priority_boost_enabled else "off"
    global_daily_limit = max(settings.points_redemption_daily_limit, 0)
    global_daily_remaining = max(global_daily_limit - redemptions_used_today, 0)
    global_daily_limit_text = "без ограничений"
    if global_daily_limit > 0:
        global_daily_limit_text = (
            f"{redemptions_used_today}/{global_daily_limit} (осталось {global_daily_remaining})"
        )
    global_weekly_limit = max(settings.points_redemption_weekly_limit, 0)
    global_weekly_remaining = max(global_weekly_limit - redemptions_used_this_week, 0)
    global_weekly_limit_text = "без ограничений"
    if global_weekly_limit > 0:
        global_weekly_limit_text = f"{redemptions_used_this_week}/{global_weekly_limit} (осталось {global_weekly_remaining})"
    global_daily_spend_cap = max(settings.points_redemption_daily_spend_cap, 0)
    global_daily_spend_remaining = max(global_daily_spend_cap - redemptions_spent_today, 0)
    global_daily_spend_text = "без ограничений"
    if global_daily_spend_cap > 0:
        global_daily_spend_text = (
            f"{redemptions_spent_today}/{global_daily_spend_cap} points "
            f"(осталось {global_daily_spend_remaining})"
        )
    global_weekly_spend_cap = max(settings.points_redemption_weekly_spend_cap, 0)
    global_weekly_spend_remaining = max(global_weekly_spend_cap - redemptions_spent_this_week, 0)
    global_weekly_spend_text = "без ограничений"
    if global_weekly_spend_cap > 0:
        global_weekly_spend_text = (
            f"{redemptions_spent_this_week}/{global_weekly_spend_cap} points "
            f"(осталось {global_weekly_spend_remaining})"
        )
    global_monthly_spend_cap = max(settings.points_redemption_monthly_spend_cap, 0)
    global_monthly_spend_remaining = max(global_monthly_spend_cap - redemptions_spent_this_month, 0)
    global_monthly_spend_text = "без ограничений"
    if global_monthly_spend_cap > 0:
        global_monthly_spend_text = (
            f"{redemptions_spent_this_month}/{global_monthly_spend_cap} points "
            f"(осталось {global_monthly_spend_remaining})"
        )
    min_account_age_required = max(settings.points_redemption_min_account_age_seconds, 0)
    min_account_age_text = f"{min_account_age_required} сек"
    if min_account_age_required > 0:
        min_account_age_text = (
            f"{min_account_age_required} сек (осталось {min_account_age_remaining})"
        )
    min_earned_points_required = max(settings.points_redemption_min_earned_points, 0)
    min_earned_points_remaining = max(min_earned_points_required - points_summary.total_earned, 0)
    min_earned_points_text = f"{min_earned_points_required} points"
    if min_earned_points_required > 0:
        min_earned_points_text = (
            f"{min_earned_points_required} points (начислено {points_summary.total_earned}, "
            f"осталось {min_earned_points_remaining})"
        )

    risk_tone = "ok"
    if risk_snapshot.level == "MEDIUM":
        risk_tone = "warn"
    elif risk_snapshot.level == "HIGH":
        risk_tone = "critical"

    risk_cards = _kpi_grid(
        [
            _kpi_card("Ставок", str(bids_total)),
            _kpi_card("Снято ставок", str(bids_removed), tone="warn" if bids_removed > 0 else ""),
            _kpi_card("Жалоб создано", str(complaints_created)),
            _kpi_card(
                "Жалоб на пользователя",
                str(complaints_against),
                tone="warn" if complaints_against > 0 else "",
            ),
            _kpi_card("Фрод-сигналов", str(fraud_total)),
            _kpi_card(
                "Открытых сигналов", str(fraud_open), tone="critical" if fraud_open > 0 else ""
            ),
            _kpi_card("Риск-уровень", risk_snapshot.level, tone=risk_tone),
            _kpi_card("Риск-скор", str(risk_snapshot.score)),
        ]
    )
    points_cards = _kpi_grid(
        [
            _kpi_card("Points баланс", str(points_summary.balance)),
            _kpi_card("Начислено всего", f"+{points_summary.total_earned}"),
            _kpi_card("Списано всего", f"-{points_summary.total_spent}"),
            _kpi_card("Points операций", str(points_summary.operations_count)),
            _kpi_card("Бустов фидбека", str(boost_feedback_count)),
            _kpi_card("Бустов гаранта", str(boost_guarantor_count)),
            _kpi_card("Бустов апелляций", str(boost_appeal_count)),
            _kpi_card("Списано на бусты", f"-{boost_points_spent_total}"),
        ]
    )
    points_policy_cards = _kpi_grid(
        [
            _kpi_card(
                "Политика фидбек-буста",
                (
                    f"{feedback_boost_policy_status} | cost {settings.feedback_priority_boost_cost_points} | "
                    f"limit {settings.feedback_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.feedback_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card(
                "Политика буста гаранта",
                (
                    f"{guarantor_boost_policy_status} | cost {settings.guarantor_priority_boost_cost_points} | "
                    f"limit {settings.guarantor_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.guarantor_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card(
                "Политика буста апелляций",
                (
                    f"{appeal_boost_policy_status} | cost {settings.appeal_priority_boost_cost_points} | "
                    f"limit {settings.appeal_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.appeal_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card(
                "Глобальная политика редимпшенов",
                "on" if settings.points_redemption_enabled else "off",
            ),
            _kpi_card("Глобальный дневной лимит редимпшена", global_daily_limit_text),
            _kpi_card("Глобальный недельный лимит редимпшена", global_weekly_limit_text),
            _kpi_card("Глобальный лимит списания на бусты", global_daily_spend_text),
            _kpi_card("Глобальный недельный лимит списания на бусты", global_weekly_spend_text),
            _kpi_card("Глобальный месячный лимит списания на бусты", global_monthly_spend_text),
            _kpi_card(
                "Минимальный остаток после буста",
                f"{max(settings.points_redemption_min_balance, 0)} points",
            ),
            _kpi_card("Мин. возраст аккаунта для буста", min_account_age_text),
            _kpi_card("Мин. начислено points для буста", min_earned_points_text),
            _kpi_card(
                "Глобальный кулдаун редимпшена",
                f"{max(settings.points_redemption_cooldown_seconds, 0)} сек",
            ),
        ]
    )
    trade_cards = _kpi_grid(
        [
            _kpi_card("Отзывов получено", str(trade_feedback_summary.total_received)),
            _kpi_card("Видимых отзывов", str(trade_feedback_summary.visible_received)),
            _kpi_card("Скрытых отзывов", str(trade_feedback_summary.hidden_received)),
            _kpi_card("Средняя оценка (видимые)", average_trade_rating_text),
        ]
    )

    body = (
        f"{_render_app_header(f'Управление пользователем {user.id}', auth, 'Профиль модерации, риск и rewards')}"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/manage/users'))}'>К пользователям</a>"
        f"<a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"<p><b>TG User ID:</b> {user.tg_user_id} | <b>Username:</b> {escape(user.username or '-')}</p>"
        f"<p><b>Moderator:</b> {moderator_text} | <b>Roles:</b> {escape(roles_text)} | <b>Blacklisted:</b> {blacklist_status} | <b>Verified:</b> {verification_text}</p>"
        f"<p><b>Verification description:</b> {escape(verification_desc)}</p>"
        f"{risk_cards}"
        f"<p><b>Риск-факторы:</b> {escape(risk_reasons_text)}</p>"
        f"<div class='card'>{controls}</div>"
        "</div>"
        "<div class='section-card'>"
        "<h2>Rewards / points</h2>"
        f"{points_cards}"
        "<details class='details'><summary>Показать policy и лимиты</summary>"
        f"{points_policy_cards}"
        "</details>"
        f"{points_adjust_form}"
        f"<p><b>Фильтр:</b> {escape(points_filter_query)} | <b>Страница:</b> {points_page}/{points_total_pages} | "
        f"<b>Записей:</b> {points_total_items}</p>"
        f"<p>{points_filter_links}</p>"
        "<div class='table-wrap'><table><thead><tr><th>Created</th><th>Amount</th><th>Type</th><th>Reason</th></tr></thead>"
        f"<tbody>{points_rows}</tbody></table></div>"
        f"{points_pager}"
        "</div>"
        "<div class='section-card'>"
        "<h2>Репутация по сделкам</h2>"
        f"{trade_cards}"
        "<div class='table-wrap'><table><thead><tr><th>ID</th><th>Auction</th><th>Автор</th><th>Оценка</th><th>Статус</th><th>Комментарий</th><th>Создано</th></tr></thead>"
        f"<tbody>{trade_feedback_rows}</tbody></table></div>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, f'/trade-feedback?status=all&target_tg={user.tg_user_id}'))}'>Отзывы о пользователе (все)</a>"
        f"<a href='{escape(_path_with_auth(request, f'/trade-feedback?status=hidden&target_tg={user.tg_user_id}'))}'>Отзывы о пользователе (скрытые)</a>"
        f"<a href='{escape(_path_with_auth(request, f'/trade-feedback?status=all&author_tg={user.tg_user_id}'))}'>Отзывы, оставленные пользователем</a></p>"
        "</div>"
        "<div class='section-card'>"
        "<h2>Последние жалобы на пользователя</h2>"
        "<div class='table-wrap'><table><thead><tr><th>ID</th><th>Auction</th><th>Status</th><th>Reason</th><th>Created</th></tr></thead>"
        f"<tbody>{complaints_rows}</tbody></table></div>"
        "<h2>Последние фрод-сигналы по пользователю</h2>"
        "<div class='table-wrap'><table><thead><tr><th>ID</th><th>Auction</th><th>Score</th><th>Status</th><th>Created</th></tr></thead>"
        f"<tbody>{signal_rows}</tbody></table></div>"
        "</div>"
    )
    return HTMLResponse(_render_page("Manage User", body))


@router.get("/manage/users", response_class=HTMLResponse)
async def manage_users(
    request: Request,
    page: int = 0,
    q: str = "",
    density: str | None = None,
) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response

    page = max(page, 0)
    page_size = 30
    offset = page * page_size
    query_value = q.strip()

    now = datetime.now(UTC)
    admin_ids = set(settings.parsed_admin_user_ids())

    def _manage_users_path(
        *, page_value: int, q_value: str | None = None, density_value: str | None = None
    ) -> str:
        query = {
            "page": str(page_value),
            "q": q_value if q_value is not None else query_value,
            "density": density_value or dense_config.density,
        }
        return f"/manage/users?{urlencode(query)}"

    stmt = select(User).order_by(User.created_at.desc())
    if query_value:
        if query_value.isdigit():
            stmt = stmt.where(
                or_(
                    User.tg_user_id == int(query_value),
                    User.username.ilike(f"%{query_value}%"),
                )
            )
        else:
            stmt = stmt.where(User.username.ilike(f"%{query_value}%"))

    async with SessionFactory() as session:
        dense_config = await _load_dense_list_config(
            session,
            request=request,
            auth=auth,
            queue_key="manage_users",
            requested_density=density,
            table_id="manage-users-table",
            quick_filter_placeholder="id / tg / username",
        )
        users = (
            (
                await session.execute(
                    stmt.offset(offset).limit(page_size + 1),
                )
            )
            .scalars()
            .all()
        )

        has_next = len(users) > page_size
        users = users[:page_size]

        user_ids = [user.id for user in users]
        role_user_ids: set[int] = set()
        banned_user_ids: set[int] = set()
        risk_by_user_id = await _load_user_risk_snapshot_map(session, user_ids=user_ids, now=now)
        verified_user_ids = await load_verified_user_ids(session, user_ids=user_ids)

        if user_ids:
            role_user_ids = set(
                (
                    await session.execute(
                        select(UserRoleAssignment.user_id).where(
                            UserRoleAssignment.user_id.in_(user_ids),
                            UserRoleAssignment.role.in_(
                                (UserRole.OWNER, UserRole.ADMIN, UserRole.MODERATOR)
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            banned_user_ids = set(
                (
                    await session.execute(
                        select(BlacklistEntry.user_id).where(
                            BlacklistEntry.user_id.in_(user_ids),
                            BlacklistEntry.is_active.is_(True),
                            (
                                BlacklistEntry.expires_at.is_(None)
                                | (BlacklistEntry.expires_at > now)
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )

    rows = []
    default_risk_snapshot = evaluate_user_risk_snapshot(
        complaints_against=0,
        open_fraud_signals=0,
        has_active_blacklist=False,
        removed_bids=0,
    )
    for user in users:
        is_allowlist_mod = user.tg_user_id in admin_ids
        is_dynamic_mod = user.id in role_user_ids
        moderator_text = (
            "yes (allowlist)" if is_allowlist_mod else ("yes" if is_dynamic_mod else "no")
        )
        banned_text = "yes" if user.id in banned_user_ids else "no"
        verified_text = "yes" if user.id in verified_user_ids else "no"
        risk_snapshot = risk_by_user_id.get(user.id, default_risk_snapshot)

        rows.append(
            f"<tr data-row='{escape(f'{user.id} {user.tg_user_id} {user.username or ""} {moderator_text} {banned_text} {verified_text}')}'>"
            f"<td data-col='id'>{user.id}</td>"
            f"<td data-col='tg_user_id'>{user.tg_user_id}</td>"
            f"<td data-col='username'>{escape(user.username or '-')}</td>"
            f"<td data-col='moderator'>{moderator_text}</td>"
            f"<td data-col='banned'>{banned_text}</td>"
            f"<td data-col='verified'>{verified_text}</td>"
            f"<td data-col='risk'>{_risk_snapshot_inline_html(risk_snapshot)}</td>"
            f"<td data-col='created'>{escape(_fmt_ts(user.created_at))}</td>"
            f"<td data-col='manage'><a href='{escape(_path_with_auth(request, f'/manage/user/{user.id}'))}'>open</a></td>"
            "</tr>"
        )

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _manage_users_path(page_value=page - 1)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _manage_users_path(page_value=page + 1)))}'>Вперед →</a>"
        if has_next
        else ""
    )
    dense_toolbar = render_dense_list_toolbar(
        dense_config,
        density_query_builder=lambda value: _path_with_auth(
            request,
            _manage_users_path(page_value=0, density_value=value),
        ),
    )

    moderator_grant_form = ""
    if auth.can(SCOPE_ROLE_MANAGE):
        csrf_input = _csrf_hidden_input(request, auth)
        moderator_grant_form = (
            "<div class='card'><h3>Назначить MODERATOR по TG ID</h3>"
            f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/moderator/grant'))}'>"
            "<input name='target_tg_user_id' placeholder='tg_user_id' style='width:220px' required>"
            f"<input type='hidden' name='return_to' value='{escape('/manage/users')}'>"
            f"{csrf_input}"
            "<input name='reason' placeholder='Причина выдачи роли' style='width:320px' required>"
            "<button type='submit'>Grant MODERATOR</button></form></div><br>"
        )

    body = (
        f"{_render_app_header('Пользователи', auth, 'Поиск, роли и риск-профили')}"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"{dense_toolbar}"
        "<div class='toolbar'>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/manage/users'))}'>"
        f"<input type='hidden' name='density' value='{escape(dense_config.density)}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='tg id или username' style='width:240px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        "</div>"
        f"{_kpi_grid([_kpi_card('Пользователей на странице', str(len(users))), _kpi_card('Страница', str(page + 1)), _kpi_card('Поисковый запрос', escape(query_value) if query_value else '-')])}"
        f"{moderator_grant_form}"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th data-col='id'>ID</th><th data-col='tg_user_id'>TG User ID</th><th data-col='username'>Username</th><th data-col='moderator'>Moderator</th><th data-col='banned'>Banned</th><th data-col='verified'>Verified</th><th data-col='risk'>Risk</th><th data-col='created'>Created</th><th data-col='manage'>Manage</th></tr></thead>"
        f"<tbody>{''.join(rows) if rows else '<tr><td colspan=9><span class="empty-state">Нет записей</span></td></tr>'}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(_render_page("Manage Users", body))


@router.post("/actions/user/ban")
async def action_ban_user(
    request: Request,
    target_tg_user_id: int = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    confirmed: str | None = Form(None),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/manage/users")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    if not _is_confirmed(confirmed):
        return _render_confirmation_page(
            request,
            auth,
            title="Подтверждение бана",
            message=f"Вы действительно хотите забанить TG user {target_tg_user_id}?",
            action_path="/actions/user/ban",
            fields={
                "target_tg_user_id": str(target_tg_user_id),
                "reason": reason,
                "return_to": target,
            },
            back_to=target,
        )

    actor_user_id = await _resolve_actor_user_id(auth)
    async with SessionFactory() as session:
        async with session.begin():
            result = await ban_user(
                session,
                actor_user_id=actor_user_id,
                target_tg_user_id=target_tg_user_id,
                reason=f"[web] {reason}",
            )
    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@router.post("/actions/user/verify")
async def action_verify_user(
    request: Request,
    target_tg_user_id: int = Form(...),
    custom_description: str | None = Form(None),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    confirmed: str | None = Form(None),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_TRUST_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/manage/users")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    description = (custom_description or "").strip()
    if not _is_confirmed(confirmed):
        return _render_confirmation_page(
            request,
            auth,
            title="Подтверждение верификации пользователя",
            message=f"Подтвердить Telegram верификацию для TG user {target_tg_user_id}?",
            action_path="/actions/user/verify",
            fields={
                "target_tg_user_id": str(target_tg_user_id),
                "custom_description": description,
                "return_to": target,
            },
            back_to=target,
        )

    token = settings.bot_token.strip()
    if not token:
        return _action_error_page(request, "BOT_TOKEN is empty", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        async with SessionFactory() as session:
            async with session.begin():
                result = await set_user_verification(
                    session,
                    bot,
                    actor_user_id=actor_user_id,
                    target_tg_user_id=target_tg_user_id,
                    verify=True,
                    custom_description=description,
                )
    finally:
        await bot.session.close()

    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@router.post("/actions/user/unverify")
async def action_unverify_user(
    request: Request,
    target_tg_user_id: int = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    confirmed: str | None = Form(None),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_TRUST_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/manage/users")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    if not _is_confirmed(confirmed):
        return _render_confirmation_page(
            request,
            auth,
            title="Подтверждение снятия верификации",
            message=f"Снять Telegram верификацию для TG user {target_tg_user_id}?",
            action_path="/actions/user/unverify",
            fields={
                "target_tg_user_id": str(target_tg_user_id),
                "return_to": target,
            },
            back_to=target,
        )

    token = settings.bot_token.strip()
    if not token:
        return _action_error_page(request, "BOT_TOKEN is empty", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        async with SessionFactory() as session:
            async with session.begin():
                result = await set_user_verification(
                    session,
                    bot,
                    actor_user_id=actor_user_id,
                    target_tg_user_id=target_tg_user_id,
                    verify=False,
                )
    finally:
        await bot.session.close()

    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@router.post("/actions/user/unban")
async def action_unban_user(
    request: Request,
    target_tg_user_id: int = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    confirmed: str | None = Form(None),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/manage/users")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    if not _is_confirmed(confirmed):
        return _render_confirmation_page(
            request,
            auth,
            title="Подтверждение разбана",
            message=f"Вы действительно хотите разбанить TG user {target_tg_user_id}?",
            action_path="/actions/user/unban",
            fields={
                "target_tg_user_id": str(target_tg_user_id),
                "reason": reason,
                "return_to": target,
            },
            back_to=target,
        )

    actor_user_id = await _resolve_actor_user_id(auth)
    async with SessionFactory() as session:
        async with session.begin():
            result = await unban_user(
                session,
                actor_user_id=actor_user_id,
                target_tg_user_id=target_tg_user_id,
                reason=f"[web] {reason}",
            )
    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@router.post("/actions/user/points/adjust")
async def action_adjust_user_points(
    request: Request,
    target_tg_user_id: int = Form(...),
    amount: str = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    action_id: str = Form(...),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_ROLE_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/manage/users")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    amount_value = _parse_signed_int(amount)
    if amount_value is None:
        return _action_error_page(request, "Amount must be an integer", back_to=target)
    if amount_value == 0:
        return _action_error_page(request, "Amount cannot be 0", back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    action_nonce = action_id.strip()
    if not action_nonce:
        return _action_error_page(request, "Action id is required", back_to=target)
    if len(action_nonce) > 64:
        return _action_error_page(request, "Action id is too long", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)
    changed = False
    dedupe_key = ""
    async with SessionFactory() as session:
        async with session.begin():
            target_user = await session.scalar(
                select(User).where(User.tg_user_id == target_tg_user_id).with_for_update()
            )
            if target_user is None:
                return _action_error_page(request, "User not found", back_to=target)

            dedupe_key = f"web:modpoints:{actor_user_id}:{target_user.id}:{action_nonce}"
            grant_result = await grant_points(
                session,
                user_id=target_user.id,
                amount=amount_value,
                event_type=PointsEventType.MANUAL_ADJUSTMENT,
                dedupe_key=dedupe_key,
                reason=reason,
                payload={
                    "source": "web",
                    "actor_user_id": actor_user_id,
                    "actor_tg_user_id": auth.tg_user_id,
                    "target_tg_user_id": target_tg_user_id,
                    "action_id": action_nonce,
                },
            )
            changed = grant_result.changed

            if changed:
                await log_moderation_action(
                    session,
                    actor_user_id=actor_user_id,
                    action=ModerationAction.ADJUST_USER_POINTS,
                    reason=f"[web] {reason}",
                    target_user_id=target_user.id,
                    payload={
                        "amount": amount_value,
                        "target_tg_user_id": target_tg_user_id,
                        "dedupe_key": dedupe_key,
                        "action_id": action_nonce,
                    },
                )

    logger.info(
        "[web] points adjustment %s for tg_user_id=%s amount=%s dedupe_key=%s",
        "applied" if changed else "dedupe-skip",
        target_tg_user_id,
        amount_value,
        dedupe_key,
    )
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)
