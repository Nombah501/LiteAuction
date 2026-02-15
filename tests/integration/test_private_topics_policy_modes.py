from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.private_topics_service import PURPOSE_ORDER, ensure_user_private_topics
from app.services.user_service import upsert_user


class _FromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = f"user{user_id}"
        self.first_name = "Test"
        self.last_name = "User"


class _BotPolicyStub:
    def __init__(self, *, allows_users_to_create_topics: bool) -> None:
        self._allows_users_to_create_topics = allows_users_to_create_topics
        self.created_topics = 0

    async def get_me(self):  # noqa: ANN201
        return SimpleNamespace(
            has_topics_enabled=True,
            allows_users_to_create_topics=self._allows_users_to_create_topics,
        )

    async def create_forum_topic(self, *, chat_id: int, name: str):  # noqa: ANN201, ARG002
        self.created_topics += 1
        return SimpleNamespace(message_thread_id=1000 + self.created_topics)


@pytest.mark.asyncio
async def test_private_topics_auto_policy_blocks_topic_creation(monkeypatch, integration_engine) -> None:
    from app.services import private_topics_service

    private_topics_service._TOPICS_CAPABILITY_CACHE.clear()  # noqa: SLF001
    private_topics_service._TOPIC_MUTATION_POLICY_CACHE.clear()  # noqa: SLF001
    private_topics_service._BOT_TOPICS_CAPABILITY = None  # noqa: SLF001
    private_topics_service._BOT_TOPIC_MUTATION_ALLOWED = None  # noqa: SLF001

    monkeypatch.setattr(private_topics_service.settings, "private_topics_enabled", True)
    monkeypatch.setattr(private_topics_service.settings, "private_topics_user_topic_policy", "auto")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    bot = _BotPolicyStub(allows_users_to_create_topics=False)

    async with session_factory() as session:
        async with session.begin():
            user = await upsert_user(session, _FromUser(95101), mark_private_started=True)
            result = await ensure_user_private_topics(session, bot, user=user)

    assert result.created == []
    assert result.mutation_blocked is True
    assert result.missing
    assert bot.created_topics == 0


@pytest.mark.asyncio
async def test_private_topics_auto_policy_allows_topic_creation(monkeypatch, integration_engine) -> None:
    from app.services import private_topics_service

    private_topics_service._TOPICS_CAPABILITY_CACHE.clear()  # noqa: SLF001
    private_topics_service._TOPIC_MUTATION_POLICY_CACHE.clear()  # noqa: SLF001
    private_topics_service._BOT_TOPICS_CAPABILITY = None  # noqa: SLF001
    private_topics_service._BOT_TOPIC_MUTATION_ALLOWED = None  # noqa: SLF001

    monkeypatch.setattr(private_topics_service.settings, "private_topics_enabled", True)
    monkeypatch.setattr(private_topics_service.settings, "private_topics_user_topic_policy", "auto")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    bot = _BotPolicyStub(allows_users_to_create_topics=True)

    async with session_factory() as session:
        async with session.begin():
            user = await upsert_user(session, _FromUser(95102), mark_private_started=True)
            result = await ensure_user_private_topics(session, bot, user=user)

    assert result.mutation_blocked is False
    assert len(result.created) == len(PURPOSE_ORDER)
    assert len(result.mapping) == len(PURPOSE_ORDER)
    assert result.missing == []
    assert bot.created_topics == len(PURPOSE_ORDER)
