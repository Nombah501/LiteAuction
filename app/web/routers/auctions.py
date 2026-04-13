from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from html import escape
from urllib.parse import urlencode

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select

from app.config import settings
from app.db.enums import AuctionStatus
from app.db.models import (
    Auction,
    Bid,
    BlacklistEntry,
    Complaint,
    FraudSignal,
    User,
)
from app.db.session import SessionFactory
from app.services.auction_service import refresh_auction_posts
from app.services.moderation_service import (
    end_auction,
    freeze_auction,
    list_recent_bids,
    remove_bid,
    unfreeze_auction,
)
from app.services.rbac_service import SCOPE_AUCTION_MANAGE, SCOPE_BID_MANAGE
from app.services.risk_eval_service import (
    UserRiskSnapshot,
    evaluate_user_risk_snapshot,
)
from app.services.timeline_service import build_auction_timeline_page
from app.services.verification_service import load_verified_user_ids
from app.web.components import (
    _action_error_page,
    _fmt_ts,
    _load_dense_list_config,
    _pager_html,
    _path_with_auth,
    _render_app_header,
    _render_confirmation_page,
    _render_page,
    _risk_snapshot_inline_html,
    _role_badge,
    _safe_return_to,
)
from app.web.dense_list import render_dense_list_script, render_dense_list_toolbar
from app.web.deps import (
    _auth_context_or_unauthorized,
    _csrf_failed_response,
    _csrf_hidden_input,
    _is_confirmed,
    _is_safe_local_path,
    _require_scope_permission,
    _validate_csrf_token,
)
from app.web.components import _normalize_timeline_source_query
from app.web.filters import _parse_non_negative_int

router = APIRouter()
logger = logging.getLogger(__name__)


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


async def _refresh_auction_posts_from_web(auction_id: uuid.UUID | None) -> None:
    if auction_id is None:
        return

    token = settings.bot_token.strip()
    if not token:
        logger.warning("Skipping auction post refresh for %s: BOT_TOKEN is empty", auction_id)
        return

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        await refresh_auction_posts(bot, auction_id)
    except Exception:
        logger.exception("Failed to refresh auction post from web action for %s", auction_id)
    finally:
        await bot.session.close()


@router.get("/auctions", response_class=HTMLResponse)
async def auctions(
    request: Request,
    status: str = "ACTIVE",
    page: int = 0,
    density: str | None = None,
) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response
    page = max(page, 0)
    page_size = 30
    offset = page * page_size

    allowed = {item.value for item in AuctionStatus}
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid auction status")

    async with SessionFactory() as session:
        rows = (
            (
                await session.execute(
                    select(Auction)
                    .where(Auction.status == status)
                    .order_by(Auction.created_at.desc())
                    .offset(offset)
                    .limit(page_size + 1)
                )
            )
            .scalars()
            .all()
        )

        has_next = len(rows) > page_size
        rows = rows[:page_size]
        seller_risk_map = await _load_user_risk_snapshot_map(
            session,
            user_ids=[item.seller_user_id for item in rows],
        )
        dense_config = await _load_dense_list_config(
            session,
            request=request,
            auth=auth,
            queue_key="auctions",
            requested_density=density,
            table_id="auctions-table",
            quick_filter_placeholder="auction id / seller / status",
        )

    default_risk_snapshot = evaluate_user_risk_snapshot(
        complaints_against=0,
        open_fraud_signals=0,
        has_active_blacklist=False,
        removed_bids=0,
    )

    def _auctions_path(
        *, page_value: int, status_value: str, density_value: str | None = None
    ) -> str:
        query = {
            "status": status_value,
            "page": str(page_value),
            "density": density_value or dense_config.density,
        }
        return f"/auctions?{urlencode(query)}"

    table_rows = ""
    for item in rows:
        seller_risk = seller_risk_map.get(item.seller_user_id, default_risk_snapshot)
        table_rows += (
            f"<tr data-row='{escape(f'{item.id} {item.seller_user_id} {item.status} {item.start_price} {item.buyout_price or ""}')}'>"
            f"<td data-col='id'><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.id}'))}'>{escape(str(item.id))}</a></td>"
            f"<td data-col='seller'>{item.seller_user_id}</td>"
            f"<td data-col='risk'>{_risk_snapshot_inline_html(seller_risk)}</td>"
            f"<td data-col='start'>${item.start_price}</td>"
            f"<td data-col='buyout'>${item.buyout_price if item.buyout_price is not None else '-'}</td>"
            f"<td data-col='status'>{escape(str(item.status))}</td>"
            f"<td data-col='ends_at'>{escape(_fmt_ts(item.ends_at))}</td>"
            f"<td data-col='actions'><a href='{escape(_path_with_auth(request, f'/manage/auction/{item.id}'))}'>Управлять</a></td>"
            "</tr>"
        )
    if not table_rows:
        table_rows = "<tr><td colspan='8'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _auctions_path(page_value=page - 1, status_value=status)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _auctions_path(page_value=page + 1, status_value=status)))}'>Вперед →</a>"
        if has_next
        else ""
    )
    status_chips = " ".join(
        [
            f"<a class='chip' href='{escape(_path_with_auth(request, _auctions_path(page_value=0, status_value=item.value)))}'>{item.value}</a>"
            for item in AuctionStatus
        ]
    )
    dense_toolbar = render_dense_list_toolbar(
        dense_config,
        density_query_builder=lambda value: _path_with_auth(
            request,
            _auctions_path(page_value=0, status_value=status, density_value=value),
        ),
    )

    body = (
        f"{_render_app_header('Аукционы', auth, f'Статус: {status}')}"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"{dense_toolbar}"
        f"<div class='toolbar'><span>Фильтр:</span> {status_chips}</div>"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th data-col='id'>ID</th><th data-col='seller'>Seller UID</th><th data-col='risk'>Seller Risk</th><th data-col='start'>Start</th><th data-col='buyout'>Buyout</th><th data-col='status'>Status</th><th data-col='ends_at'>Ends At</th><th data-col='actions'>Actions</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(_render_page("Auctions", body))


@router.get("/timeline/auction/{auction_id}", response_class=HTMLResponse)
async def auction_timeline(
    request: Request,
    auction_id: str,
    page: int = 0,
    limit: int = 100,
    source: str | None = None,
) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response

    if page < 0:
        raise HTTPException(status_code=400, detail="Invalid timeline page")
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="Invalid timeline limit")

    source_values, source_filter = _normalize_timeline_source_query(source)

    try:
        auction_uuid = uuid.UUID(auction_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid auction UUID")

    async with SessionFactory() as session:
        try:
            auction, page_items, total_items = await build_auction_timeline_page(
                session,
                auction_uuid,
                page=page,
                limit=limit,
                sources=source_values,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    if auction is None:
        raise HTTPException(status_code=404, detail="Auction not found")

    offset = page * limit
    start_item = offset + 1 if page_items else 0
    end_item = offset + len(page_items)
    has_prev = page > 0
    has_next = end_item < total_items

    rows = "".join(
        "<tr>"
        f"<td>{escape(_fmt_ts(item.happened_at))}</td>"
        f"<td>{escape(item.source)}</td>"
        f"<td>{escape(item.title)}</td>"
        f"<td><pre>{escape(item.details)}</pre></td>"
        "</tr>"
        for item in page_items
    )
    if not rows:
        rows = "<tr><td colspan='4'><span class='empty-state'>События отсутствуют на этой странице</span></td></tr>"

    timeline_base = f"/timeline/auction/{auction.id}"

    def _timeline_path(target_page: int, source_value: str | None = None) -> str:
        query: dict[str, str] = {"page": str(target_page), "limit": str(limit)}
        if source_value:
            query["source"] = source_value
        return f"{timeline_base}?{urlencode(query)}"

    manage_query: dict[str, str] = {
        "timeline_page": str(page),
        "timeline_limit": str(limit),
    }
    if source_filter:
        manage_query["timeline_source"] = source_filter
    manage_path = f"/manage/auction/{auction.id}?{urlencode(manage_query)}"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _timeline_path(page - 1, source_filter or None)))}'>← Назад</a>"
        if has_prev
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _timeline_path(page + 1, source_filter or None)))}'>Вперед →</a>"
        if has_next
        else ""
    )
    source_links = " | ".join(
        [
            f"<a class='chip' href='{escape(_path_with_auth(request, _timeline_path(0, None)))}'>all</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _timeline_path(0, 'auction')))}'>auction</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _timeline_path(0, 'bid')))}'>bid</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _timeline_path(0, 'complaint')))}'>complaint</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _timeline_path(0, 'fraud')))}'>fraud</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _timeline_path(0, 'moderation')))}'>moderation</a>",
        ]
    )
    filter_label = source_filter or "all"

    body = (
        f"<h1>Таймлайн аукциона {escape(str(auction.id))}</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/auctions?status=ACTIVE'))}'>К аукционам</a> | "
        f"<a href='{escape(_path_with_auth(request, '/'))}'>На главную</a> | "
        f"<a href='{escape(_path_with_auth(request, manage_path))}'>Управление</a></p>"
        f"<p><b>Статус:</b> {escape(str(auction.status))} | <b>Seller UID:</b> {auction.seller_user_id}</p>"
        f"<p><b>Фильтр source:</b> {escape(filter_label)} | {source_links}</p>"
        f"<p><b>Показано:</b> {start_item}-{end_item} из {total_items} | <b>Лимит:</b> {limit}</p>"
        "<table><thead><tr><th>Time</th><th>Source</th><th>Event</th><th>Details</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
    )
    return HTMLResponse(_render_page("Auction Timeline", body))


@router.get("/manage/auction/{auction_id}", response_class=HTMLResponse)
async def manage_auction(request: Request, auction_id: str) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response

    try:
        auction_uuid = uuid.UUID(auction_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid auction UUID")

    timeline_page = _parse_non_negative_int(request.query_params.get("timeline_page"))
    timeline_limit = _parse_non_negative_int(request.query_params.get("timeline_limit"))
    _, timeline_source = _normalize_timeline_source_query(
        request.query_params.get("timeline_source")
    )

    async with SessionFactory() as session:
        auction = await session.scalar(select(Auction).where(Auction.id == auction_uuid))
        if auction is None:
            raise HTTPException(status_code=404, detail="Auction not found")
        recent_bids = await list_recent_bids(session, auction_uuid, limit=20)

    csrf_input = _csrf_hidden_input(request, auth)

    can_manage_auction = auth.can(SCOPE_AUCTION_MANAGE)
    can_manage_bids = auth.can(SCOPE_BID_MANAGE)

    timeline_query: dict[str, str] = {}
    if timeline_page is not None:
        timeline_query["page"] = str(timeline_page)
    if timeline_limit is not None and 1 <= timeline_limit <= 500:
        timeline_query["limit"] = str(timeline_limit)
    if timeline_source:
        timeline_query["source"] = timeline_source
    timeline_path = f"/timeline/auction/{auction.id}"
    if timeline_query:
        timeline_path = f"{timeline_path}?{urlencode(timeline_query)}"

    controls = "<p><i>Только просмотр (нет прав на управление аукционом).</i></p>"
    if can_manage_auction:
        controls = (
            f"<form method='post' action='{escape(_path_with_auth(request, '/actions/auction/freeze'))}'>"
            f"<input type='hidden' name='auction_id' value='{escape(str(auction.id))}'>"
            f"<input type='hidden' name='return_to' value='{escape(f'/manage/auction/{auction.id}')}'>"
            f"{csrf_input}"
            "<input name='reason' placeholder='Причина заморозки' style='width:360px' required>"
            "<button type='submit'>Freeze</button></form><br>"
            f"<form method='post' action='{escape(_path_with_auth(request, '/actions/auction/unfreeze'))}'>"
            f"<input type='hidden' name='auction_id' value='{escape(str(auction.id))}'>"
            f"<input type='hidden' name='return_to' value='{escape(f'/manage/auction/{auction.id}')}'>"
            f"{csrf_input}"
            "<input name='reason' placeholder='Причина разморозки' style='width:360px' required>"
            "<button type='submit'>Unfreeze</button></form><br>"
            f"<form method='post' action='{escape(_path_with_auth(request, '/actions/auction/end'))}'>"
            f"<input type='hidden' name='auction_id' value='{escape(str(auction.id))}'>"
            f"<input type='hidden' name='return_to' value='{escape(f'/manage/auction/{auction.id}')}'>"
            f"{csrf_input}"
            "<input name='reason' placeholder='Причина завершения' style='width:360px' required>"
            "<button type='submit'>End Auction</button></form>"
        )

    bid_rows = []
    for bid in recent_bids:
        remove_form = ""
        if can_manage_bids and not bid.is_removed:
            remove_form = (
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/bid/remove'))}'>"
                f"<input type='hidden' name='bid_id' value='{escape(str(bid.bid_id))}'>"
                f"<input type='hidden' name='return_to' value='{escape(f'/manage/auction/{auction.id}')}'>"
                f"{csrf_input}"
                "<input name='reason' placeholder='Причина снятия' style='width:220px' required>"
                "<button type='submit'>Remove</button>"
                "</form>"
            )
        bid_rows.append(
            "<tr>"
            f"<td>{escape(str(bid.bid_id))}</td>"
            f"<td>${bid.amount}</td>"
            f"<td>{bid.tg_user_id}</td>"
            f"<td>{escape(bid.username or '-')}</td>"
            f"<td>{escape(_fmt_ts(bid.created_at))}</td>"
            f"<td>{'yes' if bid.is_removed else 'no'}</td>"
            f"<td>{remove_form}</td>"
            "</tr>"
        )

    bids_table = (
        "<table><thead><tr><th>Bid ID</th><th>Amount</th><th>TG UID</th><th>Username</th><th>Created</th><th>Removed</th><th>Action</th></tr></thead>"
        f"<tbody>{''.join(bid_rows) if bid_rows else '<tr><td colspan=7><span class="empty-state">Нет ставок</span></td></tr>'}</tbody></table>"
    )

    body = (
        f"<h1>Управление аукционом {escape(str(auction.id))}</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a> | "
        f"<a href='{escape(_path_with_auth(request, timeline_path))}'>Таймлайн</a></p>"
        f"<p><b>Status:</b> {escape(str(auction.status))} | <b>Seller UID:</b> {auction.seller_user_id}</p>"
        f"<div class='card'>{controls}</div>"
        "<h2>Последние ставки</h2>"
        f"{bids_table}"
    )
    return HTMLResponse(_render_page("Manage Auction", body))


@router.post("/actions/auction/freeze")
async def action_freeze_auction(
    request: Request,
    auction_id: str = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_AUCTION_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, f"/manage/auction/{auction_id}")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    try:
        auction_uuid = uuid.UUID(auction_id)
    except ValueError:
        return _action_error_page(request, "Invalid auction UUID", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)
    async with SessionFactory() as session:
        async with session.begin():
            result = await freeze_auction(
                session,
                actor_user_id=actor_user_id,
                auction_id=auction_uuid,
                reason=f"[web] {reason}",
            )
    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    await _refresh_auction_posts_from_web(result.auction_id)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@router.post("/actions/auction/unfreeze")
async def action_unfreeze_auction(
    request: Request,
    auction_id: str = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_AUCTION_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, f"/manage/auction/{auction_id}")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    try:
        auction_uuid = uuid.UUID(auction_id)
    except ValueError:
        return _action_error_page(request, "Invalid auction UUID", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)
    async with SessionFactory() as session:
        async with session.begin():
            result = await unfreeze_auction(
                session,
                actor_user_id=actor_user_id,
                auction_id=auction_uuid,
                reason=f"[web] {reason}",
            )
    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    await _refresh_auction_posts_from_web(result.auction_id)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@router.post("/actions/auction/end")
async def action_end_auction(
    request: Request,
    auction_id: str = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    confirmed: str | None = Form(None),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_AUCTION_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, f"/manage/auction/{auction_id}")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    try:
        auction_uuid = uuid.UUID(auction_id)
    except ValueError:
        return _action_error_page(request, "Invalid auction UUID", back_to=target)

    if not _is_confirmed(confirmed):
        return _render_confirmation_page(
            request,
            auth,
            title="Подтверждение завершения аукциона",
            message=f"Вы действительно хотите завершить аукцион {auction_id}?",
            action_path="/actions/auction/end",
            fields={
                "auction_id": auction_id,
                "reason": reason,
                "return_to": target,
            },
            back_to=target,
        )

    actor_user_id = await _resolve_actor_user_id(auth)
    async with SessionFactory() as session:
        async with session.begin():
            result = await end_auction(
                session,
                actor_user_id=actor_user_id,
                auction_id=auction_uuid,
                reason=f"[web] {reason}",
            )
    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    await _refresh_auction_posts_from_web(result.auction_id)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@router.post("/actions/bid/remove")
async def action_remove_bid(
    request: Request,
    bid_id: str = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    confirmed: str | None = Form(None),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_BID_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    try:
        bid_uuid = uuid.UUID(bid_id)
    except ValueError:
        return _action_error_page(request, "Invalid bid UUID", back_to=target)

    if not _is_confirmed(confirmed):
        return _render_confirmation_page(
            request,
            auth,
            title="Подтверждение снятия ставки",
            message=f"Вы действительно хотите снять ставку {bid_id}?",
            action_path="/actions/bid/remove",
            fields={
                "bid_id": bid_id,
                "reason": reason,
                "return_to": target,
            },
            back_to=target,
        )

    actor_user_id = await _resolve_actor_user_id(auth)
    async with SessionFactory() as session:
        async with session.begin():
            result = await remove_bid(
                session,
                actor_user_id=actor_user_id,
                bid_id=bid_uuid,
                reason=f"[web] {reason}",
            )
    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    await _refresh_auction_posts_from_web(result.auction_id)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)
