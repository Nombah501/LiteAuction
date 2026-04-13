from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.db.enums import (
    AppealSourceType,
    AppealStatus,
    ModerationAction,
)
from app.db.models import (
    Appeal,
    Bid,
    BlacklistEntry,
    Complaint,
    FraudSignal,
    ModerationLog,
    User,
)
from app.db.session import SessionFactory
from app.services.admin_queue_preset_telemetry_service import (
    load_workflow_preset_telemetry_segments,
)
from app.services.appeal_service import (
    mark_appeal_in_review,
    reject_appeal,
    resolve_appeal,
    resolve_appeal_auction_id,
)
from app.services.moderation_service import log_moderation_action
from app.services.queue_sla_health_service import (
    SLA_THRESHOLDS_BY_CONTEXT,
    decide_queue_sla_health,
)
from app.services.risk_eval_service import (
    UserRiskSnapshot,
    evaluate_user_risk_snapshot,
)
from app.services.verification_service import load_verified_user_ids
from app.web.auth import AdminAuthContext
from app.web.components import (
    _action_error_page,
    _append_timeline_event,
    _build_rationale_artifact,
    _fmt_ts,
    _load_dense_list_config,
    _pager_html,
    _render_app_header,
    _render_confirmation_page,
    _render_inline_timeline_html,
    _render_page,
    _render_workflow_preset_telemetry_panel,
    _risk_snapshot_inline_html,
    _safe_return_to,
    _triage_controls_cell,
    _triage_detail_row,
    _triage_row_context_attrs,
    _triage_shortcut_hint,
    _user_label,
)
from app.web.dense_list import render_dense_list_script, render_dense_list_toolbar
from app.web.deps import (
    _csrf_failed_response,
    _csrf_hidden_input,
    _is_confirmed,
    _path_with_auth,
    _require_scope_permission,
    _validate_csrf_token,
)
from app.web.filters import (
    _appeal_escalation_marker,
    _appeal_is_overdue,
    _appeal_sla_state_label,
    _appeal_source_label,
    _appeal_status_label,
    _parse_appeal_aging_bucket_filter,
    _parse_appeal_escalated_filter,
    _parse_appeal_overdue_filter,
    _parse_appeal_sla_health_filter,
    _parse_appeal_source_filter,
    _parse_appeal_status_filter,
)
from app.services.rbac_service import SCOPE_USER_BAN

router = APIRouter()


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


async def _resolve_actor_user_id(auth: AdminAuthContext) -> int:
    tg_user_id = auth.tg_user_id
    if tg_user_id is None:
        admin_ids = settings.parsed_admin_user_ids()
        if not admin_ids:
            from fastapi import HTTPException

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


async def _render_appeal_detail_section(
    session: AsyncSession,
    *,
    row_id: int,
    section: str,
    request: Request,
) -> dict[str, object]:
    appeal = await session.scalar(select(Appeal).where(Appeal.id == row_id))
    if appeal is None:
        return {"ok": False, "message": "Appeal not found"}

    appellant = await session.scalar(select(User).where(User.id == appeal.appellant_user_id))
    resolver = None
    if appeal.resolver_user_id is not None:
        resolver = await session.scalar(select(User).where(User.id == appeal.resolver_user_id))

    source_type = AppealSourceType(appeal.source_type)
    source_label = _appeal_source_label(source_type, appeal.source_id)

    related_auction_id = await resolve_appeal_auction_id(session, appeal)
    complaint: Complaint | None = None
    signal: FraudSignal | None = None
    if source_type == AppealSourceType.COMPLAINT and appeal.source_id is not None:
        complaint = await session.scalar(select(Complaint).where(Complaint.id == appeal.source_id))
    elif source_type == AppealSourceType.RISK and appeal.source_id is not None:
        signal = await session.scalar(select(FraudSignal).where(FraudSignal.id == appeal.source_id))

    moderation_logs = (
        (
            await session.execute(
                select(ModerationLog)
                .where(
                    ModerationLog.action.in_(
                        (ModerationAction.RESOLVE_APPEAL, ModerationAction.REJECT_APPEAL)
                    ),
                    ModerationLog.target_user_id == appeal.appellant_user_id,
                    ModerationLog.created_at >= appeal.created_at,
                )
                .order_by(ModerationLog.created_at.asc(), ModerationLog.id.asc())
            )
        )
        .scalars()
        .all()
    )

    filtered_logs: list[ModerationLog] = []
    for log_row in moderation_logs:
        payload = log_row.payload or {}
        payload_appeal_id = payload.get("appeal_id")
        if payload_appeal_id is None:
            continue
        try:
            if int(payload_appeal_id) != int(appeal.id):
                continue
        except (TypeError, ValueError):
            continue
        filtered_logs.append(log_row)

    actor_ids = {item.actor_user_id for item in filtered_logs}
    actors_by_id: dict[int, User] = {}
    if actor_ids:
        actors = (await session.execute(select(User).where(User.id.in_(actor_ids)))).scalars().all()
        actors_by_id = {item.id: item for item in actors}

    if section == "primary":
        timeline_events: list[tuple[datetime, str, str]] = []
        _append_timeline_event(
            timeline_events,
            happened_at=appeal.created_at,
            title="Appeal created",
            details=f"{source_label}, appellant={_user_label(appellant, appeal.appellant_user_id)}",
        )
        _append_timeline_event(
            timeline_events,
            happened_at=appeal.in_review_started_at,
            title="Moved to in-review",
            details=f"resolver={_user_label(resolver, appeal.resolver_user_id)}",
        )
        _append_timeline_event(
            timeline_events,
            happened_at=appeal.priority_boosted_at,
            title="Priority boosted",
            details=f"points={int(appeal.priority_boost_points_spent or 0)}",
        )
        _append_timeline_event(
            timeline_events,
            happened_at=appeal.escalated_at,
            title="Escalated",
            details=f"level=L{max(int(appeal.escalation_level or 0), 1)}",
        )
        _append_timeline_event(
            timeline_events,
            happened_at=appeal.resolved_at,
            title=f"Appeal finalized: {AppealStatus(appeal.status).value}",
            details=f"resolver={_user_label(resolver, appeal.resolver_user_id)}",
        )
        for log_row in filtered_logs:
            _append_timeline_event(
                timeline_events,
                happened_at=log_row.created_at,
                title=f"Moderation action: {str(log_row.action)}",
                details=(
                    "actor="
                    f"{_user_label(actors_by_id.get(log_row.actor_user_id), log_row.actor_user_id)}"
                ),
            )

        timeline_events.sort(key=lambda item: item[0])
        timeline_html = _render_inline_timeline_html(timeline_events)
        source_line = (
            f"<p><b>Evidence timeline:</b> appeal #{appeal.id} ({escape(source_label)})</p>"
        )
        auction_link = ""
        if related_auction_id is not None:
            auction_path = _path_with_auth(request, f"/timeline/auction/{related_auction_id}")
            auction_link = f"<p class='section-note'><a href='{escape(auction_path)}'>Open full auction timeline</a></p>"
        return {
            "ok": True,
            "html": (
                f"<div data-detail-state='loaded'>{source_line}{timeline_html}{auction_link}</div>"
            ),
        }

    if section == "secondary":
        source_bits: list[str] = [
            f"<p><b>Source evidence:</b> {escape(source_label)}</p>",
        ]
        if complaint is not None:
            source_bits.append(
                (
                    "<p class='section-note'>"
                    f"complaint #{complaint.id}, status={escape(str(complaint.status))}, created={escape(_fmt_ts(complaint.created_at))}"
                    "</p>"
                )
            )
            if complaint.resolved_at is not None:
                source_bits.append(
                    f"<p class='section-note'>complaint resolved={escape(_fmt_ts(complaint.resolved_at))}</p>"
                )
        if signal is not None:
            source_bits.append(
                (
                    "<p class='section-note'>"
                    f"signal #{signal.id}, status={escape(str(signal.status))}, "
                    f"score={signal.score}, created={escape(_fmt_ts(signal.created_at))}"
                    "</p>"
                )
            )
            if signal.resolved_at is not None:
                source_bits.append(
                    f"<p class='section-note'>signal resolved={escape(_fmt_ts(signal.resolved_at))}</p>"
                )

        artifact_items: list[str] = []
        for log_row in filtered_logs:
            payload = log_row.payload or {}
            artifact = payload.get("rationale_artifact") if isinstance(payload, dict) else None
            if not isinstance(artifact, dict):
                continue
            actor_label = _user_label(
                actors_by_id.get(log_row.actor_user_id), log_row.actor_user_id
            )
            summary = str(artifact.get("summary") or "-")
            recorded_at = str(artifact.get("recorded_at") or _fmt_ts(log_row.created_at))
            source_value = str(artifact.get("source") or "web")
            artifact_items.append(
                (
                    "<li>"
                    f"<b>{escape(str(log_row.action))}</b> - {escape(summary)}"
                    "<div class='section-note'>"
                    f"actor={escape(actor_label)}, "
                    f"recorded_at={escape(recorded_at)}, "
                    f"source={escape(source_value)}"
                    "</div>"
                    "</li>"
                )
            )
        if artifact_items:
            source_bits.append(
                f"<p><b>Rationale artifacts:</b></p><ul>{''.join(artifact_items)}</ul>"
            )
        else:
            source_bits.append("<p class='section-note'>No rationale artifacts yet.</p>")

        return {
            "ok": True,
            "html": f"<div data-detail-state='loaded'>{''.join(source_bits)}</div>",
        }

    audit_items = [
        f"<li>appeal_id={appeal.id}</li>",
        f"<li>created_at={escape(_fmt_ts(appeal.created_at))}</li>",
        f"<li>resolver={escape(_user_label(resolver, appeal.resolver_user_id))}</li>",
        f"<li>status={escape(str(appeal.status))}</li>",
        "<li>record_policy=append_only</li>",
    ]
    for log_row in filtered_logs:
        audit_items.append(
            (
                f"<li>log#{log_row.id} {escape(str(log_row.action))} "
                f"at {escape(_fmt_ts(log_row.created_at))} by "
                f"{escape(_user_label(actors_by_id.get(log_row.actor_user_id), log_row.actor_user_id))}</li>"
            )
        )
    html = (
        "<div data-detail-state='loaded'><p><b>Audit trail (immutable)</b></p>"
        f"<ul>{''.join(audit_items)}</ul></div>"
    )
    return {"ok": True, "html": html}


@router.get("/appeals", response_class=HTMLResponse)
async def appeals(
    request: Request,
    status: str = "open",
    source: str = "all",
    overdue: str = "all",
    escalated: str = "all",
    sla_health: str = "all",
    aging: str = "all",
    page: int = 0,
    q: str = "",
    density: str | None = None,
    telemetry_preset_id: int | None = None,
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    page = max(page, 0)
    page_size = 30
    offset = page * page_size
    query_value = q.strip()
    status_value = status.strip().lower()
    source_value = source.strip().lower()
    overdue_value = _parse_appeal_overdue_filter(overdue)
    escalated_value = _parse_appeal_escalated_filter(escalated)
    sla_health_value = _parse_appeal_sla_health_filter(sla_health)
    aging_value = _parse_appeal_aging_bucket_filter(aging)
    now = datetime.now(UTC)
    appeal_sla_thresholds = SLA_THRESHOLDS_BY_CONTEXT["appeals"]
    active_appeal_statuses = [AppealStatus.OPEN, AppealStatus.IN_REVIEW]
    overdue_clause = and_(
        Appeal.status.in_(active_appeal_statuses),
        Appeal.sla_deadline_at.is_not(None),
        Appeal.sla_deadline_at <= now,
    )

    status_filter = _parse_appeal_status_filter(status_value)
    source_filter = _parse_appeal_source_filter(source_value)

    resolver_user = aliased(User)
    stmt = (
        select(Appeal, User, resolver_user)
        .join(User, User.id == Appeal.appellant_user_id)
        .outerjoin(resolver_user, resolver_user.id == Appeal.resolver_user_id)
    )

    if status_filter is not None:
        stmt = stmt.where(Appeal.status == status_filter)
    if source_filter is not None:
        stmt = stmt.where(Appeal.source_type == source_filter)

    if overdue_value == "only":
        stmt = stmt.where(overdue_clause)
    elif overdue_value == "none":
        stmt = stmt.where(
            or_(
                Appeal.status.notin_(active_appeal_statuses),
                Appeal.sla_deadline_at.is_(None),
                Appeal.sla_deadline_at > now,
            )
        )

    warning_cutoff = now + appeal_sla_thresholds.warning_window
    critical_cutoff = now + appeal_sla_thresholds.critical_window
    if sla_health_value == "healthy":
        stmt = stmt.where(
            Appeal.status.in_(active_appeal_statuses),
            Appeal.sla_deadline_at.is_not(None),
            Appeal.sla_deadline_at > warning_cutoff,
        )
    elif sla_health_value == "warning":
        stmt = stmt.where(
            Appeal.status.in_(active_appeal_statuses),
            Appeal.sla_deadline_at.is_not(None),
            Appeal.sla_deadline_at > critical_cutoff,
            Appeal.sla_deadline_at <= warning_cutoff,
        )
    elif sla_health_value == "critical":
        stmt = stmt.where(
            Appeal.status.in_(active_appeal_statuses),
            Appeal.sla_deadline_at.is_not(None),
            Appeal.sla_deadline_at > now,
            Appeal.sla_deadline_at <= critical_cutoff,
        )
    elif sla_health_value == "overdue":
        stmt = stmt.where(overdue_clause)
    elif sla_health_value == "no_sla":
        stmt = stmt.where(
            Appeal.status.in_(active_appeal_statuses),
            Appeal.sla_deadline_at.is_(None),
        )

    fresh_cutoff = now - appeal_sla_thresholds.aging_fresh_max
    aging_cutoff = now - appeal_sla_thresholds.aging_aging_max
    stale_cutoff = now - appeal_sla_thresholds.aging_stale_max
    if aging_value == "fresh":
        stmt = stmt.where(Appeal.created_at.is_not(None), Appeal.created_at >= fresh_cutoff)
    elif aging_value == "aging":
        stmt = stmt.where(
            Appeal.created_at.is_not(None),
            Appeal.created_at < fresh_cutoff,
            Appeal.created_at >= aging_cutoff,
        )
    elif aging_value == "stale":
        stmt = stmt.where(
            Appeal.created_at.is_not(None),
            Appeal.created_at < aging_cutoff,
            Appeal.created_at >= stale_cutoff,
        )
    elif aging_value == "critical":
        stmt = stmt.where(Appeal.created_at.is_not(None), Appeal.created_at < stale_cutoff)
    elif aging_value == "overdue":
        stmt = stmt.where(overdue_clause)
    elif aging_value == "unknown":
        stmt = stmt.where(Appeal.created_at.is_(None))

    if escalated_value == "only":
        stmt = stmt.where(or_(Appeal.escalated_at.is_not(None), Appeal.escalation_level > 0))
    elif escalated_value == "none":
        stmt = stmt.where(Appeal.escalated_at.is_(None), Appeal.escalation_level <= 0)

    if query_value:
        if query_value.isdigit():
            q_int = int(query_value)
            stmt = stmt.where(
                or_(
                    Appeal.appeal_ref.ilike(f"%{query_value}%"),
                    Appeal.resolution_note.ilike(f"%{query_value}%"),
                    User.username.ilike(f"%{query_value}%"),
                    User.tg_user_id == q_int,
                    Appeal.id == q_int,
                    Appeal.source_id == q_int,
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    Appeal.appeal_ref.ilike(f"%{query_value}%"),
                    Appeal.resolution_note.ilike(f"%{query_value}%"),
                    User.username.ilike(f"%{query_value}%"),
                )
            )

    stmt = (
        stmt.order_by(
            Appeal.priority_boosted_at.desc().nullslast(),
            Appeal.created_at.desc(),
            Appeal.id.desc(),
        )
        .offset(offset)
        .limit(page_size + 1)
    )

    async with SessionFactory() as session:
        dense_config = await _load_dense_list_config(
            session,
            request=request,
            auth=auth,
            queue_key="appeals",
            requested_density=density,
            table_id="appeals-table",
            quick_filter_placeholder="id / ref / source / appellant / note",
        )
        telemetry_segments = await load_workflow_preset_telemetry_segments(
            session,
            queue_context="appeals",
            lookback_hours=24 * 7,
        )
        rows = (await session.execute(stmt)).all()
        has_next = len(rows) > page_size
        rows = rows[:page_size]
        appellant_risk_map = await _load_user_risk_snapshot_map(
            session,
            user_ids=[appellant.id for _, appellant, _ in rows],
            now=now,
        )

    base_query = {
        "status": status_value,
        "source": source_value,
        "overdue": overdue_value,
        "escalated": escalated_value,
        "sla_health": sla_health_value,
        "aging": aging_value,
        "q": query_value,
        "density": dense_config.density,
    }
    if telemetry_preset_id is not None and telemetry_preset_id > 0:
        base_query["telemetry_preset_id"] = str(telemetry_preset_id)

    def _appeals_path(
        *,
        page_value: int,
        status_filter: str | None = None,
        source_filter_value: str | None = None,
        overdue_filter: str | None = None,
        escalated_filter: str | None = None,
        sla_health_filter: str | None = None,
        aging_filter: str | None = None,
        query_filter: str | None = None,
        density_value: str | None = None,
        telemetry_preset_id_value: int | None = telemetry_preset_id,
    ) -> str:
        query = dict(base_query)
        query.update(
            {
                "status": status_filter or status_value,
                "source": source_filter_value or source_value,
                "overdue": overdue_filter or overdue_value,
                "escalated": escalated_filter or escalated_value,
                "sla_health": sla_health_filter or sla_health_value,
                "aging": aging_filter or aging_value,
                "q": query_value if query_filter is None else query_filter,
                "page": str(page_value),
                "density": density_value or dense_config.density,
            }
        )
        if telemetry_preset_id_value is None:
            query.pop("telemetry_preset_id", None)
        elif telemetry_preset_id_value > 0:
            query["telemetry_preset_id"] = str(telemetry_preset_id_value)
        return f"/appeals?{urlencode(query)}"

    return_to = _appeals_path(page_value=page)
    csrf_input = _csrf_hidden_input(request, auth)
    table_rows = ""
    default_risk_snapshot = evaluate_user_risk_snapshot(
        complaints_against=0,
        open_fraud_signals=0,
        has_active_blacklist=False,
        removed_bids=0,
    )

    for appeal, appellant, resolver in rows:
        source_label = _appeal_source_label(AppealSourceType(appeal.source_type), appeal.source_id)
        appellant_label = (
            f"@{appellant.username}" if appellant.username else str(appellant.tg_user_id)
        )
        appellant_risk = appellant_risk_map.get(appellant.id, default_risk_snapshot)
        resolver_label = "-"
        if resolver is not None:
            resolver_label = (
                f"@{resolver.username}" if resolver.username else str(resolver.tg_user_id)
            )

        actions = "-"
        appeal_status = AppealStatus(appeal.status)
        is_overdue = _appeal_is_overdue(appeal, now=now)
        status_label = _appeal_status_label(appeal_status)
        if is_overdue:
            status_label += " ⏰"
        if appeal.escalated_at is not None or int(appeal.escalation_level or 0) > 0:
            status_label += " ⚠"
        if appeal.priority_boosted_at is not None:
            status_label += " ⚡"
        sla_state_label = _appeal_sla_state_label(appeal, now=now)
        sla_decision = decide_queue_sla_health(
            queue_context="appeals",
            status=appeal.status,
            created_at=appeal.created_at,
            deadline_at=appeal.sla_deadline_at,
            now=now,
        )
        health_hint = f"SLA:{sla_decision.health_state} | age:{sla_decision.aging_bucket}"
        escalation_marker = _appeal_escalation_marker(appeal)
        appeal_priority = "normal"
        if appeal.priority_boosted_at is not None:
            appeal_priority = "urgent"
        elif int(appeal.escalation_level or 0) > 0 or is_overdue:
            appeal_priority = "high"
        elif appeal_status in {AppealStatus.OPEN, AppealStatus.IN_REVIEW}:
            appeal_priority = "high"
        row_context_attrs = _triage_row_context_attrs(
            risk_level=str(appellant_risk.level),
            priority_level=appeal_priority,
        )
        if appeal_status in {AppealStatus.OPEN, AppealStatus.IN_REVIEW}:
            action_forms: list[str] = []
            if appeal_status == AppealStatus.OPEN:
                action_forms.append(
                    f"<form method='post' action='{escape(_path_with_auth(request, '/actions/appeal/review'))}' style='display:inline-block;margin-right:6px'>"
                    f"<input type='hidden' name='appeal_id' value='{appeal.id}'>"
                    f"<input type='hidden' name='return_to' value='{escape(return_to)}'>"
                    f"{csrf_input}"
                    "<input name='reason' placeholder='Комментарий' style='width:130px' required>"
                    "<button type='submit'>В работу</button></form>"
                )

            action_forms.append(
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/appeal/resolve'))}' style='display:inline-block;margin-right:6px'>"
                f"<input type='hidden' name='appeal_id' value='{appeal.id}'>"
                f"<input type='hidden' name='return_to' value='{escape(return_to)}'>"
                f"{csrf_input}"
                "<input name='reason' placeholder='Причина' style='width:130px' required>"
                "<button type='submit'>Удовлетворить</button></form>"
            )
            action_forms.append(
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/appeal/reject'))}' style='display:inline-block'>"
                f"<input type='hidden' name='appeal_id' value='{appeal.id}'>"
                f"<input type='hidden' name='return_to' value='{escape(return_to)}'>"
                f"{csrf_input}"
                "<input name='reason' placeholder='Причина' style='width:130px' required>"
                "<button type='submit'>Отклонить</button></form>"
            )
            actions = "".join(action_forms)

        table_rows += (
            f"<tr data-row='{escape(f'{appeal.id} {appeal.appeal_ref} {source_label} {appellant_label} {appeal.status} {appeal.resolution_note or ""}')}' "
            f"data-triage-row='1' data-row-id='{appeal.id}' tabindex='0'{row_context_attrs}>"
            f"<td>{_triage_controls_cell(appeal.id)}</td>"
            f"<td data-col='id'>{appeal.id}</td>"
            f"<td data-col='reference'>{escape(appeal.appeal_ref)}</td>"
            f"<td data-col='source'>{escape(source_label)}</td>"
            f"<td data-col='appellant'><a href='{escape(_path_with_auth(request, f'/manage/user/{appellant.id}'))}'>{escape(appellant_label)}</a></td>"
            f"<td data-col='risk'>{_risk_snapshot_inline_html(appellant_risk)}</td>"
            f"<td data-col='status' data-status-cell='1'>{escape(status_label)}</td>"
            f"<td data-col='resolution'>{escape((appeal.resolution_note or '-')[:160])}</td>"
            f"<td data-col='moderator'>{escape(resolver_label)}</td>"
            f"<td data-col='created'>{escape(_fmt_ts(appeal.created_at))}</td>"
            f"<td data-col='sla' data-sla-health='{escape(sla_decision.health_state)}' data-aging-bucket='{escape(sla_decision.aging_bucket)}'>{escape(sla_state_label)}<br><small>{escape(health_hint)}</small></td>"
            f"<td data-col='deadline'>{escape(_fmt_ts(appeal.sla_deadline_at))}</td>"
            f"<td data-col='escalation'>{escape(escalation_marker)}</td>"
            f"<td data-col='closed'>{escape(_fmt_ts(appeal.resolved_at))}</td>"
            f"<td data-col='actions'>{actions}</td>"
            "</tr>"
        )
        table_rows += _triage_detail_row(
            appeal.id,
            col_count=15,
            title=f"Appeal #{appeal.id}",
            subtitle=f"Ref {appeal.appeal_ref} / {source_label}",
        )

    if not table_rows:
        table_rows = "<tr><td colspan='15'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _appeals_path(page_value=page - 1)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _appeals_path(page_value=page + 1)))}'>Вперед →</a>"
        if has_next
        else ""
    )
    dense_toolbar = render_dense_list_toolbar(
        dense_config,
        density_query_builder=lambda value: _path_with_auth(
            request,
            _appeals_path(page_value=0, density_value=value),
        ),
    )
    telemetry_panel = _render_workflow_preset_telemetry_panel(
        request,
        queue_context="appeals",
        segments=telemetry_segments,
        selected_preset_id=telemetry_preset_id,
        preset_filter_path_builder=lambda preset_id: _appeals_path(
            page_value=page,
            telemetry_preset_id_value=preset_id,
        ),
    )

    body = (
        f"{_render_app_header('Апелляции', auth, 'SLA, эскалации и решения по спорным кейсам')}"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a>"
        f"<a href='{escape(_path_with_auth(request, '/violators?status=active'))}'>К нарушителям</a></p>"
        f"{dense_toolbar}"
        f"{telemetry_panel}"
        f"{_triage_shortcut_hint()}"
        "<div class='toolbar'>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/appeals'))}'>"
        f"<input type='hidden' name='status' value='{escape(status_value)}'>"
        f"<input type='hidden' name='source' value='{escape(source_value)}'>"
        f"<input type='hidden' name='overdue' value='{escape(overdue_value)}'>"
        f"<input type='hidden' name='escalated' value='{escape(escalated_value)}'>"
        f"<input type='hidden' name='sla_health' value='{escape(sla_health_value)}'>"
        f"<input type='hidden' name='aging' value='{escape(aging_value)}'>"
        f"<input type='hidden' name='density' value='{escape(dense_config.density)}'>"
        f"<input type='hidden' name='telemetry_preset_id' value='{escape(str(telemetry_preset_id) if telemetry_preset_id else '')}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='референс / tg id / username' style='width:300px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        "</div>"
        "<div class='stack-rows'>"
        f"<div class='toolbar'><span>Статус:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, status_filter='open')))}'>Открытые</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, status_filter='in_review')))}'>На рассмотрении</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, status_filter='resolved')))}'>Удовлетворенные</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, status_filter='rejected')))}'>Отклоненные</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, status_filter='all')))}'>Все</a></div>"
        f"<div class='toolbar'><span>Источник:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, source_filter_value='complaint')))}'>Жалобы</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, source_filter_value='risk')))}'>Фрод</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, source_filter_value='manual')))}'>Ручные</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, source_filter_value='all')))}'>Все</a></div>"
        f"<div class='toolbar'><span>SLA:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, overdue_filter='all')))}'>Все</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, overdue_filter='only')))}'>Просроченные</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, overdue_filter='none')))}'>Непросроченные</a></div>"
        f"<div class='toolbar'><span>SLA health:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, sla_health_filter='all')))}'>Все</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, sla_health_filter='healthy')))}'>В норме</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, sla_health_filter='warning')))}'>Внимание</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, sla_health_filter='critical')))}'>Критично</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, sla_health_filter='overdue')))}'>Просрочена</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, sla_health_filter='no_sla')))}'>Без SLA</a></div>"
        f"<div class='toolbar'><span>Возраст:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, aging_filter='all')))}'>Все</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, aging_filter='fresh')))}'>Свежие</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, aging_filter='aging')))}'>Aging</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, aging_filter='stale')))}'>Stale</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, aging_filter='critical')))}'>Critical</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, aging_filter='overdue')))}'>Overdue</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, aging_filter='unknown')))}'>Unknown</a></div>"
        f"<div class='toolbar'><span>Эскалация:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, escalated_filter='all')))}'>Все</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, escalated_filter='only')))}'>Эскалированные</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _appeals_path(page_value=0, escalated_filter='none')))}'>Без эскалации</a></div>"
        "</div>"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th>Pick</th><th data-col='id'>ID</th><th data-col='reference'>Референс</th><th data-col='source'>Источник</th><th data-col='appellant'>Апеллянт</th><th data-col='risk'>Риск апеллянта</th><th data-col='status'>Статус</th><th data-col='resolution'>Решение</th><th data-col='moderator'>Модератор</th><th data-col='created'>Создано</th><th data-col='sla'>SLA статус</th><th data-col='deadline'>SLA дедлайн</th><th data-col='escalation'>Эскалация</th><th data-col='closed'>Закрыто</th><th data-col='actions'>Действия</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(_render_page("Апелляции", body))


@router.post("/actions/appeal/resolve")
async def action_resolve_appeal(
    request: Request,
    appeal_id: int = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    confirmed: str | None = Form(None),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/appeals?status=open&source=all")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    if not _is_confirmed(confirmed):
        return _render_confirmation_page(
            request,
            auth,
            title="Подтверждение решения апелляции",
            message=f"Вы действительно хотите удовлетворить апелляцию #{appeal_id}?",
            action_path="/actions/appeal/resolve",
            fields={
                "appeal_id": str(appeal_id),
                "reason": reason,
                "return_to": target,
            },
            back_to=target,
        )

    actor_user_id = await _resolve_actor_user_id(auth)
    async with SessionFactory() as session:
        async with session.begin():
            result = await resolve_appeal(
                session,
                appeal_id=appeal_id,
                resolver_user_id=actor_user_id,
                note=f"[web] {reason}",
            )
            if result.ok and result.appeal is not None:
                related_auction_id = await resolve_appeal_auction_id(session, result.appeal)
                rationale_artifact = _build_rationale_artifact(
                    summary=reason,
                    actor_user_id=actor_user_id,
                    actor_tg_user_id=auth.tg_user_id,
                    source="web.appeals.resolve",
                    happened_at=datetime.now(UTC),
                )
                await log_moderation_action(
                    session,
                    actor_user_id=actor_user_id,
                    action=ModerationAction.RESOLVE_APPEAL,
                    reason=result.appeal.resolution_note or f"[web] {reason}",
                    target_user_id=result.appeal.appellant_user_id,
                    auction_id=related_auction_id,
                    payload={
                        "appeal_id": result.appeal.id,
                        "appeal_ref": result.appeal.appeal_ref,
                        "source_type": result.appeal.source_type,
                        "source_id": result.appeal.source_id,
                        "rationale_artifact": rationale_artifact,
                    },
                )

    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@router.post("/actions/appeal/review")
async def action_review_appeal(
    request: Request,
    appeal_id: int = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/appeals?status=open&source=all")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)
    async with SessionFactory() as session:
        async with session.begin():
            result = await mark_appeal_in_review(
                session,
                appeal_id=appeal_id,
                reviewer_user_id=actor_user_id,
                note=f"[web-review] {reason}",
            )

    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@router.post("/actions/appeal/reject")
async def action_reject_appeal(
    request: Request,
    appeal_id: int = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    confirmed: str | None = Form(None),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/appeals?status=open&source=all")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    if not _is_confirmed(confirmed):
        return _render_confirmation_page(
            request,
            auth,
            title="Подтверждение отклонения апелляции",
            message=f"Вы действительно хотите отклонить апелляцию #{appeal_id}?",
            action_path="/actions/appeal/reject",
            fields={
                "appeal_id": str(appeal_id),
                "reason": reason,
                "return_to": target,
            },
            back_to=target,
        )

    actor_user_id = await _resolve_actor_user_id(auth)
    async with SessionFactory() as session:
        async with session.begin():
            result = await reject_appeal(
                session,
                appeal_id=appeal_id,
                resolver_user_id=actor_user_id,
                note=f"[web] {reason}",
            )
            if result.ok and result.appeal is not None:
                related_auction_id = await resolve_appeal_auction_id(session, result.appeal)
                rationale_artifact = _build_rationale_artifact(
                    summary=reason,
                    actor_user_id=actor_user_id,
                    actor_tg_user_id=auth.tg_user_id,
                    source="web.appeals.reject",
                    happened_at=datetime.now(UTC),
                )
                await log_moderation_action(
                    session,
                    actor_user_id=actor_user_id,
                    action=ModerationAction.REJECT_APPEAL,
                    reason=result.appeal.resolution_note or f"[web] {reason}",
                    target_user_id=result.appeal.appellant_user_id,
                    auction_id=related_auction_id,
                    payload={
                        "appeal_id": result.appeal.id,
                        "appeal_ref": result.appeal.appeal_ref,
                        "source_type": result.appeal.source_type,
                        "source_id": result.appeal.source_id,
                        "rationale_artifact": rationale_artifact,
                    },
                )

    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)
