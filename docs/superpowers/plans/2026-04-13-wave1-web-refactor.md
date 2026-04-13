# Wave 1: Web Panel Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose the 6269-line `app/web/main.py` monolith into domain-scoped routers, replace f-string HTML with Jinja2 templates, fix session secret fallback, and tune connection pool.

**Architecture:** FastAPI routers mounted on a thin app factory. Jinja2 templates with autoescape. Shared deps extracted into `deps.py`. URL paths preserved 1:1.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLAlchemy async, aiogram 3

---

## File Structure

### New files created:

| File | Responsibility |
|---|---|
| `app/web/deps.py` | Shared FastAPI dependencies: get_db, require_auth, require_scope, csrf helpers |
| `app/web/filters.py` | All `_parse_*` filter functions for query params |
| `app/web/components.py` | UI helper functions: _kpi_card, _panel, _pager, _user_label, _render_page, etc. |
| `app/web/routers/__init__.py` | Aggregates all sub-routers, exports `include_all_routers(app)` |
| `app/web/routers/auth_routes.py` | /login, /auth/telegram, /logout |
| `app/web/routers/dashboard.py` | /, /settings, /actions/settings/runtime/* |
| `app/web/routers/auctions.py` | /auctions, /timeline/auction/{id}, /manage/auction/{id}, freeze/unfreeze/end/remove |
| `app/web/routers/complaints.py` | /complaints |
| `app/web/routers/signals.py` | /signals |
| `app/web/routers/trade_feedback.py` | /trade-feedback, hide/unhide actions |
| `app/web/routers/appeals.py` | /appeals, resolve/review/reject actions |
| `app/web/routers/users.py` | /manage/users, /manage/user/{id}, ban/unban/verify/unverify/points |
| `app/web/routers/roles.py` | moderator grant/revoke actions |
| `app/web/routers/violators.py` | /violators |
| `app/web/routers/presets.py` | dense-list preferences, workflow presets CRUD |
| `app/web/routers/telemetry.py` | workflow preset telemetry |
| `app/web/routers/triage.py` | triage detail-section, bulk actions |
| `app/web/templates/base.html` | Base layout with CSS, header, nav |
| `app/web/templates/components/*.html` | Reusable partials |

### Modified files:

| File | Change |
|---|---|
| `app/web/main.py` | Replaced by thin app factory (~80 lines) |
| `app/web/auth.py` | Remove bot_token fallback from `_session_secret()` |
| `app/db/session.py` | Add pool configuration to engine |
| `pyproject.toml` | Add `jinja2>=3.1,<4.0` dependency |

---

## Task 1: Add jinja2 dependency and connection pool tuning

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/db/session.py`

- [ ] **Step 1: Add jinja2 to pyproject.toml**

In `pyproject.toml`, add `"jinja2>=3.1,<4.0"` to the `dependencies` list, after the `python-multipart` line.

- [ ] **Step 2: Update connection pool in app/db/session.py**

Replace the engine creation line:

```python
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
```

with:

```python
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
)
```

- [ ] **Step 3: Install new dependency**

Run: `pip install jinja2>=3.1,<4.0`

- [ ] **Step 4: Verify existing tests still pass**

Run: `python -m pytest tests/test_web_security.py tests/test_web_dense_list_contract.py -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml app/db/session.py
git commit -m "feat: add jinja2 dependency and tune db connection pool"
```

---

## Task 2: Fix session secret fallback

**Files:**
- Modify: `app/web/auth.py:40-44`

- [ ] **Step 1: Update `_session_secret()` in `app/web/auth.py`**

Replace lines 40-44:

```python
def _session_secret() -> str:
    value = settings.admin_web_session_secret.strip()
    if value:
        return value
    return settings.bot_token
```

with:

```python
def _session_secret() -> str:
    value = settings.admin_web_session_secret.strip()
    if not value:
        raise RuntimeError(
            "ADMIN_WEB_SESSION_SECRET must be set. "
            "Generate: python -c 'import secrets; print(secrets.token_hex(32))'"
        )
    return value
```

- [ ] **Step 2: Verify auth tests still pass**

Run: `python -m pytest tests/test_web_security.py -q`
Expected: All pass (tests use session cookie or token, not the fallback)

- [ ] **Step 3: Commit**

```bash
git add app/web/auth.py
git commit -m "fix: require explicit ADMIN_WEB_SESSION_SECRET, remove bot_token fallback"
```

---

## Task 3: Create deps.py — shared dependencies and CSRF helpers

**Files:**
- Create: `app/web/deps.py`

This file extracts shared auth, CSRF, and DB dependency patterns from `app/web/main.py`. All functions are moved verbatim from their current locations.

- [ ] **Step 1: Create `app/web/deps.py`**

```python
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import SessionFactory
from app.services.rbac_service import VIEWER_SCOPES
from app.web.auth import AdminAuthContext, get_admin_auth_context
from app.web.components import _render_page

logger = logging.getLogger(__name__)


async def get_db() -> AsyncSession:
    async with SessionFactory() as session:
        yield session


def _require_or_redirect(request: Request) -> RedirectResponse | None:
    auth = get_admin_auth_context(request)
    if not auth.authorized:
        login_path = _path_with_auth(request, "/login")
        return RedirectResponse(url=login_path, status_code=303)
    return None


def _auth_context_or_unauthorized(
    request: Request,
) -> tuple[Response | None, AdminAuthContext]:
    auth = get_admin_auth_context(request)
    if not auth.authorized:
        body = _render_page("Unauthorized", "<p>Access denied.</p>")
        return HTMLResponse(body, status_code=401), auth
    return None, auth


def _require_scope_permission(
    request: Request, scope: str
) -> tuple[HTMLResponse | None, AdminAuthContext]:
    response, auth = _auth_context_or_unauthorized(request)
    if response is not None:
        return response, auth
    if scope not in auth.scopes:
        body = _render_page("Forbidden", "<p>Insufficient permissions.</p>")
        return HTMLResponse(body, status_code=403), auth
    return None, auth


def _require_owner_permission(
    request: Request,
) -> tuple[HTMLResponse | None, AdminAuthContext]:
    return _require_scope_permission(request, "__owner__")


def _path_with_auth(request: Request, path: str) -> str:
    token = request.query_params.get("token")
    if token:
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}token={token}"
    return path


def _csrf_secret() -> bytes:
    return settings.bot_token.encode("utf-8")


def _csrf_subject(request: Request, auth: AdminAuthContext) -> str | None:
    if auth.tg_user_id is not None:
        return f"csrf:{auth.tg_user_id}"
    token = request.query_params.get("token") or request.headers.get("x-admin-token", "")
    if token:
        return f"csrf:token:{hashlib.sha256(token.encode()).hexdigest()[:16]}"
    return None


def _build_csrf_token(request: Request, auth: AdminAuthContext) -> str:
    subject = _csrf_subject(request, auth)
    if subject is None:
        return ""
    sig = hmac.new(_csrf_secret(), subject.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    return sig


def _csrf_hidden_input(request: Request, auth: AdminAuthContext) -> str:
    token = _build_csrf_token(request, auth)
    if not token:
        return ""
    return f"<input type='hidden' name='csrf_token' value='{token}' />"


def _validate_csrf_token(
    request: Request, auth: AdminAuthContext, csrf_token: str
) -> bool:
    expected = _build_csrf_token(request, auth)
    if not expected:
        return False
    return hmac.compare_digest(expected, csrf_token)


def _csrf_failed_response(request: Request, *, back_to: str) -> HTMLResponse:
    body = _render_page(
        "CSRF Error",
        f"<div class='notice notice-error'><p>Invalid or expired CSRF token. "
        f"<a href='{back_to}'>Go back</a></p></div>",
    )
    return HTMLResponse(body, status_code=403)


def _is_confirmed(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() == "yes"


def _is_safe_local_path(path: str | None) -> bool:
    if not path:
        return False
    return path.startswith("/") and "://" not in path and not path.startswith("//")


def _safe_back_to_from_request(request: Request, fallback: str = "/") -> str:
    raw = request.query_params.get("back_to") or request.headers.get("referer")
    if _is_safe_local_path(raw):
        return raw
    return fallback
```

- [ ] **Step 2: Verify no import errors**

Run: `python -c "from app.web.deps import get_db, _build_csrf_token; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/web/deps.py
git commit -m "refactor(web): extract shared deps, auth, csrf helpers"
```

---

## Task 4: Create filters.py — query param parsers

**Files:**
- Create: `app/web/filters.py`

This extracts all `_parse_*` functions from `app/web/main.py` lines 223-338 plus helper label functions.

- [ ] **Step 1: Create `app/web/filters.py`**

Move the following functions verbatim from `app/web/main.py`:
- `_parse_appeal_status_filter` (line 223)
- `_parse_appeal_source_filter` (line 238)
- `_parse_appeal_overdue_filter` (line 251)
- `_parse_appeal_escalated_filter` (line 258)
- `_parse_appeal_sla_health_filter` (line 265)
- `_parse_appeal_aging_bucket_filter` (line 272)
- `_parse_complaint_sla_health_filter` (line 279)
- `_parse_complaint_aging_bucket_filter` (line 286)
- `_parse_signal_sla_health_filter` (line 293)
- `_parse_signal_aging_bucket_filter` (line 300)
- `_parse_trade_feedback_status` (line 307)
- `_parse_trade_feedback_moderated_filter` (line 314)
- `_parse_trade_feedback_min_rating` (line 321)
- `_parse_optional_tg_user_id` (line 331)
- `_parse_ymd_filter` (line 1029)
- `_parse_signed_int` (line 1722)
- `_parse_non_negative_int` (line 1461)
- `_appeal_source_label` (line 341)
- `_appeal_status_label` (line 349)
- `_appeal_is_overdue` (line 363)
- `_format_duration_compact` (line 373)
- `_appeal_sla_state_label` (line 382)
- `_appeal_escalation_marker` (line 400)
- `_violator_status_label` (line 1018)
- `_normalize_points_filter_query` (line 1810)
- `_points_filter_query_value` (line 1829)
- `_points_event_label` (line 1843)

Keep all imports these functions need at the top of the file.

- [ ] **Step 2: Verify no import errors**

Run: `python -c "from app.web.filters import _parse_appeal_status_filter; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/web/filters.py
git commit -m "refactor(web): extract query param filter parsers"
```

---

## Task 5: Create components.py — UI helpers and template renderer

**Files:**
- Create: `app/web/components.py`

This extracts all UI rendering helper functions from `app/web/main.py`.

- [ ] **Step 1: Create `app/web/components.py`**

Move the following functions verbatim from `app/web/main.py`:
- `_timezone` (line 204)
- `_fmt_ts` (line 211)
- `_pct` (line 217)
- `_user_label` (line 432)
- `_role_badge` (line 1312)
- `_render_page` (line 1189) — the full function including the inline CSS (~100 lines)
- `_render_app_header` (line 1321)
- `_kpi_card` (line 1335)
- `_kpi_grid` (line 1346)
- `_panel` (line 1350)
- `_pager_html` (line 1361)
- `_details_block` (line 1372)
- `_append_timeline_event` (line 442)
- `_render_inline_timeline_html` (line 454)
- `_render_confirmation_page` (line 1137)
- `_action_error_page` (line 3338)
- `_scope_title` (line 1429)
- `_triage_controls_cell` (line 1881)
- `_triage_row_context_attrs` (line 1888)
- `_triage_detail_row` (line 1894)
- `_triage_shortcut_hint` (line 1908)
- `_normalize_dashboard_preset` (line 1377)
- `_dashboard_preset_toolbar` (line 1383)
- `_dashboard_preset_script` (line 1402)
- `_normalize_timeline_source_query` (line 1443)
- `_format_preset_telemetry_time` (line 1467)
- `_format_preset_telemetry_time_delta` (line 1475)
- `_format_preset_telemetry_rate_delta` (line 1484)
- `_format_preset_telemetry_churn_delta` (line 1491)
- `_render_workflow_preset_telemetry_panel` (line 1498)
- `_normalize_requested_density` (line 1622)
- `_resolve_dense_density` (line 1633)
- `_load_dense_list_config` (line 1643)
- `_risk_snapshot_inline_text` (line 1006)
- `_risk_snapshot_inline_html` (line 1010)
- `_safe_return_to` (line 3332)
- `_build_rationale_artifact` (line 411)

Plus all constants:
- `_DENSE_ALLOWED_DENSITIES` (line 144)
- `_QUEUE_ALLOWED_COLUMNS` (line 145)

- [ ] **Step 2: Verify no import errors**

Run: `python -c "from app.web.components import _render_page, _kpi_card; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/web/components.py
git commit -m "refactor(web): extract UI component helpers"
```

---

## Task 6: Create routers/__init__.py and app factory

**Files:**
- Create: `app/web/routers/__init__.py`
- Modify: `app/web/main.py` (replace entirely)

- [ ] **Step 1: Create `app/web/routers/__init__.py`**

```python
from fastapi import FastAPI


def include_all_routers(app: FastAPI) -> None:
    from app.web.routers.auth_routes import router as auth_router
    from app.web.routers.dashboard import router as dashboard_router
    from app.web.routers.auctions import router as auctions_router
    from app.web.routers.complaints import router as complaints_router
    from app.web.routers.signals import router as signals_router
    from app.web.routers.trade_feedback import router as trade_feedback_router
    from app.web.routers.appeals import router as appeals_router
    from app.web.routers.users import router as users_router
    from app.web.routers.roles import router as roles_router
    from app.web.routers.violators import router as violators_router
    from app.web.routers.presets import router as presets_router
    from app.web.routers.telemetry import router as telemetry_router
    from app.web.routers.triage import router as triage_router

    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(auctions_router)
    app.include_router(complaints_router)
    app.include_router(signals_router)
    app.include_router(trade_feedback_router)
    app.include_router(appeals_router)
    app.include_router(users_router)
    app.include_router(roles_router)
    app.include_router(violators_router)
    app.include_router(presets_router)
    app.include_router(telemetry_router)
    app.include_router(triage_router)
```

- [ ] **Step 2: Replace `app/web/main.py` with thin app factory**

The new `app/web/main.py` should be approximately:

```python
from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.web.routers import include_all_routers

app = FastAPI(title="LiteAuction Admin", version="0.2.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


include_all_routers(app)


def main() -> None:
    uvicorn.run("app.web.main:app", host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    main()
```

Note: `/health` stays in main.py because it's infrastructure-level, not domain-scoped.

- [ ] **Step 3: Commit**

```bash
git add app/web/routers/__init__.py app/web/main.py
git commit -m "refactor(web): create router registry and thin app factory"
```

At this point the app will not start because routers are not yet created. That is expected — subsequent tasks create each router.

---

## Task 7: Create routers/auth_routes.py

**Files:**
- Create: `app/web/routers/auth_routes.py`

Moves functions: `login_page` (line 1985), `telegram_auth_callback` (line 2032), `logout` (line 2057).

- [ ] **Step 1: Create the router file**

Create `app/web/routers/auth_routes.py` with `APIRouter()`.

Import from `app.web.deps`: `_path_with_auth`, `_csrf_hidden_input`, `_require_or_redirect`.
Import from `app.web.auth`: `build_admin_session_cookie`, `validate_telegram_login`, `get_admin_auth_context`.
Import from `app.web.components`: `_render_page`.
Import from `app.config`: `settings`.

Copy the three route handlers verbatim. Each function body stays exactly the same — only imports change.

- [ ] **Step 2: Commit**

```bash
git add app/web/routers/auth_routes.py
git commit -m "refactor(web): extract auth routes"
```

---

## Task 8: Create routers/dashboard.py

**Files:**
- Create: `app/web/routers/dashboard.py`

Moves functions: `dashboard` (line 2064), `runtime_settings_page` (line 2312), `action_set_runtime_setting` (line 2373), `action_delete_runtime_setting` (line 2405).

Also moves: `_render_runtime_setting_value` (line 2305).

- [ ] **Step 1: Create the router file**

Create `app/web/routers/dashboard.py` with `APIRouter()`.

This is the largest page renderer (~240 lines for `dashboard` alone). Copy all four route handlers plus the helper verbatim. Update imports to use `app.web.deps`, `app.web.components`, `app.web.filters`.

- [ ] **Step 2: Commit**

```bash
git add app/web/routers/dashboard.py
git commit -m "refactor(web): extract dashboard and settings routes"
```

---

## Task 9: Create routers/auctions.py

**Files:**
- Create: `app/web/routers/auctions.py`

Moves functions: `auctions` (line 3099), `auction_timeline` (line 3222), `manage_auction` (line 3348), `action_freeze_auction` (line 5508), `action_unfreeze_auction` (line 5548), `action_end_auction` (line 5588), `action_remove_bid` (line 5644).

Also moves: `_refresh_auction_posts_from_web` (line 1168).

- [ ] **Step 1: Create the router file**

Create `app/web/routers/auctions.py` with `APIRouter()`.

Copy all route handlers. Update imports.

- [ ] **Step 2: Commit**

```bash
git add app/web/routers/auctions.py
git commit -m "refactor(web): extract auction routes"
```

---

## Task 10: Create routers/complaints.py

**Files:**
- Create: `app/web/routers/complaints.py`

Moves: `complaints` (line 2430), `_render_complaint_detail_section` (line 680).

- [ ] **Step 1: Create the router file and commit**

```bash
git add app/web/routers/complaints.py
git commit -m "refactor(web): extract complaints routes"
```

---

## Task 11: Create routers/signals.py

**Files:**
- Create: `app/web/routers/signals.py`

Moves: `signals` (line 2603), `_render_signal_detail_section` (line 806).

- [ ] **Step 1: Create the router file and commit**

```bash
git add app/web/routers/signals.py
git commit -m "refactor(web): extract signals routes"
```

---

## Task 12: Create routers/trade_feedback.py

**Files:**
- Create: `app/web/routers/trade_feedback.py`

Moves: `trade_feedback` (line 2793), `action_hide_trade_feedback` (line 5400), `action_unhide_trade_feedback` (line 5454).

- [ ] **Step 1: Create the router file and commit**

```bash
git add app/web/routers/trade_feedback.py
git commit -m "refactor(web): extract trade feedback routes"
```

---

## Task 13: Create routers/appeals.py

**Files:**
- Create: `app/web/routers/appeals.py`

Moves: `appeals` (line 4459), `action_resolve_appeal` (line 5700), `action_review_appeal` (line 5775), `action_reject_appeal` (line 5810), `_render_appeal_detail_section` (line 469).

- [ ] **Step 1: Create the router file and commit**

```bash
git add app/web/routers/appeals.py
git commit -m "refactor(web): extract appeals routes"
```

---

## Task 14: Create routers/users.py

**Files:**
- Create: `app/web/routers/users.py`

Moves: `manage_users` (line 4061), `manage_user` (line 3453), `action_ban_user` (line 5885), `action_unban_user` (line 6053), `action_verify_user` (line 5935), `action_unverify_user` (line 5996), `action_adjust_user_points` (line 6103).

Also moves: `_resolve_actor_user_id` (line 1957), `_load_user_risk_snapshot_map` (line 928).

- [ ] **Step 1: Create the router file and commit**

```bash
git add app/web/routers/users.py
git commit -m "refactor(web): extract user management routes"
```

---

## Task 15: Create routers/roles.py

**Files:**
- Create: `app/web/routers/roles.py`

Moves: `action_grant_moderator` (line 6191), `action_revoke_moderator` (line 6228).

- [ ] **Step 1: Create the router file and commit**

```bash
git add app/web/routers/roles.py
git commit -m "refactor(web): extract role management routes"
```

---

## Task 16: Create routers/violators.py

**Files:**
- Create: `app/web/routers/violators.py`

Moves: `violators` (line 4248).

- [ ] **Step 1: Create the router file and commit**

```bash
git add app/web/routers/violators.py
git commit -m "refactor(web): extract violators routes"
```

---

## Task 17: Create routers/presets.py

**Files:**
- Create: `app/web/routers/presets.py`

Moves: `action_save_dense_list_preferences` (line 4891), `action_workflow_presets` (line 4937), `_normalize_workflow_preset_telemetry_payload` (line 1736), `_resolve_workflow_preset_telemetry_preset_id` (line 1751), `_workflow_preset_result_is_successful` (line 1771), `_record_workflow_preset_telemetry_safe` (line 1775).

- [ ] **Step 1: Create the router file and commit**

```bash
git add app/web/routers/presets.py
git commit -m "refactor(web): extract preset management routes"
```

---

## Task 18: Create routers/telemetry.py

**Files:**
- Create: `app/web/routers/telemetry.py`

Moves: `action_workflow_presets_telemetry` (line 5075).

- [ ] **Step 1: Create the router file and commit**

```bash
git add app/web/routers/telemetry.py
git commit -m "refactor(web): extract telemetry routes"
```

---

## Task 19: Create routers/triage.py

**Files:**
- Create: `app/web/routers/triage.py`

Moves: `action_triage_detail_section` (line 5107), `action_triage_bulk` (line 5199).

- [ ] **Step 1: Create the router file and commit**

```bash
git add app/web/routers/triage.py
git commit -m "refactor(web): extract triage action routes"
```

---

## Task 20: Verify all routes work end-to-end

**Files:** None (verification only)

- [ ] **Step 1: Start the admin panel locally**

Run: `BOT_TOKEN=test ADMIN_WEB_SESSION_SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))') python -m app.web.main`

Expected: Server starts on port 8080 without errors.

- [ ] **Step 2: Hit /health endpoint**

Run: `curl http://localhost:8080/health`
Expected: `{"status":"ok"}`

- [ ] **Step 3: Hit /login page**

Run: `curl -s http://localhost:8080/login | head -5`
Expected: HTML response with login form.

- [ ] **Step 4: Run all web integration tests**

Run: `python -m pytest tests/integration/test_web_*.py -v`
Expected: All pass.

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest -q tests`
Expected: Same pass count as before (331+).

- [ ] **Step 6: Run lint**

Run: `python -m ruff check app tests`
Expected: Clean, no errors.

---

## Task 21: Create Jinja2 base template and migrate CSS

**Files:**
- Create: `app/web/templates/base.html`

This is the foundation for all subsequent template migration. It contains the full inline CSS currently in `_render_page()`.

- [ ] **Step 1: Create templates directory**

```bash
mkdir -p app/web/templates/components
```

- [ ] **Step 2: Create `app/web/templates/base.html`**

Extract the full CSS block from `_render_page()` (lines 1191-1287 of the original main.py, now in components.py) into a `<style>` tag in this template. The template structure:

```html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{% block title %}LiteAuction Admin{% endblock %}</title>
    <style>
    /* paste the full CSS from _render_page here */
    </style>
</head>
<body>
<div class="page-shell">
    {% block header %}{% endblock %}
    {% block body %}{% endblock %}
</div>
</body>
</html>
```

- [ ] **Step 3: Create a Jinja2 template loader in `app/web/components.py`**

Add at the top of `components.py`:

```python
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

_templates_dir = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_templates_dir),
    autoescape=True,
)
_jinja_env.filters["fmt_ts"] = _fmt_ts
_jinja_env.filters["pct"] = _pct
```

Add a `render_template(name, **context)` convenience function.

- [ ] **Step 4: Verify template loads**

Run: `python -c "from app.web.components import render_template; print(render_template('base.html'))"`
Expected: HTML with CSS printed.

- [ ] **Step 5: Commit**

```bash
git add app/web/templates/ app/web/components.py
git commit -m "refactor(web): add Jinja2 base template with CSS"
```

---

## Task 22: Migrate page templates one domain at a time

This is a repetitive task. For each domain page, replace the f-string HTML with a Jinja2 template.

**Sub-tasks (each is a separate commit):**

- [ ] **22a: login.html** — migrate `login_page` route
- [ ] **22b: dashboard.html** — migrate `dashboard` route
- [ ] **22c: settings.html** — migrate `runtime_settings_page` route
- [ ] **22d: complaints.html** — migrate `complaints` route
- [ ] **22e: signals.html** — migrate `signals` route
- [ ] **22f: trade_feedback.html** — migrate `trade_feedback` route
- [ ] **22g: auctions/list.html** — migrate `auctions` route
- [ ] **22h: auctions/timeline.html** — migrate `auction_timeline` route
- [ ] **22i: auctions/detail.html** — migrate `manage_auction` route
- [ ] **22j: users/list.html** — migrate `manage_users` route
- [ ] **22k: users/detail.html** — migrate `manage_user` route
- [ ] **22l: violators.html** — migrate `violators` route
- [ ] **22m: appeals.html** — migrate `appeals` route

For each sub-task:
1. Create the `.html` template file
2. Update the route handler to use `render_template()` instead of f-string concatenation
3. Run web integration tests for that domain
4. Commit

After all sub-tasks: `_render_page()` can be removed from `components.py` if no longer called.

---

## Task 23: Final cleanup and validation

- [ ] **Step 1: Remove dead code from components.py**

Remove `_render_page()` and any other functions that are no longer called after template migration.

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest -q tests`
Expected: Same pass count.

- [ ] **Step 3: Run lint**

Run: `python -m ruff check app tests`
Expected: Clean.

- [ ] **Step 4: Verify all 39 routes respond**

Run: `curl -s http://localhost:8080/health && echo ""`
Run: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/login`
For all GET routes, verify 200 or 303 status codes.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "refactor(web): cleanup after full Wave 1 migration"
```

---

## Self-Review Checklist

- [x] Spec coverage: all 4 scope items have tasks (split routers: T6-19, Jinja2: T21-22, session secret: T2, pool: T1)
- [x] No placeholders: each task specifies exact functions and line numbers to move
- [x] Type consistency: all functions use same signatures as original
- [x] Route mapping: all 39 routes accounted for in router assignments
