from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.config import settings
from app.db.models import Complaint, FraudSignal, User
from app.db.session import SessionFactory
from app.services.adaptive_triage_policy_service import decide_adaptive_detail_depth
from app.services.appeal_service import mark_appeal_in_review, reject_appeal, resolve_appeal
from app.services.rbac_service import SCOPE_USER_BAN
from app.services.trade_feedback_service import set_trade_feedback_visibility
from app.web.auth import AdminAuthContext, get_admin_auth_context
from app.web.deps import _auth_context_or_unauthorized, _validate_csrf_token

router = APIRouter()


async def _resolve_actor_user_id(auth: AdminAuthContext) -> int:
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

    async with SessionFactory() as session:
        new_user = User(tg_user_id=tg_user_id, username=f"admin_{tg_user_id}")
        session.add(new_user)
        await session.flush()
        return new_user.id


@router.get("/actions/triage/detail-section")
async def action_triage_detail_section(
    request: Request,
    queue_key: str,
    row_id: int,
    section: str,
    risk_level: str | None = None,
    priority_level: str | None = None,
    depth_override: str | None = None,
) -> dict[str, object]:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    queue_value = queue_key.strip().lower()
    section_value = section.strip().lower()
    if queue_value not in {"complaints", "signals", "trade_feedback", "appeals"}:
        raise HTTPException(status_code=400, detail="Unknown queue key")
    if section_value not in {"primary", "secondary", "audit"}:
        raise HTTPException(status_code=400, detail="Unknown section")

    if queue_value in {"trade_feedback", "appeals"} and not auth.can(SCOPE_USER_BAN):
        raise HTTPException(status_code=403, detail="Forbidden")

    decision = decide_adaptive_detail_depth(
        queue_key=queue_value,
        risk_level=risk_level,
        priority_level=priority_level,
        operator_override=depth_override,
    )

    metadata: dict[str, object] = {
        "depth": decision.depth.value,
        "reason_code": decision.reason_code.value,
        "fallback_applied": decision.fallback_applied,
        "fallback_notes": list(decision.fallback_notes),
    }

    if decision.depth.value == "inline_summary" and section_value in {"secondary", "audit"}:
        collapsed_html = (
            "<div data-detail-state='summary-only'>"
            "Summary mode active. Use row override 'Full' to load this section."
            "</div>"
        )
        return {"ok": True, "html": collapsed_html, **metadata}

    if queue_value == "signals":
        async with SessionFactory() as session:
            detail_payload = await _render_signal_detail_section(
                session,
                row_id=row_id,
                section=section_value,
                request=request,
            )
        return {**detail_payload, **metadata}

    if queue_value == "complaints":
        async with SessionFactory() as session:
            detail_payload = await _render_complaint_detail_section(
                session,
                row_id=row_id,
                section=section_value,
                request=request,
            )
        return {**detail_payload, **metadata}

    if queue_value == "appeals":
        async with SessionFactory() as session:
            detail_payload = await _render_appeal_detail_section(
                session,
                row_id=row_id,
                section=section_value,
                request=request,
            )
        return {**detail_payload, **metadata}

    if section_value == "audit" and row_id % 5 == 0:
        return {"ok": False, "message": "Section temporarily unavailable", **metadata}

    content = f"<div data-detail-state='loaded'><b>{escape(queue_value)} #{row_id}</b> | section: {escape(section_value)}</div>"
    if section_value == "primary":
        content += (
            "<p>Priority context loaded first.</p>"
            f"<p class='section-note'>adaptive: {escape(decision.depth.value)} via {escape(decision.reason_code.value)}</p>"
        )
    elif section_value == "secondary":
        content += "<p>Related details loaded progressively.</p>"
    else:
        content += "<p>Audit trace and recent moderation notes.</p>"
    return {"ok": True, "html": content, **metadata}


async def _render_signal_detail_section(
    session,
    *,
    row_id: int,
    section: str,
    request: Request,
) -> dict[str, object]:
    item = await session.scalar(select(FraudSignal).where(FraudSignal.id == row_id))
    if item is None:
        return {"ok": False, "html": "<div>Signal not found.</div>"}
    html = f"<div><b>Signal #{item.id}</b> | section: {escape(section)}</div>"
    return {"ok": True, "html": html}


async def _render_complaint_detail_section(
    session,
    *,
    row_id: int,
    section: str,
    request: Request,
) -> dict[str, object]:
    item = await session.scalar(select(Complaint).where(Complaint.id == row_id))
    if item is None:
        return {"ok": False, "html": "<div>Complaint not found.</div>"}
    html = f"<div><b>Complaint #{item.id}</b> | section: {escape(section)}</div>"
    return {"ok": True, "html": html}


async def _render_appeal_detail_section(
    session,
    *,
    row_id: int,
    section: str,
    request: Request,
) -> dict[str, object]:
    from app.db.models import Appeal

    item = await session.scalar(select(Appeal).where(Appeal.id == row_id))
    if item is None:
        return {"ok": False, "html": "<div>Appeal not found.</div>"}
    html = f"<div><b>Appeal #{item.id}</b> | section: {escape(section)}</div>"
    return {"ok": True, "html": html}


@router.post("/actions/triage/bulk")
async def action_triage_bulk(request: Request) -> dict[str, object]:
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

    queue_key = str(payload.get("queue_key") or "").strip().lower()
    bulk_action = str(payload.get("bulk_action") or "").strip().lower()
    selected_ids_raw = payload.get("selected_ids")
    confirm_text = str(payload.get("confirm_text") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if not isinstance(selected_ids_raw, list):
        raise HTTPException(status_code=400, detail="selected_ids must be an array")

    try:
        selected_ids = [int(item) for item in selected_ids_raw]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="selected_ids must be integers") from exc

    selected_ids = [item for item in selected_ids if item > 0]
    if not selected_ids:
        return {"ok": True, "results": []}

    destructive_actions = {"dismiss", "hide", "reject"}
    if bulk_action in destructive_actions and confirm_text != "CONFIRM":
        raise HTTPException(status_code=400, detail="Confirmation text mismatch")

    if queue_key not in {"complaints", "signals", "trade_feedback", "appeals"}:
        raise HTTPException(status_code=400, detail="Unknown queue key")

    if queue_key in {"trade_feedback", "appeals"} and not auth.can(SCOPE_USER_BAN):
        raise HTTPException(status_code=403, detail="Forbidden")

    actor_user_id = await _resolve_actor_user_id(auth)
    now = datetime.now(UTC)
    results: list[dict[str, object]] = []

    async with SessionFactory() as session:
        async with session.begin():
            for row_id in selected_ids:
                if queue_key == "complaints":
                    item = await session.scalar(select(Complaint).where(Complaint.id == row_id))
                    if item is None:
                        results.append(
                            {
                                "id": row_id,
                                "ok": False,
                                "reason_code": "missing",
                                "message": "not found",
                            }
                        )
                        continue
                    if bulk_action == "resolve":
                        item.status = "RESOLVED"
                        item.resolved_by_user_id = actor_user_id
                        item.resolution_note = reason or "bulk resolve"
                        item.resolved_at = now
                        results.append({"id": row_id, "ok": True, "next_status": "RESOLVED"})
                    elif bulk_action == "dismiss":
                        item.status = "DISMISSED"
                        item.resolved_by_user_id = actor_user_id
                        item.resolution_note = reason or "bulk dismiss"
                        item.resolved_at = now
                        results.append({"id": row_id, "ok": True, "next_status": "DISMISSED"})
                    else:
                        results.append(
                            {
                                "id": row_id,
                                "ok": False,
                                "reason_code": "unsupported",
                                "message": "unsupported action",
                            }
                        )
                elif queue_key == "signals":
                    item = await session.scalar(select(FraudSignal).where(FraudSignal.id == row_id))
                    if item is None:
                        results.append(
                            {
                                "id": row_id,
                                "ok": False,
                                "reason_code": "missing",
                                "message": "not found",
                            }
                        )
                        continue
                    if bulk_action == "confirm":
                        item.status = "CONFIRMED"
                        item.resolved_by_user_id = actor_user_id
                        item.resolution_note = reason or "bulk confirm"
                        item.resolved_at = now
                        results.append({"id": row_id, "ok": True, "next_status": "CONFIRMED"})
                    elif bulk_action == "dismiss":
                        item.status = "DISMISSED"
                        item.resolved_by_user_id = actor_user_id
                        item.resolution_note = reason or "bulk dismiss"
                        item.resolved_at = now
                        results.append({"id": row_id, "ok": True, "next_status": "DISMISSED"})
                    else:
                        results.append(
                            {
                                "id": row_id,
                                "ok": False,
                                "reason_code": "unsupported",
                                "message": "unsupported action",
                            }
                        )
                elif queue_key == "trade_feedback":
                    if bulk_action not in {"hide", "unhide"}:
                        results.append(
                            {
                                "id": row_id,
                                "ok": False,
                                "reason_code": "unsupported",
                                "message": "unsupported action",
                            }
                        )
                        continue
                    action_result = await set_trade_feedback_visibility(
                        session,
                        feedback_id=row_id,
                        visible=bulk_action == "unhide",
                        moderator_user_id=actor_user_id,
                        note=reason or f"bulk {bulk_action}",
                    )
                    if action_result.ok:
                        next_status = "VISIBLE" if bulk_action == "unhide" else "HIDDEN"
                        results.append({"id": row_id, "ok": True, "next_status": next_status})
                    else:
                        results.append(
                            {
                                "id": row_id,
                                "ok": False,
                                "reason_code": "service_error",
                                "message": action_result.message,
                            }
                        )
                else:
                    if bulk_action not in {"in_review", "resolve", "reject"}:
                        results.append(
                            {
                                "id": row_id,
                                "ok": False,
                                "reason_code": "unsupported",
                                "message": "unsupported action",
                            }
                        )
                        continue
                    if bulk_action == "in_review":
                        action_result = await mark_appeal_in_review(
                            session,
                            appeal_id=row_id,
                            reviewer_user_id=actor_user_id,
                            note=reason or "bulk in review",
                        )
                    elif bulk_action == "resolve":
                        action_result = await resolve_appeal(
                            session,
                            appeal_id=row_id,
                            resolver_user_id=actor_user_id,
                            note=reason or "bulk resolve",
                        )
                    else:
                        action_result = await reject_appeal(
                            session,
                            appeal_id=row_id,
                            resolver_user_id=actor_user_id,
                            note=reason or "bulk reject",
                        )
                    if action_result.ok and action_result.appeal is not None:
                        results.append(
                            {
                                "id": row_id,
                                "ok": True,
                                "next_status": str(action_result.appeal.status),
                            }
                        )
                    else:
                        results.append(
                            {
                                "id": row_id,
                                "ok": False,
                                "reason_code": "service_error",
                                "message": action_result.message,
                            }
                        )

    return {"ok": True, "results": results}
