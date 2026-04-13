from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from html import escape
from typing import AsyncGenerator
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import SessionFactory
from app.services.rbac_service import (
    SCOPE_AUCTION_MANAGE,
    SCOPE_BID_MANAGE,
    SCOPE_ROLE_MANAGE,
    SCOPE_TRUST_MANAGE,
    SCOPE_USER_BAN,
    VIEWER_SCOPES,
)
from app.web.auth import AdminAuthContext, get_admin_auth_context


def _render_page(title: str, body: str) -> str:
    from app.web.components import render_page

    return render_page(title, body)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionFactory() as session:
        yield session


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


def _require_scope_permission(
    request: Request, scope: str
) -> tuple[Response | None, AdminAuthContext]:
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


def _require_owner_permission(request: Request) -> tuple[Response | None, AdminAuthContext]:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response, auth
    if auth.role == "owner":
        return None, auth

    back_to = _safe_back_to_from_request(request)
    body = (
        "<h1>Недостаточно прав</h1>"
        "<div class='notice notice-warn'><p>Доступ к runtime-настройкам разрешен только owner.</p></div>"
        f"<p><a href='{escape(_path_with_auth(request, back_to))}'>Назад</a></p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
    )
    return HTMLResponse(_render_page("Forbidden", body), status_code=403), auth
