# External Integrations

**Analysis Date:** 2026-02-19

## APIs & External Services

**Messaging Platform:**
- Telegram Bot API - primary product boundary for bot polling, moderation actions, topic routing, and verification.
  - SDK/Client: `aiogram` in `app/main.py`, `app/services/auction_service.py`, and `app/services/verification_service.py`.
  - Auth: `BOT_TOKEN` (settings field `bot_token`) in `app/config.py`.

**Issue Tracking Automation:**
- GitHub REST API - creates issues for approved feedback outbox events.
  - SDK/Client: custom HTTP client with `urllib.request` in `app/services/github_automation_service.py`.
  - Auth: `GITHUB_TOKEN` (settings field `github_token`) in `app/config.py`.

**Web Auth Widget:**
- Telegram Login Widget (`telegram-widget.js`) embedded in admin login page.
  - SDK/Client: script include and callback handling in `app/web/main.py`.
  - Auth: Telegram login payload hash validated against bot token in `app/web/auth.py`.

## Data Storage

**Databases:**
- PostgreSQL (async SQLAlchemy models and migrations).
  - Connection: `DATABASE_URL` (runtime) and `TEST_DATABASE_URL` (integration tests) in `app/config.py`, `.github/workflows/ci.yml`, and `tests/integration/conftest.py`.
  - Client: SQLAlchemy async engine/session in `app/db/session.py`; migrations via Alembic in `alembic/env.py`.

**File Storage:**
- Local filesystem only (project files/migrations/config); no external object storage client detected in `app/**/*.py`.

**Caching:**
- Redis used for anti-spam guards, notification digest/quiet-hours state, and metrics.
  - Connection: `REDIS_URL` (settings field `redis_url`) in `app/config.py`.
  - Client: `redis.asyncio.Redis` in `app/infra/redis_client.py`.

## Authentication & Identity

**Auth Provider:**
- Telegram Login for admin panel identities, plus admin token fallback.
  - Implementation: signed Telegram auth callback (`/auth/telegram`) and HMAC session cookies in `app/web/main.py` and `app/web/auth.py`.
  - Additional gate: admin allowlist (`ADMIN_USER_IDS`) and optional static panel token (`ADMIN_PANEL_TOKEN`) in `app/config.py`.

## Monitoring & Observability

**Error Tracking:**
- None detected (no Sentry/New Relic/Datadog SDK imports in `app/**/*.py`).

**Logs:**
- Python standard logging with centralized setup in `app/logging_setup.py`; service-level structured messages in `app/main.py`, `app/services/outbox_watcher.py`, and `app/web/main.py`.

## CI/CD & Deployment

**Hosting:**
- Container-first runtime is detected (Docker + Compose services), but managed cloud host target is not detected.
  - Runtime references: `Dockerfile`, `README.md`, `AGENTS.md`, and `docker-compose.yml` (service/env references found via grep).

**CI Pipeline:**
- GitHub Actions for lint, unit tests, and Postgres-backed integration tests in `.github/workflows/ci.yml`.

## Environment Configuration

**Required env vars:**
- `BOT_TOKEN` - required for bot runtime and Telegram login signature checks (`app/config.py`, `app/main.py`, `app/web/auth.py`).
- `DATABASE_URL` - required DB endpoint (`app/db/session.py`, `alembic/env.py`).
- `REDIS_URL` - required Redis endpoint (`app/infra/redis_client.py`).
- `ADMIN_USER_IDS` - required for admin web actions and role model (`app/config.py`, `app/web/main.py`).
- `ADMIN_PANEL_TOKEN` - optional token auth for admin panel (`app/web/auth.py`).
- `ADMIN_WEB_SESSION_SECRET` - optional explicit cookie signing secret (`app/web/auth.py`).
- `APP_CONFIG_FILE` - optional override path for TOML defaults (`app/config.py`).
- `GITHUB_AUTOMATION_ENABLED`, `GITHUB_TOKEN`, `GITHUB_REPO_OWNER`, `GITHUB_REPO_NAME` - required when outbox-to-GitHub automation is enabled (`app/config.py`, `app/services/outbox_service.py`).

**Secrets location:**
- Local/dev secrets are loaded from `.env` (present) via `SettingsConfigDict(env_file=".env")` in `app/config.py`.
- CI test values are set in `.github/workflows/ci.yml`; repository secret management backend is not declared in repo files.

## Webhooks & Callbacks

**Incoming:**
- `GET /auth/telegram` callback for Telegram Login Widget in `app/web/main.py`.
- No Telegram bot webhook endpoint detected; bot uses long polling via `dp.start_polling(...)` in `app/main.py`.

**Outgoing:**
- GitHub issue creation: `POST https://api.github.com/repos/{owner}/{repo}/issues` from `app/services/github_automation_service.py`.
- Telegram API calls via aiogram methods (examples: `verify_user`, `verify_chat`, `send_message_draft`) in `app/services/verification_service.py` and `app/services/message_draft_service.py`.

---

*Integration audit: 2026-02-19*
