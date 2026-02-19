# Technology Stack

**Analysis Date:** 2026-02-19

## Languages

**Primary:**
- Python 3.12+ - application runtime, bot, web admin, migrations, and tests in `app/main.py`, `app/web/main.py`, `alembic/env.py`, `tests/integration/conftest.py`, and `pyproject.toml`.

**Secondary:**
- TOML - project/dependency and runtime defaults in `pyproject.toml` and `config/defaults.toml`.
- YAML - CI/CD workflows in `.github/workflows/ci.yml` and `.github/workflows/pr-policy.yml`.
- SQL (via SQLAlchemy/Alembic) - schema and migrations in `app/db/models.py` and `alembic/versions/*.py`.

## Runtime

**Environment:**
- CPython 3.12 (`python:3.12-slim`) in `Dockerfile` and GitHub Actions setup in `.github/workflows/ci.yml`.

**Package Manager:**
- pip (install path `pip install .` and `pip install ".[dev]"`) in `Dockerfile` and `.github/workflows/ci.yml`.
- Build backend: setuptools + wheel in `pyproject.toml`.
- Lockfile: missing (no `poetry.lock`, `Pipfile.lock`, or `requirements.txt` detected).

## Frameworks

**Core:**
- aiogram 3.x - Telegram bot framework and Bot API client in `app/main.py`, `app/bot/handlers/moderation.py`, and `pyproject.toml`.
- FastAPI 0.116+ - admin web panel and auth callback endpoints in `app/web/main.py` and `app/web/auth.py`.
- SQLAlchemy 2.x + asyncpg - async ORM/DB access for PostgreSQL in `app/db/session.py`, `app/db/models.py`, and `pyproject.toml`.
- pydantic-settings 2.x - environment and TOML-driven settings in `app/config.py`.

**Testing:**
- pytest + pytest-asyncio - unit and async/integration tests in `pyproject.toml`, `tests/`, and `tests/integration/conftest.py`.

**Build/Dev:**
- Alembic - DB migrations in `alembic/env.py`, `alembic.ini`, and `alembic/versions/*.py`.
- Ruff - linting gate in `pyproject.toml` and `.github/workflows/ci.yml`.
- Uvicorn - ASGI server for admin panel in `app/web/main.py`.
- Docker/Docker Compose - local multi-service runtime referenced in `Dockerfile`, `README.md`, and `AGENTS.md`.

## Key Dependencies

**Critical:**
- `aiogram` - all Telegram bot interactions (polling, callbacks, topic workflows, verification calls) in `app/main.py`, `app/services/verification_service.py`, and `app/services/channel_dm_intake_service.py`.
- `SQLAlchemy` + `asyncpg` - transaction and persistence layer in `app/db/session.py` and `app/services/*.py`.
- `redis` - cooldowns, digests, and metrics state in `app/infra/redis_client.py`, `app/services/anti_fool_service.py`, and `app/services/notification_metrics_service.py`.

**Infrastructure:**
- `alembic` - schema lifecycle management in `alembic/env.py` and migration files under `alembic/versions/`.
- `python-dotenv` - local env loading in `app/config.py`.
- `fastapi` + `uvicorn[standard]` + `python-multipart` - admin UI and form actions in `app/web/main.py`.

## Configuration

**Environment:**
- Settings source order is kwargs -> env vars -> `.env` -> TOML defaults -> in-code defaults in `app/config.py`.
- Primary settings contract lives in `Settings` at `app/config.py` (DB, Redis, bot/admin auth, moderation topics, GitHub automation, feature flags).
- Alternate TOML config path uses `APP_CONFIG_FILE` in `app/config.py`.
- Environment examples/files detected: `.env` (present), `.env.example` (present), `.env.full.example` (present).

**Build:**
- Python package and dev tools config in `pyproject.toml`.
- Container build in `Dockerfile`.
- Migration config in `alembic.ini` and `alembic/env.py`.
- CI build/test matrix in `.github/workflows/ci.yml`.

## Platform Requirements

**Development:**
- Python 3.12, PostgreSQL, Redis, and Docker Compose services (`bot`, `admin`, `db`, `redis`) documented in `AGENTS.md` and `README.md`.
- Integration tests require dedicated test DB URL and gate flags in `tests/integration/conftest.py` and `.github/workflows/ci.yml`.

**Production:**
- Containerized deployment target is implied by `Dockerfile` and `docker-compose.yml` (service images referenced in `docker-compose.yml` by grep hits and runtime notes in `README.md`).
- Web admin served by FastAPI/Uvicorn on port 8080 in `app/web/main.py`.

---

*Stack analysis: 2026-02-19*
