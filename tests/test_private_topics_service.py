from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import private_topics_service


def test_topics_capability_cache_uses_telegram_hint() -> None:
    private_topics_service._TOPICS_CAPABILITY_CACHE.clear()  # noqa: SLF001

    hint_user = SimpleNamespace(has_topics_enabled=False)
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


@pytest.mark.asyncio
async def test_topics_overview_falls_back_when_topics_disabled() -> None:
    private_topics_service._TOPICS_CAPABILITY_CACHE.clear()  # noqa: SLF001

    text = await private_topics_service.render_user_topics_overview(
        session=None,  # type: ignore[arg-type]
        bot=None,
        user=SimpleNamespace(tg_user_id=909),
        telegram_user=SimpleNamespace(has_topics_enabled=False),
    )

    assert "топики отключены" in text
