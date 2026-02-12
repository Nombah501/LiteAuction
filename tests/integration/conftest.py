from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base

if os.getenv("RUN_INTEGRATION_TESTS") != "1":
    pytest.skip("Integration tests are disabled (set RUN_INTEGRATION_TESTS=1)", allow_module_level=True)


@pytest_asyncio.fixture
async def integration_engine():
    db_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        pytest.skip("No TEST_DATABASE_URL or DATABASE_URL set")

    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # pragma: no cover
        await engine.dispose()
        pytest.skip(f"Integration database is unavailable: {exc}")

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(integration_engine) -> AsyncSession:
    session_factory = async_sessionmaker(
        bind=integration_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
