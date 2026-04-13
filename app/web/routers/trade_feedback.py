from __future__ import annotations

import uuid
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from app.db.enums import ModerationAction
from app.db.models import Auction, TradeFeedback, User
from app.db.session import SessionFactory
from app.services.admin_queue_preset_telemetry_service import (
    load_workflow_preset_telemetry_segments,
)
from app.services.moderation_service import log_moderation_action
from app.services.rbac_service import SCOPE_USER_BAN
from app.services.trade_feedback_service import set_trade_feedback_visibility
from app.web.components import (
    _action_error_page,
    _fmt_ts,
    _load_dense_list_config,
    _pager_html,
    _render_app_header,
    _render_page,
    _render_workflow_preset_telemetry_panel,
    _safe_return_to,
    _triage_controls_cell,
    _triage_detail_row,
    _triage_row_context_attrs,
    _triage_shortcut_hint,
)
from app.web.deps import (
    _csrf_failed_response,
    _csrf_hidden_input,
    _path_with_auth,
    _require_scope_permission,
    _validate_csrf_token,
)
from app.web.dense_list import render_dense_list_script, render_dense_list_toolbar
from app.web.filters import (
    _parse_optional_tg_user_id,
    _parse_trade_feedback_min_rating,
    _parse_trade_feedback_moderated_filter,
    _parse_trade_feedback_status,
)

router = APIRouter()


async def _resolve_actor_user_id(auth) -> int:
    from app.config import settings

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


@router.get("/trade-feedback", response_class=HTMLResponse)
async def trade_feedback(
    request: Request,
    status: str = "visible",
    moderated: str = "all",
    page: int = 0,
    q: str = "",
    min_rating: str = "",
    author_tg: str = "",
    target_tg: str = "",
    moderator_tg: str = "",
    density: str | None = None,
    telemetry_preset_id: int | None = None,
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    page = max(page, 0)
    page_size = 30
    offset = page * page_size
    status_value = _parse_trade_feedback_status(status)
    moderated_value = _parse_trade_feedback_moderated_filter(moderated)
    query_value = q.strip()
    min_rating_value = _parse_trade_feedback_min_rating(min_rating)
    author_tg_value = _parse_optional_tg_user_id(author_tg)
    target_tg_value = _parse_optional_tg_user_id(target_tg)
    moderator_tg_value = _parse_optional_tg_user_id(moderator_tg)

    author_user = aliased(User)
    target_user = aliased(User)
    moderator_user = aliased(User)

    stmt = (
        select(TradeFeedback, Auction, author_user, target_user, moderator_user)
        .join(Auction, Auction.id == TradeFeedback.auction_id)
        .join(author_user, author_user.id == TradeFeedback.author_user_id)
        .join(target_user, target_user.id == TradeFeedback.target_user_id)
        .outerjoin(moderator_user, moderator_user.id == TradeFeedback.moderator_user_id)
    )

    if status_value != "all":
        stmt = stmt.where(TradeFeedback.status == status_value.upper())

    if moderated_value == "only":
        stmt = stmt.where(TradeFeedback.moderated_at.is_not(None))
    elif moderated_value == "none":
        stmt = stmt.where(TradeFeedback.moderated_at.is_(None))

    if min_rating_value is not None:
        stmt = stmt.where(TradeFeedback.rating >= min_rating_value)

    if author_tg_value is not None:
        stmt = stmt.where(author_user.tg_user_id == author_tg_value)

    if target_tg_value is not None:
        stmt = stmt.where(target_user.tg_user_id == target_tg_value)

    if moderator_tg_value is not None:
        stmt = stmt.where(moderator_user.tg_user_id == moderator_tg_value)

    if query_value:
        auction_uuid: uuid.UUID | None = None
        try:
            auction_uuid = uuid.UUID(query_value)
        except ValueError:
            auction_uuid = None

        if query_value.isdigit():
            q_int = int(query_value)
            stmt = stmt.where(
                or_(
                    TradeFeedback.id == q_int,
                    author_user.tg_user_id == q_int,
                    target_user.tg_user_id == q_int,
                )
            )
        elif auction_uuid is not None:
            stmt = stmt.where(TradeFeedback.auction_id == auction_uuid)
        else:
            stmt = stmt.where(
                or_(
                    author_user.username.ilike(f"%{query_value}%"),
                    target_user.username.ilike(f"%{query_value}%"),
                    TradeFeedback.comment.ilike(f"%{query_value}%"),
                    TradeFeedback.moderation_note.ilike(f"%{query_value}%"),
                )
            )

    stmt = (
        stmt.order_by(TradeFeedback.created_at.desc(), TradeFeedback.id.desc())
        .offset(offset)
        .limit(page_size + 1)
    )

    async with SessionFactory() as session:
        rows = (await session.execute(stmt)).all()
        dense_config = await _load_dense_list_config(
            session,
            request=request,
            auth=auth,
            queue_key="trade_feedback",
            requested_density=density,
            table_id="trade-feedback-table",
            quick_filter_placeholder="id / auction / users / comment",
        )
        telemetry_segments = await load_workflow_preset_telemetry_segments(
            session,
            queue_context="feedback",
            lookback_hours=24 * 7,
        )

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    base_query = {
        "status": status_value,
        "moderated": moderated_value,
        "q": query_value,
        "density": dense_config.density,
    }
    if telemetry_preset_id is not None and telemetry_preset_id > 0:
        base_query["telemetry_preset_id"] = str(telemetry_preset_id)
    if min_rating_value is not None:
        base_query["min_rating"] = str(min_rating_value)
    if author_tg_value is not None:
        base_query["author_tg"] = str(author_tg_value)
    if target_tg_value is not None:
        base_query["target_tg"] = str(target_tg_value)
    if moderator_tg_value is not None:
        base_query["moderator_tg"] = str(moderator_tg_value)

    def _trade_feedback_path(*, page_value: int | None = None, **extra: str) -> str:
        query = dict(base_query)
        query.update(extra)
        if page_value is not None:
            query["page"] = str(page_value)
        encoded = urlencode(query)
        return "/trade-feedback" if not encoded else f"/trade-feedback?{encoded}"

    csrf_input = _csrf_hidden_input(request, auth)
    return_to = _trade_feedback_path(page_value=page)
    table_rows = ""

    for item, auction, author, target, moderator in rows:
        author_label = f"@{author.username}" if author.username else str(author.tg_user_id)
        target_label = f"@{target.username}" if target.username else str(target.tg_user_id)
        moderator_label = "-"
        moderator_cell = "-"
        if moderator is not None:
            moderator_label = (
                f"@{moderator.username}" if moderator.username else str(moderator.tg_user_id)
            )
            moderator_cell = (
                f"<a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, moderator_tg=str(moderator.tg_user_id))))}'>"
                f"{escape(moderator_label)}</a>"
            )

        moderation_note = escape((item.moderation_note or "-")[:160])

        feedback_risk = "low"
        if int(item.rating) <= 1:
            feedback_risk = "critical"
        elif int(item.rating) <= 2:
            feedback_risk = "high"
        elif int(item.rating) == 3:
            feedback_risk = "medium"

        feedback_priority = "normal"
        if item.moderated_at is None and int(item.rating) <= 2:
            feedback_priority = "urgent"
        elif item.moderated_at is None:
            feedback_priority = "high"
        row_context_attrs = _triage_row_context_attrs(
            risk_level=feedback_risk,
            priority_level=feedback_priority,
        )

        status_label = "Виден" if item.status == "VISIBLE" else "Скрыт"
        action_form = "-"
        if item.status == "VISIBLE":
            action_form = (
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/trade-feedback/hide'))}'>"
                f"<input type='hidden' name='feedback_id' value='{item.id}'>"
                f"<input type='hidden' name='return_to' value='{escape(return_to)}'>"
                f"{csrf_input}"
                "<input name='reason' placeholder='Причина скрытия' style='width:180px'>"
                "<button type='submit'>Скрыть</button></form>"
            )
        else:
            action_form = (
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/trade-feedback/unhide'))}'>"
                f"<input type='hidden' name='feedback_id' value='{item.id}'>"
                f"<input type='hidden' name='return_to' value='{escape(return_to)}'>"
                f"{csrf_input}"
                "<input name='reason' placeholder='Причина возврата' style='width:180px'>"
                "<button type='submit'>Показать</button></form>"
            )

        table_rows += (
            f"<tr data-row='{escape(f'{item.id} {auction.id} {author_label} {target_label} {item.status} {item.rating} {item.comment or ""} {item.moderation_note or ""}')}' "
            f"data-triage-row='1' data-row-id='{item.id}' tabindex='0'{row_context_attrs}>"
            f"<td>{_triage_controls_cell(item.id)}</td>"
            f"<td data-col='id'>{item.id}</td>"
            f"<td data-col='auction'><a href='{escape(_path_with_auth(request, f'/timeline/auction/{auction.id}'))}'>{escape(str(auction.id))}</a></td>"
            f"<td data-col='author'><a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, author_tg=str(author.tg_user_id), target_tg='')))}'>{escape(author_label)}</a></td>"
            f"<td data-col='target'><a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, target_tg=str(target.tg_user_id), author_tg='')))}'>{escape(target_label)}</a></td>"
            f"<td data-col='rating'>{item.rating}/5</td>"
            f"<td data-col='comment'>{escape((item.comment or '-')[:180])}</td>"
            f"<td data-col='status' data-status-cell='1'>{status_label}</td>"
            f"<td data-col='moderator'>{moderator_cell}</td>"
            f"<td data-col='note'>{moderation_note}</td>"
            f"<td data-col='created'>{escape(_fmt_ts(item.created_at))}</td>"
            f"<td data-col='moderated'>{escape(_fmt_ts(item.moderated_at))}</td>"
            f"<td data-col='actions'>{action_form}</td>"
            "</tr>"
        )
        table_rows += _triage_detail_row(
            item.id,
            col_count=13,
            title=f"Trade feedback #{item.id}",
            subtitle=f"Auction {auction.id} / {author_label} -> {target_label}",
        )

    if not table_rows:
        table_rows = "<tr><td colspan='13'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=page - 1)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=page + 1)))}'>Вперед →</a>"
        if has_next
        else ""
    )

    min_rating_text = "all" if min_rating_value is None else str(min_rating_value)
    author_filter_text = "all" if author_tg_value is None else str(author_tg_value)
    target_filter_text = "all" if target_tg_value is None else str(target_tg_value)
    moderator_filter_text = "all" if moderator_tg_value is None else str(moderator_tg_value)
    dense_toolbar = render_dense_list_toolbar(
        dense_config,
        density_query_builder=lambda value: _path_with_auth(
            request,
            _trade_feedback_path(page_value=0, density=value),
        ),
    )
    telemetry_panel = _render_workflow_preset_telemetry_panel(
        request,
        queue_context="feedback",
        segments=telemetry_segments,
        selected_preset_id=telemetry_preset_id,
        preset_filter_path_builder=lambda preset_id: _trade_feedback_path(
            page_value=page,
            telemetry_preset_id="" if preset_id is None else str(preset_id),
        ),
    )

    body = (
        f"{_render_app_header('Отзывы по сделкам', auth, 'Модерация репутации и спорных отзывов')}"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a>"
        f"<a href='{escape(_path_with_auth(request, '/manage/users'))}'>К пользователям</a></p>"
        f"{dense_toolbar}"
        f"{telemetry_panel}"
        f"{_triage_shortcut_hint()}"
        "<div class='toolbar'>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/trade-feedback'))}'>"
        f"<input type='hidden' name='status' value='{escape(status_value)}'>"
        f"<input type='hidden' name='moderated' value='{escape(moderated_value)}'>"
        f"<input type='hidden' name='density' value='{escape(dense_config.density)}'>"
        f"<input type='hidden' name='min_rating' value='{escape(min_rating_text if min_rating_text != 'all' else '')}'>"
        f"<input type='hidden' name='author_tg' value='{escape(author_filter_text if author_filter_text != 'all' else '')}'>"
        f"<input type='hidden' name='target_tg' value='{escape(target_filter_text if target_filter_text != 'all' else '')}'>"
        f"<input type='hidden' name='moderator_tg' value='{escape(moderator_filter_text if moderator_filter_text != 'all' else '')}'>"
        f"<input type='hidden' name='telemetry_preset_id' value='{escape(str(telemetry_preset_id) if telemetry_preset_id else '')}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='id / auction_id / username / tg id' style='width:320px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        "</div>"
        "<div class='toolbar'>"
        "<span>Статус:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, status='visible')))}'>Видимые</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, status='hidden')))}'>Скрытые</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, status='all')))}'>Все</a>"
        "<span>Оценка:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, min_rating='')))}'>all</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, min_rating='4')))}'>4+</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, min_rating='5')))}'>5</a>"
        "<span>Модерация:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, moderated='all')))}'>all</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, moderated='only')))}'>только модерированные</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, moderated='none')))}'>без модерации</a>"
        "</div>"
        f"<p>Автор TG: {escape(author_filter_text)} | Получатель TG: {escape(target_filter_text)} | Модератор TG: {escape(moderator_filter_text)}</p>"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th>Pick</th><th data-col='id'>ID</th><th data-col='auction'>Auction</th><th data-col='author'>Автор</th><th data-col='target'>Кому</th><th data-col='rating'>Оценка</th><th data-col='comment'>Комментарий</th><th data-col='status'>Статус</th><th data-col='moderator'>Модератор</th><th data-col='note'>Примечание</th><th data-col='created'>Создано</th><th data-col='moderated'>Модерация</th><th data-col='actions'>Действия</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(_render_page("Trade Feedback", body))


@router.post("/actions/trade-feedback/hide")
async def action_hide_trade_feedback(
    request: Request,
    feedback_id: int = Form(...),
    reason: str = Form(""),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/trade-feedback?status=visible")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    normalized_reason = reason.strip()
    if not normalized_reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)

    async with SessionFactory() as session:
        async with session.begin():
            result = await set_trade_feedback_visibility(
                session,
                feedback_id=feedback_id,
                visible=False,
                moderator_user_id=actor_user_id,
                note=normalized_reason,
            )

            if result.ok and result.changed and result.item is not None:
                await log_moderation_action(
                    session,
                    actor_user_id=actor_user_id,
                    action=ModerationAction.HIDE_TRADE_FEEDBACK,
                    reason=f"[web] {normalized_reason or 'trade feedback hidden'}",
                    target_user_id=result.item.target_user_id,
                    auction_id=result.item.auction_id,
                    payload={
                        "feedback_id": feedback_id,
                        "source": "web",
                        "from_status": result.previous_status,
                        "to_status": result.current_status,
                        "moderation_note": normalized_reason or None,
                    },
                )

    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@router.post("/actions/trade-feedback/unhide")
async def action_unhide_trade_feedback(
    request: Request,
    feedback_id: int = Form(...),
    reason: str = Form(""),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/trade-feedback?status=visible")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    normalized_reason = reason.strip()
    if not normalized_reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)

    async with SessionFactory() as session:
        async with session.begin():
            result = await set_trade_feedback_visibility(
                session,
                feedback_id=feedback_id,
                visible=True,
                moderator_user_id=actor_user_id,
                note=normalized_reason,
            )

            if result.ok and result.changed and result.item is not None:
                await log_moderation_action(
                    session,
                    actor_user_id=actor_user_id,
                    action=ModerationAction.UNHIDE_TRADE_FEEDBACK,
                    reason=f"[web] {normalized_reason or 'trade feedback unhidden'}",
                    target_user_id=result.item.target_user_id,
                    auction_id=result.item.auction_id,
                    payload={
                        "feedback_id": feedback_id,
                        "source": "web",
                        "from_status": result.previous_status,
                        "to_status": result.current_status,
                        "moderation_note": normalized_reason or None,
                    },
                )

    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)
