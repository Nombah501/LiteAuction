from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import User
from app.services.moderation_checklist_service import (
    ENTITY_COMPLAINT,
    ensure_checklist,
    render_checklist_block,
    toggle_checklist_item,
)

pytest.importorskip("aiosqlite")


@pytest.mark.asyncio
async def test_ensure_checklist_creates_template_items() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            items = await ensure_checklist(session, entity_type=ENTITY_COMPLAINT, entity_id=101)

    assert len(items) == 3
    assert all(item.is_done is False for item in items)
    assert "0/3" in render_checklist_block(items)

    await engine.dispose()


@pytest.mark.asyncio
async def test_toggle_checklist_item_updates_state() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            actor = User(tg_user_id=501, username="moderator")
            session.add(actor)
            await session.flush()

            items = await ensure_checklist(session, entity_type=ENTITY_COMPLAINT, entity_id=202)
            toggled = await toggle_checklist_item(
                session,
                entity_type=ENTITY_COMPLAINT,
                entity_id=202,
                item_code=items[0].code,
                actor_user_id=actor.id,
            )
            refreshed = await ensure_checklist(session, entity_type=ENTITY_COMPLAINT, entity_id=202)

    assert toggled is not None
    assert toggled.is_done is True
    assert refreshed[0].is_done is True
    assert "1/3" in render_checklist_block(refreshed)

    await engine.dispose()
