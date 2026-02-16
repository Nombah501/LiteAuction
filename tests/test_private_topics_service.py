from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.services import private_topics_service


def test_topics_capability_cache_uses_telegram_hint() -> None:
    private_topics_service._TOPICS_CAPABILITY_CACHE.clear()  # noqa: SLF001

    hint_user = cast(Any, SimpleNamespace(has_topics_enabled=False))
    result = private_topics_service._resolve_cached_topics_capability(  # noqa: SLF001
        tg_user_id=501,
        telegram_user=hint_user,
    )

    assert result is False
    assert private_topics_service._TOPICS_CAPABILITY_CACHE.get(501) is False  # noqa: SLF001


def test_topics_capability_cache_reuses_previous_value() -> None:
    private_topics_service._TOPICS_CAPABILITY_CACHE.clear()  # noqa: SLF001
    private_topics_service._TOPICS_CAPABILITY_CACHE[777] = True  # noqa: SLF001

    result = private_topics_service._resolve_cached_topics_capability(  # noqa: SLF001
        tg_user_id=777,
        telegram_user=None,
    )

    assert result is True


def test_topic_mutation_policy_cache_uses_telegram_hint() -> None:
    private_topics_service._TOPIC_MUTATION_POLICY_CACHE.clear()  # noqa: SLF001

    hint_user = SimpleNamespace(allows_users_to_create_topics=False)
    result = private_topics_service._resolve_cached_topic_mutation_policy(  # noqa: SLF001
        tg_user_id=501,
        telegram_user=hint_user,
    )

    assert result is False
    assert private_topics_service._TOPIC_MUTATION_POLICY_CACHE.get(501) is False  # noqa: SLF001


def test_topic_mutation_policy_cache_reuses_previous_value() -> None:
    private_topics_service._TOPIC_MUTATION_POLICY_CACHE.clear()  # noqa: SLF001
    private_topics_service._TOPIC_MUTATION_POLICY_CACHE[777] = True  # noqa: SLF001

    result = private_topics_service._resolve_cached_topic_mutation_policy(  # noqa: SLF001
        tg_user_id=777,
        telegram_user=None,
    )

    assert result is True


def test_redundant_threads_for_regular_user_include_old_topics() -> None:
    rows = [
        cast(Any, SimpleNamespace(thread_id=10)),
        cast(Any, SimpleNamespace(thread_id=20)),
        cast(Any, SimpleNamespace(thread_id=30)),
        cast(Any, SimpleNamespace(thread_id=40)),
        cast(Any, SimpleNamespace(thread_id=50)),
    ]
    mapping = {
        private_topics_service.PrivateTopicPurpose.AUCTIONS: 10,
        private_topics_service.PrivateTopicPurpose.SUPPORT: 20,
        private_topics_service.PrivateTopicPurpose.POINTS: 20,
        private_topics_service.PrivateTopicPurpose.TRADES: 20,
    }

    redundant = private_topics_service._redundant_private_topic_thread_ids(  # noqa: SLF001
        rows,
        mapping=mapping,
        include_moderation=False,
    )

    assert redundant == {30, 40, 50}


def test_redundant_threads_for_moderator_preserve_moderation_topic() -> None:
    rows = [
        cast(Any, SimpleNamespace(thread_id=10)),
        cast(Any, SimpleNamespace(thread_id=20)),
        cast(Any, SimpleNamespace(thread_id=30)),
        cast(Any, SimpleNamespace(thread_id=40)),
        cast(Any, SimpleNamespace(thread_id=50)),
    ]
    mapping = {
        private_topics_service.PrivateTopicPurpose.AUCTIONS: 10,
        private_topics_service.PrivateTopicPurpose.SUPPORT: 20,
        private_topics_service.PrivateTopicPurpose.POINTS: 20,
        private_topics_service.PrivateTopicPurpose.TRADES: 20,
        private_topics_service.PrivateTopicPurpose.MODERATION: 50,
    }

    redundant = private_topics_service._redundant_private_topic_thread_ids(  # noqa: SLF001
        rows,
        mapping=mapping,
        include_moderation=True,
    )

    assert redundant == {30, 40}


@pytest.mark.asyncio
async def test_ensure_user_private_topics_ignores_existing_when_topics_capability_off(monkeypatch) -> None:
    async def _existing_topics_map(*_args, **_kwargs):  # noqa: ANN202
        return {
            private_topics_service.PrivateTopicPurpose.AUCTIONS: 11,
            private_topics_service.PrivateTopicPurpose.SUPPORT: 22,
        }

    monkeypatch.setattr(private_topics_service, "_load_user_topics_map", _existing_topics_map)

    result = await private_topics_service.ensure_user_private_topics(
        session=None,  # type: ignore[arg-type]
        bot=None,
        user=cast(Any, SimpleNamespace(id=1, tg_user_id=909)),
        telegram_user=cast(Any, SimpleNamespace(has_topics_enabled=False)),
    )

    assert result.mapping == {}


@pytest.mark.asyncio
async def test_topics_overview_falls_back_when_topics_disabled() -> None:
    private_topics_service._TOPICS_CAPABILITY_CACHE.clear()  # noqa: SLF001

    text = await private_topics_service.render_user_topics_overview(
        session=None,  # type: ignore[arg-type]
        bot=None,
        user=cast(Any, SimpleNamespace(tg_user_id=909)),
        telegram_user=cast(Any, SimpleNamespace(has_topics_enabled=False)),
    )

    assert "топики отключены" in text


@pytest.mark.asyncio
async def test_topics_overview_reports_policy_blocked_sections(monkeypatch) -> None:
    private_topics_service._TOPICS_CAPABILITY_CACHE.clear()  # noqa: SLF001
    private_topics_service._TOPIC_MUTATION_POLICY_CACHE.clear()  # noqa: SLF001
    monkeypatch.setattr(private_topics_service.settings, "private_topics_enabled", True)
    monkeypatch.setattr(private_topics_service.settings, "private_topics_user_topic_policy", "block")

    class _BotStub:
        async def get_me(self):  # noqa: ANN201
            return SimpleNamespace(has_topics_enabled=True, allows_users_to_create_topics=False)

    async def _empty_topics_map(*_args, **_kwargs):  # noqa: ANN202
        return {}

    monkeypatch.setattr(private_topics_service, "_load_user_topics_map", _empty_topics_map)

    text = await private_topics_service.render_user_topics_overview(
        session=None,  # type: ignore[arg-type]
        bot=_BotStub(),
        user=cast(Any, SimpleNamespace(id=1, tg_user_id=909)),
        telegram_user=None,
    )

    assert "политикой тем" in text
