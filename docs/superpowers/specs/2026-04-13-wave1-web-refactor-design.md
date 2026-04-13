# Wave 1: Web Panel Refactor + Security Fixes

Date: 2026-04-13
Status: Approved

## Problem

`app/web/main.py` is a 6269-line monolith containing 39 routes, 130 functions,
inline CSS, HTML via f-strings, business logic, auth helpers, and UI components.
This makes the admin panel impossible to maintain, test, or extend safely.

Additional issues:
- Session secret falls back to bot_token when `ADMIN_WEB_SESSION_SECRET` is unset
- Database engine created without pool configuration

## Scope

Four changes, one PR:

1. **Split web/main.py into routers/** — decompose by domain
2. **Introduce Jinja2 templates** — replace f-string HTML with autoescaped templates
3. **Fix session secret fallback** — require explicit `ADMIN_WEB_SESSION_SECRET`
4. **Tune connection pool** — add pool_size, max_overflow, pool_recycle, pool_timeout

## Architecture

### File Structure

```
app/web/
├── __init__.py
├── main.py              # FastAPI app factory, router mount, lifespan (~100 lines)
├── auth.py              # unchanged
├── dense_list.py        # unchanged
├── deps.py              # shared dependencies: get_db, require_auth, require_scope, csrf
├── filters.py           # _parse_* filter functions for query params
├── components.py        # UI helper functions (_kpi_card, _panel, _user_label, etc.)
├── routers/
│   ├── __init__.py      # aggregates all sub-routers
│   ├── auth.py          # /login, /auth/telegram, /logout
│   ├── dashboard.py     # /, /settings, runtime setting actions
│   ├── auctions.py      # /auctions, /timeline/auction/{id}, /manage/auction/{id}
│   ├── complaints.py    # /complaints
│   ├── signals.py       # /signals
│   ├── trade_feedback.py # /trade-feedback, hide/unhide actions
│   ├── appeals.py       # /appeals, resolve/review/reject actions
│   ├── users.py         # /manage/users, /manage/user/{id}, ban/unban/verify/points
│   ├── roles.py         # moderator grant/revoke actions
│   ├── violators.py     # /violators
│   ├── presets.py       # dense-list preferences, workflow presets, telemetry
│   └── triage.py        # triage detail-section, bulk actions
├── templates/
│   ├── base.html        # layout: CSS, nav, header/footer
│   ├── components/      # reusable partials
│   │   ├── kpi.html
│   │   ├── panel.html
│   │   ├── pager.html
│   │   ├── table.html
│   │   ├── notice.html
│   │   └── confirmation.html
│   ├── auth/login.html
│   ├── dashboard.html
│   ├── settings.html
│   ├── auctions/list.html
│   ├── auctions/detail.html
│   ├── auctions/timeline.html
│   ├── complaints.html
│   ├── signals.html
│   ├── trade_feedback.html
│   ├── appeals.html
│   ├── users/list.html
│   ├── users/detail.html
│   └── violators.html
└── static/              # reserved (CSS stays inline in base.html for now)
```

### Dependency Injection (deps.py)

Shared FastAPI dependencies extracted from current inline patterns:

- `get_db()` — async session generator
- `require_auth(request)` — returns `AdminAuthContext`, raises 401/redirect if unauthorized
- `require_scope(scope)` — dependency factory, raises 403 if scope not in auth
- `build_csrf_token(request, auth)` — CSRF token generation
- `validate_csrf(request, auth, token)` — CSRF validation

### Template System

Jinja2 with `autoescape=True` (default), loaded from `app/web/templates/`.

Custom filters registered:
- `fmt_ts` — format datetime in configured timezone
- `pct` — format percentage
- `user_label` — render user display name

The current ~100 lines of inline CSS (from `_render_page`) move into `<style>` block
in `templates/base.html`. No external CSS framework — existing design is preserved.

### Route Mapping

Current URL -> New router:

| Current route | Router |
|---|---|
| /health | main.py (app-level) |
| /login, /auth/telegram, /logout | routers/auth.py |
| /, /settings, /actions/settings/* | routers/dashboard.py |
| /auctions, /timeline/*, /manage/auction/* | routers/auctions.py |
| /complaints | routers/complaints.py |
| /signals | routers/signals.py |
| /trade-feedback, /actions/trade-feedback/* | routers/trade_feedback.py |
| /appeals, /actions/appeal/* | routers/appeals.py |
| /manage/users, /manage/user/*, /actions/user/ban/*, verify, unban, points | routers/users.py |
| /actions/user/moderator/* | routers/roles.py |
| /violators | routers/violators.py |
| /actions/dense-list/*, /actions/workflow-presets/*, /actions/triage/* | routers/presets.py + routers/triage.py |

### Security Fix: Session Secret

`app/web/auth.py` `_session_secret()` currently falls back to `settings.bot_token`
when `ADMIN_WEB_SESSION_SECRET` is empty. This is changed to raise `RuntimeError`
at startup if the secret is not explicitly configured.

Migration note: operators must set `ADMIN_WEB_SESSION_SECRET` env var before deploying.
Generate via: `python -c 'import secrets; print(secrets.token_hex(32))'`

### Connection Pool Configuration

`app/db/session.py` engine creation updated:

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

## Dependencies Added

- `jinja2>=3.1,<4.0` — template engine (add to pyproject.toml dependencies)

## Testing Strategy

- Existing integration tests in `tests/integration/test_web_*.py` must pass unchanged
- All 39 routes must return same HTTP status codes and response content
- URL paths are preserved (no redirects needed)

## Rollout Notes

- Deploy requires `ADMIN_WEB_SESSION_SECRET` env var to be set
- Template directory `app/web/templates/` must be included in Docker build context
- No database migration needed
- No bot behavior changes

## Risks

- Large PR (~3000+ lines changed) — mitigated by preserving exact same logic, only restructuring
- Template autoescape may double-escape already-escaped content — must audit HTML output
- CSRF token generation moves to deps.py — must ensure same crypto behavior
