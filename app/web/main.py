from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime
from html import escape
import logging
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select

from app.config import settings
from app.db.enums import AuctionStatus, UserRole
from app.db.models import Auction, Bid, BlacklistEntry, Complaint, FraudSignal, User, UserRoleAssignment
from app.db.session import SessionFactory
from app.services.auction_service import refresh_auction_posts
from app.services.complaint_service import list_complaints
from app.services.fraud_service import list_fraud_signals
from app.services.moderation_dashboard_service import get_moderation_dashboard_snapshot
from app.services.timeline_service import build_auction_timeline_page
from app.services.rbac_service import (
    SCOPE_AUCTION_MANAGE,
    SCOPE_BID_MANAGE,
    SCOPE_ROLE_MANAGE,
    SCOPE_USER_BAN,
)
from app.services.moderation_service import (
    ban_user,
    end_auction,
    freeze_auction,
    grant_moderator_role,
    is_moderator_tg_user,
    list_user_roles,
    list_recent_bids,
    remove_bid,
    revoke_moderator_role,
    unban_user,
    unfreeze_auction,
)
from app.web.auth import (
    AdminAuthContext,
    build_admin_session_cookie,
    get_admin_auth_context,
    validate_telegram_login,
)

app = FastAPI(title="LiteAuction Admin", version="0.2.0")
logger = logging.getLogger(__name__)


def _timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.tz)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _fmt_ts(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(_timezone()).strftime("%Y-%m-%d %H:%M:%S")


def _pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.0%"
    return f"{(numerator / denominator) * 100:.1f}%"


def _token_from_request(request: Request) -> str | None:
    token = request.query_params.get("token")
    if token:
        return token
    header = request.headers.get("x-admin-token", "")
    return header or None


def _path_with_auth(request: Request, path: str) -> str:
    token = _token_from_request(request)
    if not token:
        return path
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}token={token}"


def _csrf_secret() -> bytes:
    value = settings.admin_web_session_secret.strip() or settings.bot_token
    return value.encode("utf-8")


def _csrf_subject(request: Request, auth: AdminAuthContext) -> str | None:
    if auth.tg_user_id is not None:
        return f"tg:{auth.tg_user_id}"

    token = _token_from_request(request)
    if auth.via == "token" and token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"tok:{digest}"

    return None


def _build_csrf_token(request: Request, auth: AdminAuthContext) -> str:
    subject = _csrf_subject(request, auth)
    if subject is None:
        return ""

    issued_at = int(datetime.now(UTC).timestamp())
    nonce = secrets.token_hex(8)
    payload = f"{subject}|{issued_at}|{nonce}"
    signature = hmac.new(_csrf_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{signature}"


def _csrf_hidden_input(request: Request, auth: AdminAuthContext) -> str:
    token = _build_csrf_token(request, auth)
    return f"<input type='hidden' name='csrf_token' value='{escape(token)}'>"


def _validate_csrf_token(request: Request, auth: AdminAuthContext, csrf_token: str) -> bool:
    parts = csrf_token.split("|")
    if len(parts) != 4:
        return False

    subject_raw, issued_raw, nonce_raw, signature_raw = parts
    if not issued_raw.isdigit() or not nonce_raw:
        return False

    expected_subject = _csrf_subject(request, auth)
    if expected_subject is None or subject_raw != expected_subject:
        return False

    payload = f"{subject_raw}|{issued_raw}|{nonce_raw}"
    expected_signature = hmac.new(
        _csrf_secret(),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature_raw, expected_signature):
        return False

    now_ts = int(datetime.now(UTC).timestamp())
    issued_ts = int(issued_raw)
    if issued_ts > now_ts + 30:
        return False

    ttl_seconds = max(settings.admin_web_csrf_ttl_seconds, 60)
    if now_ts - issued_ts > ttl_seconds:
        return False

    return True


def _csrf_failed_response(request: Request, *, back_to: str) -> HTMLResponse:
    body = (
        "<h1>CSRF check failed</h1>"
        "<div class='notice notice-error'><p>Обновите страницу и повторите действие.</p></div>"
        f"<p><a href='{escape(_path_with_auth(request, back_to))}'>Назад</a></p>"
    )
    return HTMLResponse(_render_page("Forbidden", body), status_code=403)


def _is_confirmed(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on", "confirm"}


def _render_confirmation_page(
    request: Request,
    auth: AdminAuthContext,
    *,
    title: str,
    message: str,
    action_path: str,
    fields: dict[str, str],
    back_to: str,
) -> HTMLResponse:
    hidden_fields = "".join(
        f"<input type='hidden' name='{escape(key)}' value='{escape(value)}'>"
        for key, value in fields.items()
    )
    form = (
        f"<form method='post' action='{escape(_path_with_auth(request, action_path))}'>"
        f"{hidden_fields}"
        f"{_csrf_hidden_input(request, auth)}"
        "<input type='hidden' name='confirmed' value='1'>"
        "<button type='submit'>Подтвердить</button>"
        "</form>"
    )
    body = (
        f"<h1>{escape(title)}</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<div class='card'><p>{escape(message)}</p>{form}</div>"
        f"<p><a href='{escape(_path_with_auth(request, back_to))}'>Отмена</a></p>"
    )
    return HTMLResponse(_render_page("Confirm Action", body))


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


def _render_page(title: str, body: str) -> str:
    styles = (
        ":root{"
        "--bg-0:#f5f8f9;--bg-1:#e8f0f1;--ink:#1d2b33;--muted:#5d7078;"
        "--card:#ffffff;--line:#d2dde2;--soft:#eef4f6;--accent:#0f766e;--accent-ink:#0b4f4a;}"
        "*{box-sizing:border-box;}"
        "body{margin:0;font-family:'IBM Plex Sans','Trebuchet MS','Segoe UI',sans-serif;"
        "line-height:1.45;color:var(--ink);"
        "background:radial-gradient(1400px 600px at -10% -20%,#d7ece7 0%,transparent 70%),"
        "radial-gradient(1200px 500px at 120% -30%,#e5ebe2 0%,transparent 68%),"
        "linear-gradient(180deg,var(--bg-0),var(--bg-1));}"
        ".page-shell{max-width:1280px;margin:20px auto;padding:20px 24px;border:1px solid var(--line);"
        "border-radius:16px;background:rgba(255,255,255,0.86);backdrop-filter:blur(2px);"
        "box-shadow:0 20px 36px rgba(18,33,40,0.09);overflow:auto;}"
        "h1{margin:0 0 12px;font-size:30px;letter-spacing:0.2px;}"
        "h2,h3{margin-top:20px;margin-bottom:10px;}"
        "p{margin:10px 0;}"
        "table{border-collapse:separate;border-spacing:0;width:100%;margin-top:12px;background:var(--card);"
        "border:1px solid var(--line);border-radius:12px;overflow:hidden;}"
        "th,td{border-bottom:1px solid var(--line);padding:9px 10px;text-align:left;font-size:14px;vertical-align:top;}"
        "th{background:var(--soft);font-weight:600;color:var(--accent-ink);letter-spacing:0.2px;}"
        "tr:nth-child(even) td{background:#fbfdfe;}"
        "tr:last-child td{border-bottom:none;}"
        ".kpi{display:inline-block;margin:0 14px 10px 0;background:var(--card);border:1px solid var(--line);"
        "padding:10px 12px;border-radius:10px;box-shadow:0 4px 12px rgba(15,26,31,0.06);}"
        "a{color:var(--accent);text-decoration:none;font-weight:600;}"
        "a:hover{text-decoration:underline;}"
        "a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{"
        "outline:3px solid #ffb454;outline-offset:2px;}"
        ".chip{display:inline-block;padding:4px 9px;border-radius:999px;border:1px solid #b8c9c7;"
        "background:#f3faf8;font-size:12px;font-weight:600;margin-right:6px;margin-bottom:4px;}"
        ".notice{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:10px 0;}"
        ".notice p{margin:0;}"
        ".notice-error{background:#fff1f1;border-color:#e4b5b5;color:#822727;}"
        ".notice-warn{background:#fff8ec;border-color:#e9c28e;color:#7b4a0d;}"
        ".notice-info{background:#edf7f7;border-color:#b4d7d4;color:#0b4f4a;}"
        ".empty-state{color:var(--muted);font-style:italic;}"
        ".card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;"
        "box-shadow:0 3px 10px rgba(11,31,36,0.05);}"
        "pre{white-space:pre-wrap;background:var(--soft);padding:8px;border-radius:8px;border:1px solid var(--line);margin:0;}"
        "input,button,select,textarea{font:inherit;}"
        "input,select,textarea{border:1px solid #b6c7cc;border-radius:8px;padding:7px 9px;background:#fff;color:var(--ink);"
        "max-width:100%;}"
        "button{border:1px solid #0f766e;background:linear-gradient(180deg,#179186,#11756d);color:#fff;"
        "font-weight:700;border-radius:8px;padding:7px 12px;cursor:pointer;box-shadow:0 3px 8px rgba(6,74,69,0.23);}"
        "button:hover{filter:brightness(1.03);}"
        "button:active{transform:translateY(1px);}"
        "@media (max-width:900px){.page-shell{margin:10px;padding:12px;border-radius:12px;}"
        "h1{font-size:24px;}th,td{font-size:13px;padding:7px;}"
        "table{display:block;overflow-x:auto;white-space:nowrap;}"
        ".kpi{margin-right:8px;}}"
    )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title>"
        f"<style>{styles}</style>"
        "</head><body><div class='page-shell'>"
        f"{body}</div></body></html>"
    )


def _require_or_redirect(request: Request) -> RedirectResponse | None:
    auth = get_admin_auth_context(request)
    if auth.authorized:
        return None
    return RedirectResponse(url=_path_with_auth(request, "/login"), status_code=303)


def _auth_context_or_unauthorized(request: Request) -> tuple[Response | None, AdminAuthContext]:
    auth = get_admin_auth_context(request)
    if auth.authorized:
        return None, auth
    return RedirectResponse(url=_path_with_auth(request, "/login"), status_code=303), auth


def _role_badge(auth: AdminAuthContext) -> str:
    role = auth.role
    via = auth.via
    scope_names = sorted(auth.scopes)
    scope_label = ",".join(scope_names) if scope_names else "read-only"
    return f"role={role}, via={via}, scopes={scope_label}"


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


def _normalize_timeline_source_query(raw: str | None) -> tuple[list[str] | None, str | None]:
    if raw is None:
        return None, None

    values: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        value = part.strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)

    if not values:
        return None, None
    return values, ",".join(values)


def _parse_non_negative_int(raw: str | None) -> int | None:
    if raw is None or not raw.isdigit():
        return None
    return int(raw)


def _is_safe_local_path(path: str | None) -> bool:
    if not path:
        return False
    if not path.startswith("/"):
        return False
    if path.startswith("//"):
        return False
    return True


def _safe_back_to_from_request(request: Request, fallback: str = "/") -> str:
    direct = request.query_params.get("return_to")
    if direct is not None and _is_safe_local_path(direct):
        return direct

    referer = request.headers.get("referer") or ""
    if referer:
        parsed = urlsplit(referer)
        referer_path = parsed.path or ""
        if _is_safe_local_path(referer_path):
            referer_query = f"?{parsed.query}" if parsed.query else ""
            return f"{referer_path}{referer_query}"

    return fallback


def _require_scope_permission(request: Request, scope: str) -> tuple[Response | None, AdminAuthContext]:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response, auth
    if auth.can(scope):
        return None, auth

    scope_title = _scope_title(scope)
    back_to = _safe_back_to_from_request(request)
    body = (
        "<h1>Недостаточно прав</h1>"
        f"<div class='notice notice-warn'><p>Для этого действия нужна роль с правом: <b>{escape(scope_title)}</b>.</p></div>"
        f"<p><a href='{escape(_path_with_auth(request, back_to))}'>Назад</a></p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
    )
    return HTMLResponse(_render_page("Forbidden", body), status_code=403), auth


async def _resolve_actor_user_id(auth: AdminAuthContext) -> int:
    tg_user_id = auth.tg_user_id
    if tg_user_id is None:
        admin_ids = settings.parsed_admin_user_ids()
        if not admin_ids:
            raise HTTPException(status_code=500, detail="ADMIN_USER_IDS is required for web actions")
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    auth = get_admin_auth_context(request)
    if auth.authorized:
        return RedirectResponse(url=_path_with_auth(request, "/"), status_code=303)

    bot_username = settings.bot_username.strip()
    auth_url = f"{str(request.base_url).rstrip('/')}/auth/telegram"

    widget_html = ""
    if bot_username:
        widget_html = (
            "<script async src='https://telegram.org/js/telegram-widget.js?22' "
            f"data-telegram-login='{escape(bot_username)}' "
            "data-size='large' data-request-access='write' "
            f"data-auth-url='{escape(auth_url)}'></script>"
        )
    else:
        widget_html = (
            "<p><b>Telegram Login не настроен.</b><br>"
            "Укажите <code>BOT_USERNAME</code> в <code>.env</code>, чтобы включить вход через Telegram.</p>"
        )

    fallback = ""
    if settings.admin_panel_token.strip():
        fallback = (
            "<p>Также можно открыть панель по ссылке с токеном: "
            "<code>/?token=...</code></p>"
        )

    body = (
        "<h1>LiteAuction Admin Login</h1>"
        "<div class='card'>"
        "<p>Войдите через Telegram-аккаунт модератора.</p>"
        f"{widget_html}"
        f"{fallback}"
        "</div>"
    )
    return HTMLResponse(_render_page("Admin Login", body))


@app.get("/auth/telegram")
async def telegram_auth_callback(request: Request) -> Response:
    payload = {key: value for key, value in request.query_params.items()}
    ok, reason = validate_telegram_login(payload)
    if not ok:
        body = (
            "<h1>Ошибка входа</h1>"
            f"<p>{escape(reason)}</p>"
            f"<p><a href='{escape(_path_with_auth(request, '/login'))}'>Вернуться к логину</a></p>"
        )
        return HTMLResponse(_render_page("Login Error", body), status_code=403)

    user_id = int(payload["id"])
    response = RedirectResponse(url=_path_with_auth(request, "/"), status_code=303)
    response.set_cookie(
        key="la_admin_session",
        value=build_admin_session_cookie(user_id),
        max_age=max(settings.admin_web_auth_max_age_seconds, 60),
        httponly=True,
        secure=settings.admin_web_cookie_secure,
        samesite="lax",
    )
    return response


@app.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse(url=_path_with_auth(request, "/login"), status_code=303)
    response.delete_cookie("la_admin_session")
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response

    async with SessionFactory() as session:
        snapshot = await get_moderation_dashboard_snapshot(session)

    engaged_with_private = max(
        snapshot.users_with_engagement - snapshot.users_engaged_without_private_start,
        0,
    )

    body = (
        "<h1>LiteAuction Admin</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<div class='kpi'><b>Открытые жалобы:</b> {snapshot.open_complaints}</div>"
        f"<div class='kpi'><b>Открытые сигналы:</b> {snapshot.open_signals}</div>"
        f"<div class='kpi'><b>Активные аукционы:</b> {snapshot.active_auctions}</div>"
        f"<div class='kpi'><b>Ставок/час:</b> {snapshot.bids_last_hour}</div>"
        "<hr>"
        "<h2>Воронка онбординга / soft-gate</h2>"
        f"<div class='kpi'><b>Всего пользователей:</b> {snapshot.total_users}</div>"
        f"<div class='kpi'><b>Private /start:</b> {snapshot.users_private_started} ({_pct(snapshot.users_private_started, snapshot.total_users)})</div>"
        f"<div class='kpi'><b>С hint:</b> {snapshot.users_with_soft_gate_hint}</div>"
        f"<div class='kpi'><b>Hint за 24ч:</b> {snapshot.users_soft_gate_hint_last_24h}</div>"
        f"<div class='kpi'><b>Конверсия после hint:</b> {snapshot.users_converted_after_hint} ({_pct(snapshot.users_converted_after_hint, snapshot.users_with_soft_gate_hint)})</div>"
        f"<div class='kpi'><b>Ожидают после hint:</b> {snapshot.users_pending_after_hint}</div>"
        "<hr>"
        "<h2>Активность пользователей</h2>"
        f"<div class='kpi'><b>Пользователи со ставками:</b> {snapshot.users_with_bid_activity}</div>"
        f"<div class='kpi'><b>Пользователи с жалобами:</b> {snapshot.users_with_report_activity}</div>"
        f"<div class='kpi'><b>Уникально вовлеченные:</b> {snapshot.users_with_engagement}</div>"
        f"<div class='kpi'><b>Вовлеченные без private /start:</b> {snapshot.users_engaged_without_private_start}</div>"
        f"<div class='kpi'><b>Вовлеченные с private /start:</b> {engaged_with_private} ({_pct(engaged_with_private, snapshot.users_with_engagement)})</div>"
        "<hr>"
        "<ul>"
        f"<li><a href='{escape(_path_with_auth(request, '/complaints?status=OPEN'))}'>Открытые жалобы</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/signals?status=OPEN'))}'>Открытые фрод-сигналы</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/auctions?status=ACTIVE'))}'>Активные аукционы</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/auctions?status=FROZEN'))}'>Замороженные аукционы</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/manage/users'))}'>Управление пользователями</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/logout'))}'>Выйти</a></li>"
        "</ul>"
    )
    return HTMLResponse(_render_page("LiteAuction Admin", body))


@app.get("/complaints", response_class=HTMLResponse)
async def complaints(request: Request, status: str = "OPEN", page: int = 0) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response
    page = max(page, 0)
    page_size = 30
    offset = page * page_size

    async with SessionFactory() as session:
        rows = await list_complaints(
            session,
            auction_id=None,
            status=status,
            limit=page_size + 1,
            offset=offset,
        )

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    table_rows = "".join(
        "<tr>"
        f"<td>{item.id}</td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.auction_id}'))}'>{escape(str(item.auction_id))}</a></td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/manage/user/{item.reporter_user_id}'))}'>{item.reporter_user_id}</a></td>"
        f"<td>{escape(item.status)}</td>"
        f"<td>{escape(item.reason[:120])}</td>"
        f"<td>{escape(_fmt_ts(item.created_at))}</td>"
        "</tr>"
        for item in rows
    )
    if not table_rows:
        table_rows = "<tr><td colspan='6'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, f'/complaints?status={status}&page={page-1}'))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, f'/complaints?status={status}&page={page+1}'))}'>Вперед →</a>"
        if has_next
        else ""
    )

    body = (
        f"<h1>Жалобы ({escape(status)})</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        "<table><thead><tr><th>ID</th><th>Auction</th><th>Reporter UID</th><th>Status</th><th>Reason</th><th>Created</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
    )
    return HTMLResponse(_render_page("Complaints", body))


@app.get("/signals", response_class=HTMLResponse)
async def signals(request: Request, status: str = "OPEN", page: int = 0) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response
    page = max(page, 0)
    page_size = 30
    offset = page * page_size

    async with SessionFactory() as session:
        rows = await list_fraud_signals(
            session,
            auction_id=None,
            status=status,
            limit=page_size + 1,
            offset=offset,
        )

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    table_rows = "".join(
        "<tr>"
        f"<td>{item.id}</td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.auction_id}'))}'>{escape(str(item.auction_id))}</a></td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/manage/user/{item.user_id}'))}'>{item.user_id}</a></td>"
        f"<td>{item.score}</td>"
        f"<td>{escape(item.status)}</td>"
        f"<td>{escape(_fmt_ts(item.created_at))}</td>"
        "</tr>"
        for item in rows
    )
    if not table_rows:
        table_rows = "<tr><td colspan='6'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, f'/signals?status={status}&page={page-1}'))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, f'/signals?status={status}&page={page+1}'))}'>Вперед →</a>"
        if has_next
        else ""
    )

    body = (
        f"<h1>Фрод-сигналы ({escape(status)})</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        "<table><thead><tr><th>ID</th><th>Auction</th><th>User ID</th><th>Score</th><th>Status</th><th>Created</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
    )
    return HTMLResponse(_render_page("Fraud Signals", body))


@app.get("/auctions", response_class=HTMLResponse)
async def auctions(request: Request, status: str = "ACTIVE", page: int = 0) -> Response:
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
            await session.execute(
                select(Auction)
                .where(Auction.status == status)
                .order_by(Auction.created_at.desc())
                .offset(offset)
                .limit(page_size + 1)
            )
        ).scalars().all()

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    table_rows = "".join(
        "<tr>"
        f"<td><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.id}'))}'>{escape(str(item.id))}</a></td>"
        f"<td>{item.seller_user_id}</td>"
        f"<td>${item.start_price}</td>"
        f"<td>${item.buyout_price if item.buyout_price is not None else '-'}</td>"
        f"<td>{escape(str(item.status))}</td>"
        f"<td>{escape(_fmt_ts(item.ends_at))}</td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/manage/auction/{item.id}'))}'>Управлять</a></td>"
        "</tr>"
        for item in rows
    )
    if not table_rows:
        table_rows = "<tr><td colspan='7'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, f'/auctions?status={status}&page={page-1}'))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, f'/auctions?status={status}&page={page+1}'))}'>Вперед →</a>"
        if has_next
        else ""
    )

    body = (
        f"<h1>Аукционы ({escape(status)})</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        "<table><thead><tr><th>ID</th><th>Seller UID</th><th>Start</th><th>Buyout</th><th>Status</th><th>Ends At</th><th>Actions</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
    )
    return HTMLResponse(_render_page("Auctions", body))


@app.get("/timeline/auction/{auction_id}", response_class=HTMLResponse)
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


def _safe_return_to(return_to: str | None, fallback: str) -> str:
    if return_to is not None and _is_safe_local_path(return_to):
        return return_to
    return fallback


def _action_error_page(request: Request, message: str, *, back_to: str) -> HTMLResponse:
    body = (
        "<h1>Action failed</h1>"
        f"<div class='notice notice-error'><p>{escape(message)}</p></div>"
        f"<p><a href='{escape(_path_with_auth(request, back_to))}'>Назад</a></p>"
    )
    return HTMLResponse(_render_page("Action Error", body), status_code=400)


@app.get("/manage/auction/{auction_id}", response_class=HTMLResponse)
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
    _, timeline_source = _normalize_timeline_source_query(request.query_params.get("timeline_source"))

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
        f"<tbody>{''.join(bid_rows) if bid_rows else '<tr><td colspan=7><span class=\"empty-state\">Нет ставок</span></td></tr>'}</tbody></table>"
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


@app.get("/manage/user/{user_id}", response_class=HTMLResponse)
async def manage_user(request: Request, user_id: int) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response

    now = datetime.now(UTC)
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
            await session.scalar(select(func.count(FraudSignal.id)).where(FraudSignal.user_id == user.id))
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
            await session.execute(
                select(Complaint)
                .where(Complaint.target_user_id == user.id)
                .order_by(Complaint.created_at.desc())
                .limit(10)
            )
        ).scalars().all()
        recent_fraud_signals = (
            await session.execute(
                select(FraudSignal)
                .where(FraudSignal.user_id == user.id)
                .order_by(FraudSignal.created_at.desc())
                .limit(10)
            )
        ).scalars().all()

    can_ban_users = auth.can(SCOPE_USER_BAN)
    can_manage_roles = auth.can(SCOPE_ROLE_MANAGE)
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

    controls = "<p><i>Только просмотр (нет прав на управление пользователями).</i></p>"
    if controls_blocks:
        controls = "<br>".join(controls_blocks)

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
        complaints_rows = "<tr><td colspan='5'><span class='empty-state'>Нет записей</span></td></tr>"

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

    roles_text = ", ".join(sorted(role.value for role in user_roles)) if user_roles else "-"
    moderator_text = "yes" if has_moderator_access else "no"
    blacklist_status = "active" if active_blacklist_entry is not None else "no"

    body = (
        f"<h1>Управление пользователем {user.id}</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/manage/users'))}'>К пользователям</a> | "
        f"<a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"<p><b>TG User ID:</b> {user.tg_user_id} | <b>Username:</b> {escape(user.username or '-')}</p>"
        f"<p><b>Moderator:</b> {moderator_text} | <b>Roles:</b> {escape(roles_text)} | <b>Blacklisted:</b> {blacklist_status}</p>"
        f"<div class='kpi'><b>Ставок:</b> {bids_total}</div>"
        f"<div class='kpi'><b>Снято ставок:</b> {bids_removed}</div>"
        f"<div class='kpi'><b>Жалоб создано:</b> {complaints_created}</div>"
        f"<div class='kpi'><b>Жалоб на пользователя:</b> {complaints_against}</div>"
        f"<div class='kpi'><b>Фрод-сигналов:</b> {fraud_total}</div>"
        f"<div class='kpi'><b>Открытых сигналов:</b> {fraud_open}</div>"
        f"<div class='card'>{controls}</div>"
        "<h2>Последние жалобы на пользователя</h2>"
        "<table><thead><tr><th>ID</th><th>Auction</th><th>Status</th><th>Reason</th><th>Created</th></tr></thead>"
        f"<tbody>{complaints_rows}</tbody></table>"
        "<h2>Последние фрод-сигналы по пользователю</h2>"
        "<table><thead><tr><th>ID</th><th>Auction</th><th>Score</th><th>Status</th><th>Created</th></tr></thead>"
        f"<tbody>{signal_rows}</tbody></table>"
    )
    return HTMLResponse(_render_page("Manage User", body))


@app.get("/manage/users", response_class=HTMLResponse)
async def manage_users(request: Request, page: int = 0, q: str = "") -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response

    page = max(page, 0)
    page_size = 30
    offset = page * page_size
    query_value = q.strip()

    now = datetime.now(UTC)
    admin_ids = set(settings.parsed_admin_user_ids())

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
        users = (
            await session.execute(
                stmt.offset(offset).limit(page_size + 1),
            )
        ).scalars().all()

        has_next = len(users) > page_size
        users = users[:page_size]

        user_ids = [user.id for user in users]
        role_user_ids: set[int] = set()
        banned_user_ids: set[int] = set()

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
                ).scalars().all()
            )
            banned_user_ids = set(
                (
                    await session.execute(
                        select(BlacklistEntry.user_id).where(
                            BlacklistEntry.user_id.in_(user_ids),
                            BlacklistEntry.is_active.is_(True),
                            (BlacklistEntry.expires_at.is_(None) | (BlacklistEntry.expires_at > now)),
                        )
                    )
                ).scalars().all()
            )

    rows = []
    for user in users:
        is_allowlist_mod = user.tg_user_id in admin_ids
        is_dynamic_mod = user.id in role_user_ids
        moderator_text = "yes (allowlist)" if is_allowlist_mod else ("yes" if is_dynamic_mod else "no")
        banned_text = "yes" if user.id in banned_user_ids else "no"

        rows.append(
            "<tr>"
            f"<td>{user.id}</td>"
            f"<td>{user.tg_user_id}</td>"
            f"<td>{escape(user.username or '-')}</td>"
            f"<td>{moderator_text}</td>"
            f"<td>{banned_text}</td>"
            f"<td>{escape(_fmt_ts(user.created_at))}</td>"
            f"<td><a href='{escape(_path_with_auth(request, f'/manage/user/{user.id}'))}'>open</a></td>"
            "</tr>"
        )

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, f'/manage/users?page={page-1}&q={query_value}'))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, f'/manage/users?page={page+1}&q={query_value}'))}'>Вперед →</a>"
        if has_next
        else ""
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
        "<h1>Пользователи</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/manage/users'))}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='tg id или username' style='width:240px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        f"{moderator_grant_form}"
        "<table><thead><tr><th>ID</th><th>TG User ID</th><th>Username</th><th>Moderator</th><th>Banned</th><th>Created</th><th>Manage</th></tr></thead>"
        f"<tbody>{''.join(rows) if rows else '<tr><td colspan=7><span class=\"empty-state\">Нет записей</span></td></tr>'}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
    )
    return HTMLResponse(_render_page("Manage Users", body))


@app.post("/actions/auction/freeze")
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


@app.post("/actions/auction/unfreeze")
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


@app.post("/actions/auction/end")
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


@app.post("/actions/bid/remove")
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


@app.post("/actions/user/ban")
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


@app.post("/actions/user/unban")
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


@app.post("/actions/user/moderator/grant")
async def action_grant_moderator(
    request: Request,
    target_tg_user_id: int = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_ROLE_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/manage/users")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    async with SessionFactory() as session:
        async with session.begin():
            result = await grant_moderator_role(
                session,
                target_tg_user_id=target_tg_user_id,
            )
    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)

    logger.info(
        "[web] moderator role granted to tg_user_id=%s, reason=%s",
        target_tg_user_id,
        reason,
    )
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@app.post("/actions/user/moderator/revoke")
async def action_revoke_moderator(
    request: Request,
    target_tg_user_id: int = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_ROLE_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/manage/users")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    async with SessionFactory() as session:
        async with session.begin():
            result = await revoke_moderator_role(
                session,
                target_tg_user_id=target_tg_user_id,
            )
    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)

    logger.info(
        "[web] moderator role revoked for tg_user_id=%s, reason=%s",
        target_tg_user_id,
        reason,
    )
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


def main() -> None:
    uvicorn.run("app.web.main:app", host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    main()
