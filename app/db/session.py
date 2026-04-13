from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
)
SessionFactory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def ping_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def dispose_database() -> None:
    await engine.dispose()
