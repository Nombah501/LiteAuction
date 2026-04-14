from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.services.admin_queue_preset_telemetry_service import (
    load_workflow_preset_telemetry_segments,
)
from app.db.session import SessionFactory
from app.services.rbac_service import SCOPE_USER_BAN
from app.web.deps import _require_scope_permission

router = APIRouter()


@router.get("/actions/workflow-presets/telemetry")
async def action_workflow_presets_telemetry(
    request: Request,
    queue_context: str | None = None,
    lookback_hours: int = 24 * 7,
) -> dict[str, object]:
    response, _auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        detail = "Unauthorized" if response.status_code == 401 else "Forbidden"
        raise HTTPException(status_code=response.status_code, detail=detail)

    context_filter = queue_context.strip().lower() if queue_context is not None else None

    try:
        async with SessionFactory() as session:
            segments = await load_workflow_preset_telemetry_segments(
                session,
                queue_context=context_filter,
                lookback_hours=lookback_hours,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "lookback_hours": max(int(lookback_hours), 1),
        "queue_context": context_filter,
        "segments": segments,
        "segment_count": len(segments),
    }
