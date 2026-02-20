from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from html import escape
import logging
from typing import Callable
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.db.enums import AppealSourceType, AppealStatus, AuctionStatus, ModerationAction, PointsEventType, UserRole
from app.db.models import (
    Appeal,
    Auction,
    Bid,
    BlacklistEntry,
    Complaint,
    FeedbackItem,
    FraudSignal,
    GuarantorRequest,
    ModerationLog,
    TradeFeedback,
    User,
    UserRoleAssignment,
)
from app.db.session import SessionFactory
from app.services.appeal_service import (
    mark_appeal_in_review,
    reject_appeal,
    resolve_appeal,
    resolve_appeal_auction_id,
)
from app.services.admin_list_preferences_service import (
    DEFAULT_DENSITY,
    load_admin_list_preference,
    save_admin_list_preference,
)
from app.services.admin_queue_presets_service import (
    QUEUE_CONTEXT_TO_QUEUE_KEY,
    QUEUE_KEY_TO_QUEUE_CONTEXT,
    delete_preset,
    resolve_queue_preset_state,
    save_preset,
    select_preset,
    set_admin_default,
    update_preset,
)
from app.services.admin_queue_preset_telemetry_service import (
    load_workflow_preset_telemetry_segments,
    record_workflow_preset_telemetry_event,
)
from app.services.adaptive_triage_policy_service import decide_adaptive_detail_depth
from app.services.auction_service import refresh_auction_posts
from app.services.complaint_service import list_complaints
from app.services.fraud_service import list_fraud_signals
from app.services.moderation_dashboard_service import get_moderation_dashboard_snapshot
from app.services.timeline_service import build_auction_timeline_page
from app.services.rbac_service import (
    SCOPE_AUCTION_MANAGE,
    SCOPE_BID_MANAGE,
    SCOPE_TRUST_MANAGE,
    SCOPE_ROLE_MANAGE,
    SCOPE_USER_BAN,
)
from app.services.moderation_service import (
    ban_user,
    end_auction,
    freeze_auction,
    grant_moderator_role,
    is_moderator_tg_user,
    log_moderation_action,
    list_user_roles,
    list_recent_bids,
    remove_bid,
    revoke_moderator_role,
    unban_user,
    unfreeze_auction,
)
from app.services.points_service import (
    count_user_points_entries,
    get_points_redemption_account_age_remaining_seconds,
    get_points_redemptions_spent_today,
    get_points_redemptions_spent_this_week,
    get_points_redemptions_spent_this_month,
    get_points_redemptions_used_today,
    get_points_redemptions_used_this_week,
    get_user_points_summary,
    grant_points,
    list_user_points_entries,
)
from app.services.queue_sla_health_service import SLA_THRESHOLDS_BY_CONTEXT, decide_queue_sla_health
from app.services.risk_eval_service import UserRiskSnapshot, evaluate_user_risk_snapshot, format_risk_reason_label
from app.services.runtime_settings_service import (
    build_runtime_settings_snapshot,
    delete_runtime_setting_override,
    upsert_runtime_setting_override,
)
from app.services.trade_feedback_service import (
    get_trade_feedback_summary,
    list_received_trade_feedback,
    set_trade_feedback_visibility,
)
from app.services.verification_service import (
    get_user_verification_status,
    load_verified_user_ids,
    set_user_verification,
)
from app.web.dense_list import DenseListConfig, render_dense_list_script, render_dense_list_toolbar
from app.web.auth import (
    AdminAuthContext,
    build_admin_session_cookie,
    get_admin_auth_context,
    validate_telegram_login,
)

app = FastAPI(title="LiteAuction Admin", version="0.2.0")
logger = logging.getLogger(__name__)

_DENSE_ALLOWED_DENSITIES = frozenset({"compact", "standard", "comfortable"})
_QUEUE_ALLOWED_COLUMNS: dict[str, tuple[str, ...]] = {
    "complaints": ("id", "auction", "reporter", "status", "reason", "created"),
    "signals": ("id", "auction", "user", "risk", "score", "status", "created"),
    "trade_feedback": (
        "id",
        "auction",
        "author",
        "target",
        "rating",
        "comment",
        "status",
        "moderator",
        "note",
        "created",
        "moderated",
        "actions",
    ),
    "auctions": ("id", "seller", "risk", "start", "buyout", "status", "ends_at", "actions"),
    "manage_users": (
        "id",
        "tg_user_id",
        "username",
        "moderator",
        "banned",
        "verified",
        "risk",
        "created",
        "manage",
    ),
    "violators": ("id", "tg_user_id", "username", "status", "reason", "actor", "created", "expires", "actions"),
    "appeals": (
        "id",
        "reference",
        "source",
        "appellant",
        "risk",
        "status",
        "resolution",
        "moderator",
        "created",
        "sla",
        "deadline",
        "escalation",
        "closed",
        "actions",
    ),
}


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


def _parse_appeal_status_filter(raw: str) -> AppealStatus | None:
    value = raw.strip().lower()
    if value == "all":
        return None
    if value == "open":
        return AppealStatus.OPEN
    if value == "in_review":
        return AppealStatus.IN_REVIEW
    if value == "resolved":
        return AppealStatus.RESOLVED
    if value == "rejected":
        return AppealStatus.REJECTED
    raise HTTPException(status_code=400, detail="Invalid appeals status filter")


def _parse_appeal_source_filter(raw: str) -> AppealSourceType | None:
    value = raw.strip().lower()
    if value == "all":
        return None
    if value == "complaint":
        return AppealSourceType.COMPLAINT
    if value == "risk":
        return AppealSourceType.RISK
    if value == "manual":
        return AppealSourceType.MANUAL
    raise HTTPException(status_code=400, detail="Invalid appeals source filter")


def _parse_appeal_overdue_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "only", "none"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid appeals overdue filter")


def _parse_appeal_escalated_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "only", "none"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid appeals escalated filter")


def _parse_appeal_sla_health_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "healthy", "warning", "critical", "overdue", "no_sla"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid appeals SLA health filter")


def _parse_appeal_aging_bucket_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "fresh", "aging", "stale", "critical", "overdue", "unknown"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid appeals aging filter")


def _parse_trade_feedback_status(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "visible", "hidden"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid trade feedback status filter")


def _parse_trade_feedback_moderated_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "only", "none"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid trade feedback moderated filter")


def _parse_trade_feedback_min_rating(raw: str) -> int | None:
    value = raw.strip()
    if not value:
        return None
    parsed = _parse_non_negative_int(value)
    if parsed is None or parsed < 1 or parsed > 5:
        raise HTTPException(status_code=400, detail="Invalid trade feedback rating filter")
    return parsed


def _parse_optional_tg_user_id(raw: str) -> int | None:
    value = raw.strip()
    if not value:
        return None
    parsed = _parse_non_negative_int(value)
    if parsed is None or parsed <= 0:
        raise HTTPException(status_code=400, detail="Invalid trade feedback tg user filter")
    return parsed


def _appeal_source_label(source_type: AppealSourceType, source_id: int | None) -> str:
    if source_type == AppealSourceType.COMPLAINT:
        return f"Жалоба #{source_id}" if source_id is not None else "Жалоба"
    if source_type == AppealSourceType.RISK:
        return f"Фрод-сигнал #{source_id}" if source_id is not None else "Фрод-сигнал"
    return "Ручная"


def _appeal_status_label(status: AppealStatus | str) -> str:
    raw = status.value if isinstance(status, AppealStatus) else str(status)
    normalized = raw.strip().upper()
    if normalized == AppealStatus.OPEN.value:
        return "Открыта"
    if normalized == AppealStatus.IN_REVIEW.value:
        return "На рассмотрении"
    if normalized == AppealStatus.RESOLVED.value:
        return "Удовлетворена"
    if normalized == AppealStatus.REJECTED.value:
        return "Отклонена"
    return raw


def _appeal_is_overdue(appeal: Appeal, *, now: datetime | None = None) -> bool:
    status = AppealStatus(appeal.status)
    if status not in {AppealStatus.OPEN, AppealStatus.IN_REVIEW}:
        return False
    if appeal.sla_deadline_at is None:
        return False
    current_time = now or datetime.now(UTC)
    return appeal.sla_deadline_at <= current_time


def _format_duration_compact(delta: timedelta) -> str:
    total_seconds = max(int(delta.total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


def _appeal_sla_state_label(appeal: Appeal, *, now: datetime | None = None) -> str:
    status = AppealStatus(appeal.status)
    if status not in {AppealStatus.OPEN, AppealStatus.IN_REVIEW}:
        return "Закрыта"

    if appeal.sla_deadline_at is None:
        return "Без SLA"

    current_time = now or datetime.now(UTC)
    if appeal.sla_deadline_at <= current_time:
        escalation_level = int(appeal.escalation_level or 0)
        if appeal.escalated_at is not None or escalation_level > 0:
            return f"Просрочена, эскалация L{max(escalation_level, 1)}"
        return "Просрочена"

    return f"До SLA: {_format_duration_compact(appeal.sla_deadline_at - current_time)}"


def _appeal_escalation_marker(appeal: Appeal) -> str:
    escalation_level = int(appeal.escalation_level or 0)
    if appeal.escalated_at is None and escalation_level <= 0:
        return "-"

    normalized_level = max(escalation_level, 1)
    if appeal.escalated_at is None:
        return f"L{normalized_level}"
    return f"L{normalized_level} ({_fmt_ts(appeal.escalated_at)})"


def _build_rationale_artifact(
    *,
    summary: str,
    actor_user_id: int,
    actor_tg_user_id: int | None,
    source: str,
    happened_at: datetime,
) -> dict[str, object]:
    normalized_summary = summary.strip()
    if len(normalized_summary) > 280:
        normalized_summary = f"{normalized_summary[:277]}..."
    return {
        "summary": normalized_summary,
        "actor_user_id": actor_user_id,
        "actor_tg_user_id": actor_tg_user_id,
        "source": source,
        "recorded_at": happened_at.isoformat(),
        "immutable": True,
    }


def _user_label(user: User | None, user_id: int | None = None) -> str:
    if user is not None:
        if user.username:
            return f"@{user.username}"
        return str(user.tg_user_id)
    if user_id is not None:
        return f"uid:{user_id}"
    return "-"


def _append_timeline_event(
    events: list[tuple[datetime, str, str]],
    *,
    happened_at: datetime | None,
    title: str,
    details: str,
) -> None:
    if happened_at is None:
        return
    events.append((happened_at, title, details))


def _render_inline_timeline_html(events: list[tuple[datetime, str, str]]) -> str:
    if not events:
        return "<div data-detail-state='empty'>Timeline not available yet.</div>"
    items = "".join(
        (
            "<li>"
            f"<b>{escape(_fmt_ts(happened_at))}</b> - {escape(title)}"
            f"<div class='section-note'>{escape(details)}</div>"
            "</li>"
        )
        for happened_at, title, details in events
    )
    return f"<ol>{items}</ol>"


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
    ).scalars().all()

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
        actors = (
            await session.execute(select(User).where(User.id.in_(actor_ids)))
        ).scalars().all()
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
            auction_link = (
                f"<p class='section-note'><a href='{escape(auction_path)}'>Open full auction timeline</a></p>"
            )
        return {
            "ok": True,
            "html": (
                "<div data-detail-state='loaded'>"
                f"{source_line}{timeline_html}{auction_link}"
                "</div>"
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
            actor_label = _user_label(actors_by_id.get(log_row.actor_user_id), log_row.actor_user_id)
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
                    (BlacklistEntry.expires_at.is_(None) | (BlacklistEntry.expires_at > current_time)),
                )
            )
        ).scalars().all()
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


def _risk_snapshot_inline_text(risk_snapshot) -> str:
    return f"{risk_snapshot.level} ({risk_snapshot.score})"


def _risk_snapshot_inline_html(risk_snapshot) -> str:
    label = _risk_snapshot_inline_text(risk_snapshot)
    if not risk_snapshot.reasons:
        return escape(label)
    reasons = ", ".join(format_risk_reason_label(code) for code in risk_snapshot.reasons)
    return f"<span title='{escape(reasons)}'>{escape(label)}</span>"


def _violator_status_label(status: str) -> str:
    normalized = status.strip().lower()
    if normalized == "active":
        return "Активный"
    if normalized == "inactive":
        return "Неактивный"
    if normalized == "all":
        return "Все"
    return status


def _parse_ymd_filter(raw: str, *, field_name: str) -> datetime | None:
    value = raw.strip()
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} date format") from exc
    return parsed.replace(tzinfo=UTC)


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
        "--bg-0:#f3f6f8;--bg-1:#dfe8ee;--ink:#13212c;--muted:#536676;"
        "--card:#ffffff;--line:#cad8e2;--soft:#eef3f8;--accent:#0f5f8f;--accent-ink:#0c4366;"
        "--ok:#0f7a56;--warn:#9b6c08;--critical:#a22929;"
        "--ok-bg:#edf8f3;--warn-bg:#fff8e7;--critical-bg:#fff1f1;}"
        "*{box-sizing:border-box;}"
        "body{margin:0;font-family:'IBM Plex Sans','Trebuchet MS','Segoe UI',sans-serif;"
        "line-height:1.45;color:var(--ink);"
        "background:radial-gradient(1400px 600px at -10% -20%,#d5e3f7 0%,transparent 70%),"
        "radial-gradient(1200px 500px at 120% -30%,#dbe8de 0%,transparent 68%),"
        "linear-gradient(180deg,var(--bg-0),var(--bg-1));}"
        ".page-shell{max-width:1280px;margin:16px auto;padding:16px 18px;border:1px solid var(--line);"
        "border-radius:16px;background:rgba(255,255,255,0.9);backdrop-filter:blur(3px);"
        "box-shadow:0 14px 26px rgba(16,35,48,0.08);overflow:auto;}"
        ".app-header{display:flex;flex-wrap:wrap;justify-content:space-between;gap:10px;margin-bottom:12px;}"
        ".app-title{margin:0;font-size:31px;letter-spacing:0.2px;}"
        ".app-subtitle{margin:4px 0 0;color:var(--muted);font-size:14px;}"
        ".access-pill{display:inline-flex;align-items:center;gap:7px;padding:6px 10px;border-radius:999px;"
        "background:#f2f6fb;border:1px solid #cfdeea;color:#567085;font-size:11px;font-weight:600;}"
        "h1{margin:0 0 12px;font-size:30px;letter-spacing:0.2px;}"
        "h2,h3{margin-top:4px;margin-bottom:10px;}"
        "p{margin:10px 0;}"
        ".section-card{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:12px;"
        "box-shadow:0 2px 8px rgba(13,29,39,0.05);margin:10px 0;}"
        ".section-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:8px;}"
        ".section-eyebrow{margin:0;font-size:11px;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;color:#6e8292;}"
        ".section-note{margin:0;color:var(--muted);font-size:13px;}"
        ".kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px;}"
        ".kpi{display:flex;flex-direction:column;gap:4px;margin:0;background:var(--card);border:1px solid var(--line);"
        "padding:8px 10px;border-radius:10px;box-shadow:0 1px 5px rgba(15,26,31,0.05);min-height:58px;}"
        ".kpi b{color:var(--accent-ink);font-size:12px;font-weight:700;}"
        ".kpi-critical{border-color:#e4b5b5;background:var(--critical-bg);}"
        ".kpi-warn{border-color:#edd8a3;background:var(--warn-bg);}"
        ".kpi-ok{border-color:#b9dfcf;background:var(--ok-bg);}"
        ".toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 10px;border:1px solid var(--line);"
        "background:#f6f9fc;border-radius:10px;margin:8px 0;}"
        ".toolbar form{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0;}"
        ".link-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;}"
        ".link-tile{display:block;padding:9px 10px;border:1px solid #ccd9e5;border-radius:10px;background:#f7fafd;"
        "font-weight:600;color:var(--accent-ink);text-decoration:none;box-shadow:0 1px 4px rgba(14,38,61,0.06);}"
        ".link-tile:hover{text-decoration:none;background:#eef5fb;}"
        ".stack-rows{display:grid;gap:10px;}"
        ".details{border:1px solid var(--line);border-radius:11px;background:var(--soft);padding:8px 10px;margin-top:10px;}"
        ".details summary{cursor:pointer;font-weight:600;color:var(--accent-ink);margin:2px 0 8px;}"
        ".table-wrap{overflow:auto;border-radius:12px;}"
        "table{border-collapse:separate;border-spacing:0;width:100%;margin-top:12px;background:var(--card);"
        "border:1px solid var(--line);border-radius:12px;overflow:hidden;}"
        "th,td{border-bottom:1px solid var(--line);padding:9px 10px;text-align:left;font-size:14px;vertical-align:top;}"
        "th{background:var(--soft);font-weight:600;color:var(--accent-ink);letter-spacing:0.2px;}"
        ".dense-list-shell[data-density='compact'] th,.dense-list-shell[data-density='compact'] td{padding:5px 7px;font-size:12px;}"
        ".dense-list-shell[data-density='comfortable'] th,.dense-list-shell[data-density='comfortable'] td{padding:12px 13px;font-size:15px;}"
        ".dense-list-shell .is-pinned{position:sticky;left:var(--pin-left,0px);background:var(--card);box-shadow:1px 0 0 rgba(19,33,44,0.12);min-width:max-content;}"
        ".dense-column-controls{display:flex;flex-wrap:wrap;gap:6px;align-items:center;max-width:100%;}"
        ".dense-column-row{display:inline-flex;align-items:center;gap:6px;border:1px solid #c7d4de;background:#ffffff;border-radius:999px;padding:4px 8px;font-size:12px;}"
        ".dense-column-row input{margin:0 2px 0 0;}"
        ".dense-column-row button{padding:2px 7px;min-height:24px;border-radius:6px;font-size:11px;}"
        ".dense-column-key{font-weight:700;color:var(--accent-ink);min-width:52px;}"
        ".dense-list-toolbar input[type='search']{min-width:250px;}"
        "tr:nth-child(even) td{background:#fbfdfe;}"
        "tr[data-triage-row='1'].is-dimmed td{opacity:0.45;}"
        "tr[data-triage-row='1'].is-focused td{box-shadow:inset 0 0 0 2px #9bc2dd;}"
        "tr[data-triage-detail] td{background:#f7fbff !important;}"
        ".dense-bulk-controls{display:inline-flex;flex-wrap:wrap;gap:6px;align-items:center;margin-left:8px;}"
        "tr:last-child td{border-bottom:none;}"
        "a{color:var(--accent);text-decoration:none;font-weight:600;}"
        "a:hover{text-decoration:underline;}"
        "a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{"
        "outline:3px solid #ffb454;outline-offset:2px;}"
        ".chip{display:inline-block;padding:4px 9px;border-radius:999px;border:1px solid #b8c9c7;"
        "background:#f3faf8;font-size:12px;font-weight:600;margin-right:6px;margin-bottom:4px;}"
        ".chip-active{background:#e8f2ff;border-color:#b8cfea;color:var(--accent-ink);}"
        ".page-links{display:flex;flex-wrap:wrap;gap:12px;font-size:14px;align-items:center;margin:8px 0 0;}"
        ".pager{display:flex;gap:12px;align-items:center;margin-top:10px;}"
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
        ".app-title{font-size:25px;}"
        ".kpi-grid{grid-template-columns:1fr;}"
        ".link-grid{grid-template-columns:1fr;}"
        "table{display:block;overflow-x:auto;white-space:nowrap;}"
        ".toolbar form{width:100%;}}"
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
    scope_count = len(auth.scopes)
    if scope_count == 0:
        return f"role={role}, via={via}, read-only"
    return f"role={role}, via={via}, scopes={scope_count}"


def _render_app_header(title: str, auth: AdminAuthContext, subtitle: str = "") -> str:
    subtitle_html = ""
    if subtitle:
        subtitle_html = f"<p class='app-subtitle'>{escape(subtitle)}</p>"
    scope_names = sorted(auth.scopes)
    scope_hint = ",".join(scope_names) if scope_names else "read-only"
    return (
        "<header class='app-header'>"
        f"<div><h1 class='app-title'>{escape(title)}</h1>{subtitle_html}</div>"
        f"<div class='access-pill' title='{escape(scope_hint)}'>Access: {escape(_role_badge(auth))}</div>"
        "</header>"
    )


def _kpi_card(label: str, value: str, *, tone: str = "") -> str:
    tone_class = ""
    if tone == "critical":
        tone_class = " kpi-critical"
    elif tone == "warn":
        tone_class = " kpi-warn"
    elif tone == "ok":
        tone_class = " kpi-ok"
    return f"<div class='kpi{tone_class}'><b>{escape(label)}:</b> {value}</div>"


def _kpi_grid(items: list[str]) -> str:
    return f"<div class='kpi-grid'>{''.join(items)}</div>"


def _panel(title: str, content: str, *, eyebrow: str = "", note: str = "") -> str:
    eyebrow_html = f"<p class='section-eyebrow'>{escape(eyebrow)}</p>" if eyebrow else ""
    note_html = f"<p class='section-note'>{escape(note)}</p>" if note else ""
    return (
        "<section class='section-card'>"
        f"<div class='section-head'><div>{eyebrow_html}<h2>{escape(title)}</h2></div>{note_html}</div>"
        f"{content}"
        "</section>"
    )


def _pager_html(prev_link: str, next_link: str) -> str:
    parts: list[str] = []
    if prev_link:
        parts.append(prev_link)
    if next_link:
        parts.append(next_link)
    if not parts:
        return ""
    return f"<div class='pager'>{' '.join(parts)}</div>"


def _details_block(summary: str, content: str, *, open_by_default: bool = False) -> str:
    open_attr = " open" if open_by_default else ""
    return f"<details class='details'{open_attr}><summary>{escape(summary)}</summary>{content}</details>"


def _normalize_dashboard_preset(raw: str | None) -> str:
    if raw in {"incident", "routine", "rewards"}:
        return raw
    return "incident"


def _dashboard_preset_toolbar(request: Request, active_preset: str) -> str:
    options = [
        ("incident", "Инцидент", "критичное"),
        ("routine", "Рутина", "операционный обзор"),
        ("rewards", "Rewards", "баллы и policy"),
    ]
    chips: list[str] = []
    for preset_key, label, hint in options:
        classes = "chip"
        if preset_key == active_preset:
            classes = "chip chip-active"
        chips.append(
            f"<a class='{classes}' data-preset='{preset_key}' "
            f"href='{escape(_path_with_auth(request, f'/?preset={preset_key}'))}' "
            f"title='{escape(hint)}'>{escape(label)}</a>"
        )
    return (
        "<div class='toolbar'>"
        "<b>Режим экрана:</b>"
        f"{''.join(chips)}"
        "</div>"
    )


def _dashboard_preset_script(default_preset: str = "incident") -> str:
    return (
        "<script>"
        "(function(){"
        "var key='la_dashboard_preset';"
        "var valid={incident:1,routine:1,rewards:1};"
        "var url=new URL(window.location.href);"
        "var current=url.searchParams.get('preset');"
        "if(current&&valid[current]){try{sessionStorage.setItem(key,current);}catch(_e){};}"
        "else{"
        "var stored=null;"
        "try{stored=sessionStorage.getItem(key);}catch(_e){}"
        "if(stored&&valid[stored]){url.searchParams.set('preset',stored);window.location.replace(url.toString());return;}"
        f"try{{sessionStorage.setItem(key,'{default_preset}');}}catch(_e){{}}"
        "}"
        "var links=document.querySelectorAll('[data-preset]');"
        "for(var i=0;i<links.length;i++){"
        "links[i].addEventListener('click',function(){"
        "var value=this.getAttribute('data-preset');"
        "if(valid[value]){try{sessionStorage.setItem(key,value);}catch(_e){}}"
        "});"
        "}"
        "})();"
        "</script>"
    )


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


def _format_preset_telemetry_time(avg_ms: float | None) -> str:
    if avg_ms is None:
        return "-"
    if avg_ms >= 1000:
        return f"{avg_ms / 1000:.1f}s"
    return f"{avg_ms:.0f}ms"


def _format_preset_telemetry_time_delta(delta_ms: float | None) -> str:
    if delta_ms is None:
        return "-"
    sign = "+" if delta_ms > 0 else ""
    if abs(delta_ms) >= 1000:
        return f"{sign}{delta_ms / 1000:.1f}s"
    return f"{sign}{delta_ms:.0f}ms"


def _format_preset_telemetry_rate_delta(delta: float | None) -> str:
    if delta is None:
        return "-"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta * 100:.1f}pp"


def _format_preset_telemetry_churn_delta(delta: float | None) -> str:
    if delta is None:
        return "-"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.2f}"


def _render_workflow_preset_telemetry_panel(
    request: Request,
    *,
    queue_context: str,
    segments: list[dict[str, object]],
    selected_preset_id: int | None,
    preset_filter_path_builder: Callable[[int | None], str],
) -> str:
    preset_ids: list[int] = []
    for item in segments:
        preset_value = item.get("preset_id")
        if isinstance(preset_value, int):
            preset_ids.append(preset_value)
    preset_ids = sorted(set(preset_ids))

    chips = [
        (
            "all",
            selected_preset_id is None,
            _path_with_auth(request, preset_filter_path_builder(None)),
        )
    ]
    for preset_id in preset_ids:
        chips.append(
            (
                f"preset #{preset_id}",
                selected_preset_id == preset_id,
                _path_with_auth(request, preset_filter_path_builder(preset_id)),
            )
        )

    chip_html = "".join(
        f"<a class='{'chip chip-active' if is_active else 'chip'}' href='{escape(path)}'>{escape(label)}</a>"
        for label, is_active, path in chips
    )

    filtered = [
        item
        for item in segments
        if selected_preset_id is None or item.get("preset_id") == selected_preset_id
    ]

    if not filtered:
        rows_html = "<tr><td colspan='8'><span class='empty-state'>No telemetry events for this filter yet.</span></td></tr>"
    else:
        rows_html = ""
        for item in filtered[:10]:
            preset_id = item.get("preset_id")
            preset_label = f"#{preset_id}" if isinstance(preset_id, int) else "none"
            events_total_raw = item.get("events_total")
            if isinstance(events_total_raw, int):
                events_total = events_total_raw
            elif isinstance(events_total_raw, float):
                events_total = int(events_total_raw)
            else:
                events_total = 0
            avg_time = item.get("avg_time_to_action_ms")
            avg_time_value = float(avg_time) if isinstance(avg_time, (int, float)) else None
            reopen_rate_raw = item.get("reopen_rate")
            reopen_rate = float(reopen_rate_raw) if isinstance(reopen_rate_raw, (int, float)) else 0.0
            avg_churn_raw = item.get("avg_filter_churn_count")
            avg_churn = float(avg_churn_raw) if isinstance(avg_churn_raw, (int, float)) else 0.0
            trend_guardrail = bool(item.get("trend_low_sample_guardrail"))
            trend_min_sample_raw = item.get("trend_min_sample_size")
            trend_min_sample = int(trend_min_sample_raw) if isinstance(trend_min_sample_raw, int) else 0
            prev_events_raw = item.get("trend_previous_events_total")
            prev_events = int(prev_events_raw) if isinstance(prev_events_raw, int) else 0
            time_delta_raw = item.get("time_to_action_delta_ms")
            time_delta = float(time_delta_raw) if isinstance(time_delta_raw, (int, float)) else None
            reopen_delta_raw = item.get("reopen_rate_delta")
            reopen_delta = float(reopen_delta_raw) if isinstance(reopen_delta_raw, (int, float)) else None
            churn_delta_raw = item.get("filter_churn_delta")
            churn_delta = float(churn_delta_raw) if isinstance(churn_delta_raw, (int, float)) else None

            trend_time = _format_preset_telemetry_time_delta(time_delta)
            trend_reopen = _format_preset_telemetry_rate_delta(reopen_delta)
            trend_churn = _format_preset_telemetry_churn_delta(churn_delta)
            if trend_guardrail:
                guardrail_note = f"guardrail ({events_total}/{prev_events} < {trend_min_sample})"
                trend_time = guardrail_note
                trend_reopen = guardrail_note
                trend_churn = guardrail_note

            rows_html += (
                "<tr>"
                f"<td>{escape(preset_label)}</td>"
                f"<td>{events_total}</td>"
                f"<td>{escape(_format_preset_telemetry_time(avg_time_value))}</td>"
                f"<td>{reopen_rate * 100:.1f}%</td>"
                f"<td>{avg_churn:.2f}</td>"
                f"<td>{escape(trend_time)}</td>"
                f"<td>{escape(trend_reopen)}</td>"
                f"<td>{escape(trend_churn)}</td>"
                "</tr>"
            )

    queue_label = {
        "moderation": "Moderation queue",
        "risk": "Risk queue",
        "feedback": "Feedback queue",
        "appeals": "Appeals queue",
    }.get(queue_context, queue_context)

    return (
        "<div class='section-card'>"
        "<p class='section-eyebrow'>Preset telemetry insights (7d)</p>"
        f"<p><b>{escape(queue_label)}</b></p>"
        "<p class='section-note'>Telemetry is advisory only and does not automate moderation decisions.</p>"
        "<p class='section-note'>Trend deltas compare the current lookback window with the previous one.</p>"
        f"<div class='toolbar'><span>Preset filter:</span>{chip_html}</div>"
        "<div class='table-wrap'><table><thead><tr><th>Preset</th><th>Events</th><th>Avg time-to-action</th><th>Reopen rate</th><th>Avg filter churn</th><th>Δ time-to-action</th><th>Δ reopen rate</th><th>Δ filter churn</th></tr></thead>"
        f"<tbody>{rows_html}</tbody></table></div>"
        "</div>"
    )


def _normalize_requested_density(raw_density: str | None) -> str | None:
    if raw_density is None:
        return None
    value = raw_density.strip().lower()
    if not value:
        return None
    if value not in _DENSE_ALLOWED_DENSITIES:
        raise HTTPException(status_code=400, detail="Invalid density value")
    return value


def _resolve_dense_density(*, requested: str | None, persisted: str) -> str:
    requested_density = _normalize_requested_density(requested)
    if requested_density is not None:
        return requested_density
    persisted_density = persisted.strip().lower()
    if persisted_density in _DENSE_ALLOWED_DENSITIES:
        return persisted_density
    return DEFAULT_DENSITY


async def _load_dense_list_config(
    session: AsyncSession,
    *,
    request: Request,
    auth: AdminAuthContext,
    queue_key: str,
    requested_density: str | None,
    table_id: str,
    quick_filter_placeholder: str,
) -> DenseListConfig:
    allowed_columns = _QUEUE_ALLOWED_COLUMNS.get(queue_key)
    if not allowed_columns:
        raise HTTPException(status_code=500, detail="Dense list queue is not configured")

    admin_token = _token_from_request(request)
    queue_context = QUEUE_KEY_TO_QUEUE_CONTEXT.get(queue_key)
    preset_notice = ""
    preset_items: tuple[tuple[str, str], ...] = ()
    active_preset_id: int | None = None
    active_preset_name = ""

    if queue_context is not None:
        preset_state = await resolve_queue_preset_state(
            session,
            auth=auth,
            queue_context=queue_context,
            allowed_columns=allowed_columns,
            admin_token=admin_token,
        )
        preference = preset_state["state"]
        if preset_state.get("active_preset") is None and preset_state.get("source") == "none":
            preference = await load_admin_list_preference(
                session,
                auth=auth,
                queue_key=queue_key,
                allowed_columns=allowed_columns,
                admin_token=admin_token,
            )
        active = preset_state["active_preset"]
        if active is not None:
            active_preset_id = int(active["id"])
            active_preset_name = str(active["name"])
        preset_notice = str(preset_state.get("notice") or "")
        preset_items = tuple((str(item["id"]), str(item["name"])) for item in preset_state["presets"])
    else:
        preference = await load_admin_list_preference(
            session,
            auth=auth,
            queue_key=queue_key,
            allowed_columns=allowed_columns,
            admin_token=admin_token,
        )

    columns = preference["columns"]

    return DenseListConfig(
        queue_key=queue_key,
        density=_resolve_dense_density(requested=requested_density, persisted=preference["density"]),
        table_id=table_id,
        quick_filter_placeholder=quick_filter_placeholder,
        columns_order=tuple(columns["order"]),
        columns_visible=tuple(columns["visible"]),
        columns_pinned=tuple(columns["pinned"]),
        preferences_action_path=_path_with_auth(request, "/actions/dense-list/preferences"),
        csrf_token=_build_csrf_token(request, auth),
        preset_enabled=queue_context is not None,
        preset_context=queue_context or "",
        preset_items=preset_items,
        active_preset_id=active_preset_id,
        active_preset_name=active_preset_name,
        preset_notice=preset_notice,
        presets_action_path=_path_with_auth(request, "/actions/workflow-presets"),
    )


def _parse_signed_int(raw: str | None) -> int | None:
    if raw is None:
        return None

    value = raw.strip()
    if not value:
        return None

    try:
        return int(value)
    except ValueError:
        return None


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


def _resolve_workflow_preset_telemetry_preset_id(*, action: str, payload: dict[str, object], result: dict[str, object]) -> int | None:
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


def _normalize_points_filter_query(raw: str | None) -> PointsEventType | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"", "all", "all_types"}:
        return None
    if value in {"feedback", "feedback_approved"}:
        return PointsEventType.FEEDBACK_APPROVED
    if value in {"manual", "manual_adjustment"}:
        return PointsEventType.MANUAL_ADJUSTMENT
    if value in {"boost", "feedback_priority_boost", "priority"}:
        return PointsEventType.FEEDBACK_PRIORITY_BOOST
    if value in {"gboost", "guarantor_priority_boost", "guarant_boost"}:
        return PointsEventType.GUARANTOR_PRIORITY_BOOST
    if value in {"aboost", "appeal_priority_boost", "appeal_boost"}:
        return PointsEventType.APPEAL_PRIORITY_BOOST
    raise HTTPException(status_code=400, detail="Invalid points filter")


def _points_filter_query_value(filter_value: PointsEventType | None) -> str:
    if filter_value is None:
        return "all"
    if filter_value == PointsEventType.FEEDBACK_APPROVED:
        return "feedback"
    if filter_value == PointsEventType.FEEDBACK_PRIORITY_BOOST:
        return "boost"
    if filter_value == PointsEventType.GUARANTOR_PRIORITY_BOOST:
        return "gboost"
    if filter_value == PointsEventType.APPEAL_PRIORITY_BOOST:
        return "aboost"
    return "manual"


def _points_event_label(event_type: PointsEventType) -> str:
    if event_type == PointsEventType.FEEDBACK_APPROVED:
        return "Награда за фидбек"
    if event_type == PointsEventType.FEEDBACK_PRIORITY_BOOST:
        return "Списание за приоритет фидбека"
    if event_type == PointsEventType.GUARANTOR_PRIORITY_BOOST:
        return "Списание за приоритет гаранта"
    if event_type == PointsEventType.APPEAL_PRIORITY_BOOST:
        return "Списание за приоритет апелляции"
    return "Ручная корректировка"


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


def _triage_controls_cell(row_id: int) -> str:
    return (
        f"<button type='button' data-triage-toggle='1' data-row-id='{row_id}' aria-expanded='false'>Details</button>"
        f" <label><input type='checkbox' data-bulk-select-id='1' value='{row_id}'>select</label>"
    )


def _triage_row_context_attrs(*, risk_level: str, priority_level: str) -> str:
    risk_value = risk_level.strip().lower() or "low"
    priority_value = priority_level.strip().lower() or "normal"
    return f" data-risk-level='{escape(risk_value)}' data-priority-level='{escape(priority_value)}'"


def _triage_detail_row(row_id: int, *, col_count: int, title: str, subtitle: str = "") -> str:
    subtitle_html = f"<p class='section-note'>{escape(subtitle)}</p>" if subtitle else ""
    return (
        f"<tr data-triage-detail='{row_id}' data-expanded='0' hidden>"
        f"<td colspan='{col_count}'>"
        "<div class='section-card'>"
        f"<p class='section-eyebrow'>Inline detail</p><h3>{escape(title)}</h3>{subtitle_html}"
        f"<div data-detail-panel='1' data-row-id='{row_id}'></div>"
        "</div>"
        "</td>"
        "</tr>"
    )


def _triage_shortcut_hint() -> str:
    return (
        "<div class='toolbar'>"
        "<span data-shortcut='focus-search'>/ search</span>"
        "<span data-shortcut='row-next'>j next</span>"
        "<span data-shortcut='row-prev'>k prev</span>"
        "<span data-shortcut='toggle-detail'>o or Enter open/close</span>"
        "<span data-shortcut='bulk-select'>x select row</span>"
        "</div>"
    )


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

    dashboard_preset = _normalize_dashboard_preset(request.query_params.get("preset"))
    show_onboarding = dashboard_preset == "routine"
    show_activity = dashboard_preset == "routine"
    show_rewards_weekly = dashboard_preset in {"routine", "rewards"}
    show_rewards_24h = dashboard_preset == "rewards"
    show_rewards_policy = dashboard_preset == "rewards"

    engaged_with_private = max(
        snapshot.users_with_engagement - snapshot.users_engaged_without_private_start,
        0,
    )
    global_daily_limit_line = "<div class='kpi'><b>Global redemption daily limit:</b> unlimited</div>"
    if settings.points_redemption_daily_limit > 0:
        global_daily_limit_line = (
            f"<div class='kpi'><b>Global redemption daily limit:</b> {settings.points_redemption_daily_limit}/day</div>"
        )
    global_weekly_limit_line = "<div class='kpi'><b>Global redemption weekly limit:</b> unlimited</div>"
    if settings.points_redemption_weekly_limit > 0:
        global_weekly_limit_line = (
            f"<div class='kpi'><b>Global redemption weekly limit:</b> {settings.points_redemption_weekly_limit}/week</div>"
        )
    global_daily_spend_cap_line = "<div class='kpi'><b>Global redemption daily spend cap:</b> unlimited</div>"
    if settings.points_redemption_daily_spend_cap > 0:
        global_daily_spend_cap_line = (
            "<div class='kpi'><b>Global redemption daily spend cap:</b> "
            f"{settings.points_redemption_daily_spend_cap} points/day</div>"
        )
    global_weekly_spend_cap_line = "<div class='kpi'><b>Global redemption weekly spend cap:</b> unlimited</div>"
    if settings.points_redemption_weekly_spend_cap > 0:
        global_weekly_spend_cap_line = (
            "<div class='kpi'><b>Global redemption weekly spend cap:</b> "
            f"{settings.points_redemption_weekly_spend_cap} points/week</div>"
        )
    global_monthly_spend_cap_line = "<div class='kpi'><b>Global redemption monthly spend cap:</b> unlimited</div>"
    if settings.points_redemption_monthly_spend_cap > 0:
        global_monthly_spend_cap_line = (
            "<div class='kpi'><b>Global redemption monthly spend cap:</b> "
            f"{settings.points_redemption_monthly_spend_cap} points/month</div>"
        )

    owner_settings_link = ""
    if auth.role == "owner":
        owner_settings_link = (
            f"<a class='link-tile' href='{escape(_path_with_auth(request, '/settings'))}'>Runtime settings</a>"
        )

    overview_cards = _kpi_grid(
        [
            _kpi_card("Открытые жалобы", str(snapshot.open_complaints), tone="critical"),
            _kpi_card("Открытые сигналы", str(snapshot.open_signals), tone="warn"),
            _kpi_card("Активные аукционы", str(snapshot.active_auctions), tone="ok"),
            _kpi_card("Ставок/час", str(snapshot.bids_last_hour)),
        ]
    )
    onboarding_cards = _kpi_grid(
        [
            _kpi_card("Всего пользователей", str(snapshot.total_users)),
            _kpi_card(
                "Private /start",
                f"{snapshot.users_private_started} ({_pct(snapshot.users_private_started, snapshot.total_users)})",
            ),
            _kpi_card("С hint", str(snapshot.users_with_soft_gate_hint)),
            _kpi_card("Hint за 24ч", str(snapshot.users_soft_gate_hint_last_24h)),
            _kpi_card(
                "Конверсия после hint",
                f"{snapshot.users_converted_after_hint} ({_pct(snapshot.users_converted_after_hint, snapshot.users_with_soft_gate_hint)})",
            ),
            _kpi_card("Ожидают после hint", str(snapshot.users_pending_after_hint)),
        ]
    )
    activity_cards = _kpi_grid(
        [
            _kpi_card("Пользователи со ставками", str(snapshot.users_with_bid_activity)),
            _kpi_card("Пользователи с жалобами", str(snapshot.users_with_report_activity)),
            _kpi_card("Уникально вовлеченные", str(snapshot.users_with_engagement)),
            _kpi_card("Вовлеченные без private /start", str(snapshot.users_engaged_without_private_start)),
            _kpi_card(
                "Вовлеченные с private /start",
                f"{engaged_with_private} ({_pct(engaged_with_private, snapshot.users_with_engagement)})",
            ),
        ]
    )
    points_core_cards = _kpi_grid(
        [
            _kpi_card("Активные points-пользователи (7д)", str(snapshot.points_active_users_7d)),
            _kpi_card("Пользователи с положительным балансом", str(snapshot.points_users_with_positive_balance)),
            _kpi_card(
                "Редимеры points (7д)",
                f"{snapshot.points_redeemers_7d} ({_pct(snapshot.points_redeemers_7d, snapshot.points_users_with_positive_balance)})",
            ),
            _kpi_card("Редимеры фидбек-буста (7д)", str(snapshot.points_feedback_boost_redeemers_7d)),
            _kpi_card("Редимеры буста гаранта (7д)", str(snapshot.points_guarantor_boost_redeemers_7d)),
            _kpi_card("Редимеры буста апелляции (7д)", str(snapshot.points_appeal_boost_redeemers_7d)),
        ]
    )
    points_24h_cards = _kpi_grid(
        [
            _kpi_card("Points начислено (24ч)", f"+{snapshot.points_earned_24h}"),
            _kpi_card("Points списано (24ч)", f"-{snapshot.points_spent_24h}"),
            _kpi_card("Бустов фидбека (24ч)", str(snapshot.feedback_boost_redeems_24h)),
            _kpi_card("Бустов гаранта (24ч)", str(snapshot.guarantor_boost_redeems_24h)),
            _kpi_card("Бустов апелляций (24ч)", str(snapshot.appeal_boost_redeems_24h)),
        ]
    )
    points_policy_cards = _kpi_grid(
        [
            _kpi_card(
                "Policy feedback",
                (
                    f"{'on' if settings.feedback_priority_boost_enabled else 'off'} | "
                    f"cost {settings.feedback_priority_boost_cost_points} | "
                    f"limit {settings.feedback_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.feedback_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card(
                "Policy guarantor",
                (
                    f"{'on' if settings.guarantor_priority_boost_enabled else 'off'} | "
                    f"cost {settings.guarantor_priority_boost_cost_points} | "
                    f"limit {settings.guarantor_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.guarantor_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card(
                "Policy appeal",
                (
                    f"{'on' if settings.appeal_priority_boost_enabled else 'off'} | "
                    f"cost {settings.appeal_priority_boost_cost_points} | "
                    f"limit {settings.appeal_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.appeal_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card("Policy redemptions", "on" if settings.points_redemption_enabled else "off"),
            global_daily_limit_line,
            global_weekly_limit_line,
            global_daily_spend_cap_line,
            global_weekly_spend_cap_line,
            global_monthly_spend_cap_line,
            _kpi_card("Min balance after redemption", f"{max(settings.points_redemption_min_balance, 0)} points"),
            _kpi_card(
                "Min account age for redemption",
                f"{max(settings.points_redemption_min_account_age_seconds, 0)}s",
            ),
            _kpi_card(
                "Min earned points for redemption",
                f"{max(settings.points_redemption_min_earned_points, 0)} points",
            ),
            _kpi_card("Global redemption cooldown", f"{max(settings.points_redemption_cooldown_seconds, 0)}s"),
        ]
    )
    points_summary = (
        f"active(7d): {snapshot.points_active_users_7d} | "
        f"positive balance: {snapshot.points_users_with_positive_balance} | "
        f"redeemers(7d): {snapshot.points_redeemers_7d}"
    )
    points_cards = (
        f"<p class='section-note'><b>Кратко:</b> {escape(points_summary)}</p>"
        f"{_details_block('Развернуть weekly rewards метрики', points_core_cards, open_by_default=show_rewards_weekly)}"
        f"{_details_block('Развернуть rewards активность за 24ч', points_24h_cards, open_by_default=show_rewards_24h)}"
        f"{_details_block('Развернуть policy и лимиты rewards', points_policy_cards, open_by_default=show_rewards_policy)}"
    )
    onboarding_summary = (
        f"users: {snapshot.total_users} | "
        f"private /start: {snapshot.users_private_started} ({_pct(snapshot.users_private_started, snapshot.total_users)}) | "
        f"pending after hint: {snapshot.users_pending_after_hint}"
    )
    onboarding_collapsed = _details_block(
        f"Развернуть воронку онбординга ({onboarding_summary})",
        onboarding_cards,
        open_by_default=show_onboarding,
    )
    activity_summary = (
        f"engaged users: {snapshot.users_with_engagement} | "
        f"with bids: {snapshot.users_with_bid_activity} | "
        f"without private /start: {snapshot.users_engaged_without_private_start}"
    )
    activity_collapsed = _details_block(
        f"Развернуть метрики активности ({activity_summary})",
        activity_cards,
        open_by_default=show_activity,
    )
    preset_toolbar = _dashboard_preset_toolbar(request, dashboard_preset)

    quick_actions = (
        "<div class='link-grid'>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/complaints?status=OPEN'))}'>Открытые жалобы</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/signals?status=OPEN'))}'>Открытые фрод-сигналы</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/auctions?status=ACTIVE'))}'>Активные аукционы</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/auctions?status=FROZEN'))}'>Замороженные аукционы</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/appeals?status=open&source=all'))}'>Апелляции</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/trade-feedback?status=visible'))}'>Отзывы по сделкам</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/violators?status=active'))}'>Нарушители</a>"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/manage/users'))}'>Управление пользователями</a>"
        f"{owner_settings_link}"
        f"<a class='link-tile' href='{escape(_path_with_auth(request, '/logout'))}'>Выйти</a>"
        "</div>"
    )

    body = (
        f"{_render_app_header('LiteAuction Admin', auth, 'Операционный центр модерации и риск-контроль')}"
        f"{preset_toolbar}"
        f"{_panel('Пульс модерации', overview_cards, eyebrow='priority metrics', note='Критичные метрики всегда сверху')}"
        f"{_panel('Быстрые действия', quick_actions, eyebrow='navigation', note='Основные сценарии оператора')}"
        f"{_panel('Воронка онбординга / soft-gate', onboarding_collapsed, eyebrow='growth')}"
        f"{_panel('Активность пользователей', activity_collapsed, eyebrow='engagement')}"
        f"{_panel('Points utility', points_cards, eyebrow='rewards') }"
        f"{_dashboard_preset_script()}"
    )
    return HTMLResponse(_render_page("LiteAuction Admin", body))


def _render_runtime_setting_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@app.get("/settings", response_class=HTMLResponse)
async def runtime_settings_page(request: Request) -> Response:
    response, auth = _require_owner_permission(request)
    if response is not None:
        return response

    async with SessionFactory() as session:
        snapshot_items = await build_runtime_settings_snapshot(session)

    csrf_input = _csrf_hidden_input(request, auth)
    rows: list[str] = []
    for item in snapshot_items:
        override_raw_value = item.override_raw_value
        override_text = "<span class='empty-state'>none</span>"
        if override_raw_value is not None:
            override_text = escape(override_raw_value)

        updated_by = str(item.updated_by_user_id) if item.updated_by_user_id is not None else "-"
        updated_at = _fmt_ts(item.updated_at)
        rows.append(
            "<tr>"
            f"<td><code>{escape(item.key)}</code></td>"
            f"<td>{escape(item.description)}</td>"
            f"<td>{escape(_render_runtime_setting_value(item.default_value))}</td>"
            f"<td><b>{escape(_render_runtime_setting_value(item.effective_value))}</b></td>"
            f"<td>{override_text}</td>"
            f"<td>{escape(updated_by)}</td>"
            f"<td>{escape(updated_at)}</td>"
            "<td>"
            f"<form method='post' action='{escape(_path_with_auth(request, '/actions/settings/runtime/set'))}'>"
            f"<input type='hidden' name='key' value='{escape(item.key)}'>"
            "<input type='hidden' name='return_to' value='/settings'>"
            f"{csrf_input}"
            f"<input name='value' value='{escape(override_raw_value or '')}' style='width:170px' placeholder='override value' required>"
            "<button type='submit'>Save</button>"
            "</form>"
            f"<form method='post' action='{escape(_path_with_auth(request, '/actions/settings/runtime/delete'))}'>"
            f"<input type='hidden' name='key' value='{escape(item.key)}'>"
            "<input type='hidden' name='return_to' value='/settings'>"
            f"{csrf_input}"
            "<button type='submit'>Remove override</button>"
            "</form>"
            "</td>"
            "</tr>"
        )

    body = (
        f"{_render_app_header('Runtime settings', auth, 'Owner-only operational overrides')}"
        "<div class='section-card'>"
        "<div class='notice'><p>Owner-only operational overrides. Keys are allowlisted and validated.</p></div>"
        "<div class='table-wrap'><table><thead><tr>"
        "<th>Key</th><th>Description</th><th>Default</th><th>Effective</th><th>Override</th><th>Updated by</th><th>Updated at</th><th>Actions</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows) if rows else '<tr><td colspan=8><span class="empty-state">Нет настроек</span></td></tr>'}</tbody>"
        "</table></div>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        "</div>"
    )
    return HTMLResponse(_render_page("Runtime Settings", body))


@app.post("/actions/settings/runtime/set")
async def action_set_runtime_setting(
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
    return_to: str = Form("/settings"),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_owner_permission(request)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/settings")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)
    try:
        async with SessionFactory() as session:
            async with session.begin():
                await upsert_runtime_setting_override(
                    session,
                    key=key,
                    raw_value=value,
                    updated_by_user_id=actor_user_id,
                )
    except ValueError as exc:
        return _action_error_page(request, str(exc), back_to=target)

    return RedirectResponse(_path_with_auth(request, target), status_code=303)


@app.post("/actions/settings/runtime/delete")
async def action_delete_runtime_setting(
    request: Request,
    key: str = Form(...),
    return_to: str = Form("/settings"),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_owner_permission(request)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/settings")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    try:
        async with SessionFactory() as session:
            async with session.begin():
                await delete_runtime_setting_override(session, key=key)
    except ValueError as exc:
        return _action_error_page(request, str(exc), back_to=target)

    return RedirectResponse(_path_with_auth(request, target), status_code=303)


@app.get("/complaints", response_class=HTMLResponse)
async def complaints(
    request: Request,
    status: str = "OPEN",
    page: int = 0,
    density: str | None = None,
    telemetry_preset_id: int | None = None,
) -> Response:
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
    ) -> str:
        query = {
            "status": status_value,
            "page": str(page_value),
            "density": density_value or dense_config.density,
        }
        if telemetry_preset_id_value is not None and telemetry_preset_id_value > 0:
            query["telemetry_preset_id"] = str(telemetry_preset_id_value)
        return f"/complaints?{urlencode(query)}"

    table_rows = ""
    for item in rows:
        complaint_priority = "high" if str(item.status).upper() == "OPEN" else "normal"
        row_context_attrs = _triage_row_context_attrs(risk_level="low", priority_level=complaint_priority)
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
            "</tr>"
        )
        table_rows += _triage_detail_row(
            item.id,
            col_count=7,
            title=f"Complaint #{item.id}",
            subtitle=f"Auction {item.auction_id} / reporter {item.reporter_user_id}",
        )
    if not table_rows:
        table_rows = "<tr><td colspan='7'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _complaints_path(page_value=page-1, status_value=status)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _complaints_path(page_value=page+1, status_value=status)))}'>Вперед →</a>"
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
        f"{_render_app_header('Жалобы', auth, f'Статус: {status}') }"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"{dense_toolbar}"
        f"{telemetry_panel}"
        f"{_triage_shortcut_hint()}"
        "<div class='toolbar'>"
        f"<a class='chip' href='{escape(_path_with_auth(request, status_open_path))}'>OPEN</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, status_resolved_path))}'>RESOLVED</a>"
        "</div>"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th>Pick</th><th data-col='id'>ID</th><th data-col='auction'>Auction</th><th data-col='reporter'>Reporter UID</th><th data-col='status'>Status</th><th data-col='reason'>Reason</th><th data-col='created'>Created</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(_render_page("Complaints", body))


@app.get("/signals", response_class=HTMLResponse)
async def signals(
    request: Request,
    status: str = "OPEN",
    page: int = 0,
    density: str | None = None,
    telemetry_preset_id: int | None = None,
) -> Response:
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
    ) -> str:
        query = {
            "status": status_value,
            "page": str(page_value),
            "density": density_value or dense_config.density,
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
            "</tr>"
        )
        table_rows += _triage_detail_row(
            item.id,
            col_count=8,
            title=f"Signal #{item.id}",
            subtitle=f"Auction {item.auction_id} / user {item.user_id} / score {item.score}",
        )
    if not table_rows:
        table_rows = "<tr><td colspan='8'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _signals_path(page_value=page-1, status_value=status)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _signals_path(page_value=page+1, status_value=status)))}'>Вперед →</a>"
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
        f"{_render_app_header('Фрод-сигналы', auth, f'Статус: {status}') }"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"{dense_toolbar}"
        f"{telemetry_panel}"
        f"{_triage_shortcut_hint()}"
        "<div class='toolbar'>"
        f"<a class='chip' href='{escape(_path_with_auth(request, status_open_path))}'>OPEN</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, status_resolved_path))}'>RESOLVED</a>"
        "</div>"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th>Pick</th><th data-col='id'>ID</th><th data-col='auction'>Auction</th><th data-col='user'>User ID</th><th data-col='risk'>User Risk</th><th data-col='score'>Score</th><th data-col='status'>Status</th><th data-col='created'>Created</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(_render_page("Fraud Signals", body))


@app.get("/trade-feedback", response_class=HTMLResponse)
async def trade_feedback(
    request: Request,
    status: str = "visible",
    moderated: str = "all",
    page: int = 0,
    q: str = "",
    min_rating: str = "",
    author_tg: str = "",
    target_tg: str = "",
    moderator_tg: str = "",
    density: str | None = None,
    telemetry_preset_id: int | None = None,
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    page = max(page, 0)
    page_size = 30
    offset = page * page_size
    status_value = _parse_trade_feedback_status(status)
    moderated_value = _parse_trade_feedback_moderated_filter(moderated)
    query_value = q.strip()
    min_rating_value = _parse_trade_feedback_min_rating(min_rating)
    author_tg_value = _parse_optional_tg_user_id(author_tg)
    target_tg_value = _parse_optional_tg_user_id(target_tg)
    moderator_tg_value = _parse_optional_tg_user_id(moderator_tg)

    author_user = aliased(User)
    target_user = aliased(User)
    moderator_user = aliased(User)

    stmt = (
        select(TradeFeedback, Auction, author_user, target_user, moderator_user)
        .join(Auction, Auction.id == TradeFeedback.auction_id)
        .join(author_user, author_user.id == TradeFeedback.author_user_id)
        .join(target_user, target_user.id == TradeFeedback.target_user_id)
        .outerjoin(moderator_user, moderator_user.id == TradeFeedback.moderator_user_id)
    )

    if status_value != "all":
        stmt = stmt.where(TradeFeedback.status == status_value.upper())

    if moderated_value == "only":
        stmt = stmt.where(TradeFeedback.moderated_at.is_not(None))
    elif moderated_value == "none":
        stmt = stmt.where(TradeFeedback.moderated_at.is_(None))

    if min_rating_value is not None:
        stmt = stmt.where(TradeFeedback.rating >= min_rating_value)

    if author_tg_value is not None:
        stmt = stmt.where(author_user.tg_user_id == author_tg_value)

    if target_tg_value is not None:
        stmt = stmt.where(target_user.tg_user_id == target_tg_value)

    if moderator_tg_value is not None:
        stmt = stmt.where(moderator_user.tg_user_id == moderator_tg_value)

    if query_value:
        auction_uuid: uuid.UUID | None = None
        try:
            auction_uuid = uuid.UUID(query_value)
        except ValueError:
            auction_uuid = None

        if query_value.isdigit():
            q_int = int(query_value)
            stmt = stmt.where(
                or_(
                    TradeFeedback.id == q_int,
                    author_user.tg_user_id == q_int,
                    target_user.tg_user_id == q_int,
                )
            )
        elif auction_uuid is not None:
            stmt = stmt.where(TradeFeedback.auction_id == auction_uuid)
        else:
            stmt = stmt.where(
                or_(
                    author_user.username.ilike(f"%{query_value}%"),
                    target_user.username.ilike(f"%{query_value}%"),
                    TradeFeedback.comment.ilike(f"%{query_value}%"),
                    TradeFeedback.moderation_note.ilike(f"%{query_value}%"),
                )
            )

    stmt = (
        stmt.order_by(TradeFeedback.created_at.desc(), TradeFeedback.id.desc())
        .offset(offset)
        .limit(page_size + 1)
    )

    async with SessionFactory() as session:
        rows = (await session.execute(stmt)).all()
        dense_config = await _load_dense_list_config(
            session,
            request=request,
            auth=auth,
            queue_key="trade_feedback",
            requested_density=density,
            table_id="trade-feedback-table",
            quick_filter_placeholder="id / auction / users / comment",
        )
        telemetry_segments = await load_workflow_preset_telemetry_segments(
            session,
            queue_context="feedback",
            lookback_hours=24 * 7,
        )

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    base_query = {
        "status": status_value,
        "moderated": moderated_value,
        "q": query_value,
        "density": dense_config.density,
    }
    if telemetry_preset_id is not None and telemetry_preset_id > 0:
        base_query["telemetry_preset_id"] = str(telemetry_preset_id)
    if min_rating_value is not None:
        base_query["min_rating"] = str(min_rating_value)
    if author_tg_value is not None:
        base_query["author_tg"] = str(author_tg_value)
    if target_tg_value is not None:
        base_query["target_tg"] = str(target_tg_value)
    if moderator_tg_value is not None:
        base_query["moderator_tg"] = str(moderator_tg_value)

    def _trade_feedback_path(*, page_value: int | None = None, **extra: str) -> str:
        query = dict(base_query)
        query.update(extra)
        if page_value is not None:
            query["page"] = str(page_value)
        encoded = urlencode(query)
        return "/trade-feedback" if not encoded else f"/trade-feedback?{encoded}"

    csrf_input = _csrf_hidden_input(request, auth)
    return_to = _trade_feedback_path(page_value=page)
    table_rows = ""

    for item, auction, author, target, moderator in rows:
        author_label = f"@{author.username}" if author.username else str(author.tg_user_id)
        target_label = f"@{target.username}" if target.username else str(target.tg_user_id)
        moderator_label = "-"
        moderator_cell = "-"
        if moderator is not None:
            moderator_label = f"@{moderator.username}" if moderator.username else str(moderator.tg_user_id)
            moderator_cell = (
                f"<a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, moderator_tg=str(moderator.tg_user_id))))}'>"
                f"{escape(moderator_label)}</a>"
            )

        moderation_note = escape((item.moderation_note or "-")[:160])

        feedback_risk = "low"
        if int(item.rating) <= 1:
            feedback_risk = "critical"
        elif int(item.rating) <= 2:
            feedback_risk = "high"
        elif int(item.rating) == 3:
            feedback_risk = "medium"

        feedback_priority = "normal"
        if item.moderated_at is None and int(item.rating) <= 2:
            feedback_priority = "urgent"
        elif item.moderated_at is None:
            feedback_priority = "high"
        row_context_attrs = _triage_row_context_attrs(
            risk_level=feedback_risk,
            priority_level=feedback_priority,
        )

        status_label = "Виден" if item.status == "VISIBLE" else "Скрыт"
        action_form = "-"
        if item.status == "VISIBLE":
            action_form = (
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/trade-feedback/hide'))}'>"
                f"<input type='hidden' name='feedback_id' value='{item.id}'>"
                f"<input type='hidden' name='return_to' value='{escape(return_to)}'>"
                f"{csrf_input}"
                "<input name='reason' placeholder='Причина скрытия' style='width:180px'>"
                "<button type='submit'>Скрыть</button></form>"
            )
        else:
            action_form = (
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/trade-feedback/unhide'))}'>"
                f"<input type='hidden' name='feedback_id' value='{item.id}'>"
                f"<input type='hidden' name='return_to' value='{escape(return_to)}'>"
                f"{csrf_input}"
                "<input name='reason' placeholder='Причина возврата' style='width:180px'>"
                "<button type='submit'>Показать</button></form>"
            )

        table_rows += (
            f"<tr data-row='{escape(f"{item.id} {auction.id} {author_label} {target_label} {item.status} {item.rating} {item.comment or ''} {item.moderation_note or ''}")}' "
            f"data-triage-row='1' data-row-id='{item.id}' tabindex='0'{row_context_attrs}>"
            f"<td>{_triage_controls_cell(item.id)}</td>"
            f"<td data-col='id'>{item.id}</td>"
            f"<td data-col='auction'><a href='{escape(_path_with_auth(request, f'/timeline/auction/{auction.id}'))}'>{escape(str(auction.id))}</a></td>"
            f"<td data-col='author'><a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, author_tg=str(author.tg_user_id), target_tg='')))}'>{escape(author_label)}</a></td>"
            f"<td data-col='target'><a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, target_tg=str(target.tg_user_id), author_tg='')))}'>{escape(target_label)}</a></td>"
            f"<td data-col='rating'>{item.rating}/5</td>"
            f"<td data-col='comment'>{escape((item.comment or '-')[:180])}</td>"
            f"<td data-col='status' data-status-cell='1'>{status_label}</td>"
            f"<td data-col='moderator'>{moderator_cell}</td>"
            f"<td data-col='note'>{moderation_note}</td>"
            f"<td data-col='created'>{escape(_fmt_ts(item.created_at))}</td>"
            f"<td data-col='moderated'>{escape(_fmt_ts(item.moderated_at))}</td>"
            f"<td data-col='actions'>{action_form}</td>"
            "</tr>"
        )
        table_rows += _triage_detail_row(
            item.id,
            col_count=13,
            title=f"Trade feedback #{item.id}",
            subtitle=f"Auction {auction.id} / {author_label} -> {target_label}",
        )

    if not table_rows:
        table_rows = "<tr><td colspan='13'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=page - 1)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=page + 1)))}'>Вперед →</a>"
        if has_next
        else ""
    )

    min_rating_text = "all" if min_rating_value is None else str(min_rating_value)
    author_filter_text = "all" if author_tg_value is None else str(author_tg_value)
    target_filter_text = "all" if target_tg_value is None else str(target_tg_value)
    moderator_filter_text = "all" if moderator_tg_value is None else str(moderator_tg_value)
    dense_toolbar = render_dense_list_toolbar(
        dense_config,
        density_query_builder=lambda value: _path_with_auth(
            request,
            _trade_feedback_path(page_value=0, density=value),
        ),
    )
    telemetry_panel = _render_workflow_preset_telemetry_panel(
        request,
        queue_context="feedback",
        segments=telemetry_segments,
        selected_preset_id=telemetry_preset_id,
        preset_filter_path_builder=lambda preset_id: _trade_feedback_path(
            page_value=page,
            telemetry_preset_id="" if preset_id is None else str(preset_id),
        ),
    )

    body = (
        f"{_render_app_header('Отзывы по сделкам', auth, 'Модерация репутации и спорных отзывов')}"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a>"
        f"<a href='{escape(_path_with_auth(request, '/manage/users'))}'>К пользователям</a></p>"
        f"{dense_toolbar}"
        f"{telemetry_panel}"
        f"{_triage_shortcut_hint()}"
        "<div class='toolbar'>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/trade-feedback'))}'>"
        f"<input type='hidden' name='status' value='{escape(status_value)}'>"
        f"<input type='hidden' name='moderated' value='{escape(moderated_value)}'>"
        f"<input type='hidden' name='density' value='{escape(dense_config.density)}'>"
        f"<input type='hidden' name='min_rating' value='{escape(min_rating_text if min_rating_text != 'all' else '')}'>"
        f"<input type='hidden' name='author_tg' value='{escape(author_filter_text if author_filter_text != 'all' else '')}'>"
        f"<input type='hidden' name='target_tg' value='{escape(target_filter_text if target_filter_text != 'all' else '')}'>"
        f"<input type='hidden' name='moderator_tg' value='{escape(moderator_filter_text if moderator_filter_text != 'all' else '')}'>"
        f"<input type='hidden' name='telemetry_preset_id' value='{escape(str(telemetry_preset_id) if telemetry_preset_id else '')}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='id / auction_id / username / tg id' style='width:320px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        "</div>"
        "<div class='toolbar'>"
        "<span>Статус:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, status='visible')))}'>Видимые</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, status='hidden')))}'>Скрытые</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, status='all')))}'>Все</a>"
        "<span>Оценка:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, min_rating='')))}'>all</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, min_rating='4')))}'>4+</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, min_rating='5')))}'>5</a>"
        "<span>Модерация:</span>"
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, moderated='all')))}'>all</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, moderated='only')))}'>только модерированные</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, moderated='none')))}'>без модерации</a>"
        "</div>"
        f"<p>Автор TG: {escape(author_filter_text)} | Получатель TG: {escape(target_filter_text)} | Модератор TG: {escape(moderator_filter_text)}</p>"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th>Pick</th><th data-col='id'>ID</th><th data-col='auction'>Auction</th><th data-col='author'>Автор</th><th data-col='target'>Кому</th><th data-col='rating'>Оценка</th><th data-col='comment'>Комментарий</th><th data-col='status'>Статус</th><th data-col='moderator'>Модератор</th><th data-col='note'>Примечание</th><th data-col='created'>Создано</th><th data-col='moderated'>Модерация</th><th data-col='actions'>Действия</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(_render_page("Trade Feedback", body))


@app.get("/auctions", response_class=HTMLResponse)
async def auctions(
    request: Request,
    status: str = "ACTIVE",
    page: int = 0,
    density: str | None = None,
) -> Response:
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
        seller_risk_map = await _load_user_risk_snapshot_map(
            session,
            user_ids=[item.seller_user_id for item in rows],
        )
        dense_config = await _load_dense_list_config(
            session,
            request=request,
            auth=auth,
            queue_key="auctions",
            requested_density=density,
            table_id="auctions-table",
            quick_filter_placeholder="auction id / seller / status",
        )

    default_risk_snapshot = evaluate_user_risk_snapshot(
        complaints_against=0,
        open_fraud_signals=0,
        has_active_blacklist=False,
        removed_bids=0,
    )
    def _auctions_path(*, page_value: int, status_value: str, density_value: str | None = None) -> str:
        query = {
            "status": status_value,
            "page": str(page_value),
            "density": density_value or dense_config.density,
        }
        return f"/auctions?{urlencode(query)}"

    table_rows = ""
    for item in rows:
        seller_risk = seller_risk_map.get(item.seller_user_id, default_risk_snapshot)
        table_rows += (
            f"<tr data-row='{escape(f"{item.id} {item.seller_user_id} {item.status} {item.start_price} {item.buyout_price or ''}")}'>"
            f"<td data-col='id'><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.id}'))}'>{escape(str(item.id))}</a></td>"
            f"<td data-col='seller'>{item.seller_user_id}</td>"
            f"<td data-col='risk'>{_risk_snapshot_inline_html(seller_risk)}</td>"
            f"<td data-col='start'>${item.start_price}</td>"
            f"<td data-col='buyout'>${item.buyout_price if item.buyout_price is not None else '-'}</td>"
            f"<td data-col='status'>{escape(str(item.status))}</td>"
            f"<td data-col='ends_at'>{escape(_fmt_ts(item.ends_at))}</td>"
            f"<td data-col='actions'><a href='{escape(_path_with_auth(request, f'/manage/auction/{item.id}'))}'>Управлять</a></td>"
            "</tr>"
        )
    if not table_rows:
        table_rows = "<tr><td colspan='8'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _auctions_path(page_value=page-1, status_value=status)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _auctions_path(page_value=page+1, status_value=status)))}'>Вперед →</a>"
        if has_next
        else ""
    )
    status_chips = " ".join(
        [
            f"<a class='chip' href='{escape(_path_with_auth(request, _auctions_path(page_value=0, status_value=item.value)))}'>{item.value}</a>"
            for item in AuctionStatus
        ]
    )
    dense_toolbar = render_dense_list_toolbar(
        dense_config,
        density_query_builder=lambda value: _path_with_auth(
            request,
            _auctions_path(page_value=0, status_value=status, density_value=value),
        ),
    )

    body = (
        f"{_render_app_header('Аукционы', auth, f'Статус: {status}') }"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"{dense_toolbar}"
        f"<div class='toolbar'><span>Фильтр:</span> {status_chips}</div>"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th data-col='id'>ID</th><th data-col='seller'>Seller UID</th><th data-col='risk'>Seller Risk</th><th data-col='start'>Start</th><th data-col='buyout'>Buyout</th><th data-col='status'>Status</th><th data-col='ends_at'>Ends At</th><th data-col='actions'>Actions</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
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
async def manage_user(
    request: Request,
    user_id: int,
    points_page: int = 1,
    points_filter: str = "all",
) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response

    now = datetime.now(UTC)
    points_page_size = 10
    points_page = max(points_page, 1)
    points_filter_value = _normalize_points_filter_query(points_filter)
    points_filter_query = _points_filter_query_value(points_filter_value)
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

        points_summary = await get_user_points_summary(session, user_id=user.id)
        points_total_items = await count_user_points_entries(
            session,
            user_id=user.id,
            event_type=points_filter_value,
        )
        points_total_pages = max((points_total_items + points_page_size - 1) // points_page_size, 1)
        if points_total_items > 0 and points_page > points_total_pages:
            points_page = points_total_pages

        points_entries = await list_user_points_entries(
            session,
            user_id=user.id,
            limit=points_page_size,
            offset=(points_page - 1) * points_page_size,
            event_type=points_filter_value,
        )
        boost_feedback_count = int(
            await session.scalar(
                select(func.count(FeedbackItem.id)).where(
                    FeedbackItem.submitter_user_id == user.id,
                    FeedbackItem.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_feedback_points_spent_total = int(
            await session.scalar(
                select(func.coalesce(func.sum(FeedbackItem.priority_boost_points_spent), 0)).where(
                    FeedbackItem.submitter_user_id == user.id,
                    FeedbackItem.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_guarantor_count = int(
            await session.scalar(
                select(func.count(GuarantorRequest.id)).where(
                    GuarantorRequest.submitter_user_id == user.id,
                    GuarantorRequest.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_guarantor_points_spent_total = int(
            await session.scalar(
                select(func.coalesce(func.sum(GuarantorRequest.priority_boost_points_spent), 0)).where(
                    GuarantorRequest.submitter_user_id == user.id,
                    GuarantorRequest.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_appeal_count = int(
            await session.scalar(
                select(func.count(Appeal.id)).where(
                    Appeal.appellant_user_id == user.id,
                    Appeal.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_appeal_points_spent_total = int(
            await session.scalar(
                select(func.coalesce(func.sum(Appeal.priority_boost_points_spent), 0)).where(
                    Appeal.appellant_user_id == user.id,
                    Appeal.priority_boosted_at.is_not(None),
                )
            )
            or 0
        )
        boost_points_spent_total = (
            boost_feedback_points_spent_total
            + boost_guarantor_points_spent_total
            + boost_appeal_points_spent_total
        )
        redemptions_used_today = await get_points_redemptions_used_today(
            session,
            user_id=user.id,
            now=now,
        )
        redemptions_used_this_week = await get_points_redemptions_used_this_week(
            session,
            user_id=user.id,
            now=now,
        )
        redemptions_spent_today = await get_points_redemptions_spent_today(
            session,
            user_id=user.id,
            now=now,
        )
        redemptions_spent_this_week = await get_points_redemptions_spent_this_week(
            session,
            user_id=user.id,
            now=now,
        )
        redemptions_spent_this_month = await get_points_redemptions_spent_this_month(
            session,
            user_id=user.id,
            now=now,
        )
        min_account_age_remaining = await get_points_redemption_account_age_remaining_seconds(
            session,
            user_id=user.id,
            min_account_age_seconds=settings.points_redemption_min_account_age_seconds,
            now=now,
        )

        trade_feedback_summary = await get_trade_feedback_summary(session, target_user_id=user.id)
        trade_feedback_received = await list_received_trade_feedback(session, target_user_id=user.id, limit=10)
        verification_status = await get_user_verification_status(session, tg_user_id=user.tg_user_id)

    can_ban_users = auth.can(SCOPE_USER_BAN)
    can_manage_roles = auth.can(SCOPE_ROLE_MANAGE)
    can_manage_trust = auth.can(SCOPE_TRUST_MANAGE)
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

    if can_manage_trust:
        if verification_status.is_verified:
            controls_blocks.append(
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/unverify'))}'>"
                f"<input type='hidden' name='target_tg_user_id' value='{user.tg_user_id}'>"
                f"<input type='hidden' name='return_to' value='{escape(f'/manage/user/{user.id}')}'>"
                f"{csrf_input}"
                "<button type='submit'>Снять верификацию</button></form>"
            )
        else:
            controls_blocks.append(
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/verify'))}'>"
                f"<input type='hidden' name='target_tg_user_id' value='{user.tg_user_id}'>"
                f"<input type='hidden' name='return_to' value='{escape(f'/manage/user/{user.id}')}'>"
                f"{csrf_input}"
                "<input name='custom_description' placeholder='Описание для Telegram (необязательно)' style='width:360px'>"
                "<button type='submit'>Подтвердить верификацию</button></form>"
            )

    controls = "<p><i>Только просмотр (нет прав на управление пользователями).</i></p>"
    if controls_blocks:
        controls = "<br>".join(controls_blocks)

    risk_snapshot = evaluate_user_risk_snapshot(
        complaints_against=complaints_against,
        open_fraud_signals=fraud_open,
        has_active_blacklist=active_blacklist_entry is not None,
        removed_bids=bids_removed,
        is_verified_user=verification_status.is_verified,
    )
    risk_reasons_text = "-"
    if risk_snapshot.reasons:
        risk_reasons_text = ", ".join(format_risk_reason_label(code) for code in risk_snapshot.reasons)

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

    points_rows = "".join(
        "<tr>"
        f"<td>{escape(_fmt_ts(item.created_at))}</td>"
        f"<td>{'+{}'.format(item.amount) if item.amount > 0 else item.amount}</td>"
        f"<td>{escape(_points_event_label(PointsEventType(item.event_type)))}</td>"
        f"<td>{escape(item.reason[:200])}</td>"
        "</tr>"
        for item in points_entries
    )
    if not points_rows:
        points_rows = "<tr><td colspan='4'><span class='empty-state'>Нет операций</span></td></tr>"

    trade_feedback_rows = "".join(
        "<tr>"
        f"<td>{view.item.id}</td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/timeline/auction/{view.auction.id}'))}'>{escape(str(view.auction.id))}</a></td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/manage/user/{view.author.id}'))}'>{escape(f'@{view.author.username}' if view.author.username else str(view.author.tg_user_id))}</a></td>"
        f"<td>{view.item.rating}/5</td>"
        f"<td>{'Виден' if view.item.status == 'VISIBLE' else 'Скрыт'}</td>"
        f"<td>{escape((view.item.comment or '-')[:180])}</td>"
        f"<td>{escape(_fmt_ts(view.item.created_at))}</td>"
        "</tr>"
        for view in trade_feedback_received
    )
    if not trade_feedback_rows:
        trade_feedback_rows = "<tr><td colspan='7'><span class='empty-state'>Нет отзывов</span></td></tr>"

    average_trade_rating_text = "-"
    if trade_feedback_summary.average_visible_rating is not None:
        average_trade_rating_text = f"{trade_feedback_summary.average_visible_rating:.1f}"

    def _points_manage_path(target_page: int, filter_query: str) -> str:
        query = urlencode({"points_page": str(target_page), "points_filter": filter_query})
        return f"/manage/user/{user.id}?{query}"

    points_manage_return_to = _points_manage_path(points_page, points_filter_query)

    points_prev_link = ""
    if points_page > 1:
        points_prev_link = (
            f"<a href='{escape(_path_with_auth(request, _points_manage_path(points_page - 1, points_filter_query)))}'>"
            "← Предыдущая страница</a>"
        )
    points_next_link = ""
    if points_page < points_total_pages:
        points_next_link = (
            f"<a href='{escape(_path_with_auth(request, _points_manage_path(points_page + 1, points_filter_query)))}'>"
            "Следующая страница →</a>"
        )
    points_pager = ""
    if points_prev_link or points_next_link:
        points_pager = f"<p>{points_prev_link} {' | ' if points_prev_link and points_next_link else ''}{points_next_link}</p>"

    points_filter_links = " ".join(
        (
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'all')))}'>all</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'feedback')))}'>feedback</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'manual')))}'>manual</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'boost')))}'>boost</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'gboost')))}'>gboost</a>",
            f"<a class='chip' href='{escape(_path_with_auth(request, _points_manage_path(1, 'aboost')))}'>aboost</a>",
        )
    )

    points_adjust_form = ""
    if can_manage_roles:
        points_action_id = secrets.token_hex(12)
        points_adjust_form = (
            "<div class='card'><h3>Ручная корректировка points</h3>"
            f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/points/adjust'))}'>"
            f"<input type='hidden' name='target_tg_user_id' value='{user.tg_user_id}'>"
            f"<input type='hidden' name='return_to' value='{escape(points_manage_return_to)}'>"
            f"<input type='hidden' name='action_id' value='{points_action_id}'>"
            f"{csrf_input}"
            "<input name='amount' placeholder='+10 или -5' style='width:140px' required>"
            "<input name='reason' placeholder='Причина корректировки' style='width:320px' required>"
            "<button type='submit'>Применить</button>"
            "</form></div>"
        )

    roles_text = ", ".join(sorted(role.value for role in user_roles)) if user_roles else "-"
    moderator_text = "yes" if has_moderator_access else "no"
    blacklist_status = "active" if active_blacklist_entry is not None else "no"
    verification_text = "verified" if verification_status.is_verified else "no"
    verification_desc = verification_status.custom_description or "-"
    feedback_boost_policy_status = "on" if settings.feedback_priority_boost_enabled else "off"
    guarantor_boost_policy_status = "on" if settings.guarantor_priority_boost_enabled else "off"
    appeal_boost_policy_status = "on" if settings.appeal_priority_boost_enabled else "off"
    global_daily_limit = max(settings.points_redemption_daily_limit, 0)
    global_daily_remaining = max(global_daily_limit - redemptions_used_today, 0)
    global_daily_limit_text = "без ограничений"
    if global_daily_limit > 0:
        global_daily_limit_text = f"{redemptions_used_today}/{global_daily_limit} (осталось {global_daily_remaining})"
    global_weekly_limit = max(settings.points_redemption_weekly_limit, 0)
    global_weekly_remaining = max(global_weekly_limit - redemptions_used_this_week, 0)
    global_weekly_limit_text = "без ограничений"
    if global_weekly_limit > 0:
        global_weekly_limit_text = (
            f"{redemptions_used_this_week}/{global_weekly_limit} (осталось {global_weekly_remaining})"
        )
    global_daily_spend_cap = max(settings.points_redemption_daily_spend_cap, 0)
    global_daily_spend_remaining = max(global_daily_spend_cap - redemptions_spent_today, 0)
    global_daily_spend_text = "без ограничений"
    if global_daily_spend_cap > 0:
        global_daily_spend_text = (
            f"{redemptions_spent_today}/{global_daily_spend_cap} points "
            f"(осталось {global_daily_spend_remaining})"
        )
    global_weekly_spend_cap = max(settings.points_redemption_weekly_spend_cap, 0)
    global_weekly_spend_remaining = max(global_weekly_spend_cap - redemptions_spent_this_week, 0)
    global_weekly_spend_text = "без ограничений"
    if global_weekly_spend_cap > 0:
        global_weekly_spend_text = (
            f"{redemptions_spent_this_week}/{global_weekly_spend_cap} points "
            f"(осталось {global_weekly_spend_remaining})"
        )
    global_monthly_spend_cap = max(settings.points_redemption_monthly_spend_cap, 0)
    global_monthly_spend_remaining = max(global_monthly_spend_cap - redemptions_spent_this_month, 0)
    global_monthly_spend_text = "без ограничений"
    if global_monthly_spend_cap > 0:
        global_monthly_spend_text = (
            f"{redemptions_spent_this_month}/{global_monthly_spend_cap} points "
            f"(осталось {global_monthly_spend_remaining})"
        )
    min_account_age_required = max(settings.points_redemption_min_account_age_seconds, 0)
    min_account_age_text = f"{min_account_age_required} сек"
    if min_account_age_required > 0:
        min_account_age_text = (
            f"{min_account_age_required} сек (осталось {min_account_age_remaining})"
        )
    min_earned_points_required = max(settings.points_redemption_min_earned_points, 0)
    min_earned_points_remaining = max(min_earned_points_required - points_summary.total_earned, 0)
    min_earned_points_text = f"{min_earned_points_required} points"
    if min_earned_points_required > 0:
        min_earned_points_text = (
            f"{min_earned_points_required} points (начислено {points_summary.total_earned}, "
            f"осталось {min_earned_points_remaining})"
        )

    risk_tone = "ok"
    if risk_snapshot.level == "MEDIUM":
        risk_tone = "warn"
    elif risk_snapshot.level == "HIGH":
        risk_tone = "critical"

    risk_cards = _kpi_grid(
        [
            _kpi_card("Ставок", str(bids_total)),
            _kpi_card("Снято ставок", str(bids_removed), tone="warn" if bids_removed > 0 else ""),
            _kpi_card("Жалоб создано", str(complaints_created)),
            _kpi_card("Жалоб на пользователя", str(complaints_against), tone="warn" if complaints_against > 0 else ""),
            _kpi_card("Фрод-сигналов", str(fraud_total)),
            _kpi_card("Открытых сигналов", str(fraud_open), tone="critical" if fraud_open > 0 else ""),
            _kpi_card("Риск-уровень", risk_snapshot.level, tone=risk_tone),
            _kpi_card("Риск-скор", str(risk_snapshot.score)),
        ]
    )
    points_cards = _kpi_grid(
        [
            _kpi_card("Points баланс", str(points_summary.balance)),
            _kpi_card("Начислено всего", f"+{points_summary.total_earned}"),
            _kpi_card("Списано всего", f"-{points_summary.total_spent}"),
            _kpi_card("Points операций", str(points_summary.operations_count)),
            _kpi_card("Бустов фидбека", str(boost_feedback_count)),
            _kpi_card("Бустов гаранта", str(boost_guarantor_count)),
            _kpi_card("Бустов апелляций", str(boost_appeal_count)),
            _kpi_card("Списано на бусты", f"-{boost_points_spent_total}"),
        ]
    )
    points_policy_cards = _kpi_grid(
        [
            _kpi_card(
                "Политика фидбек-буста",
                (
                    f"{feedback_boost_policy_status} | cost {settings.feedback_priority_boost_cost_points} | "
                    f"limit {settings.feedback_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.feedback_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card(
                "Политика буста гаранта",
                (
                    f"{guarantor_boost_policy_status} | cost {settings.guarantor_priority_boost_cost_points} | "
                    f"limit {settings.guarantor_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.guarantor_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card(
                "Политика буста апелляций",
                (
                    f"{appeal_boost_policy_status} | cost {settings.appeal_priority_boost_cost_points} | "
                    f"limit {settings.appeal_priority_boost_daily_limit}/day | "
                    f"cooldown {max(settings.appeal_priority_boost_cooldown_seconds, 0)}s"
                ),
            ),
            _kpi_card("Глобальная политика редимпшенов", "on" if settings.points_redemption_enabled else "off"),
            _kpi_card("Глобальный дневной лимит редимпшена", global_daily_limit_text),
            _kpi_card("Глобальный недельный лимит редимпшена", global_weekly_limit_text),
            _kpi_card("Глобальный лимит списания на бусты", global_daily_spend_text),
            _kpi_card("Глобальный недельный лимит списания на бусты", global_weekly_spend_text),
            _kpi_card("Глобальный месячный лимит списания на бусты", global_monthly_spend_text),
            _kpi_card("Минимальный остаток после буста", f"{max(settings.points_redemption_min_balance, 0)} points"),
            _kpi_card("Мин. возраст аккаунта для буста", min_account_age_text),
            _kpi_card("Мин. начислено points для буста", min_earned_points_text),
            _kpi_card("Глобальный кулдаун редимпшена", f"{max(settings.points_redemption_cooldown_seconds, 0)} сек"),
        ]
    )
    trade_cards = _kpi_grid(
        [
            _kpi_card("Отзывов получено", str(trade_feedback_summary.total_received)),
            _kpi_card("Видимых отзывов", str(trade_feedback_summary.visible_received)),
            _kpi_card("Скрытых отзывов", str(trade_feedback_summary.hidden_received)),
            _kpi_card("Средняя оценка (видимые)", average_trade_rating_text),
        ]
    )

    body = (
        f"{_render_app_header(f'Управление пользователем {user.id}', auth, 'Профиль модерации, риск и rewards')}"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/manage/users'))}'>К пользователям</a>"
        f"<a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"<p><b>TG User ID:</b> {user.tg_user_id} | <b>Username:</b> {escape(user.username or '-')}</p>"
        f"<p><b>Moderator:</b> {moderator_text} | <b>Roles:</b> {escape(roles_text)} | <b>Blacklisted:</b> {blacklist_status} | <b>Verified:</b> {verification_text}</p>"
        f"<p><b>Verification description:</b> {escape(verification_desc)}</p>"
        f"{risk_cards}"
        f"<p><b>Риск-факторы:</b> {escape(risk_reasons_text)}</p>"
        f"<div class='card'>{controls}</div>"
        "</div>"
        "<div class='section-card'>"
        "<h2>Rewards / points</h2>"
        f"{points_cards}"
        "<details class='details'><summary>Показать policy и лимиты</summary>"
        f"{points_policy_cards}"
        "</details>"
        f"{points_adjust_form}"
        f"<p><b>Фильтр:</b> {escape(points_filter_query)} | <b>Страница:</b> {points_page}/{points_total_pages} | "
        f"<b>Записей:</b> {points_total_items}</p>"
        f"<p>{points_filter_links}</p>"
        "<div class='table-wrap'><table><thead><tr><th>Created</th><th>Amount</th><th>Type</th><th>Reason</th></tr></thead>"
        f"<tbody>{points_rows}</tbody></table></div>"
        f"{points_pager}"
        "</div>"
        "<div class='section-card'>"
        "<h2>Репутация по сделкам</h2>"
        f"{trade_cards}"
        "<div class='table-wrap'><table><thead><tr><th>ID</th><th>Auction</th><th>Автор</th><th>Оценка</th><th>Статус</th><th>Комментарий</th><th>Создано</th></tr></thead>"
        f"<tbody>{trade_feedback_rows}</tbody></table></div>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, f'/trade-feedback?status=all&target_tg={user.tg_user_id}'))}'>Отзывы о пользователе (все)</a>"
        f"<a href='{escape(_path_with_auth(request, f'/trade-feedback?status=hidden&target_tg={user.tg_user_id}'))}'>Отзывы о пользователе (скрытые)</a>"
        f"<a href='{escape(_path_with_auth(request, f'/trade-feedback?status=all&author_tg={user.tg_user_id}'))}'>Отзывы, оставленные пользователем</a></p>"
        "</div>"
        "<div class='section-card'>"
        "<h2>Последние жалобы на пользователя</h2>"
        "<div class='table-wrap'><table><thead><tr><th>ID</th><th>Auction</th><th>Status</th><th>Reason</th><th>Created</th></tr></thead>"
        f"<tbody>{complaints_rows}</tbody></table></div>"
        "<h2>Последние фрод-сигналы по пользователю</h2>"
        "<div class='table-wrap'><table><thead><tr><th>ID</th><th>Auction</th><th>Score</th><th>Status</th><th>Created</th></tr></thead>"
        f"<tbody>{signal_rows}</tbody></table></div>"
        "</div>"
    )
    return HTMLResponse(_render_page("Manage User", body))


@app.get("/manage/users", response_class=HTMLResponse)
async def manage_users(
    request: Request,
    page: int = 0,
    q: str = "",
    density: str | None = None,
) -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response

    page = max(page, 0)
    page_size = 30
    offset = page * page_size
    query_value = q.strip()

    now = datetime.now(UTC)
    admin_ids = set(settings.parsed_admin_user_ids())

    def _manage_users_path(*, page_value: int, q_value: str | None = None, density_value: str | None = None) -> str:
        query = {
            "page": str(page_value),
            "q": q_value if q_value is not None else query_value,
            "density": density_value or dense_config.density,
        }
        return f"/manage/users?{urlencode(query)}"

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
        dense_config = await _load_dense_list_config(
            session,
            request=request,
            auth=auth,
            queue_key="manage_users",
            requested_density=density,
            table_id="manage-users-table",
            quick_filter_placeholder="id / tg / username",
        )
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
        risk_by_user_id = await _load_user_risk_snapshot_map(session, user_ids=user_ids, now=now)
        verified_user_ids = await load_verified_user_ids(session, user_ids=user_ids)

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
    default_risk_snapshot = evaluate_user_risk_snapshot(
        complaints_against=0,
        open_fraud_signals=0,
        has_active_blacklist=False,
        removed_bids=0,
    )
    for user in users:
        is_allowlist_mod = user.tg_user_id in admin_ids
        is_dynamic_mod = user.id in role_user_ids
        moderator_text = "yes (allowlist)" if is_allowlist_mod else ("yes" if is_dynamic_mod else "no")
        banned_text = "yes" if user.id in banned_user_ids else "no"
        verified_text = "yes" if user.id in verified_user_ids else "no"
        risk_snapshot = risk_by_user_id.get(user.id, default_risk_snapshot)

        rows.append(
            f"<tr data-row='{escape(f"{user.id} {user.tg_user_id} {user.username or ''} {moderator_text} {banned_text} {verified_text}")}'>"
            f"<td data-col='id'>{user.id}</td>"
            f"<td data-col='tg_user_id'>{user.tg_user_id}</td>"
            f"<td data-col='username'>{escape(user.username or '-')}</td>"
            f"<td data-col='moderator'>{moderator_text}</td>"
            f"<td data-col='banned'>{banned_text}</td>"
            f"<td data-col='verified'>{verified_text}</td>"
            f"<td data-col='risk'>{_risk_snapshot_inline_html(risk_snapshot)}</td>"
            f"<td data-col='created'>{escape(_fmt_ts(user.created_at))}</td>"
            f"<td data-col='manage'><a href='{escape(_path_with_auth(request, f'/manage/user/{user.id}'))}'>open</a></td>"
            "</tr>"
        )

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _manage_users_path(page_value=page-1)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _manage_users_path(page_value=page+1)))}'>Вперед →</a>"
        if has_next
        else ""
    )
    dense_toolbar = render_dense_list_toolbar(
        dense_config,
        density_query_builder=lambda value: _path_with_auth(
            request,
            _manage_users_path(page_value=0, density_value=value),
        ),
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
        f"{_render_app_header('Пользователи', auth, 'Поиск, роли и риск-профили')}"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"{dense_toolbar}"
        "<div class='toolbar'>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/manage/users'))}'>"
        f"<input type='hidden' name='density' value='{escape(dense_config.density)}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='tg id или username' style='width:240px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        "</div>"
        f"{_kpi_grid([_kpi_card('Пользователей на странице', str(len(users))), _kpi_card('Страница', str(page + 1)), _kpi_card('Поисковый запрос', escape(query_value) if query_value else '-')] )}"
        f"{moderator_grant_form}"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th data-col='id'>ID</th><th data-col='tg_user_id'>TG User ID</th><th data-col='username'>Username</th><th data-col='moderator'>Moderator</th><th data-col='banned'>Banned</th><th data-col='verified'>Verified</th><th data-col='risk'>Risk</th><th data-col='created'>Created</th><th data-col='manage'>Manage</th></tr></thead>"
        f"<tbody>{''.join(rows) if rows else '<tr><td colspan=9><span class=\"empty-state\">Нет записей</span></td></tr>'}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(_render_page("Manage Users", body))


@app.get("/violators", response_class=HTMLResponse)
async def violators(
    request: Request,
    status: str = "active",
    page: int = 0,
    q: str = "",
    by: str = "",
    created_from: str = "",
    created_to: str = "",
    density: str | None = None,
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    page = max(page, 0)
    page_size = 30
    offset = page * page_size
    query_value = q.strip()
    moderator_value = by.strip()
    created_from_value = created_from.strip()
    created_to_value = created_to.strip()
    status_value = status.strip().lower()
    if status_value not in {"active", "inactive", "all"}:
        raise HTTPException(status_code=400, detail="Invalid violators status filter")

    created_from_dt = _parse_ymd_filter(created_from_value, field_name="created_from")
    created_to_dt = _parse_ymd_filter(created_to_value, field_name="created_to")
    created_to_exclusive = created_to_dt + timedelta(days=1) if created_to_dt is not None else None
    if created_from_dt is not None and created_to_exclusive is not None and created_from_dt >= created_to_exclusive:
        raise HTTPException(status_code=400, detail="Invalid violators date range")

    actor_user = aliased(User)
    stmt = (
        select(BlacklistEntry, User, actor_user)
        .join(User, User.id == BlacklistEntry.user_id)
        .outerjoin(actor_user, actor_user.id == BlacklistEntry.created_by_user_id)
    )

    if status_value == "active":
        stmt = stmt.where(BlacklistEntry.is_active.is_(True))
    elif status_value == "inactive":
        stmt = stmt.where(BlacklistEntry.is_active.is_(False))

    if created_from_dt is not None:
        stmt = stmt.where(BlacklistEntry.created_at >= created_from_dt)
    if created_to_exclusive is not None:
        stmt = stmt.where(BlacklistEntry.created_at < created_to_exclusive)

    if query_value:
        if query_value.isdigit():
            stmt = stmt.where(
                or_(
                    User.username.ilike(f"%{query_value}%"),
                    BlacklistEntry.reason.ilike(f"%{query_value}%"),
                    User.tg_user_id == int(query_value),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    User.username.ilike(f"%{query_value}%"),
                    BlacklistEntry.reason.ilike(f"%{query_value}%"),
                )
            )

    if moderator_value:
        if moderator_value.isdigit():
            stmt = stmt.where(
                or_(
                    actor_user.tg_user_id == int(moderator_value),
                    actor_user.username.ilike(f"%{moderator_value}%"),
                )
            )
        else:
            stmt = stmt.where(actor_user.username.ilike(f"%{moderator_value}%"))

    stmt = (
        stmt.order_by(BlacklistEntry.created_at.desc())
        .offset(offset)
        .limit(page_size + 1)
    )

    async with SessionFactory() as session:
        dense_config = await _load_dense_list_config(
            session,
            request=request,
            auth=auth,
            queue_key="violators",
            requested_density=density,
            table_id="violators-table",
            quick_filter_placeholder="tg / username / reason / moderator",
        )
        rows = (await session.execute(stmt)).all()

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    base_query = {
        "q": query_value,
        "by": moderator_value,
        "created_from": created_from_value,
        "created_to": created_to_value,
        "density": dense_config.density,
    }

    def _violators_path(
        *,
        target_page: int,
        status_filter: str | None = None,
        density_value: str | None = None,
    ) -> str:
        query = {
            **base_query,
            "status": status_filter or status_value,
            "page": str(target_page),
            "density": density_value or dense_config.density,
        }
        return f"/violators?{urlencode(query)}"

    csrf_input = _csrf_hidden_input(request, auth)
    return_to = _violators_path(target_page=page)

    table_rows = ""
    for entry, target_user, actor in rows:
        actor_label = "-"
        if actor is not None:
            actor_label = f"@{actor.username}" if actor.username else str(actor.tg_user_id)
        target_label = f"@{target_user.username}" if target_user.username else "-"

        actions = "-"
        if entry.is_active:
            actions = (
                f"<form method='post' action='{escape(_path_with_auth(request, '/actions/user/unban'))}'>"
                f"<input type='hidden' name='target_tg_user_id' value='{target_user.tg_user_id}'>"
                f"<input type='hidden' name='return_to' value='{escape(return_to)}'>"
                f"{csrf_input}"
                "<input name='reason' placeholder='Причина разбана' style='width:180px' required>"
                "<button type='submit'>Разбанить</button>"
                "</form>"
            )

        table_rows += (
            f"<tr data-row='{escape(f"{entry.id} {target_user.tg_user_id} {target_user.username or ''} {entry.reason} {actor_label}")}'>"
            f"<td data-col='id'>{entry.id}</td>"
            f"<td data-col='tg_user_id'><a href='{escape(_path_with_auth(request, f'/manage/user/{target_user.id}'))}'>{target_user.tg_user_id}</a></td>"
            f"<td data-col='username'>{escape(target_label)}</td>"
            f"<td data-col='status'>{_violator_status_label('active' if entry.is_active else 'inactive')}</td>"
            f"<td data-col='reason'>{escape(entry.reason[:160])}</td>"
            f"<td data-col='actor'>{escape(actor_label)}</td>"
            f"<td data-col='created'>{escape(_fmt_ts(entry.created_at))}</td>"
            f"<td data-col='expires'>{escape(_fmt_ts(entry.expires_at))}</td>"
            f"<td data-col='actions'>{actions}</td>"
            "</tr>"
        )

    if not table_rows:
        table_rows = "<tr><td colspan='9'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, _violators_path(target_page=page - 1)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _violators_path(target_page=page + 1)))}'>Вперед →</a>"
        if has_next
        else ""
    )
    inactive_status_path = _violators_path(target_page=0, status_filter="inactive")
    all_status_path = _violators_path(target_page=0, status_filter="all")
    active_status_path = _violators_path(target_page=0, status_filter="active")
    dense_toolbar = render_dense_list_toolbar(
        dense_config,
        density_query_builder=lambda value: _path_with_auth(
            request,
            _violators_path(target_page=0, density_value=value),
        ),
    )

    body = (
        f"{_render_app_header('Нарушители', auth, 'Бан-лист, статусы и модераторские действия')}"
        "<div class='section-card'>"
        f"<p class='page-links'><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a>"
        f"<a href='{escape(_path_with_auth(request, '/manage/users'))}'>К пользователям</a></p>"
        f"{dense_toolbar}"
        "<div class='toolbar'>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/violators'))}'>"
        f"<input type='hidden' name='status' value='{escape(status_value)}'>"
        f"<input type='hidden' name='density' value='{escape(dense_config.density)}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='tg id / username / причина' style='width:280px'>"
        f"<input name='by' value='{escape(moderator_value)}' placeholder='модератор tg id / username' style='width:220px'>"
        f"<input name='created_from' value='{escape(created_from_value)}' placeholder='с YYYY-MM-DD' style='width:150px'>"
        f"<input name='created_to' value='{escape(created_to_value)}' placeholder='по YYYY-MM-DD' style='width:150px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        "</div>"
        "<div class='toolbar'>"
        f"<span>Фильтр:</span><a class='chip' href='{escape(_path_with_auth(request, active_status_path))}'>Активные</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, inactive_status_path))}'>Неактивные</a>"
        f"<a class='chip' href='{escape(_path_with_auth(request, all_status_path))}'>Все</a>"
        "</div>"
        f"<div class='table-wrap dense-list-shell' data-dense-list='{escape(dense_config.table_id)}' data-density='{escape(dense_config.density)}'><table id='{escape(dense_config.table_id)}'><thead><tr><th data-col='id'>ID</th><th data-col='tg_user_id'>TG User ID</th><th data-col='username'>Username</th><th data-col='status'>Статус</th><th data-col='reason'>Причина</th><th data-col='actor'>Кем</th><th data-col='created'>Создано</th><th data-col='expires'>Истекает</th><th data-col='actions'>Действия</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        f"{_pager_html(prev_link, next_link)}"
        f"{render_dense_list_script(dense_config)}"
        "</div>"
    )
    return HTMLResponse(_render_page("Нарушители", body))


@app.get("/appeals", response_class=HTMLResponse)
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
        stmt = stmt.where(Appeal.created_at.is_not(None), Appeal.created_at < fresh_cutoff, Appeal.created_at >= aging_cutoff)
    elif aging_value == "stale":
        stmt = stmt.where(Appeal.created_at.is_not(None), Appeal.created_at < aging_cutoff, Appeal.created_at >= stale_cutoff)
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
        stmt.order_by(Appeal.priority_boosted_at.desc().nullslast(), Appeal.created_at.desc(), Appeal.id.desc())
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
        appellant_label = f"@{appellant.username}" if appellant.username else str(appellant.tg_user_id)
        appellant_risk = appellant_risk_map.get(appellant.id, default_risk_snapshot)
        resolver_label = "-"
        if resolver is not None:
            resolver_label = f"@{resolver.username}" if resolver.username else str(resolver.tg_user_id)

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
            f"<tr data-row='{escape(f"{appeal.id} {appeal.appeal_ref} {source_label} {appellant_label} {appeal.status} {appeal.resolution_note or ''}")}' "
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
        f"<a href='{escape(_path_with_auth(request, _appeals_path(page_value=page-1)))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, _appeals_path(page_value=page+1)))}'>Вперед →</a>"
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


@app.post("/actions/dense-list/preferences")
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


@app.post("/actions/workflow-presets")
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
                        filters_payload=payload.get("filters") if isinstance(payload.get("filters"), dict) else {},
                        sort_payload=payload.get("sort") if isinstance(payload.get("sort"), dict) else {},
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
                        filters_payload=payload.get("filters") if isinstance(payload.get("filters"), dict) else {},
                        sort_payload=payload.get("sort") if isinstance(payload.get("sort"), dict) else {},
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


@app.get("/actions/workflow-presets/telemetry")
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


@app.get("/actions/triage/detail-section")
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


@app.post("/actions/triage/bulk")
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
                        results.append({"id": row_id, "ok": False, "reason_code": "missing", "message": "not found"})
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
                        results.append({"id": row_id, "ok": False, "reason_code": "unsupported", "message": "unsupported action"})
                elif queue_key == "signals":
                    item = await session.scalar(select(FraudSignal).where(FraudSignal.id == row_id))
                    if item is None:
                        results.append({"id": row_id, "ok": False, "reason_code": "missing", "message": "not found"})
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
                        results.append({"id": row_id, "ok": False, "reason_code": "unsupported", "message": "unsupported action"})
                elif queue_key == "trade_feedback":
                    if bulk_action not in {"hide", "unhide"}:
                        results.append({"id": row_id, "ok": False, "reason_code": "unsupported", "message": "unsupported action"})
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
                        results.append({"id": row_id, "ok": False, "reason_code": "service_error", "message": action_result.message})
                else:
                    if bulk_action not in {"in_review", "resolve", "reject"}:
                        results.append({"id": row_id, "ok": False, "reason_code": "unsupported", "message": "unsupported action"})
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
                        results.append({"id": row_id, "ok": False, "reason_code": "service_error", "message": action_result.message})

    return {"ok": True, "results": results}


@app.post("/actions/trade-feedback/hide")
async def action_hide_trade_feedback(
    request: Request,
    feedback_id: int = Form(...),
    reason: str = Form(""),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/trade-feedback?status=visible")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    normalized_reason = reason.strip()
    if not normalized_reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)

    async with SessionFactory() as session:
        async with session.begin():
            result = await set_trade_feedback_visibility(
                session,
                feedback_id=feedback_id,
                visible=False,
                moderator_user_id=actor_user_id,
                note=normalized_reason,
            )

            if result.ok and result.changed and result.item is not None:
                await log_moderation_action(
                    session,
                    actor_user_id=actor_user_id,
                    action=ModerationAction.HIDE_TRADE_FEEDBACK,
                    reason=f"[web] {normalized_reason or 'trade feedback hidden'}",
                    target_user_id=result.item.target_user_id,
                    auction_id=result.item.auction_id,
                    payload={
                        "feedback_id": feedback_id,
                        "source": "web",
                        "from_status": result.previous_status,
                        "to_status": result.current_status,
                        "moderation_note": normalized_reason or None,
                    },
                )

    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@app.post("/actions/trade-feedback/unhide")
async def action_unhide_trade_feedback(
    request: Request,
    feedback_id: int = Form(...),
    reason: str = Form(""),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/trade-feedback?status=visible")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    normalized_reason = reason.strip()
    if not normalized_reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)

    async with SessionFactory() as session:
        async with session.begin():
            result = await set_trade_feedback_visibility(
                session,
                feedback_id=feedback_id,
                visible=True,
                moderator_user_id=actor_user_id,
                note=normalized_reason,
            )

            if result.ok and result.changed and result.item is not None:
                await log_moderation_action(
                    session,
                    actor_user_id=actor_user_id,
                    action=ModerationAction.UNHIDE_TRADE_FEEDBACK,
                    reason=f"[web] {normalized_reason or 'trade feedback unhidden'}",
                    target_user_id=result.item.target_user_id,
                    auction_id=result.item.auction_id,
                    payload={
                        "feedback_id": feedback_id,
                        "source": "web",
                        "from_status": result.previous_status,
                        "to_status": result.current_status,
                        "moderation_note": normalized_reason or None,
                    },
                )

    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


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


@app.post("/actions/appeal/resolve")
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


@app.post("/actions/appeal/review")
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


@app.post("/actions/appeal/reject")
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


@app.post("/actions/user/verify")
async def action_verify_user(
    request: Request,
    target_tg_user_id: int = Form(...),
    custom_description: str | None = Form(None),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    confirmed: str | None = Form(None),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_TRUST_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/manage/users")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    description = (custom_description or "").strip()
    if not _is_confirmed(confirmed):
        return _render_confirmation_page(
            request,
            auth,
            title="Подтверждение верификации пользователя",
            message=f"Подтвердить Telegram верификацию для TG user {target_tg_user_id}?",
            action_path="/actions/user/verify",
            fields={
                "target_tg_user_id": str(target_tg_user_id),
                "custom_description": description,
                "return_to": target,
            },
            back_to=target,
        )

    token = settings.bot_token.strip()
    if not token:
        return _action_error_page(request, "BOT_TOKEN is empty", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        async with SessionFactory() as session:
            async with session.begin():
                result = await set_user_verification(
                    session,
                    bot,
                    actor_user_id=actor_user_id,
                    target_tg_user_id=target_tg_user_id,
                    verify=True,
                    custom_description=description,
                )
    finally:
        await bot.session.close()

    if not result.ok:
        return _action_error_page(request, result.message, back_to=target)
    return RedirectResponse(url=_path_with_auth(request, target), status_code=303)


@app.post("/actions/user/unverify")
async def action_unverify_user(
    request: Request,
    target_tg_user_id: int = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    confirmed: str | None = Form(None),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_TRUST_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/manage/users")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    if not _is_confirmed(confirmed):
        return _render_confirmation_page(
            request,
            auth,
            title="Подтверждение снятия верификации",
            message=f"Снять Telegram верификацию для TG user {target_tg_user_id}?",
            action_path="/actions/user/unverify",
            fields={
                "target_tg_user_id": str(target_tg_user_id),
                "return_to": target,
            },
            back_to=target,
        )

    token = settings.bot_token.strip()
    if not token:
        return _action_error_page(request, "BOT_TOKEN is empty", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        async with SessionFactory() as session:
            async with session.begin():
                result = await set_user_verification(
                    session,
                    bot,
                    actor_user_id=actor_user_id,
                    target_tg_user_id=target_tg_user_id,
                    verify=False,
                )
    finally:
        await bot.session.close()

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


@app.post("/actions/user/points/adjust")
async def action_adjust_user_points(
    request: Request,
    target_tg_user_id: int = Form(...),
    amount: str = Form(...),
    reason: str = Form(...),
    return_to: str | None = Form(None),
    csrf_token: str = Form(...),
    action_id: str = Form(...),
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_ROLE_MANAGE)
    if response is not None:
        return response

    target = _safe_return_to(return_to, "/manage/users")
    if not _validate_csrf_token(request, auth, csrf_token):
        return _csrf_failed_response(request, back_to=target)

    amount_value = _parse_signed_int(amount)
    if amount_value is None:
        return _action_error_page(request, "Amount must be an integer", back_to=target)
    if amount_value == 0:
        return _action_error_page(request, "Amount cannot be 0", back_to=target)

    reason = reason.strip()
    if not reason:
        return _action_error_page(request, "Reason is required", back_to=target)

    action_nonce = action_id.strip()
    if not action_nonce:
        return _action_error_page(request, "Action id is required", back_to=target)
    if len(action_nonce) > 64:
        return _action_error_page(request, "Action id is too long", back_to=target)

    actor_user_id = await _resolve_actor_user_id(auth)
    changed = False
    dedupe_key = ""
    async with SessionFactory() as session:
        async with session.begin():
            target_user = await session.scalar(select(User).where(User.tg_user_id == target_tg_user_id).with_for_update())
            if target_user is None:
                return _action_error_page(request, "User not found", back_to=target)

            dedupe_key = f"web:modpoints:{actor_user_id}:{target_user.id}:{action_nonce}"
            grant_result = await grant_points(
                session,
                user_id=target_user.id,
                amount=amount_value,
                event_type=PointsEventType.MANUAL_ADJUSTMENT,
                dedupe_key=dedupe_key,
                reason=reason,
                payload={
                    "source": "web",
                    "actor_user_id": actor_user_id,
                    "actor_tg_user_id": auth.tg_user_id,
                    "target_tg_user_id": target_tg_user_id,
                    "action_id": action_nonce,
                },
            )
            changed = grant_result.changed

            if changed:
                await log_moderation_action(
                    session,
                    actor_user_id=actor_user_id,
                    action=ModerationAction.ADJUST_USER_POINTS,
                    reason=f"[web] {reason}",
                    target_user_id=target_user.id,
                    payload={
                        "amount": amount_value,
                        "target_tg_user_id": target_tg_user_id,
                        "dedupe_key": dedupe_key,
                        "action_id": action_nonce,
                    },
                )

    logger.info(
        "[web] points adjustment %s for tg_user_id=%s amount=%s dedupe_key=%s",
        "applied" if changed else "dedupe-skip",
        target_tg_user_id,
        amount_value,
        dedupe_key,
    )
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
