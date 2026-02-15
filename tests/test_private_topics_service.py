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
