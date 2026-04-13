from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.db.session import SessionFactory
from app.services.moderation_service import (
    grant_moderator_role,
    revoke_moderator_role,
)
from app.services.rbac_service import SCOPE_ROLE_MANAGE
from app.web.components import _action_error_page, _safe_return_to
from app.web.deps import (
    _csrf_failed_response,
    _path_with_auth,
    _require_scope_permission,
    _validate_csrf_token,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/actions/user/moderator/grant")
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


@router.post("/actions/user/moderator/revoke")
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
