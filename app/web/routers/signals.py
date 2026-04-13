from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app.db.models import (
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
from app.services.fraud_service import list_fraud_signals
from app.services.queue_sla_health_service import (
    SLA_THRESHOLDS_BY_CONTEXT,
    decide_queue_sla_health,
)
from app.services.risk_eval_service import (
    UserRiskSnapshot,
    evaluate_user_risk_snapshot,
)
from app.services.verification_service import load_verified_user_ids
from app.web.components import (
    _append_timeline_event,
    _fmt_ts,
    _load_dense_list_config,
    _pager_html,
    _render_app_header,
    _render_inline_timeline_html,
    _render_page,
    _render_workflow_preset_telemetry_panel,
    _risk_snapshot_inline_html,
    _triage_controls_cell,
    _triage_detail_row,
    _triage_row_context_attrs,
    _triage_shortcut_hint,
    _user_label,
)
from app.web.deps import _auth_context_or_unauthorized, _path_with_auth
from app.web.dense_list import render_dense_list_script, render_dense_list_toolbar
from app.web.filters import _parse_signal_aging_bucket_filter, _parse_signal_sla_health_filter

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


async def _render_signal_detail_section(
    session,
    *,
    row_id: int,
    section: str,
    request: Request,
) -> dict[str, object]:
    signal = await session.scalar(select(FraudSignal).where(FraudSignal.id == row_id))
    if signal is None:
        return {"ok": False, "message": "Signal not found"}

    signal_user = await session.scalar(select(User).where(User.id == signal.user_id))
    resolver = None
    if signal.resolved_by_user_id is not None:
        resolver = await session.scalar(select(User).where(User.id == signal.resolved_by_user_id))

    mod_logs = (
        (
            await session.execute(
                select(ModerationLog)
                .where(
                    ModerationLog.auction_id == signal.auction_id,
                    ModerationLog.created_at >= signal.created_at,
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
            happened_at=signal.created_at,
            title="Fraud signal created",
            details=f"user={_user_label(signal_user, signal.user_id)}, score={signal.score}, status={signal.status}",
        )
        _append_timeline_event(
            timeline_events,
            happened_at=signal.resolved_at,
            title=f"Signal finalized: {signal.status}",
            details=f"resolver={_user_label(resolver, signal.resolved_by_user_id)}, note={signal.resolution_note or '-'}",
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
        source_line = f"<p><b>Evidence timeline:</b> fraud signal #{signal.id}</p>"
        auction_link = f"<p class='section-note'><a href='{escape(_path_with_auth(request, f'/timeline/auction/{signal.auction_id}'))}'>Open full auction timeline</a></p>"
        return {
            "ok": True,
            "html": f"<div data-detail-state='loaded'>{source_line}{timeline_html}{auction_link}</div>",
        }

    if section == "secondary":
        source_bits: list[str] = [
            f"<p><b>Source evidence:</b> signal #{signal.id}, score={signal.score}, status={escape(str(signal.status))}</p>",
            f"<p class='section-note'>auction={escape(str(signal.auction_id))}, user={escape(_user_label(signal_user, signal.user_id))}</p>",
        ]
        if signal.resolution_note:
            source_bits.append(
                f"<p class='section-note'>resolution={escape(signal.resolution_note[:200])}</p>"
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
        f"<li>signal_id={signal.id}</li>",
        f"<li>created_at={escape(_fmt_ts(signal.created_at))}</li>",
        f"<li>score={signal.score}</li>",
        f"<li>status={escape(str(signal.status))}</li>",
        f"<li>resolver={escape(_user_label(resolver, signal.resolved_by_user_id))}</li>",
        "<li>record_policy=append_only</li>",
    ]
    for log_row in mod_logs:
        audit_items.append(
            f"<li>log#{log_row.id} {escape(str(log_row.action))} at {escape(_fmt_ts(log_row.created_at))} by {escape(_user_label(actors_by_id.get(log_row.actor_user_id), log_row.actor_user_id))}</li>"
        )
    html = f"<div data-detail-state='loaded'><p><b>Audit trail (immutable)</b></p><ul>{''.join(audit_items)}</ul></div>"
    return {"ok": True, "html": html}


@router.get("/signals", response_class=HTMLResponse)
async def signals(
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
    sla_health_value = _parse_signal_sla_health_filter(sla_health)
    aging_value = _parse_signal_aging_bucket_filter(aging)
    now = datetime.now(UTC)
    signal_sla_thresholds = SLA_THRESHOLDS_BY_CONTEXT["risk"]
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
        risk_by_user_id = await _load_user_risk_snapshot_map(
            session,
            user_ids=[item.user_id for item in rows],
        )
        dense_config = await _load_dense_list_config(
            session,
            request=request,
            auth=auth,
            queue_key="signals",
            requested_density=density,
            table_id="signals-table",
            quick_filter_placeholder="id / auction / user / status",
        )
        telemetry_segments = await load_workflow_preset_telemetry_segments(
            session,
            queue_context="risk",
            lookback_hours=24 * 7,
        )

    default_risk_snapshot = evaluate_user_risk_snapshot(
        complaints_against=0,
        open_fraud_signals=0,
        has_active_blacklist=False,
        removed_bids=0,
    )

    def _signals_path(
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
        return f"/signals?{urlencode(query)}"

    table_rows = ""
    for item in rows:
        user_risk = risk_by_user_id.get(item.user_id, default_risk_snapshot)
        signal_risk_level = str(user_risk.level).strip().lower() or "low"
        signal_priority = "normal"
        if int(item.score) >= 80:
            signal_priority = "urgent"
        elif int(item.score) >= 50:
            signal_priority = "high"
        signal_deadline = (
            item.created_at + signal_sla_thresholds.warning_window if item.created_at else None
        )
        sla_decision = decide_queue_sla_health(
            queue_context="risk",
            status=item.status,
            created_at=item.created_at,
            deadline_at=signal_deadline,
            now=now,
        )
        if sla_decision.health_state in ("critical", "overdue"):
            signal_priority = "urgent"
        sla_hint = f"SLA:{sla_decision.health_state} | age:{sla_decision.aging_bucket}"
        row_context_attrs = _triage_row_context_attrs(
            risk_level=signal_risk_level,
            priority_level=signal_priority,
        )
        table_rows += (
            f"<tr data-row='{escape(f'{item.id} {item.auction_id} {item.user_id} {item.status} {item.score}')}' "
            f"data-triage-row='1' data-row-id='{item.id}' tabindex='0'{row_context_attrs}>"
            f"<td>{_triage_controls_cell(item.id)}</td>"
            f"<td data-col='id'>{item.id}</td>"
            f"<td data-col='auction'><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.auction_id}'))}'>{escape(str(item.auction_id))}</a></td>"
            f"<td data-col='user'><a href='{escape(_path_with_auth(request, f'/manage/user/{item.user_id}'))}'>{item.user_id}</a></td>"
            f"<td data-col='risk'>{_risk_snapshot_inline_html(user_risk)}</td>"
            f"<td data-col='score'>{item.score}</td>"
            f"<td data-col='status' data-status-cell='1'>{escape(item.status)}</td>"
            f"<td data-col='created'>{escape(_fmt_ts(item.created_at))}</td>"
            f"<td data-col='sla' data-sla-health='{escape(sla_decision.health_state)}' data-aging-bucket='{escape(sla_decision.aging_bucket)}'><small>{escape(sla_hint)}</small></td>"
            "</tr>"
        )
        table_rows += _triage_detail_row(
            item.id,
            col_count=9,
            title=f"Signal #{item.id}",
            subtitle=f"Auction {item.auction_id} / user {item.user_id} / score {item.score}",
        )
    if not table_rows:
        table_rows = "<tr><td colspan='8'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _signals_path(page_value=page - 1, status_value=status)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _signals_path(page_value=page + 1, status_value=status)))}'>Вперед →</a>"
        if has_next
        else ""
    )
    status_open_path = _signals_path(page_value=0, status_value="OPEN")
    status_resolved_path = _signals_path(page_value=0, status_value="RESOLVED")
    dense_toolbar = render_dense_list_toolbar(
        dense_config,
        density_query_builder=lambda value: _path_with_auth(
            request,
            _signals_path(page_value=0, status_value=status, density_value=value),
        ),
    )
    telemetry_panel = _render_workflow_preset_telemetry_panel(
        request,
        queue_context="risk",
        segments=telemetry_segments,
        selected_preset_id=telemetry_preset_id,
        preset_filter_path_builder=lambda preset_id: _signals_path(
            page_value=page,
            status_value=status,
            telemetry_preset_id_value=preset_id,
        ),
    )

    body = (
        f"{_render_app_header('Фрод-сигналы', auth, f'Статус: {status}')}"
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
        f"<a class='chip' href='{escape(_path_with_auth(request, _signals_path(page_value=0, status_value=status, sla_health_value='all')))}'>Все</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _signals_path(page_value=0, status_value=status, sla_health_value='healthy')))}'>В норме</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _signals_path(page_value=0, status_value=status, sla_health_value='warning')))}'>Внимание</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _signals_path(page_value=0, status_value=status, sla_health_value='critical')))}'>Критично</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _signals_path(page_value=0, status_value=status, sla_health_value='overdue')))}'>Просрочена</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _signals_path(page_value=0, status_value=status, sla_health_value='no_sla')))}'>Без SLA</a></div>"
        "<div class='toolbar'><span>Возраст:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _signals_path(page_value=0, status_value=status, aging_value='all')))}'>Все</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _signals_path(page_value=0, status_value=status, aging_value='fresh')))}'>Свежие</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _signals_path(page_value=0, status_value=status, aging_value='aging')))}'>Aging</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _signals_path(page_value=0, status_value=status, aging_value='stale')))}'>Stale</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _signals_path(page_value=0, status_value=status, aging_value='critical')))}'>Critical</a></div>"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th>Pick</th><th data-col='id'>ID</th><th data-col='auction'>Auction</th><th data-col='user'>User ID</th><th data-col='risk'>User Risk</th><th data-col='score'>Score</th><th data-col='status'>Status</th><th data-col='created'>Created</th><th data-col='sla'>SLA</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(_render_page("Fraud Signals", body))
