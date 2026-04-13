from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC
from html import escape
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jinja2 import Environment, FileSystemLoader

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User
from app.services.admin_list_preferences_service import DEFAULT_DENSITY
from app.services.admin_queue_presets_service import QUEUE_KEY_TO_QUEUE_CONTEXT
from app.services.rbac_service import (
    SCOPE_AUCTION_MANAGE,
    SCOPE_BID_MANAGE,
    SCOPE_ROLE_MANAGE,
    SCOPE_TRUST_MANAGE,
    SCOPE_USER_BAN,
)
from app.services.risk_eval_service import format_risk_reason_label
from app.web.auth import AdminAuthContext
from app.web.dense_list import DenseListConfig
from app.web.deps import _path_with_auth

logger = logging.getLogger(__name__)

_templates_dir = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_templates_dir),
    autoescape=True,
)

_DENSE_ALLOWED_DENSITIES = frozenset({"compact", "standard", "comfortable"})
_QUEUE_ALLOWED_COLUMNS: dict[str, tuple[str, ...]] = {
    "complaints": ("id", "auction", "reporter", "status", "reason", "created", "sla", "deadline"),
    "signals": ("id", "auction", "user", "risk", "score", "status", "created", "sla"),
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
    "violators": (
        "id",
        "tg_user_id",
        "username",
        "status",
        "reason",
        "actor",
        "created",
        "expires",
        "actions",
    ),
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


_jinja_env.filters["fmt_ts"] = _fmt_ts
_jinja_env.filters["pct"] = _pct


def render_template(name: str, **context: object) -> str:
    return _jinja_env.get_template(name).render(**context)


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


def _risk_snapshot_inline_text(risk_snapshot) -> str:
    return f"{risk_snapshot.level} ({risk_snapshot.score})"


def _risk_snapshot_inline_html(risk_snapshot) -> str:
    label = _risk_snapshot_inline_text(risk_snapshot)
    if not risk_snapshot.reasons:
        return escape(label)
    reasons = ", ".join(format_risk_reason_label(code) for code in risk_snapshot.reasons)
    return f"<span title='{escape(reasons)}'>{escape(label)}</span>"


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
    from app.web.deps import _csrf_hidden_input

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
    return f"<div class='toolbar'><b>Режим экрана:</b>{''.join(chips)}</div>"


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
            reopen_rate = (
                float(reopen_rate_raw) if isinstance(reopen_rate_raw, (int, float)) else 0.0
            )
            avg_churn_raw = item.get("avg_filter_churn_count")
            avg_churn = float(avg_churn_raw) if isinstance(avg_churn_raw, (int, float)) else 0.0
            trend_guardrail = bool(item.get("trend_low_sample_guardrail"))
            trend_min_sample_raw = item.get("trend_min_sample_size")
            trend_min_sample = (
                int(trend_min_sample_raw) if isinstance(trend_min_sample_raw, int) else 0
            )
            prev_events_raw = item.get("trend_previous_events_total")
            prev_events = int(prev_events_raw) if isinstance(prev_events_raw, int) else 0
            time_delta_raw = item.get("time_to_action_delta_ms")
            time_delta = float(time_delta_raw) if isinstance(time_delta_raw, (int, float)) else None
            reopen_delta_raw = item.get("reopen_rate_delta")
            reopen_delta = (
                float(reopen_delta_raw) if isinstance(reopen_delta_raw, (int, float)) else None
            )
            churn_delta_raw = item.get("filter_churn_delta")
            churn_delta = (
                float(churn_delta_raw) if isinstance(churn_delta_raw, (int, float)) else None
            )

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
    from app.web.deps import _build_csrf_token, _token_from_request
    from app.services.admin_list_preferences_service import load_admin_list_preference
    from app.services.admin_queue_presets_service import resolve_queue_preset_state

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
        preset_items = tuple(
            (str(item["id"]), str(item["name"])) for item in preset_state["presets"]
        )
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
        density=_resolve_dense_density(
            requested=requested_density, persisted=preference["density"]
        ),
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


def _safe_return_to(return_to: str | None, fallback: str) -> str:
    from app.web.deps import _is_safe_local_path

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
