from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.moderation_checklist_service import (
    ENTITY_COMPLAINT,
    add_checklist_reply,
    ensure_checklist,
    list_checklist_replies,
    render_checklist_block,
)
from app.services.user_service import upsert_user


class _FromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = f"user{user_id}"
        self.first_name = "Test"
        self.last_name = "User"


@pytest.mark.asyncio
async def test_checklist_task_reply_is_persisted_and_rendered(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            actor = await upsert_user(session, _FromUser(96201), mark_private_started=True)
            items = await ensure_checklist(session, entity_type=ENTITY_COMPLAINT, entity_id=501)
            added = await add_checklist_reply(
                session,
                entity_type=ENTITY_COMPLAINT,
                entity_id=501,
                item_code=items[0].code,
                actor_user_id=actor.id,
                reply_text="Проверил фактологию и ставки",
            )
            replies = await list_checklist_replies(
                session,
                entity_type=ENTITY_COMPLAINT,
                entity_id=501,
            )
            rendered = render_checklist_block(items, replies_by_item=replies)

    assert added is not None
    assert added.actor_label.startswith("@")
    assert items[0].code in replies
    assert replies[items[0].code][0].reply_text == "Проверил фактологию и ставки"
    assert "Проверил фактологию и ставки" in rendered
