from __future__ import annotations

from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from app.config import settings
from app.db.models import User
from app.db.session import SessionFactory
from app.services.moderation_dashboard_service import get_moderation_dashboard_snapshot
from app.services.runtime_settings_service import (
    build_runtime_settings_snapshot,
    delete_runtime_setting_override,
    upsert_runtime_setting_override,
)
from app.web.auth import AdminAuthContext
from app.web.components import (
    _action_error_page,
    _dashboard_preset_script,
    _dashboard_preset_toolbar,
    _details_block,
    _kpi_card,
    _kpi_grid,
    _normalize_dashboard_preset,
    _panel,
    _pct,
    _render_app_header,
    _safe_return_to,
    render_template,
)
from app.web.deps import (
    _auth_context_or_unauthorized,
    _csrf_failed_response,
    _csrf_hidden_input,
    _path_with_auth,
    _require_owner_permission,
    _validate_csrf_token,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response

    async with SessionFactory() as session:
        snapshot = await get_moderation_dashboard_snapshot(session)

    dashboard_preset = _normalize_dashboard_preset(request.query_params.get("preset"))
    show_onboarding = dashboard_preset == "routine"
    show_activity = dashboard_preset == "routine"
    show_rewards_weekly = dashboard_preset in {"routine", "rewards"}
    show_rewards_24h = dashboard_preset == "rewards"
    show_rewards_policy = dashboard_preset == "rewards"

    engaged_with_private = max(
        snapshot.users_with_engagement - snapshot.users_engaged_without_private_start,
        0,
    )
    global_daily_limit_line = (
        "<div class='kpi'><b>Global redemption daily limit:</b> unlimited</div>"
    )
    if settings.points_redemption_daily_limit > 0:
        global_daily_limit_line = f"<div class='kpi'><b>Global redemption daily limit:</b> {settings.points_redemption_daily_limit}/day</div>"
    global_weekly_limit_line = (
        "<div class='kpi'><b>Global redemption weekly limit:</b> unlimited</div>"
    )
    if settings.points_redemption_weekly_limit > 0:
        global_weekly_limit_line = f"<div class='kpi'><b>Global redemption weekly limit:</b> {settings.points_redemption_weekly_limit}/week</div>"
    global_daily_spend_cap_line = (
        "<div class='kpi'><b>Global redemption daily spend cap:</b> unlimited</div>"
    )
    if settings.points_redemption_daily_spend_cap > 0:
        global_daily_spend_cap_line = (
            "<div class='kpi'><b>Global redemption daily spend cap:</b> "
            f"{settings.points_redemption_daily_spend_cap} points/day</div>"
        )
    global_weekly_spend_cap_line = (
        "<div class='kpi'><b>Global redemption weekly spend cap:</b> unlimited</div>"
    )
    if settings.points_redemption_weekly_spend_cap > 0:
        global_weekly_spend_cap_line = (
            "<div class='kpi'><b>Global redemption weekly spend cap:</b> "
            f"{settings.points_redemption_weekly_spend_cap} points/week</div>"
        )
    global_monthly_spend_cap_line = (
        "<div class='kpi'><b>Global redemption monthly spend cap:</b> unlimited</div>"
    )
    if settings.points_redemption_monthly_spend_cap > 0:
        global_monthly_spend_cap_line = (
            "<div class='kpi'><b>Global redemption monthly spend cap:</b> "
            f"{settings.points_redemption_monthly_spend_cap} points/month</div>"
        )

    owner_settings_link = ""
    if auth.role == "owner":
        owner_settings_link = f"<a class='link-tile' href='{escape(_path_with_auth(request, '/settings'))}'>Runtime settings</a>"

    overview_cards = _kpi_grid(
        [
            _kpi_card("Открытые жалобы", str(snapshot.open_complaints), tone="critical"),
            _kpi_card("Открытые сигналы", str(snapshot.open_signals), tone="warn"),
            _kpi_card("Активные аукционы", str(snapshot.active_auctions), tone="ok"),
            _kpi_card("Ставок/час", str(snapshot.bids_last_hour)),
        ]
    )
    onboarding_cards = _kpi_grid(
        [
            _kpi_card("Всего пользователей", str(snapshot.total_users)),
            _kpi_card(
                "Private /start",
                f"{snapshot.users_private_started} ({_pct(snapshot.users_private_started, snapshot.total_users)})",
            ),
            _kpi_card("С hint", str(snapshot.users_with_soft_gate_hint)),
            _kpi_card("Hint за 24ч", str(snapshot.users_soft_gate_hint_last_24h)),
            _kpi_card(
                "Конверсия после hint",
                f"{snapshot.users_converted_after_hint} ({_pct(snapshot.users_converted_after_hint, snapshot.users_with_soft_gate_hint)})",
            ),
            _kpi_card("Ожидают после hint", str(snapshot.users_pending_after_hint)),
        ]
    )
    activity_cards = _kpi_grid(
        [
            _kpi_card("Пользователи со ставками", str(snapshot.users_with_bid_activity)),
            _kpi_card("Пользователи с жалобами", str(snapshot.users_with_report_activity)),
            _kpi_card("Уникально вовлеченные", str(snapshot.users_with_engagement)),
            _kpi_card(
                "Вовлеченные без private /start", str(snapshot.users_engaged_without_private_start)
            ),
            _kpi_card(
                "Вовлеченные с private /start",
                f"{engaged_with_private} ({_pct(engaged_with_private, snapshot.users_with_engagement)})",
            ),
        ]
    )
    points_core_cards = _kpi_grid(
        [
            _kpi_card("Активные points-пользователи (7д)", str(snapshot.points_active_users_7d)),
            _kpi_card(
                "Пользователи с положительным балансом",
                str(snapshot.points_users_with_positive_balance),
            ),
            _kpi_card(
                "Редимеры points (7д)",
                f"{snapshot.points_redeemers_7d} ({_pct(snapshot.points_redeemers_7d, snapshot.points_users_with_positive_balance)})",
            ),
            _kpi_card(
                "Редимеры фидбек-буста (7д)", str(snapshot.points_feedback_boost_redeemers_7d)
            ),
            _kpi_card(
                "Редимеры буста гаранта (7д)", str(snapshot.points_guarantor_boost_redeemers_7d)
            ),
            _kpi_card(
                "Редимеры буста апелляции (7д)", str(snapshot.points_appeal_boost_redeemers_7d)
            ),
        ]
    )
    points_24h_cards = _kpi_grid(
        [
            _kpi_card("Points начислено (24ч)", f"+{snapshot.points_earned_24h}"),
            _kpi_card("Points списано (24ч)", f"-{snapshot.points_spent_24h}"),
            _kpi_card("Бустов фидбека (24ч)", str(snapshot.feedback_boost_redeems_24h)),
            _kpi_card("Бустов гаранта (24ч)", str(snapshot.guarantor_boost_redeems_24h)),
            _kpi_card("Бустов апелляций (24ч)", str(snapshot.appeal_boost_redeems_24h)),
        ]
    )
    points_policy_cards = _kpi_grid(
        [
            _kpi_card(
                "Policy feedback",
                (
                    f"{'on' if settings.feedback_priority_boost_enabled else 'off'} | "
                    f"cost {settings.feedback_priority_boost_cost_points} | "
                    f"limit {settings.feedback_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.feedback_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card(
                "Policy guarantor",
                (
                    f"{'on' if settings.guarantor_priority_boost_enabled else 'off'} | "
                    f"cost {settings.guarantor_priority_boost_cost_points} | "
                    f"limit {settings.guarantor_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.guarantor_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card(
                "Policy appeal",
                (
                    f"{'on' if settings.appeal_priority_boost_enabled else 'off'} | "
                    f"cost {settings.appeal_priority_boost_cost_points} | "
                    f"limit {settings.appeal_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.appeal_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card("Policy redemptions", "on" if settings.points_redemption_enabled else "off"),
            global_daily_limit_line,
            global_weekly_limit_line,
            global_daily_spend_cap_line,
            global_weekly_spend_cap_line,
            global_monthly_spend_cap_line,
            _kpi_card(
                "Min balance after redemption",
                f"{max(settings.points_redemption_min_balance, 0)} points",
            ),
            _kpi_card(
                "Min account age for redemption",
                f"{max(settings.points_redemption_min_account_age_seconds, 0)}s",
            ),
            _kpi_card(
                "Min earned points for redemption",
                f"{max(settings.points_redemption_min_earned_points, 0)} points",
            ),
            _kpi_card(
                "Global redemption cooldown",
                f"{max(settings.points_redemption_cooldown_seconds, 0)}s",
            ),
        ]
    )
    points_summary = (
        f"active(7d): {snapshot.points_active_users_7d} | "
        f"positive balance: {snapshot.points_users_with_positive_balance} | "
        f"redeemers(7d): {snapshot.points_redeemers_7d}"
    )
    points_cards = (
        f"<p class='section-note'><b>Кратко:</b> {escape(points_summary)}</p>"
        f"{_details_block('Развернуть weekly rewards метрики', points_core_cards, open_by_default=show_rewards_weekly)}"
        f"{_details_block('Развернуть rewards активность за 24ч', points_24h_cards, open_by_default=show_rewards_24h)}"
        f"{_details_block('Развернуть policy и лимиты rewards', points_policy_cards, open_by_default=show_rewards_policy)}"
    )
    onboarding_summary = (
        f"users: {snapshot.total_users} | "
        f"private /start: {snapshot.users_private_started} ({_pct(snapshot.users_private_started, snapshot.total_users)}) | "
        f"pending after hint: {snapshot.users_pending_after_hint}"
    )
    onboarding_collapsed = _details_block(
        f"Развернуть воронку онбординга ({onboarding_summary})",
        onboarding_cards,
        open_by_default=show_onboarding,
    )
    activity_summary = (
        f"engaged users: {snapshot.users_with_engagement} | "
        f"with bids: {snapshot.users_with_bid_activity} | "
        f"without private /start: {snapshot.users_engaged_without_private_start}"
    )
    activity_collapsed = _details_block(
        f"Развернуть метрики активности ({activity_summary})",
        activity_cards,
        open_by_default=show_activity,
    )
    preset_toolbar = _dashboard_preset_toolbar(request, dashboard_preset)

    quick_actions = (
        "<div class='link-grid'>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/complaints?status=OPEN'))}'>Открытые жалобы</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/signals?status=OPEN'))}'>Открытые фрод-сигналы</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/auctions?status=ACTIVE'))}'>Активные аукционы</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/auctions?status=FROZEN'))}'>Замороженные аукционы</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/appeals?status=open&source=all'))}'>Апелляции</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/trade-feedback?status=visible'))}'>Отзывы по сделкам</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/violators?status=active'))}'>Нарушители</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/manage/users'))}'>Управление пользователями</a>"
        f"{owner_settings_link}"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/logout'))}'>Выйти</a>"
        "</div>"
    )

    body = (
        f"{_render_app_header('LiteAuction Admin', auth, 'Операционный центр модерации и риск-контроль')}"
        f"{preset_toolbar}"
        f"{_panel('Пульс модерации', overview_cards, eyebrow='priority metrics', note='Критичные метрики всегда сверху')}"
        f"{_panel('Быстрые действия', quick_actions, eyebrow='navigation', note='Основные сценарии оператора')}"
        f"{_panel('Воронка онбординга / soft-gate', onboarding_collapsed, eyebrow='growth')}"
        f"{_panel('Активность пользователей', activity_collapsed, eyebrow='engagement')}"
        f"{_panel('Points utility', points_cards, eyebrow='rewards')}"
        f"{_dashboard_preset_script()}"
    )
    return HTMLResponse(render_template("dashboard.html", title="LiteAuction Admin", body=body))


@router.get("/settings", response_class=HTMLResponse)
async def runtime_settings_page(request: Request) -> Response:
    response, auth = _require_owner_permission(request)
    if response is not None:
        return response

    async with SessionFactory() as session:
        snapshot_items = await build_runtime_settings_snapshot(session)

    csrf_input = _csrf_hidden_input(request, auth)
    html = render_template(
        "settings.html",
        app_header=_render_app_header("Runtime settings", auth, "Owner-only operational overrides"),
        snapshot_items=snapshot_items,
        csrf_input=csrf_input,
        path_set=_path_with_auth(request, "/actions/settings/runtime/set"),
        path_delete=_path_with_auth(request, "/actions/settings/runtime/delete"),
        home_path=_path_with_auth(request, "/"),
    )
    return HTMLResponse(html)


async def _resolve_actor_user_id(auth: AdminAuthContext) -> int:
    tg_user_id = auth.tg_user_id
    if tg_user_id is None:
        admin_ids = settings.parsed_admin_user_ids()
        if not admin_ids:
            from fastapi import HTTPException

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


@router.post("/actions/settings/runtime/set")
async def action_set_runtime_setting(
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
    return_to: str = Form("/settings"),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_owner_permission(request)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/settings")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)
    try:
        async with SessionFactory() as session:
            async with session.begin():
                await upsert_runtime_setting_override(
                    session,
                    key=key,
                    raw_value=value,
                    updated_by_user_id=actor_user_id,
                )
    except ValueError as exc:
        return _action_error_page(request, str(exc), back_to=target)

    return RedirectResponse(_path_with_auth(request, target), status_code=303)


@router.post("/actions/settings/runtime/delete")
async def action_delete_runtime_setting(
    request: Request,
    key: str = Form(...),
    return_to: str = Form("/settings"),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_owner_permission(request)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/settings")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    try:
        async with SessionFactory() as session:
            async with session.begin():
                await delete_runtime_setting_override(session, key=key)
    except ValueError as exc:
        return _action_error_page(request, str(exc), back_to=target)

    return RedirectResponse(_path_with_auth(request, target), status_code=303)
