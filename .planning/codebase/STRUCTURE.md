# Codebase Structure

**Analysis Date:** 2026-02-19

## Directory Layout

```text
LiteAuction/
├── app/                  # Runtime application code (bot, web, services, DB, infra)
├── alembic/              # Migration environment and versioned schema changes
├── tests/                # Unit and integration test suites
├── config/               # Non-secret default configuration (`defaults.toml`)
├── scripts/              # Automation/maintenance scripts (sprint sync)
├── planning/             # Sprint manifests, PR scaffolds, planning status
├── docs/                 # QA, release, and planning documentation
├── .github/workflows/    # CI and PR policy workflows
├── Dockerfile            # Bot image build and runtime command
└── pyproject.toml        # Python package and tool configuration
```

## Directory Purposes

**app/:**
- Purpose: Main production codebase for both execution surfaces.
- Contains: `app/main.py`, `app/web/main.py`, `app/bot/`, `app/services/`, `app/db/`, `app/infra/`.
- Key files: `app/main.py`, `app/web/main.py`, `app/config.py`, `app/db/models.py`.

**app/bot/:**
- Purpose: Telegram adapter layer and conversation state assets.
- Contains: Handlers in `app/bot/handlers/`, keyboards in `app/bot/keyboards/`, FSM states in `app/bot/states/`.
- Key files: `app/bot/handlers/__init__.py`, `app/bot/handlers/start.py`, `app/bot/handlers/moderation.py`, `app/bot/handlers/create_auction.py`.

**app/services/:**
- Purpose: Domain/application logic, side-effect orchestration, and background processors.
- Contains: Auction/moderation/risk/appeal/feedback/points/outbox/private-topic services.
- Key files: `app/services/auction_service.py`, `app/services/moderation_service.py`, `app/services/private_topics_service.py`, `app/services/outbox_service.py`, `app/services/rbac_service.py`.

**app/db/:**
- Purpose: Database contract and access primitives.
- Contains: Declarative base, enum types, models, async engine/session factory.
- Key files: `app/db/base.py`, `app/db/enums.py`, `app/db/models.py`, `app/db/session.py`.

**app/web/:**
- Purpose: Admin web adapter and auth helpers.
- Contains: Route/controller module and web auth context.
- Key files: `app/web/main.py`, `app/web/auth.py`.

**alembic/:**
- Purpose: Schema migration runtime and history.
- Contains: Alembic env in `alembic/env.py`, migration scripts in `alembic/versions/*.py`.
- Key files: `alembic/env.py`, `alembic/versions/0001_initial_schema.py`, `alembic/versions/0035_notification_quiet_hours_timezone.py`.

**tests/:**
- Purpose: Regression and integration safety net.
- Contains: Unit-style tests in `tests/test_*.py`; DB/integration scenarios in `tests/integration/*.py`.
- Key files: `tests/integration/conftest.py`, `tests/test_web_security.py`, `tests/integration/test_rbac_db_integration.py`.

**planning/:**
- Purpose: Sprint and issue/PR planning source of truth.
- Contains: Status file, sprint manifests, draft PR scaffolds.
- Key files: `planning/STATUS.md`, `planning/sprints/sprint-51.toml`, `planning/sprints/sprint-template.toml`.

## Key File Locations

**Entry Points:**
- `app/main.py`: Telegram bot process startup + watcher lifecycle.
- `app/web/main.py`: FastAPI app, all admin routes, and `uvicorn` entry.
- `app/healthcheck.py`: DB/Redis probe command entrypoint.
- `alembic/env.py`: Migration bootstrapping for Alembic.

**Configuration:**
- `app/config.py`: Settings model and source precedence (`env`, `.env`, TOML).
- `config/defaults.toml`: Non-secret runtime defaults and policy tuning.
- `pyproject.toml`: Dependencies, Python version, lint/test tools.

**Core Logic:**
- `app/services/auction_service.py`: Auction lifecycle, bids, post refresh, finalize watcher logic.
- `app/services/moderation_service.py`: Freeze/unfreeze/end/remove/ban/role logic.
- `app/services/timeline_service.py`: Timeline projection and pagination.
- `app/services/private_topics_service.py`: Private topic routing + notification policy integration.

**Testing:**
- `tests/`: Unit and adapter-focused tests.
- `tests/integration/`: DB-backed end-to-end service/web/bot scenarios.
- `.github/workflows/ci.yml`: Lint, unit tests, integration DB tests.

## Naming Conventions

**Files:**
- `snake_case.py` for production modules: `app/services/notification_quiet_hours_service.py`.
- Handler modules grouped by workflow noun: `app/bot/handlers/publish_auction.py`, `app/bot/handlers/trade_feedback.py`.
- Tests named `test_<behavior>.py`: `tests/test_notification_policy_service.py`, `tests/integration/test_web_appeals.py`.

**Directories:**
- Bounded by technical role: `app/bot/`, `app/services/`, `app/db/`, `app/web/`, `app/infra/`.
- Domain-specific code stays flat under `app/services/` instead of nested packages.

## Where to Add New Code

**New Feature:**
- Primary code: Add domain behavior in `app/services/<feature>_service.py`; wire Telegram endpoints in `app/bot/handlers/<feature>.py` or web endpoints in `app/web/main.py`.
- Tests: Add unit regressions in `tests/test_<feature>.py`; add DB/integration paths in `tests/integration/test_<feature>.py` when transactions or migrations are involved.

**New Component/Module:**
- Implementation: Keep transport-only code in `app/bot/handlers/` or `app/web/`; keep reusable business logic in `app/services/`; keep persistence schema in `app/db/models.py` + `alembic/versions/*.py`.

**Utilities:**
- Shared helpers: Place cross-domain pure helpers in an existing relevant service module under `app/services/`; keep infra clients in `app/infra/` and avoid adding adapter logic there.

## Special Directories

**`alembic/versions/`:**
- Purpose: Ordered migration scripts for schema evolution.
- Generated: Yes (created by Alembic workflow, then edited/committed).
- Committed: Yes.

**`planning/sprints/`:**
- Purpose: Declarative sprint manifests consumed by `scripts/sprint_sync.py`.
- Generated: No (authored/maintained manually).
- Committed: Yes.

**`lite_auction_bot.egg-info/`:**
- Purpose: Local packaging metadata from editable/install steps.
- Generated: Yes.
- Committed: Yes (currently present in repository).

**`.planning/codebase/`:**
- Purpose: Mapper output consumed by orchestration/planning commands.
- Generated: Yes (by GSD mapping workflow).
- Committed: Yes.

---

*Structure analysis: 2026-02-19*
