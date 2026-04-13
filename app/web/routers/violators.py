from __future__ import annotations

from datetime import timedelta
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from app.db.models import BlacklistEntry, User
from app.db.session import SessionFactory
from app.services.rbac_service import SCOPE_USER_BAN
from app.web.components import (
    _fmt_ts,
    _load_dense_list_config,
    _pager_html,
    _path_with_auth,
    _render_app_header,
    _render_page,
)
from app.web.dense_list import render_dense_list_script, render_dense_list_toolbar
from app.web.deps import _csrf_hidden_input, _require_scope_permission
from app.web.filters import _parse_ymd_filter, _violator_status_label

router = APIRouter()


@router.get("/violators", response_class=HTMLResponse)
async def violators(
    request: Request,
    status: str = "active",
    page: int = 0,
    q: str = "",
    by: str = "",
    created_from: str = "",
    created_to: str = "",
    density: str | None = None,
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    page = max(page, 0)
    page_size = 30
    offset = page * page_size
    query_value = q.strip()
    moderator_value = by.strip()
    created_from_value = created_from.strip()
    created_to_value = created_to.strip()
    status_value = status.strip().lower()
    if status_value not in {"active", "inactive", "all"}:
        raise HTTPException(status_code=400, detail="Invalid violators status filter")

    created_from_dt = _parse_ymd_filter(created_from_value, field_name="created_from")
    created_to_dt = _parse_ymd_filter(created_to_value, field_name="created_to")
    created_to_exclusive = created_to_dt + timedelta(days=1) if created_to_dt is not None else None
    if (
        created_from_dt is not None
        and created_to_exclusive is not None
        and created_from_dt >= created_to_exclusive
    ):
        raise HTTPException(status_code=400, detail="Invalid violators date range")

    actor_user = aliased(User)
    stmt = (
        select(BlacklistEntry, User, actor_user)
        .join(User, User.id == BlacklistEntry.user_id)
        .outerjoin(actor_user, actor_user.id == BlacklistEntry.created_by_user_id)
    )

    if status_value == "active":
        stmt = stmt.where(BlacklistEntry.is_active.is_(True))
    elif status_value == "inactive":
        stmt = stmt.where(BlacklistEntry.is_active.is_(False))

    if created_from_dt is not None:
        stmt = stmt.where(BlacklistEntry.created_at >= created_from_dt)
    if created_to_exclusive is not None:
        stmt = stmt.where(BlacklistEntry.created_at < created_to_exclusive)

    if query_value:
        if query_value.isdigit():
            stmt = stmt.where(
                or_(
                    User.username.ilike(f"%{query_value}%"),
                    BlacklistEntry.reason.ilike(f"%{query_value}%"),
                    User.tg_user_id == int(query_value),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    User.username.ilike(f"%{query_value}%"),
                    BlacklistEntry.reason.ilike(f"%{query_value}%"),
                )
            )

    if moderator_value:
        if moderator_value.isdigit():
            stmt = stmt.where(
                or_(
                    actor_user.tg_user_id == int(moderator_value),
                    actor_user.username.ilike(f"%{moderator_value}%"),
                )
            )
        else:
            stmt = stmt.where(actor_user.username.ilike(f"%{moderator_value}%"))

    stmt = stmt.order_by(BlacklistEntry.created_at.desc()).offset(offset).limit(page_size + 1)

    async with SessionFactory() as session:
        dense_config = await _load_dense_list_config(
            session,
            request=request,
            auth=auth,
            queue_key="violators",
            requested_density=density,
            table_id="violators-table",
            quick_filter_placeholder="tg / username / reason / moderator",
        )
        rows = (await session.execute(stmt)).all()

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    base_query = {
        "q": query_value,
        "by": moderator_value,
        "created_from": created_from_value,
        "created_to": created_to_value,
        "density": dense_config.density,
    }

    def _violators_path(
        *,
        target_page: int,
        status_filter: str | None = None,
        density_value: str | None = None,
    ) -> str:
        query = {
            **base_query,
            "status": status_filter or status_value,
            "page": str(target_page),
            "density": density_value or dense_config.density,
        }
        return f"/violators?{urlencode(query)}"

    csrf_input = _csrf_hidden_input(request, auth)
    return_to = _violators_path(target_page=page)

    table_rows = ""
    for entry, target_user, actor in rows:
        actor_label = "-"
        if actor is not None:
            actor_label = f"@{actor.username}" if actor.username else str(actor.tg_user_id)
        target_label = f"@{target_user.username}" if target_user.username else "-"

        actions = "-"
        if entry.is_active:
            actions = (
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/unban'))}'>"
                f"<input type='hidden' name='target_tg_user_id' value='{target_user.tg_user_id}'>"
                f"<input type='hidden' name='return_to' value='{escape(return_to)}'>"
                f"{csrf_input}"
                "<input name='reason' placeholder='Причина разбана' style='width:180px' required>"
                "<button type='submit'>Разбанить</button>"
                "</form>"
            )

        table_rows += (
            f"<tr data-row='{escape(f'{entry.id} {target_user.tg_user_id} {target_user.username or ""} {entry.reason} {actor_label}')}'>"
            f"<td data-col='id'>{entry.id}</td>"
            f"<td data-col='tg_user_id'><a href='{escape(_path_with_auth(request, f'/manage/user/{target_user.id}'))}'>{target_user.tg_user_id}</a></td>"
            f"<td data-col='username'>{escape(target_label)}</td>"
            f"<td data-col='status'>{_violator_status_label('active' if entry.is_active else 'inactive')}</td>"
            f"<td data-col='reason'>{escape(entry.reason[:160])}</td>"
            f"<td data-col='actor'>{escape(actor_label)}</td>"
            f"<td data-col='created'>{escape(_fmt_ts(entry.created_at))}</td>"
            f"<td data-col='expires'>{escape(_fmt_ts(entry.expires_at))}</td>"
            f"<td data-col='actions'>{actions}</td>"
            "</tr>"
        )

    if not table_rows:
        table_rows = "<tr><td colspan='9'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _violators_path(target_page=page - 1)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _violators_path(target_page=page + 1)))}'>Вперед →</a>"
        if has_next
        else ""
    )
    inactive_status_path = _violators_path(target_page=0, status_filter="inactive")
    all_status_path = _violators_path(target_page=0, status_filter="all")
    active_status_path = _violators_path(target_page=0, status_filter="active")
    dense_toolbar = render_dense_list_toolbar(
        dense_config,
        density_query_builder=lambda value: _path_with_auth(
            request,
            _violators_path(target_page=0, density_value=value),
        ),
    )

    body = (
        f"{_render_app_header('Нарушители', auth, 'Бан-лист, статусы и модераторские действия')}"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a>"
        f"<a href='{escape(_path_with_auth(request, '/manage/users'))}'>К пользователям</a></p>"
        f"{dense_toolbar}"
        "<div class='toolbar'>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/violators'))}'>"
        f"<input type='hidden' name='status' value='{escape(status_value)}'>"
        f"<input type='hidden' name='density' value='{escape(dense_config.density)}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='tg id / username / причина' style='width:280px'>"
        f"<input name='by' value='{escape(moderator_value)}' placeholder='модератор tg id / username' style='width:220px'>"
        f"<input name='created_from' value='{escape(created_from_value)}' placeholder='с YYYY-MM-DD' style='width:150px'>"
        f"<input name='created_to' value='{escape(created_to_value)}' placeholder='по YYYY-MM-DD' style='width:150px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        "</div>"
        "<div class='toolbar'>"
        f"<span>Фильтр:</span><a class='chip' href='{escape(_path_with_auth(request, active_status_path))}'>Активные</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, inactive_status_path))}'>Неактивные</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, all_status_path))}'>Все</a>"
        "</div>"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th data-col='id'>ID</th><th data-col='tg_user_id'>TG User ID</th><th data-col='username'>Username</th><th data-col='status'>Статус</th><th data-col='reason'>Причина</th><th data-col='actor'>Кем</th><th data-col='created'>Создано</th><th data-col='expires'>Истекает</th><th data-col='actions'>Действия</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(_render_page("Нарушители", body))
