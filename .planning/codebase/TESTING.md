# Testing Patterns

**Analysis Date:** 2026-02-19

## Test Framework

**Runner:**
- `pytest` (declared in `pyproject.toml` under `[project.optional-dependencies].dev`).
- Async support: `pytest-asyncio` (declared in `pyproject.toml`).
- Config: no dedicated `pytest.ini`/`tox.ini`; behavior is encoded in test code and CI (`tests/integration/conftest.py`, `.github/workflows/ci.yml`).

**Assertion Library:**
- Native `pytest` assertions (`assert ...`) and `pytest.raises(...)` (examples: `tests/test_runtime_settings_service.py`, `tests/test_web_timeline_pagination.py`, `tests/integration/test_web_appeals.py`).

**Run Commands:**
```bash
python -m pytest -q tests                            # Run all tests
python -m pytest -q tests -k <pattern>              # Focused local runs (pytest selection)
RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=... python -m pytest -q tests/integration  # Integration suite
```

## Test File Organization

**Location:**
- Unit-style and service/handler/web tests live in `tests/test_*.py`.
- DB-backed integration tests live in `tests/integration/test_*.py`.

**Naming:**
- Files use `test_<feature>.py` naming (examples: `tests/test_notification_metrics_service.py`, `tests/test_create_auction_handlers.py`, `tests/integration/test_points_service.py`).
- Test functions use `test_<behavior>...` with explicit behavior intent (examples in `tests/test_web_security.py`, `tests/integration/test_web_verification_actions.py`).

**Structure:**
```
tests/
├── test_*.py                    # unit/service/handler/web tests
└── integration/
    ├── conftest.py              # integration engine/session fixtures + safety gates
    └── test_*.py                # Postgres-backed integration tests
```

## Test Structure

**Suite Organization:**
```python
@pytest.mark.asyncio
async def test_behavior_name(monkeypatch, integration_engine) -> None:
    # arrange
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    # act
    result = await some_async_call(...)

    # assert
    assert result == expected
```

**Patterns:**
- Setup pattern: create inline stubs/fakes inside each test module (`tests/test_create_auction_handlers.py`, `tests/test_notification_digest_service.py`).
- Teardown pattern: rely on fixture-managed rollback/drop in `tests/integration/conftest.py`.
- Assertion pattern: direct value assertions and response/body assertions (examples: `tests/test_web_security.py`, `tests/integration/test_web_trade_feedback.py`).

## Mocking

**Framework:**
- `pytest` `monkeypatch` fixture is the primary mocking mechanism.

**Patterns:**
```python
monkeypatch.setattr("app.web.main.SessionFactory", session_factory)
monkeypatch.setattr("app.web.main._require_scope_permission", lambda _req, _scope: (None, _stub_auth()))
monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)
```

**What to Mock:**
- External boundaries and global collaborators: Redis clients, aiogram bot instances, auth/scope guards, settings toggles, and `SessionFactory` in web/handler tests (examples: `tests/test_notification_metrics_service.py`, `tests/test_private_topics_delivery_diagnostics.py`, `tests/integration/test_web_verification_actions.py`, `tests/test_web_dashboard_presets.py`).

**What NOT to Mock:**
- Database integration behavior in `tests/integration/` uses real SQLAlchemy async engine and schema lifecycle from `Base.metadata` (`tests/integration/conftest.py`).
- Domain persistence assertions query real rows via SQLAlchemy `select(...)` in integration tests (examples: `tests/integration/test_points_service.py`, `tests/integration/test_rbac_db_integration.py`).

## Fixtures and Factories

**Test Data:**
```python
async with session_factory() as session:
    async with session.begin():
        user = User(tg_user_id=93501, username="points_user")
        session.add(user)
        await session.flush()
```

**Location:**
- Shared integration fixtures are centralized in `tests/integration/conftest.py` (`integration_engine`, `db_session`).
- No global unit-test fixture registry detected; unit tests use per-file helper classes/functions (examples: `tests/test_create_auction_handlers.py`, `tests/test_web_security.py`).

## Coverage

**Requirements:**
- No explicit coverage threshold or `--cov` enforcement detected in `.github/workflows/ci.yml` or repository config files.

**View Coverage:**
```bash
Not configured in repository defaults (run pytest coverage manually only if introduced).
```

## Test Types

**Unit Tests:**
- Focus on pure service logic, parser/validator helpers, keyboard/rendering behavior, and handler flow transitions with stubs (examples: `tests/test_runtime_settings_service.py`, `tests/test_notification_policy_service.py`, `tests/test_create_auction_handlers.py`).

**Integration Tests:**
- Focus on DB-backed business behavior, role/scope persistence, moderation/web actions, and end-to-end service interactions against real Postgres schema (examples: `tests/integration/test_points_service.py`, `tests/integration/test_web_manage_user_rewards.py`, `tests/integration/test_moderation_callbacks_e2e.py`).

**E2E Tests:**
- Browser/UI E2E framework not detected; end-to-end scope is covered at API/service/callback integration level within `tests/integration/`.

## Common Patterns

**Async Testing:**
```python
@pytest.mark.asyncio
async def test_web_verify_and_unverify_user_actions(monkeypatch, integration_engine) -> None:
    ...
```

**Error Testing:**
```python
with pytest.raises(ValueError, match="Unknown runtime setting key"):
    parse_runtime_setting_value("unknown_key", "1")
```

## CI Test Execution

- CI runs three gates in `.github/workflows/ci.yml`: lint (`ruff`), unit tests (`python -m pytest -q tests`), and integration DB tests (`python -m pytest -q tests/integration`).
- Integration CI job provisions Postgres service, applies migrations (`alembic upgrade head`), sets `RUN_INTEGRATION_TESTS=1` and `TEST_DATABASE_URL`, and re-runs integration tests on pull requests for anti-flaky validation (`.github/workflows/ci.yml`).
- Local command parity is documented in `README.md` and reinforced in `AGENTS.md`.

## Coverage Focus Areas

- Security and auth flows: CSRF/session/safe redirect handling in `tests/test_web_security.py`.
- Notifications and delivery telemetry: policy, digest, metrics, quiet-hours, and logging diagnostics in `tests/test_notification_policy_service.py`, `tests/test_notification_digest_service.py`, `tests/test_notification_metrics_service.py`, and `tests/test_private_topics_delivery_diagnostics.py`.
- Moderation, appeals, guarantor, points, and web admin paths: integration-heavy coverage in `tests/integration/test_appeal_service.py`, `tests/integration/test_guarantor_service.py`, `tests/integration/test_points_service.py`, `tests/integration/test_web_*.py`.
- Bot interaction workflows: handler-level state transitions and callback behavior in `tests/test_create_auction_handlers.py`, `tests/test_start_dashboard.py`, and `tests/test_moderation_topic_router.py`.

---

*Testing analysis: 2026-02-19*
