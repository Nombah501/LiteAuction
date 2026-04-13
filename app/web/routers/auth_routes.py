from __future__ import annotations

import logging
from html import escape

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.web.auth import (
    build_admin_session_cookie,
    get_admin_auth_context,
    validate_telegram_login,
)
from app.web.components import _render_page
from app.web.deps import _path_with_auth

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    auth = get_admin_auth_context(request)
    if auth.authorized:
        return RedirectResponse(url=_path_with_auth(request, "/"), status_code=303)

    bot_username = settings.bot_username.strip()
    auth_url = f"{str(request.base_url).rstrip('/')}/auth/telegram"
    host = (request.url.hostname or "").strip().lower()
    is_local_host = host in {"localhost", "127.0.0.1", "::1"}

    widget_html = ""
    if is_local_host:
        widget_html = (
            "<p><b>Telegram Login недоступен на localhost.</b><br>"
            "Для входа через Telegram откройте админку через публичный HTTPS-домен "
            "и задайте этот домен в BotFather (<code>/setdomain</code>)."
            "</p>"
        )
    elif bot_username:
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
        fallback = "<p>Также можно открыть панель по ссылке с токеном: <code>/?token=...</code></p>"

    body = (
        "<h1>LiteAuction Admin Login</h1>"
        "<div class='card'>"
        "<p>Войдите через Telegram-аккаунт модератора.</p>"
        f"{widget_html}"
        f"{fallback}"
        "</div>"
    )
    return HTMLResponse(_render_page("Admin Login", body))


@router.get("/auth/telegram")
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


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse(url=_path_with_auth(request, "/login"), status_code=303)
    response.delete_cookie("la_admin_session")
    return response
