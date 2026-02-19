# Architecture

**Analysis Date:** 2026-02-19

## Pattern Overview

**Overall:** Modular monolith with dual interfaces (Telegram bot + FastAPI admin) over a shared service and persistence layer.

**Key Characteristics:**
- Keep transport/adapters in `app/bot/handlers/*.py` and `app/web/main.py`, and route business rules through `app/services/*.py`.
- Centralize persistence in SQLAlchemy models in `app/db/models.py` with async sessions from `app/db/session.py`.
- Run long-lived domain jobs as in-process watchers started by `app/main.py` (`run_auction_watcher`, `run_appeal_escalation_watcher`, `run_outbox_watcher`).

## Layers

**Runtime/Entry Layer:**
- Purpose: Boot process lifecycle, initialize external clients, and start pollers/web server.
- Location: `app/main.py`, `app/web/main.py`, `app/healthcheck.py`, `alembic/env.py`.
- Contains: Bot startup (`Dispatcher`), web app bootstrap (`FastAPI`), health probe, migration bootstrap.
- Depends on: `app/config.py`, `app/db/session.py`, `app/infra/redis_client.py`, service watchers.
- Used by: Docker/CLI process commands (`python -m app.main`, `python -m app.web.main`, `alembic upgrade head`).

**Transport Layer (Bot + Web):**
- Purpose: Parse Telegram callbacks/commands and HTTP request params/forms, then call domain services.
- Location: `app/bot/handlers/*.py`, `app/web/main.py`, `app/web/auth.py`.
- Contains: Router wiring (`app/bot/handlers/__init__.py`), command handlers, FastAPI route handlers, auth/session/CSRF guards.
- Depends on: `app/services/*.py`, `app/db/session.py`, `app/db/models.py`, `app/services/rbac_service.py`.
- Used by: Telegram updates (aiogram polling) and HTTP clients (admin web).

**Application/Domain Services Layer:**
- Purpose: Implement business rules and domain workflows independent of Telegram/FastAPI wiring.
- Location: `app/services/*.py`.
- Contains: Auction lifecycle (`app/services/auction_service.py`), moderation/RBAC (`app/services/moderation_service.py`, `app/services/rbac_service.py`), timeline (`app/services/timeline_service.py`), appeals/feedback/points/outbox/private-topics.
- Depends on: `app/db/models.py`, `app/db/enums.py`, `app/config.py`, and selected external clients (aiogram bot in notification/publish paths).
- Used by: Bot handlers, web routes, and background watchers.

**Persistence Layer:**
- Purpose: Define schema and transaction primitives.
- Location: `app/db/base.py`, `app/db/enums.py`, `app/db/models.py`, `app/db/session.py`, `alembic/versions/*.py`.
- Contains: SQLAlchemy declarative models, enum contracts, async engine/session factory, migration history.
- Depends on: SQLAlchemy + settings (`app/config.py`).
- Used by: All domain services and selected web routes with direct queries.

**Infrastructure/Integration Layer:**
- Purpose: Manage external infrastructure clients and automation channels.
- Location: `app/infra/redis_client.py`, `app/services/github_automation_service.py`, `app/services/outbox_service.py`, `app/services/moderation_topic_router.py`.
- Contains: Redis client lifecycle, GitHub issue client abstraction, outbox retry/backoff processing, moderation topic routing.
- Depends on: Runtime settings and external APIs (Redis, GitHub, Telegram).
- Used by: Watchers and domain services that emit side effects.

## Data Flow

**Bid + Auction Update Flow:**

1. Telegram callback enters `app/bot/handlers/bid_actions.py` and is validated/throttled.
2. Domain mutation runs in `app/services/auction_service.py` (`process_bid_action`) with persistence in `app/db/models.py` through `SessionFactory` from `app/db/session.py`.
3. Side effects fan out via `app/services/auction_service.py` (`refresh_auction_posts`) and `app/services/private_topics_service.py` (user notifications).

**Web Moderation Action Flow:**

1. HTTP POST enters `app/web/main.py` action routes, passes auth/RBAC/CSRF checks (`app/web/auth.py`, scope guards in `app/services/rbac_service.py`).
2. Domain command executes in `app/services/moderation_service.py` (freeze/unfreeze/end/remove/ban/unban) within DB transaction.
3. Web route optionally refreshes bot-side artifacts through `app/services/auction_service.py` (`refresh_auction_posts`).

**Asynchronous Escalation/Outbox Flow:**

1. `app/main.py` starts watchers (`app/services/auction_watcher.py`, `app/services/appeal_escalation_watcher.py`, `app/services/outbox_watcher.py`).
2. Watcher ticks call service processors (`finalize_expired_auctions`, `process_overdue_appeal_escalations`, `process_pending_outbox_events`).
3. Processors persist state in `app/db/models.py` and emit integrations (Telegram messages, GitHub issues via `app/services/github_automation_service.py`).

**State Management:**
- Use short-lived conversational state in aiogram FSM states (`app/bot/states/auction_create.py`, `app/bot/states/feedback_intake.py`, `app/bot/states/guarantor_intake.py`).
- Store durable business state in PostgreSQL models (`app/db/models.py`) and runtime toggles in `RuntimeSettingOverride` consumed by `app/services/runtime_settings_service.py`.

## Key Abstractions

**Auction Aggregate Views:**
- Purpose: Keep auction rendering and action calculations consistent across bot/web.
- Examples: `TopBidView`, `AuctionView`, `BidActionResult` in `app/services/auction_service.py`.
- Pattern: Service-level dataclass DTOs returned from query/mutation functions.

**Authorization Context + Scopes:**
- Purpose: Enforce least-privilege checks consistently in bot and web.
- Examples: `AdminAuthContext` in `app/web/auth.py`, scope constants in `app/services/rbac_service.py`.
- Pattern: Scope set resolution + per-route/per-command capability gating.

**Timeline Projection:**
- Purpose: Build canonical, paginated event history from multiple sources.
- Examples: `AuctionTimelineItem` and builders in `app/services/timeline_service.py`.
- Pattern: Read-model projection assembled from `Auction`, `Bid`, `Complaint`, `FraudSignal`, `ModerationLog`.

**Outbox Integration Contract:**
- Purpose: Decouple domain approval actions from external GitHub API availability.
- Examples: `IntegrationOutbox` model in `app/db/models.py`, processing in `app/services/outbox_service.py`.
- Pattern: Durable outbox + retries/backoff + idempotent dedupe keys.

## Entry Points

**Telegram Bot Runtime:**
- Location: `app/main.py`.
- Triggers: `python -m app.main` and container default command in `Dockerfile`.
- Responsibilities: Configure logging, initialize `Bot`/`Dispatcher`, include root router from `app/bot/handlers/__init__.py`, start watchers, coordinate shutdown of bot/Redis/DB.

**Admin Web Runtime:**
- Location: `app/web/main.py`.
- Triggers: `python -m app.web.main` (runs `uvicorn.run("app.web.main:app", ...)`).
- Responsibilities: Serve admin HTML routes, enforce auth/CSRF/scope checks, run moderation and management workflows.

**Health Probe:**
- Location: `app/healthcheck.py`.
- Triggers: `python -m app.healthcheck`.
- Responsibilities: Verify DB and Redis connectivity and exit with health status code.

**Migration Runtime:**
- Location: `alembic/env.py`.
- Triggers: Alembic commands from `alembic.ini`.
- Responsibilities: Bind migration context to app settings and metadata from `app/db/base.py` + `app/db/models.py`.

## Error Handling

**Strategy:** Guardrail-first validation at adapter boundaries, transactional domain operations, and tolerant external side effects.

**Patterns:**
- Convert invalid user inputs into early replies/HTTP errors in handlers/routes (`app/bot/handlers/*.py`, `app/web/main.py`).
- Wrap watcher loops with `try/except` and continue on failure (`app/services/auction_watcher.py`, `app/services/outbox_watcher.py`, `app/services/appeal_escalation_watcher.py`).
- Handle Telegram API failure modes with granular exception branches and safe fallbacks in `app/services/auction_service.py` and `app/services/private_topics_service.py`.

## Cross-Cutting Concerns

**Logging:** `configure_logging` in `app/logging_setup.py`; module loggers used in runtime/watchers/services.
**Validation:** Form/query parsing in `app/web/main.py`; callback/command payload parsing and guards in `app/bot/handlers/*.py`; model constraints in `app/db/models.py`.
**Authentication:** Web auth context and Telegram-login signature verification in `app/web/auth.py`; role/scope resolution in `app/services/rbac_service.py`; moderation checks in `app/services/moderation_service.py`.

---

*Architecture analysis: 2026-02-19*
