from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from html import escape
import logging
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
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
from app.services.auction_service import refresh_auction_posts
from app.services.complaint_service import list_complaints
from app.services.fraud_service import list_fraud_signals
from app.services.moderation_dashboard_service import get_moderation_dashboard_snapshot
from app.services.timeline_service import build_auction_timeline_page
from app.services.rbac_service import (
    SCOPE_AUCTION_MANAGE,
    SCOPE_BID_MANAGE,
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
    get_points_redemptions_spent_today,
    get_points_redemptions_used_today,
    get_user_points_summary,
    grant_points,
    list_user_points_entries,
)
from app.services.risk_eval_service import UserRiskSnapshot, evaluate_user_risk_snapshot, format_risk_reason_label
from app.services.trade_feedback_service import (
    get_trade_feedback_summary,
    list_received_trade_feedback,
    set_trade_feedback_visibility,
)
from app.web.auth import (
    AdminAuthContext,
    build_admin_session_cookie,
    get_admin_auth_context,
    validate_telegram_login,
)

app = FastAPI(title="LiteAuction Admin", version="0.2.0")
logger = logging.getLogger(__name__)


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


def _parse_trade_feedback_status(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "visible", "hidden"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid trade feedback status filter")


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

    risk_map = {}
    for user_id in unique_user_ids:
        risk_map[user_id] = evaluate_user_risk_snapshot(
            complaints_against=complaint_counts.get(user_id, 0),
            open_fraud_signals=open_fraud_counts.get(user_id, 0),
            has_active_blacklist=user_id in active_blacklist_user_ids,
            removed_bids=removed_bid_counts.get(user_id, 0),
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
        "--bg-0:#f5f8f9;--bg-1:#e8f0f1;--ink:#1d2b33;--muted:#5d7078;"
        "--card:#ffffff;--line:#d2dde2;--soft:#eef4f6;--accent:#0f766e;--accent-ink:#0b4f4a;}"
        "*{box-sizing:border-box;}"
        "body{margin:0;font-family:'IBM Plex Sans','Trebuchet MS','Segoe UI',sans-serif;"
        "line-height:1.45;color:var(--ink);"
        "background:radial-gradient(1400px 600px at -10% -20%,#d7ece7 0%,transparent 70%),"
        "radial-gradient(1200px 500px at 120% -30%,#e5ebe2 0%,transparent 68%),"
        "linear-gradient(180deg,var(--bg-0),var(--bg-1));}"
        ".page-shell{max-width:1280px;margin:20px auto;padding:20px 24px;border:1px solid var(--line);"
        "border-radius:16px;background:rgba(255,255,255,0.86);backdrop-filter:blur(2px);"
        "box-shadow:0 20px 36px rgba(18,33,40,0.09);overflow:auto;}"
        "h1{margin:0 0 12px;font-size:30px;letter-spacing:0.2px;}"
        "h2,h3{margin-top:20px;margin-bottom:10px;}"
        "p{margin:10px 0;}"
        "table{border-collapse:separate;border-spacing:0;width:100%;margin-top:12px;background:var(--card);"
        "border:1px solid var(--line);border-radius:12px;overflow:hidden;}"
        "th,td{border-bottom:1px solid var(--line);padding:9px 10px;text-align:left;font-size:14px;vertical-align:top;}"
        "th{background:var(--soft);font-weight:600;color:var(--accent-ink);letter-spacing:0.2px;}"
        "tr:nth-child(even) td{background:#fbfdfe;}"
        "tr:last-child td{border-bottom:none;}"
        ".kpi{display:inline-block;margin:0 14px 10px 0;background:var(--card);border:1px solid var(--line);"
        "padding:10px 12px;border-radius:10px;box-shadow:0 4px 12px rgba(15,26,31,0.06);}"
        "a{color:var(--accent);text-decoration:none;font-weight:600;}"
        "a:hover{text-decoration:underline;}"
        "a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{"
        "outline:3px solid #ffb454;outline-offset:2px;}"
        ".chip{display:inline-block;padding:4px 9px;border-radius:999px;border:1px solid #b8c9c7;"
        "background:#f3faf8;font-size:12px;font-weight:600;margin-right:6px;margin-bottom:4px;}"
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
        "table{display:block;overflow-x:auto;white-space:nowrap;}"
        ".kpi{margin-right:8px;}}"
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
    scope_names = sorted(auth.scopes)
    scope_label = ",".join(scope_names) if scope_names else "read-only"
    return f"role={role}, via={via}, scopes={scope_label}"


def _scope_title(scope: str) -> str:
    if scope == SCOPE_AUCTION_MANAGE:
        return "управление аукционами"
    if scope == SCOPE_BID_MANAGE:
        return "управление ставками"
    if scope == SCOPE_USER_BAN:
        return "бан/разбан пользователей"
    if scope == SCOPE_ROLE_MANAGE:
        return "управление ролями"
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

    widget_html = ""
    if bot_username:
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

    engaged_with_private = max(
        snapshot.users_with_engagement - snapshot.users_engaged_without_private_start,
        0,
    )
    global_daily_limit_line = "<div class='kpi'><b>Global redemption daily limit:</b> unlimited</div>"
    if settings.points_redemption_daily_limit > 0:
        global_daily_limit_line = (
            f"<div class='kpi'><b>Global redemption daily limit:</b> {settings.points_redemption_daily_limit}/day</div>"
        )
    global_daily_spend_cap_line = "<div class='kpi'><b>Global redemption daily spend cap:</b> unlimited</div>"
    if settings.points_redemption_daily_spend_cap > 0:
        global_daily_spend_cap_line = (
            "<div class='kpi'><b>Global redemption daily spend cap:</b> "
            f"{settings.points_redemption_daily_spend_cap} points/day</div>"
        )

    body = (
        "<h1>LiteAuction Admin</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<div class='kpi'><b>Открытые жалобы:</b> {snapshot.open_complaints}</div>"
        f"<div class='kpi'><b>Открытые сигналы:</b> {snapshot.open_signals}</div>"
        f"<div class='kpi'><b>Активные аукционы:</b> {snapshot.active_auctions}</div>"
        f"<div class='kpi'><b>Ставок/час:</b> {snapshot.bids_last_hour}</div>"
        "<hr>"
        "<h2>Воронка онбординга / soft-gate</h2>"
        f"<div class='kpi'><b>Всего пользователей:</b> {snapshot.total_users}</div>"
        f"<div class='kpi'><b>Private /start:</b> {snapshot.users_private_started} ({_pct(snapshot.users_private_started, snapshot.total_users)})</div>"
        f"<div class='kpi'><b>С hint:</b> {snapshot.users_with_soft_gate_hint}</div>"
        f"<div class='kpi'><b>Hint за 24ч:</b> {snapshot.users_soft_gate_hint_last_24h}</div>"
        f"<div class='kpi'><b>Конверсия после hint:</b> {snapshot.users_converted_after_hint} ({_pct(snapshot.users_converted_after_hint, snapshot.users_with_soft_gate_hint)})</div>"
        f"<div class='kpi'><b>Ожидают после hint:</b> {snapshot.users_pending_after_hint}</div>"
        "<hr>"
        "<h2>Активность пользователей</h2>"
        f"<div class='kpi'><b>Пользователи со ставками:</b> {snapshot.users_with_bid_activity}</div>"
        f"<div class='kpi'><b>Пользователи с жалобами:</b> {snapshot.users_with_report_activity}</div>"
        f"<div class='kpi'><b>Уникально вовлеченные:</b> {snapshot.users_with_engagement}</div>"
        f"<div class='kpi'><b>Вовлеченные без private /start:</b> {snapshot.users_engaged_without_private_start}</div>"
        f"<div class='kpi'><b>Вовлеченные с private /start:</b> {engaged_with_private} ({_pct(engaged_with_private, snapshot.users_with_engagement)})</div>"
        "<hr>"
        "<h2>Points utility</h2>"
        f"<div class='kpi'><b>Активные points-пользователи (7д):</b> {snapshot.points_active_users_7d}</div>"
        f"<div class='kpi'><b>Пользователи с положительным балансом:</b> {snapshot.points_users_with_positive_balance}</div>"
        f"<div class='kpi'><b>Редимеры points (7д):</b> {snapshot.points_redeemers_7d} ({_pct(snapshot.points_redeemers_7d, snapshot.points_users_with_positive_balance)})</div>"
        f"<div class='kpi'><b>Редимеры фидбек-буста (7д):</b> {snapshot.points_feedback_boost_redeemers_7d}</div>"
        f"<div class='kpi'><b>Редимеры буста гаранта (7д):</b> {snapshot.points_guarantor_boost_redeemers_7d}</div>"
        f"<div class='kpi'><b>Редимеры буста апелляции (7д):</b> {snapshot.points_appeal_boost_redeemers_7d}</div>"
        f"<div class='kpi'><b>Points начислено (24ч):</b> +{snapshot.points_earned_24h}</div>"
        f"<div class='kpi'><b>Points списано (24ч):</b> -{snapshot.points_spent_24h}</div>"
        f"<div class='kpi'><b>Бустов фидбека (24ч):</b> {snapshot.feedback_boost_redeems_24h}</div>"
        f"<div class='kpi'><b>Бустов гаранта (24ч):</b> {snapshot.guarantor_boost_redeems_24h}</div>"
        f"<div class='kpi'><b>Бустов апелляций (24ч):</b> {snapshot.appeal_boost_redeems_24h}</div>"
        f"<div class='kpi'><b>Policy feedback:</b> {'on' if settings.feedback_priority_boost_enabled else 'off'} | cost {settings.feedback_priority_boost_cost_points} | limit {settings.feedback_priority_boost_daily_limit}/day | cooldown {max(settings.feedback_priority_boost_cooldown_seconds, 0)}s</div>"
        f"<div class='kpi'><b>Policy guarantor:</b> {'on' if settings.guarantor_priority_boost_enabled else 'off'} | cost {settings.guarantor_priority_boost_cost_points} | limit {settings.guarantor_priority_boost_daily_limit}/day | cooldown {max(settings.guarantor_priority_boost_cooldown_seconds, 0)}s</div>"
        f"<div class='kpi'><b>Policy appeal:</b> {'on' if settings.appeal_priority_boost_enabled else 'off'} | cost {settings.appeal_priority_boost_cost_points} | limit {settings.appeal_priority_boost_daily_limit}/day | cooldown {max(settings.appeal_priority_boost_cooldown_seconds, 0)}s</div>"
        f"{global_daily_limit_line}"
        f"{global_daily_spend_cap_line}"
        f"<div class='kpi'><b>Min balance after redemption:</b> {max(settings.points_redemption_min_balance, 0)} points</div>"
        f"<div class='kpi'><b>Global redemption cooldown:</b> {max(settings.points_redemption_cooldown_seconds, 0)}s</div>"
        "<hr>"
        "<ul>"
        f"<li><a href='{escape(_path_with_auth(request, '/complaints?status=OPEN'))}'>Открытые жалобы</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/signals?status=OPEN'))}'>Открытые фрод-сигналы</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/auctions?status=ACTIVE'))}'>Активные аукционы</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/auctions?status=FROZEN'))}'>Замороженные аукционы</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/appeals?status=open&source=all'))}'>Апелляции</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/trade-feedback?status=visible'))}'>Отзывы по сделкам</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/violators?status=active'))}'>Нарушители</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/manage/users'))}'>Управление пользователями</a></li>"
        f"<li><a href='{escape(_path_with_auth(request, '/logout'))}'>Выйти</a></li>"
        "</ul>"
    )
    return HTMLResponse(_render_page("LiteAuction Admin", body))


@app.get("/complaints", response_class=HTMLResponse)
async def complaints(request: Request, status: str = "OPEN", page: int = 0) -> Response:
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

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    table_rows = "".join(
        "<tr>"
        f"<td>{item.id}</td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.auction_id}'))}'>{escape(str(item.auction_id))}</a></td>"
        f"<td><a href='{escape(_path_with_auth(request, f'/manage/user/{item.reporter_user_id}'))}'>{item.reporter_user_id}</a></td>"
        f"<td>{escape(item.status)}</td>"
        f"<td>{escape(item.reason[:120])}</td>"
        f"<td>{escape(_fmt_ts(item.created_at))}</td>"
        "</tr>"
        for item in rows
    )
    if not table_rows:
        table_rows = "<tr><td colspan='6'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, f'/complaints?status={status}&page={page-1}'))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, f'/complaints?status={status}&page={page+1}'))}'>Вперед →</a>"
        if has_next
        else ""
    )

    body = (
        f"<h1>Жалобы ({escape(status)})</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        "<table><thead><tr><th>ID</th><th>Auction</th><th>Reporter UID</th><th>Status</th><th>Reason</th><th>Created</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
    )
    return HTMLResponse(_render_page("Complaints", body))


@app.get("/signals", response_class=HTMLResponse)
async def signals(request: Request, status: str = "OPEN", page: int = 0) -> Response:
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

    default_risk_snapshot = evaluate_user_risk_snapshot(
        complaints_against=0,
        open_fraud_signals=0,
        has_active_blacklist=False,
        removed_bids=0,
    )

    table_rows = ""
    for item in rows:
        user_risk = risk_by_user_id.get(item.user_id, default_risk_snapshot)
        table_rows += (
            "<tr>"
            f"<td>{item.id}</td>"
            f"<td><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.auction_id}'))}'>{escape(str(item.auction_id))}</a></td>"
            f"<td><a href='{escape(_path_with_auth(request, f'/manage/user/{item.user_id}'))}'>{item.user_id}</a></td>"
            f"<td>{_risk_snapshot_inline_html(user_risk)}</td>"
            f"<td>{item.score}</td>"
            f"<td>{escape(item.status)}</td>"
            f"<td>{escape(_fmt_ts(item.created_at))}</td>"
            "</tr>"
        )
    if not table_rows:
        table_rows = "<tr><td colspan='7'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, f'/signals?status={status}&page={page-1}'))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, f'/signals?status={status}&page={page+1}'))}'>Вперед →</a>"
        if has_next
        else ""
    )

    body = (
        f"<h1>Фрод-сигналы ({escape(status)})</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        "<table><thead><tr><th>ID</th><th>Auction</th><th>User ID</th><th>User Risk</th><th>Score</th><th>Status</th><th>Created</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
    )
    return HTMLResponse(_render_page("Fraud Signals", body))


@app.get("/trade-feedback", response_class=HTMLResponse)
async def trade_feedback(
    request: Request,
    status: str = "visible",
    page: int = 0,
    q: str = "",
    min_rating: str = "",
    author_tg: str = "",
    target_tg: str = "",
) -> Response:
    response, auth = _require_scope_permission(request, SCOPE_USER_BAN)
    if response is not None:
        return response

    page = max(page, 0)
    page_size = 30
    offset = page * page_size
    status_value = _parse_trade_feedback_status(status)
    query_value = q.strip()
    min_rating_value = _parse_trade_feedback_min_rating(min_rating)
    author_tg_value = _parse_optional_tg_user_id(author_tg)
    target_tg_value = _parse_optional_tg_user_id(target_tg)

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

    if min_rating_value is not None:
        stmt = stmt.where(TradeFeedback.rating >= min_rating_value)

    if author_tg_value is not None:
        stmt = stmt.where(author_user.tg_user_id == author_tg_value)

    if target_tg_value is not None:
        stmt = stmt.where(target_user.tg_user_id == target_tg_value)

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

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    base_query = {
        "status": status_value,
        "q": query_value,
    }
    if min_rating_value is not None:
        base_query["min_rating"] = str(min_rating_value)
    if author_tg_value is not None:
        base_query["author_tg"] = str(author_tg_value)
    if target_tg_value is not None:
        base_query["target_tg"] = str(target_tg_value)

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
        if moderator is not None:
            moderator_label = f"@{moderator.username}" if moderator.username else str(moderator.tg_user_id)

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
            "<tr>"
            f"<td>{item.id}</td>"
            f"<td><a href='{escape(_path_with_auth(request, f'/timeline/auction/{auction.id}'))}'>{escape(str(auction.id))}</a></td>"
            f"<td><a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, author_tg=str(author.tg_user_id), target_tg='')))}'>{escape(author_label)}</a></td>"
            f"<td><a href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, target_tg=str(target.tg_user_id), author_tg='')))}'>{escape(target_label)}</a></td>"
            f"<td>{item.rating}/5</td>"
            f"<td>{escape((item.comment or '-')[:180])}</td>"
            f"<td>{status_label}</td>"
            f"<td>{escape(moderator_label)}</td>"
            f"<td>{escape(_fmt_ts(item.created_at))}</td>"
            f"<td>{escape(_fmt_ts(item.moderated_at))}</td>"
            f"<td>{action_form}</td>"
            "</tr>"
        )

    if not table_rows:
        table_rows = "<tr><td colspan='11'><span class='empty-state'>Нет записей</span></td></tr>"

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

    body = (
        "<h1>Отзывы по сделкам</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a> | "
        f"<a href='{escape(_path_with_auth(request, '/manage/users'))}'>К пользователям</a></p>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/trade-feedback'))}'>"
        f"<input type='hidden' name='status' value='{escape(status_value)}'>"
        f"<input type='hidden' name='min_rating' value='{escape(min_rating_text if min_rating_text != 'all' else '')}'>"
        f"<input type='hidden' name='author_tg' value='{escape(author_filter_text if author_filter_text != 'all' else '')}'>"
        f"<input type='hidden' name='target_tg' value='{escape(target_filter_text if target_filter_text != 'all' else '')}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='id / auction_id / username / tg id' style='width:320px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        f"<p>Статус: "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, status='visible')))}'>Видимые</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, status='hidden')))}'>Скрытые</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, status='all')))}'>Все</a></p>"
        f"<p>Оценка: "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, min_rating='')))}'>all</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, min_rating='4')))}'>4+</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, _trade_feedback_path(page_value=0, min_rating='5')))}'>5</a></p>"
        f"<p>Автор TG: {escape(author_filter_text)} | Получатель TG: {escape(target_filter_text)}</p>"
        "<table><thead><tr><th>ID</th><th>Auction</th><th>Автор</th><th>Кому</th><th>Оценка</th><th>Комментарий</th><th>Статус</th><th>Модератор</th><th>Создано</th><th>Модерация</th><th>Действия</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
    )
    return HTMLResponse(_render_page("Trade Feedback", body))


@app.get("/auctions", response_class=HTMLResponse)
async def auctions(request: Request, status: str = "ACTIVE", page: int = 0) -> Response:
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

    default_risk_snapshot = evaluate_user_risk_snapshot(
        complaints_against=0,
        open_fraud_signals=0,
        has_active_blacklist=False,
        removed_bids=0,
    )
    table_rows = ""
    for item in rows:
        seller_risk = seller_risk_map.get(item.seller_user_id, default_risk_snapshot)
        table_rows += (
            "<tr>"
            f"<td><a href='{escape(_path_with_auth(request, f'/timeline/auction/{item.id}'))}'>{escape(str(item.id))}</a></td>"
            f"<td>{item.seller_user_id}</td>"
            f"<td>{_risk_snapshot_inline_html(seller_risk)}</td>"
            f"<td>${item.start_price}</td>"
            f"<td>${item.buyout_price if item.buyout_price is not None else '-'}</td>"
            f"<td>{escape(str(item.status))}</td>"
            f"<td>{escape(_fmt_ts(item.ends_at))}</td>"
            f"<td><a href='{escape(_path_with_auth(request, f'/manage/auction/{item.id}'))}'>Управлять</a></td>"
            "</tr>"
        )
    if not table_rows:
        table_rows = "<tr><td colspan='8'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, f'/auctions?status={status}&page={page-1}'))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, f'/auctions?status={status}&page={page+1}'))}'>Вперед →</a>"
        if has_next
        else ""
    )

    body = (
        f"<h1>Аукционы ({escape(status)})</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        "<table><thead><tr><th>ID</th><th>Seller UID</th><th>Seller Risk</th><th>Start</th><th>Buyout</th><th>Status</th><th>Ends At</th><th>Actions</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
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
        redemptions_spent_today = await get_points_redemptions_spent_today(
            session,
            user_id=user.id,
            now=now,
        )

        trade_feedback_summary = await get_trade_feedback_summary(session, target_user_id=user.id)
        trade_feedback_received = await list_received_trade_feedback(session, target_user_id=user.id, limit=10)

    can_ban_users = auth.can(SCOPE_USER_BAN)
    can_manage_roles = auth.can(SCOPE_ROLE_MANAGE)
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

    controls = "<p><i>Только просмотр (нет прав на управление пользователями).</i></p>"
    if controls_blocks:
        controls = "<br>".join(controls_blocks)

    risk_snapshot = evaluate_user_risk_snapshot(
        complaints_against=complaints_against,
        open_fraud_signals=fraud_open,
        has_active_blacklist=active_blacklist_entry is not None,
        removed_bids=bids_removed,
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
    feedback_boost_policy_status = "on" if settings.feedback_priority_boost_enabled else "off"
    guarantor_boost_policy_status = "on" if settings.guarantor_priority_boost_enabled else "off"
    appeal_boost_policy_status = "on" if settings.appeal_priority_boost_enabled else "off"
    global_daily_limit = max(settings.points_redemption_daily_limit, 0)
    global_daily_remaining = max(global_daily_limit - redemptions_used_today, 0)
    global_daily_limit_text = "без ограничений"
    if global_daily_limit > 0:
        global_daily_limit_text = f"{redemptions_used_today}/{global_daily_limit} (осталось {global_daily_remaining})"
    global_daily_spend_cap = max(settings.points_redemption_daily_spend_cap, 0)
    global_daily_spend_remaining = max(global_daily_spend_cap - redemptions_spent_today, 0)
    global_daily_spend_text = "без ограничений"
    if global_daily_spend_cap > 0:
        global_daily_spend_text = (
            f"{redemptions_spent_today}/{global_daily_spend_cap} points "
            f"(осталось {global_daily_spend_remaining})"
        )

    body = (
        f"<h1>Управление пользователем {user.id}</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/manage/users'))}'>К пользователям</a> | "
        f"<a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"<p><b>TG User ID:</b> {user.tg_user_id} | <b>Username:</b> {escape(user.username or '-')}</p>"
        f"<p><b>Moderator:</b> {moderator_text} | <b>Roles:</b> {escape(roles_text)} | <b>Blacklisted:</b> {blacklist_status}</p>"
        f"<div class='kpi'><b>Ставок:</b> {bids_total}</div>"
        f"<div class='kpi'><b>Снято ставок:</b> {bids_removed}</div>"
        f"<div class='kpi'><b>Жалоб создано:</b> {complaints_created}</div>"
        f"<div class='kpi'><b>Жалоб на пользователя:</b> {complaints_against}</div>"
        f"<div class='kpi'><b>Фрод-сигналов:</b> {fraud_total}</div>"
        f"<div class='kpi'><b>Открытых сигналов:</b> {fraud_open}</div>"
        f"<div class='kpi'><b>Риск-уровень:</b> {risk_snapshot.level}</div>"
        f"<div class='kpi'><b>Риск-скор:</b> {risk_snapshot.score}</div>"
        f"<p><b>Риск-факторы:</b> {escape(risk_reasons_text)}</p>"
        f"<div class='kpi'><b>Points баланс:</b> {points_summary.balance}</div>"
        f"<div class='kpi'><b>Начислено всего:</b> +{points_summary.total_earned}</div>"
        f"<div class='kpi'><b>Списано всего:</b> -{points_summary.total_spent}</div>"
        f"<div class='kpi'><b>Points операций:</b> {points_summary.operations_count}</div>"
        f"<div class='kpi'><b>Бустов фидбека:</b> {boost_feedback_count}</div>"
        f"<div class='kpi'><b>Бустов гаранта:</b> {boost_guarantor_count}</div>"
        f"<div class='kpi'><b>Бустов апелляций:</b> {boost_appeal_count}</div>"
        f"<div class='kpi'><b>Списано на бусты:</b> -{boost_points_spent_total}</div>"
        f"<div class='kpi'><b>Политика фидбек-буста:</b> {feedback_boost_policy_status} | cost {settings.feedback_priority_boost_cost_points} | limit {settings.feedback_priority_boost_daily_limit}/day | cooldown {max(settings.feedback_priority_boost_cooldown_seconds, 0)}s</div>"
        f"<div class='kpi'><b>Политика буста гаранта:</b> {guarantor_boost_policy_status} | cost {settings.guarantor_priority_boost_cost_points} | limit {settings.guarantor_priority_boost_daily_limit}/day | cooldown {max(settings.guarantor_priority_boost_cooldown_seconds, 0)}s</div>"
        f"<div class='kpi'><b>Политика буста апелляций:</b> {appeal_boost_policy_status} | cost {settings.appeal_priority_boost_cost_points} | limit {settings.appeal_priority_boost_daily_limit}/day | cooldown {max(settings.appeal_priority_boost_cooldown_seconds, 0)}s</div>"
        f"<div class='kpi'><b>Глобальный дневной лимит редимпшена:</b> {global_daily_limit_text}</div>"
        f"<div class='kpi'><b>Глобальный лимит списания на бусты:</b> {global_daily_spend_text}</div>"
        f"<div class='kpi'><b>Минимальный остаток после буста:</b> {max(settings.points_redemption_min_balance, 0)} points</div>"
        f"<div class='kpi'><b>Глобальный кулдаун редимпшена:</b> {max(settings.points_redemption_cooldown_seconds, 0)} сек</div>"
        f"<div class='kpi'><b>Отзывов получено:</b> {trade_feedback_summary.total_received}</div>"
        f"<div class='kpi'><b>Видимых отзывов:</b> {trade_feedback_summary.visible_received}</div>"
        f"<div class='kpi'><b>Скрытых отзывов:</b> {trade_feedback_summary.hidden_received}</div>"
        f"<div class='kpi'><b>Средняя оценка (видимые):</b> {average_trade_rating_text}</div>"
        f"<div class='card'>{controls}</div>"
        "<h2>Rewards / points</h2>"
        f"{points_adjust_form}"
        f"<p><b>Фильтр:</b> {escape(points_filter_query)} | <b>Страница:</b> {points_page}/{points_total_pages} | "
        f"<b>Записей:</b> {points_total_items}</p>"
        f"<p>{points_filter_links}</p>"
        "<table><thead><tr><th>Created</th><th>Amount</th><th>Type</th><th>Reason</th></tr></thead>"
        f"<tbody>{points_rows}</tbody></table>"
        f"{points_pager}"
        "<h2>Репутация по сделкам</h2>"
        "<table><thead><tr><th>ID</th><th>Auction</th><th>Автор</th><th>Оценка</th><th>Статус</th><th>Комментарий</th><th>Создано</th></tr></thead>"
        f"<tbody>{trade_feedback_rows}</tbody></table>"
        f"<p><a href='{escape(_path_with_auth(request, f'/trade-feedback?status=all&q={user.tg_user_id}'))}'>Открыть отзывы пользователя в модерации</a></p>"
        "<h2>Последние жалобы на пользователя</h2>"
        "<table><thead><tr><th>ID</th><th>Auction</th><th>Status</th><th>Reason</th><th>Created</th></tr></thead>"
        f"<tbody>{complaints_rows}</tbody></table>"
        "<h2>Последние фрод-сигналы по пользователю</h2>"
        "<table><thead><tr><th>ID</th><th>Auction</th><th>Score</th><th>Status</th><th>Created</th></tr></thead>"
        f"<tbody>{signal_rows}</tbody></table>"
    )
    return HTMLResponse(_render_page("Manage User", body))


@app.get("/manage/users", response_class=HTMLResponse)
async def manage_users(request: Request, page: int = 0, q: str = "") -> Response:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response

    page = max(page, 0)
    page_size = 30
    offset = page * page_size
    query_value = q.strip()

    now = datetime.now(UTC)
    admin_ids = set(settings.parsed_admin_user_ids())

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
        risk_snapshot = risk_by_user_id.get(user.id, default_risk_snapshot)

        rows.append(
            "<tr>"
            f"<td>{user.id}</td>"
            f"<td>{user.tg_user_id}</td>"
            f"<td>{escape(user.username or '-')}</td>"
            f"<td>{moderator_text}</td>"
            f"<td>{banned_text}</td>"
            f"<td>{_risk_snapshot_inline_html(risk_snapshot)}</td>"
            f"<td>{escape(_fmt_ts(user.created_at))}</td>"
            f"<td><a href='{escape(_path_with_auth(request, f'/manage/user/{user.id}'))}'>open</a></td>"
            "</tr>"
        )

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, f'/manage/users?page={page-1}&q={query_value}'))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, f'/manage/users?page={page+1}&q={query_value}'))}'>Вперед →</a>"
        if has_next
        else ""
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
        "<h1>Пользователи</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a></p>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/manage/users'))}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='tg id или username' style='width:240px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        f"{moderator_grant_form}"
        "<table><thead><tr><th>ID</th><th>TG User ID</th><th>Username</th><th>Moderator</th><th>Banned</th><th>Risk</th><th>Created</th><th>Manage</th></tr></thead>"
        f"<tbody>{''.join(rows) if rows else '<tr><td colspan=8><span class=\"empty-state\">Нет записей</span></td></tr>'}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
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
        rows = (await session.execute(stmt)).all()

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    base_query = {
        "status": status_value,
        "q": query_value,
        "by": moderator_value,
        "created_from": created_from_value,
        "created_to": created_to_value,
    }

    def _violators_path(*, target_page: int) -> str:
        return f"/violators?{urlencode({**base_query, 'page': str(target_page)})}"

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
            "<tr>"
            f"<td>{entry.id}</td>"
            f"<td><a href='{escape(_path_with_auth(request, f'/manage/user/{target_user.id}'))}'>{target_user.tg_user_id}</a></td>"
            f"<td>{escape(target_label)}</td>"
            f"<td>{_violator_status_label('active' if entry.is_active else 'inactive')}</td>"
            f"<td>{escape(entry.reason[:160])}</td>"
            f"<td>{escape(actor_label)}</td>"
            f"<td>{escape(_fmt_ts(entry.created_at))}</td>"
            f"<td>{escape(_fmt_ts(entry.expires_at))}</td>"
            f"<td>{actions}</td>"
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
    active_status_path = f"/violators?{urlencode({**base_query, 'status': 'active', 'page': '0'})}"
    inactive_status_path = f"/violators?{urlencode({**base_query, 'status': 'inactive', 'page': '0'})}"
    all_status_path = f"/violators?{urlencode({**base_query, 'status': 'all', 'page': '0'})}"

    body = (
        "<h1>Нарушители</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a> | "
        f"<a href='{escape(_path_with_auth(request, '/manage/users'))}'>К пользователям</a></p>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/violators'))}'>"
        f"<input type='hidden' name='status' value='{escape(status_value)}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='tg id / username / причина' style='width:280px'>"
        f"<input name='by' value='{escape(moderator_value)}' placeholder='модератор tg id / username' style='width:220px'>"
        f"<input name='created_from' value='{escape(created_from_value)}' placeholder='с YYYY-MM-DD' style='width:150px'>"
        f"<input name='created_to' value='{escape(created_to_value)}' placeholder='по YYYY-MM-DD' style='width:150px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        f"<p>Фильтр: "
        f"<a class='chip' href='{escape(_path_with_auth(request, active_status_path))}'>Активные</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, inactive_status_path))}'>Неактивные</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, all_status_path))}'>Все</a></p>"
        "<table><thead><tr><th>ID</th><th>TG User ID</th><th>Username</th><th>Статус</th><th>Причина</th><th>Кем</th><th>Создано</th><th>Истекает</th><th>Действия</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
    )
    return HTMLResponse(_render_page("Нарушители", body))


@app.get("/appeals", response_class=HTMLResponse)
async def appeals(
    request: Request,
    status: str = "open",
    source: str = "all",
    overdue: str = "all",
    escalated: str = "all",
    page: int = 0,
    q: str = "",
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
    now = datetime.now(UTC)

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
        stmt = stmt.where(
            Appeal.status.in_([AppealStatus.OPEN, AppealStatus.IN_REVIEW]),
            Appeal.sla_deadline_at.is_not(None),
            Appeal.sla_deadline_at <= now,
        )
    elif overdue_value == "none":
        stmt = stmt.where(
            or_(
                Appeal.status.notin_([AppealStatus.OPEN, AppealStatus.IN_REVIEW]),
                Appeal.sla_deadline_at.is_(None),
                Appeal.sla_deadline_at > now,
            )
        )

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
        rows = (await session.execute(stmt)).all()
        has_next = len(rows) > page_size
        rows = rows[:page_size]
        appellant_risk_map = await _load_user_risk_snapshot_map(
            session,
            user_ids=[appellant.id for _, appellant, _ in rows],
            now=now,
        )

    return_to = (
        f"/appeals?status={status_value}&source={source_value}&overdue={overdue_value}&escalated={escalated_value}&page={page}&q={query_value}"
    )
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
        escalation_marker = _appeal_escalation_marker(appeal)
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
            "<tr>"
            f"<td>{appeal.id}</td>"
            f"<td>{escape(appeal.appeal_ref)}</td>"
            f"<td>{escape(source_label)}</td>"
            f"<td><a href='{escape(_path_with_auth(request, f'/manage/user/{appellant.id}'))}'>{escape(appellant_label)}</a></td>"
            f"<td>{_risk_snapshot_inline_html(appellant_risk)}</td>"
            f"<td>{escape(status_label)}</td>"
            f"<td>{escape((appeal.resolution_note or '-')[:160])}</td>"
            f"<td>{escape(resolver_label)}</td>"
            f"<td>{escape(_fmt_ts(appeal.created_at))}</td>"
            f"<td>{escape(sla_state_label)}</td>"
            f"<td>{escape(_fmt_ts(appeal.sla_deadline_at))}</td>"
            f"<td>{escape(escalation_marker)}</td>"
            f"<td>{escape(_fmt_ts(appeal.resolved_at))}</td>"
            f"<td>{actions}</td>"
            "</tr>"
        )

    if not table_rows:
        table_rows = "<tr><td colspan='14'><span class='empty-state'>Нет записей</span></td></tr>"

    prev_link = (
        f"<a href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source={source_value}&overdue={overdue_value}&escalated={escalated_value}&page={page-1}&q={query_value}'))}'>← Назад</a>"
        if page > 0
        else ""
    )
    next_link = (
        f"<a href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source={source_value}&overdue={overdue_value}&escalated={escalated_value}&page={page+1}&q={query_value}'))}'>Вперед →</a>"
        if has_next
        else ""
    )

    body = (
        "<h1>Апелляции</h1>"
        f"<p><b>Access:</b> {escape(_role_badge(auth))}</p>"
        f"<p><a href='{escape(_path_with_auth(request, '/'))}'>На главную</a> | "
        f"<a href='{escape(_path_with_auth(request, '/violators?status=active'))}'>К нарушителям</a></p>"
        f"<form method='get' action='{escape(_path_with_auth(request, '/appeals'))}'>"
        f"<input type='hidden' name='status' value='{escape(status_value)}'>"
        f"<input type='hidden' name='source' value='{escape(source_value)}'>"
        f"<input type='hidden' name='overdue' value='{escape(overdue_value)}'>"
        f"<input type='hidden' name='escalated' value='{escape(escalated_value)}'>"
        f"<input name='q' value='{escape(query_value)}' placeholder='референс / tg id / username' style='width:300px'>"
        "<button type='submit'>Поиск</button>"
        "</form>"
        f"<p>Статус: "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status=open&source={source_value}&overdue={overdue_value}&escalated={escalated_value}&q={query_value}'))}'>Открытые</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status=in_review&source={source_value}&overdue={overdue_value}&escalated={escalated_value}&q={query_value}'))}'>На рассмотрении</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status=resolved&source={source_value}&overdue={overdue_value}&escalated={escalated_value}&q={query_value}'))}'>Удовлетворенные</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status=rejected&source={source_value}&overdue={overdue_value}&escalated={escalated_value}&q={query_value}'))}'>Отклоненные</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status=all&source={source_value}&overdue={overdue_value}&escalated={escalated_value}&q={query_value}'))}'>Все</a></p>"
        f"<p>Источник: "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source=complaint&overdue={overdue_value}&escalated={escalated_value}&q={query_value}'))}'>Жалобы</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source=risk&overdue={overdue_value}&escalated={escalated_value}&q={query_value}'))}'>Фрод</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source=manual&overdue={overdue_value}&escalated={escalated_value}&q={query_value}'))}'>Ручные</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source=all&overdue={overdue_value}&escalated={escalated_value}&q={query_value}'))}'>Все</a></p>"
        f"<p>SLA: "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source={source_value}&overdue=all&escalated={escalated_value}&q={query_value}'))}'>Все</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source={source_value}&overdue=only&escalated={escalated_value}&q={query_value}'))}'>Просроченные</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source={source_value}&overdue=none&escalated={escalated_value}&q={query_value}'))}'>Непросроченные</a></p>"
        f"<p>Эскалация: "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source={source_value}&overdue={overdue_value}&escalated=all&q={query_value}'))}'>Все</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source={source_value}&overdue={overdue_value}&escalated=only&q={query_value}'))}'>Эскалированные</a> "
        f"<a class='chip' href='{escape(_path_with_auth(request, f'/appeals?status={status_value}&source={source_value}&overdue={overdue_value}&escalated=none&q={query_value}'))}'>Без эскалации</a></p>"
        "<table><thead><tr><th>ID</th><th>Референс</th><th>Источник</th><th>Апеллянт</th><th>Риск апеллянта</th><th>Статус</th><th>Решение</th><th>Модератор</th><th>Создано</th><th>SLA статус</th><th>SLA дедлайн</th><th>Эскалация</th><th>Закрыто</th><th>Действия</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table>"
        f"<p>{prev_link} {' | ' if prev_link and next_link else ''} {next_link}</p>"
    )
    return HTMLResponse(_render_page("Апелляции", body))


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

    actor_user_id = await _resolve_actor_user_id(auth)
    normalized_reason = reason.strip()
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

    actor_user_id = await _resolve_actor_user_id(auth)
    normalized_reason = reason.strip()
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
