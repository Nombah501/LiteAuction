from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.db.session import SessionFactory
from app.services.admin_list_preferences_service import (
    save_admin_list_preference,
)
from app.services.admin_queue_presets_service import (
    QUEUE_CONTEXT_TO_QUEUE_KEY,
    QUEUE_KEY_TO_QUEUE_CONTEXT,
    delete_preset,
    save_preset,
    select_preset,
    set_admin_default,
    update_preset,
)
from app.services.admin_queue_preset_telemetry_service import (
    record_workflow_preset_telemetry_event,
)
from app.web.auth import AdminAuthContext, get_admin_auth_context
from app.web.components import _QUEUE_ALLOWED_COLUMNS
from app.web.deps import _token_from_request, _validate_csrf_token

router = APIRouter()
logger = logging.getLogger(__name__)


def _normalize_workflow_preset_telemetry_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}

    telemetry = payload.get("telemetry")
    if not isinstance(telemetry, dict):
        return {}

    normalized: dict[str, object] = {}
    for key in ("time_to_action_ms", "reopen_signal", "filter_churn_count"):
        if key in telemetry:
            normalized[key] = telemetry[key]
    return normalized


def _resolve_workflow_preset_telemetry_preset_id(
    *, action: str, payload: dict[str, object], result: dict[str, object]
) -> int | None:
    if action in {"save", "update", "select"}:
        preset_meta = result.get("preset")
        if isinstance(preset_meta, dict):
            preset_id = preset_meta.get("id")
            if isinstance(preset_id, int) and preset_id > 0:
                return preset_id

    raw_preset_id = payload.get("preset_id")
    if isinstance(raw_preset_id, int) and raw_preset_id > 0:
        return raw_preset_id
    if isinstance(raw_preset_id, str) and raw_preset_id.isdigit():
        parsed = int(raw_preset_id)
        if parsed > 0:
            return parsed
    return None


def _workflow_preset_result_is_successful(result: object) -> bool:
    return isinstance(result, dict) and result.get("ok") is True


async def _record_workflow_preset_telemetry_safe(
    *,
    auth: AdminAuthContext,
    queue_context: str,
    action: str,
    preset_id: int | None,
    telemetry_payload: dict[str, object],
    admin_token: str | None,
) -> None:
    if queue_context not in QUEUE_CONTEXT_TO_QUEUE_KEY:
        return
    if action not in {"save", "update", "select", "delete", "set_default"}:
        return

    try:
        async with SessionFactory() as telemetry_session:
            async with telemetry_session.begin():
                await record_workflow_preset_telemetry_event(
                    telemetry_session,
                    auth=auth,
                    queue_context=queue_context,
                    action=action,
                    preset_id=preset_id,
                    time_to_action_ms=telemetry_payload.get("time_to_action_ms"),
                    reopen_signal=telemetry_payload.get("reopen_signal"),
                    filter_churn_count=telemetry_payload.get("filter_churn_count"),
                    admin_token=admin_token,
                )
    except Exception:
        logger.exception(
            "Failed to record workflow preset telemetry",
            extra={"queue_context": queue_context, "action": action, "preset_id": preset_id},
        )


@router.post("/actions/dense-list/preferences")
async def action_save_dense_list_preferences(request: Request) -> dict[str, object]:
    auth = get_admin_auth_context(request)
    if not auth.authorized:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be an object")

    queue_key = str(payload.get("queue_key") or "").strip().lower()
    allowed_columns = _QUEUE_ALLOWED_COLUMNS.get(queue_key)
    if allowed_columns is None:
        raise HTTPException(status_code=400, detail="Unknown queue key")

    csrf_token = str(payload.get("csrf_token") or "").strip()
    if not _validate_csrf_token(request, auth, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF check failed")

    density = str(payload.get("density") or "")
    columns_payload = payload.get("columns")
    if not isinstance(columns_payload, dict):
        raise HTTPException(status_code=400, detail="columns must be an object")

    try:
        async with SessionFactory() as session:
            async with session.begin():
                preference = await save_admin_list_preference(
                    session,
                    auth=auth,
                    queue_key=queue_key,
                    density=density,
                    columns_payload=columns_payload,
                    allowed_columns=allowed_columns,
                    admin_token=_token_from_request(request),
                )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"ok": True, "preference": preference}


@router.post("/actions/workflow-presets")
async def action_workflow_presets(request: Request) -> dict[str, object]:
    auth = get_admin_auth_context(request)
    if not auth.authorized:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be an object")

    csrf_token = str(payload.get("csrf_token") or "").strip()
    if not _validate_csrf_token(request, auth, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF check failed")

    queue_context = str(payload.get("queue_context") or "").strip().lower()
    queue_key = str(payload.get("queue_key") or "").strip().lower()
    if not queue_context:
        queue_context = QUEUE_KEY_TO_QUEUE_CONTEXT.get(queue_key, "")
    if not queue_context:
        raise HTTPException(status_code=400, detail="Unknown queue context")

    context_queue_key = QUEUE_CONTEXT_TO_QUEUE_KEY.get(queue_context)
    if context_queue_key is None:
        raise HTTPException(status_code=400, detail="Unknown queue context")

    allowed_columns = _QUEUE_ALLOWED_COLUMNS.get(context_queue_key)
    if allowed_columns is None:
        raise HTTPException(status_code=400, detail="Unknown queue context")

    action = str(payload.get("action") or "").strip().lower()
    token = _token_from_request(request)
    telemetry_payload = _normalize_workflow_preset_telemetry_payload(payload)

    try:
        async with SessionFactory() as session:
            async with session.begin():
                if action == "save":
                    result = await save_preset(
                        session,
                        auth=auth,
                        queue_context=queue_context,
                        name=str(payload.get("name") or ""),
                        density=str(payload.get("density") or ""),
                        columns_payload=payload.get("columns") or {},
                        allowed_columns=allowed_columns,
                        filters_payload=payload.get("filters")
                        if isinstance(payload.get("filters"), dict)
                        else {},
                        sort_payload=payload.get("sort")
                        if isinstance(payload.get("sort"), dict)
                        else {},
                        admin_token=token,
                        overwrite=bool(payload.get("overwrite")),
                    )
                elif action == "update":
                    raw_preset_id = payload.get("preset_id")
                    if raw_preset_id is None:
                        raise HTTPException(status_code=400, detail="preset_id is required")
                    result = await update_preset(
                        session,
                        auth=auth,
                        queue_context=queue_context,
                        preset_id=int(raw_preset_id),
                        density=str(payload.get("density") or ""),
                        columns_payload=payload.get("columns") or {},
                        allowed_columns=allowed_columns,
                        filters_payload=payload.get("filters")
                        if isinstance(payload.get("filters"), dict)
                        else {},
                        sort_payload=payload.get("sort")
                        if isinstance(payload.get("sort"), dict)
                        else {},
                        admin_token=token,
                    )
                elif action == "select":
                    raw_preset_id = payload.get("preset_id")
                    preset_id = int(raw_preset_id) if raw_preset_id not in (None, "") else None
                    result = await select_preset(
                        session,
                        auth=auth,
                        queue_context=queue_context,
                        preset_id=preset_id,
                        allowed_columns=allowed_columns,
                        admin_token=token,
                    )
                elif action == "delete":
                    raw_preset_id = payload.get("preset_id")
                    if raw_preset_id is None:
                        raise HTTPException(status_code=400, detail="preset_id is required")
                    result = await delete_preset(
                        session,
                        auth=auth,
                        queue_context=queue_context,
                        preset_id=int(raw_preset_id),
                        allowed_columns=allowed_columns,
                        keep_current=bool(payload.get("keep_current", True)),
                        admin_token=token,
                    )
                elif action == "set_default":
                    raw_preset_id = payload.get("preset_id")
                    preset_id = int(raw_preset_id) if raw_preset_id not in (None, "") else None
                    result = await set_admin_default(
                        session,
                        auth=auth,
                        queue_context=queue_context,
                        preset_id=preset_id,
                    )
                else:
                    raise HTTPException(status_code=400, detail="Unknown workflow preset action")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    telemetry_recorded = False
    if _workflow_preset_result_is_successful(result):
        telemetry_preset_id = _resolve_workflow_preset_telemetry_preset_id(
            action=action,
            payload=payload,
            result=result,
        )
        await _record_workflow_preset_telemetry_safe(
            auth=auth,
            queue_context=queue_context,
            action=action,
            preset_id=telemetry_preset_id,
            telemetry_payload=telemetry_payload,
            admin_token=token,
        )
        telemetry_recorded = True

    return {"ok": True, "result": result, "telemetry_recorded": telemetry_recorded}
