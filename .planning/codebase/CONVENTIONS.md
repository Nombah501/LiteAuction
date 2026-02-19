# Coding Conventions

**Analysis Date:** 2026-02-19

## Naming Patterns

**Files:**
- Use `snake_case.py` for modules across `app/` and `tests/` (examples: `app/services/runtime_settings_service.py`, `app/bot/handlers/create_auction.py`, `tests/test_web_security.py`).
- Keep domain grouping by directory and suffix service/handler intent in file names (examples: `app/services/notification_metrics_service.py`, `app/bot/handlers/moderation.py`, `app/web/auth.py`).

**Functions:**
- Use `snake_case` for all functions and async functions, including route handlers and service methods (examples: `app/web/main.py`, `app/services/points_service.py`, `app/bot/handlers/start.py`).
- Prefix private helpers with leading underscore for parsing/rendering/normalization helpers (examples: `_parse_non_negative_int` in `app/web/main.py`, `_normalize_reason` in `app/services/notification_metrics_service.py`, `_extract_start_payload` in `app/bot/handlers/start.py`).

**Variables:**
- Use `snake_case` for locals/attributes and `UPPER_SNAKE_CASE` for constants (examples: `SessionFactory` and `engine` in `app/db/session.py`, `MAX_AUCTION_PHOTOS` in `app/bot/handlers/create_auction.py`, `_CACHE_TTL_SECONDS` in `app/services/runtime_settings_service.py`).
- Use typed collection annotations for mutable state (`list[...]`, `dict[...]`, `set[...]`) in both app and tests (examples: `app/services/runtime_settings_service.py`, `tests/test_create_auction_handlers.py`).

**Types:**
- Use `PascalCase` for classes/dataclasses/enums (examples: `Settings` in `app/config.py`, `AdminAuthContext` in `app/web/auth.py`, `PointsEventType` in `app/db/enums.py`).
- Prefer `StrEnum` for string-backed domain enums and `@dataclass(slots=True)` for value objects (examples: `app/db/enums.py`, `app/services/notification_metrics_service.py`, `app/services/runtime_settings_service.py`).

## Code Style

**Formatting:**
- Tool used: Ruff, configured in `pyproject.toml`.
- Key settings: `line-length = 100`, `target-version = "py312"` in `pyproject.toml`.
- Keep explicit type hints on public and most internal functions (examples throughout `app/services/*.py`, `app/web/auth.py`, `tests/*.py`).

**Linting:**
- Tool used: Ruff via `python -m ruff check app tests` in `.github/workflows/ci.yml` and `AGENTS.md`.
- Key rules observed in code: explicit suppressions are local and narrow using `# noqa: ...` where unavoidable (examples in `app/services/notification_metrics_service.py`, `tests/test_private_topics_delivery_diagnostics.py`).

## Import Organization

**Order:**
1. `from __future__ import annotations` first line in Python modules (examples: `app/main.py`, `app/web/main.py`, `tests/test_web_security.py`).
2. Standard library imports next (examples: `asyncio`, `logging`, `datetime` in `app/main.py`, `app/web/main.py`).
3. Third-party imports next (examples: `aiogram`, `fastapi`, `sqlalchemy`, `pytest` across `app/` and `tests/`).
4. Project-local imports (`from app...`) last, with occasional relative imports inside handler packages (`app/bot/handlers/__init__.py`).

**Path Aliases:**
- Not applicable for Python import aliases; modules import directly from `app.*` package roots (examples in `app/main.py`, `tests/integration/test_points_service.py`).

## Error Handling

**Patterns:**
- Raise typed HTTP errors in web layer for invalid user input and missing resources (examples: `raise HTTPException(...)` in `app/web/main.py`).
- Raise `ValueError` for domain parsing/validation in service helpers (examples: `parse_runtime_setting_value` in `app/services/runtime_settings_service.py`).
- Use fail-soft loops in watchers: catch broad exceptions, log, sleep, continue (examples: `app/services/auction_watcher.py`, `app/services/outbox_watcher.py`, `app/services/appeal_escalation_watcher.py`).
- Preserve cancellation semantics by re-raising `asyncio.CancelledError` in long-running tasks (examples: `app/services/auction_watcher.py`, `app/services/outbox_watcher.py`).

## Logging

**Framework:** Standard library `logging` configured in `app/logging_setup.py`.

**Patterns:**
- Define `logger = logging.getLogger(__name__)` at module scope and emit structured string messages (examples: `app/main.py`, `app/services/notification_metrics_service.py`, `app/web/main.py`).
- Use `logger.exception(...)` inside broad exception handlers to retain stack traces (examples: `app/services/outbox_watcher.py`, `app/services/auction_service.py`, `app/web/main.py`).
- Keep startup/runtime health logs in entrypoints and workers (examples: `startup_checks` in `app/main.py`, watcher files under `app/services/`).

## Comments

**When to Comment:**
- Prefer self-descriptive names over inline comments; comments are sparse in application code and most intent is encoded in function names (examples across `app/services/` and `app/bot/handlers/`).
- Use suppression comments only when interfacing with framework signatures or unavoidable broad exceptions (examples: `# noqa: ARG001` in `tests/test_private_topics_delivery_diagnostics.py`, `# noqa: BLE001` in `app/services/notification_metrics_service.py`).

**JSDoc/TSDoc:**
- Not applicable in this Python codebase.

## Function Design

**Size:**
- Prefer small-to-medium pure helpers in services and handlers (examples: parsing/formatting helpers in `app/services/runtime_settings_service.py`, `app/bot/handlers/start.py`).
- Large orchestration modules exist for UI endpoints/flows; add new helpers with leading underscore instead of inlining more logic (notably `app/web/main.py`, `app/bot/handlers/moderation.py`).

**Parameters:**
- Strong preference for explicit type hints and keyword-only parameters for multi-argument service APIs (examples: `upsert_runtime_setting_override` in `app/services/runtime_settings_service.py`, `_render_confirmation_page` in `app/web/main.py`).

**Return Values:**
- Return typed dataclasses/tuples/value objects for computed snapshots and decisions rather than untyped dicts (examples: `NotificationMetricsSnapshot` in `app/services/notification_metrics_service.py`, `AdminAuthContext` in `app/web/auth.py`).
- Use `None` for invalid parse attempts in narrow helpers where caller branches on absence (examples: `_parse_non_negative_int` in `app/web/main.py`, `_extract_boost_appeal_id` in `app/bot/handlers/start.py`).

## Module Design

**Exports:**
- Organize by domain-oriented modules under `app/services/`, `app/bot/handlers/`, `app/web/`, and `app/db/`.
- Keep router composition centralized in `app/bot/handlers/__init__.py` and include each feature router once.

**Barrel Files:**
- Limited use. `app/bot/handlers/__init__.py` acts as a barrel/composition module; most other packages import directly from concrete modules.

## Architectural Patterns

- Use layered async architecture: handlers/routes orchestrate, services own business rules, `SessionFactory` mediates DB access, and models/enums define persistence contract (see `app/bot/handlers/*.py`, `app/web/main.py`, `app/services/*.py`, `app/db/models.py`).
- Use dependency boundaries through module-level collaborators (`SessionFactory`, `redis_client`, `settings`) that tests monkeypatch heavily (examples: `app/db/session.py`, `app/infra/redis_client.py`, tests in `tests/` and `tests/integration/`).
- Use event-style watchers for periodic background operations with resilience loops (`app/services/auction_watcher.py`, `app/services/outbox_watcher.py`, `app/services/appeal_escalation_watcher.py`).

## Operational Conventions

- Run lint and tests with repository-standard commands from `AGENTS.md`, `README.md`, and `.github/workflows/ci.yml`: `python -m ruff check app tests`, `python -m pytest -q tests`, and gated integration command with `RUN_INTEGRATION_TESTS=1` + `TEST_DATABASE_URL`.
- Keep integration tests pointed to a dedicated `*_test` database only; this is enforced in `tests/integration/conftest.py` and documented in `README.md`.
- Keep schema safety and CI hygiene aligned with repo policy in `AGENTS.md` (migration required for schema changes, do not merge with failing CI).

---

*Convention analysis: 2026-02-19*
