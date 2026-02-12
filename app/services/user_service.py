from __future__ import annotations

from datetime import datetime, timezone

from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


async def upsert_user(
    session: AsyncSession,
    tg_user: TgUser,
    *,
    mark_private_started: bool = False,
) -> User:
    stmt = select(User).where(User.tg_user_id == tg_user.id)
    existing = await session.scalar(stmt)
    now_utc = datetime.now(timezone.utc)

    if existing:
        existing.username = tg_user.username
        existing.first_name = tg_user.first_name
        existing.last_name = tg_user.last_name
        if mark_private_started and existing.private_started_at is None:
            existing.private_started_at = now_utc
        existing.updated_at = now_utc
        return existing

    user = User(
        tg_user_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
        last_name=tg_user.last_name,
        private_started_at=now_utc if mark_private_started else None,
        updated_at=now_utc,
    )
    session.add(user)
    await session.flush()
    return user
