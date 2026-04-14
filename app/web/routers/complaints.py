from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Complaint, ModerationLog, User
from app.db.session import SessionFactory
from app.services.admin_queue_preset_telemetry_service import (
    load_workflow_preset_telemetry_segments,
)
from app.services.complaint_service import list_complaints
from app.services.queue_sla_health_service import (
    SLA_THRESHOLDS_BY_CONTEXT,
    decide_queue_sla_health,
)
from app.web.components import (
    _fmt_ts,
    _append_timeline_event,
    _render_app_header,
    _render_inline_timeline_html,
    _load_dense_list_config,
    _pager_html,
    _path_with_auth,
    _render_workflow_preset_telemetry_panel,
    _triage_controls_cell,
    _triage_detail_row,
    _triage_row_context_attrs,
    _triage_shortcut_hint,
    _user_label,
    render_template,
)
from app.web.dense_list import render_dense_list_script, render_dense_list_toolbar
from app.web.deps import _auth_context_or_unauthorized
from app.web.filters import (
    _parse_complaint_aging_bucket_filter,
    _parse_complaint_sla_health_filter,
)

router = APIRouter()


async def _render_complaint_detail_section(
    session: AsyncSession,
    *,
    row_id: int,
    section: str,
    request: Request,
) -> dict[str, object]:
    complaint = await session.scalar(select(Complaint).where(Complaint.id == row_id))
    if complaint is None:
        return {"ok": False, "message": "Complaint not found"}

    reporter = await session.scalar(select(User).where(User.id == complaint.reporter_user_id))
    target = None
    if complaint.target_user_id is not None:
        target = await session.scalar(select(User).where(User.id == complaint.target_user_id))
    resolver = None
    if complaint.resolved_by_user_id is not None:
        resolver = await session.scalar(
            select(User).where(User.id == complaint.resolved_by_user_id)
        )

    mod_logs = (
        (
            await session.execute(
                select(ModerationLog)
                .where(
                    ModerationLog.auction_id == complaint.auction_id,
                    ModerationLog.created_at >= complaint.created_at,
                )
                .order_by(ModerationLog.created_at.asc(), ModerationLog.id.asc())
            )
        )
        .scalars()
        .all()
    )

    actor_ids = {log_row.actor_user_id for log_row in mod_logs}
    actors_by_id: dict[int, User] = {}
    if actor_ids:
        actors = (await session.execute(select(User).where(User.id.in_(actor_ids)))).scalars().all()
        actors_by_id = {item.id: item for item in actors}

    if section == "primary":
        timeline_events: list[tuple[datetime, str, str]] = []
        _append_timeline_event(
            timeline_events,
            happened_at=complaint.created_at,
            title="Complaint created",
            details=f"reporter={_user_label(reporter, complaint.reporter_user_id)}, target={_user_label(target, complaint.target_user_id)}, reason={complaint.reason[:120]}",
        )
        _append_timeline_event(
            timeline_events,
            happened_at=complaint.resolved_at,
            title=f"Complaint finalized: {complaint.status}",
            details=f"resolver={_user_label(resolver, complaint.resolved_by_user_id)}, note={complaint.resolution_note or '-'}",
        )
        for log_row in mod_logs:
            _append_timeline_event(
                timeline_events,
                happened_at=log_row.created_at,
                title=f"Moderation action: {str(log_row.action)}",
                details=f"actor={_user_label(actors_by_id.get(log_row.actor_user_id), log_row.actor_user_id)}",
            )

        timeline_events.sort(key=lambda item: item[0])
        timeline_html = _render_inline_timeline_html(timeline_events)
        source_line = f"<p><b>Evidence timeline:</b> complaint #{complaint.id}</p>"
        auction_link = f"<p class='section-note'><a href='{escape(_path_with_auth(request, f'/timeline/auction/{complaint.auction_id}'))}'>Open full auction timeline</a></p>"
        return {
            "ok": True,
            "html": f"<div data-detail-state='loaded'>{source_line}{timeline_html}{auction_link}</div>",
        }

    if section == "secondary":
        source_bits: list[str] = [
            f"<p><b>Source evidence:</b> complaint #{complaint.id}, status={escape(str(complaint.status))}</p>",
            f"<p class='section-note'>auction={escape(str(complaint.auction_id))}, reporter={escape(_user_label(reporter, complaint.reporter_user_id))}, target={escape(_user_label(target, complaint.target_user_id))}</p>",
        ]
        if complaint.resolution_note:
            source_bits.append(
                f"<p class='section-note'>resolution={escape(complaint.resolution_note[:200])}</p>"
            )

        artifact_items: list[str] = []
        for log_row in mod_logs:
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
                f"<li><b>{escape(str(log_row.action))}</b> - {escape(summary)}"
                f"<div class='section-note'>actor={escape(actor_label)}, recorded_at={escape(recorded_at)}, source={escape(source_value)}</div></li>"
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
        f"<li>complaint_id={complaint.id}</li>",
        f"<li>created_at={escape(_fmt_ts(complaint.created_at))}</li>",
        f"<li>status={escape(str(complaint.status))}</li>",
        f"<li>resolver={escape(_user_label(resolver, complaint.resolved_by_user_id))}</li>",
        "<li>record_policy=append_only</li>",
    ]
    for log_row in mod_logs:
        audit_items.append(
            f"<li>log#{log_row.id} {escape(str(log_row.action))} at {escape(_fmt_ts(log_row.created_at))} by {escape(_user_label(actors_by_id.get(log_row.actor_user_id), log_row.actor_user_id))}</li>"
        )
    html = f"<div data-detail-state='loaded'><p><b>Audit trail (immutable)</b></p><ul>{''.join(audit_items)}</ul></div>"
    return {"ok": True, "html": html}


@router.get("/complaints", response_class=HTMLResponse)
async def complaints(
    request: Request,
    status: str = "OPEN",
    page: int = 0,
    density: str | None = None,
    telemetry_preset_id: int | None = None,
    sla_health: str = "all",
    aging: str = "all",
) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response
    page = max(page, 0)
    sla_health_value = _parse_complaint_sla_health_filter(sla_health)
    aging_value = _parse_complaint_aging_bucket_filter(aging)
    now = datetime.now(UTC)
    complaint_sla_thresholds = SLA_THRESHOLDS_BY_CONTEXT["moderation"]
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
        dense_config = await _load_dense_list_config(
            session,
            request=request,
            auth=auth,
            queue_key="complaints",
            requested_density=density,
            table_id="complaints-table",
            quick_filter_placeholder="id / auction / reporter / reason",
        )
        telemetry_segments = await load_workflow_preset_telemetry_segments(
            session,
            queue_context="moderation",
            lookback_hours=24 * 7,
        )

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    def _complaints_path(
        *,
        page_value: int,
        status_value: str,
        density_value: str | None = None,
        telemetry_preset_id_value: int | None = telemetry_preset_id,
        sla_health_value: str | None = sla_health_value,
        aging_value: str | None = aging_value,
    ) -> str:
        query = {
            "status": status_value,
            "page": str(page_value),
            "density": density_value or dense_config.density,
            "sla_health": sla_health_value or "all",
            "aging": aging_value or "all",
        }
        if telemetry_preset_id_value is not None and telemetry_preset_id_value > 0:
            query["telemetry_preset_id"] = str(telemetry_preset_id_value)
        return f"/complaints?{urlencode(query)}"

    table_rows = ""
    for item in rows:
        complaint_priority = "high" if str(item.status).upper() == "OPEN" else "normal"
        complaint_deadline = (
            item.created_at + complaint_sla_thresholds.warning_window if item.created_at else None
        )
        sla_decision = decide_queue_sla_health(
            queue_context="moderation",
            status=item.status,
            created_at=item.created_at,
            deadline_at=complaint_deadline,
            now=now,
        )
        if sla_decision.health_state in ("critical", "overdue"):
            complaint_priority = "urgent"
        sla_hint = f"SLA:{sla_decision.health_state} | age:{sla_decision.aging_bucket}"
        row_context_attrs = _triage_row_context_attrs(
            risk_level="low", priority_level=complaint_priority
        )
        table_rows += (
            f"<tr data-row='{escape(f'{item.id} {item.auction_id} {item.reporter_user_id} {item.status} {item.reason}')}' "
            f"data-triage-row='1' data-row-id='{item.id}' tabindex='0'{row_context_attrs}>"
            f"<td>{_triage_controls_cell(item.id)}</td>"
            f"<td data-col='id'>{item.id}</td>"
            f"<td data-col='auction'><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.auction_id}'))}'>{escape(str(item.auction_id))}</a></td>"
            f"<td data-col='reporter'><a href='{escape(_path_with_auth(request, f'/manage/user/{item.reporter_user_id}'))}'>{item.reporter_user_id}</a></td>"
            f"<td data-col='status' data-status-cell='1'>{escape(item.status)}</td>"
            f"<td data-col='reason'>{escape(item.reason[:120])}</td>"
            f"<td data-col='created'>{escape(_fmt_ts(item.created_at))}</td>"
            f"<td data-col='sla' data-sla-health='{escape(sla_decision.health_state)}' data-aging-bucket='{escape(sla_decision.aging_bucket)}'><small>{escape(sla_hint)}</small></td>"
            f"<td data-col='deadline'>{escape(_fmt_ts(complaint_deadline))}</td>"
            "</tr>"
        )
        table_rows += _triage_detail_row(
            item.id,
            col_count=9,
            title=f"Complaint #{item.id}",
            subtitle=f"Auction {item.auction_id} / reporter {item.reporter_user_id}",
        )
    if not table_rows:
        table_rows = "<tr><td colspan='9'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _complaints_path(page_value=page - 1, status_value=status)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _complaints_path(page_value=page + 1, status_value=status)))}'>Вперед →</a>"
        if has_next
        else ""
    )
    status_open_path = _complaints_path(page_value=0, status_value="OPEN")
    status_resolved_path = _complaints_path(page_value=0, status_value="RESOLVED")
    dense_toolbar = render_dense_list_toolbar(
        dense_config,
        density_query_builder=lambda value: _path_with_auth(
            request,
            _complaints_path(page_value=0, status_value=status, density_value=value),
        ),
    )
    telemetry_panel = _render_workflow_preset_telemetry_panel(
        request,
        queue_context="moderation",
        segments=telemetry_segments,
        selected_preset_id=telemetry_preset_id,
        preset_filter_path_builder=lambda preset_id: _complaints_path(
            page_value=page,
            status_value=status,
            telemetry_preset_id_value=preset_id,
        ),
    )

    body = (
        f"{_render_app_header('Жалобы', auth, f'Статус: {status}')}"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"{dense_toolbar}"
        f"{telemetry_panel}"
        f"{_triage_shortcut_hint()}"
        "<div class='toolbar'>"
        f"<a class='chip' href='{escape(_path_with_auth(request, status_open_path))}'>OPEN</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, status_resolved_path))}'>RESOLVED</a>"
        "</div>"
        "<div class='toolbar'><span>SLA health:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _complaints_path(page_value=0, status_value=status, sla_health_value='all')))}'>Все</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _complaints_path(page_value=0, status_value=status, sla_health_value='healthy')))}'>В норме</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _complaints_path(page_value=0, status_value=status, sla_health_value='warning')))}'>Внимание</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _complaints_path(page_value=0, status_value=status, sla_health_value='critical')))}'>Критично</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _complaints_path(page_value=0, status_value=status, sla_health_value='overdue')))}'>Просрочена</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _complaints_path(page_value=0, status_value=status, sla_health_value='no_sla')))}'>Без SLA</a></div>"
        "<div class='toolbar'><span>Возраст:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _complaints_path(page_value=0, status_value=status, aging_value='all')))}'>Все</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _complaints_path(page_value=0, status_value=status, aging_value='fresh')))}'>Свежие</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _complaints_path(page_value=0, status_value=status, aging_value='aging')))}'>Aging</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _complaints_path(page_value=0, status_value=status, aging_value='stale')))}'>Stale</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _complaints_path(page_value=0, status_value=status, aging_value='critical')))}'>Critical</a></div>"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th>Pick</th><th data-col='id'>ID</th><th data-col='auction'>Auction</th><th data-col='reporter'>Reporter UID</th><th data-col='status'>Status</th><th data-col='reason'>Reason</th><th data-col='created'>Created</th><th data-col='sla'>SLA</th><th data-col='deadline'>Deadline</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(render_template("complaints.html", title="Complaints", body=body))
