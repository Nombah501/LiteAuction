from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.bot.handlers.moderation import _render_bot_funnel_snapshot, mod_funnel_stats
from app.services.bot_funnel_metrics_service import (
    BotFunnelActorRole,
    BotFunnelDropOff,
    BotFunnelJourney,
    BotFunnelJourneySnapshot,
    BotFunnelSnapshot,
)


class _DummyFromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _DummyMessage:
    def __init__(self, *, text: str = "/funnelstats", user_id: int = 42) -> None:
        self.text = text
        self.from_user = _DummyFromUser(user_id)
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


def test_render_bot_funnel_snapshot_includes_conversion_and_dropoffs() -> None:
    snapshot = BotFunnelSnapshot(
        journey_summaries=(
            BotFunnelJourneySnapshot(
                journey=BotFunnelJourney.BID,
                starts=10,
                completes=7,
                fails=3,
                conversion_rate_percent=70.0,
                top_drop_offs=(
                    BotFunnelDropOff(
                        journey=BotFunnelJourney.BID,
                        reason="cooldown",
                        context_key="callback_bid_x1",
                        actor_role=BotFunnelActorRole.BIDDER,
                        total=2,
                    ),
                ),
            ),
        ),
        top_drop_offs=(
            BotFunnelDropOff(
                journey=BotFunnelJourney.BID,
                reason="cooldown",
                context_key="callback_bid_x1",
                actor_role=BotFunnelActorRole.BIDDER,
                total=2,
            ),
        ),
        total_starts=10,
        total_completes=7,
        total_fails=3,
    )

    text = _render_bot_funnel_snapshot(snapshot)

    assert "Bot funnel metrics snapshot" in text
    assert "Ставка: start=10, complete=7, fail=3, conversion=70.0%." in text
    assert "dropoff cooldown / callback_bid_x1 / bidder: 2" in text
    assert "Top drop-offs overall:" in text


@pytest.mark.asyncio
async def test_mod_funnel_stats_sends_snapshot(monkeypatch) -> None:
    message = _DummyMessage()
    progress_calls: list[tuple[str, str]] = []

    async def _ensure_topic(_message, _bot, _command_hint):
        return True

    async def _require_moderator(_message):
        return True

    async def _send_progress(bot, _message, *, text: str, scope_key: str):  # noqa: ARG001
        progress_calls.append((text, scope_key))

    async def _render_snapshot(*, compact_mode: bool = False) -> str:
        assert compact_mode is False
        return "funnel snapshot"

    monkeypatch.setattr("app.bot.handlers.moderation._ensure_moderation_topic", _ensure_topic)
    monkeypatch.setattr("app.bot.handlers.moderation._require_moderator", _require_moderator)
    monkeypatch.setattr("app.bot.handlers.moderation.send_progress_draft", _send_progress)
    monkeypatch.setattr("app.bot.handlers.moderation._render_bot_funnel_snapshot_text", _render_snapshot)

    await mod_funnel_stats(message, bot=SimpleNamespace())

    assert progress_calls == [("Собираю snapshot по funnel telemetry...", "funnelstats")]
    assert message.answers == ["funnel snapshot"]


@pytest.mark.asyncio
async def test_mod_funnel_stats_rejects_unknown_argument(monkeypatch) -> None:
    message = _DummyMessage(text="/funnelstats mode=short")

    async def _ensure_topic(_message, _bot, _command_hint):
        return True

    async def _require_moderator(_message):
        return True

    monkeypatch.setattr("app.bot.handlers.moderation._ensure_moderation_topic", _ensure_topic)
    monkeypatch.setattr("app.bot.handlers.moderation._require_moderator", _require_moderator)

    await mod_funnel_stats(message, bot=SimpleNamespace())

    assert message.answers == ["Формат: /funnelstats [compact]"]


@pytest.mark.asyncio
async def test_mod_funnel_stats_supports_compact_mode(monkeypatch) -> None:
    message = _DummyMessage(text="/funnelstats compact")

    async def _ensure_topic(_message, _bot, _command_hint):
        return True

    async def _require_moderator(_message):
        return True

    async def _send_progress(bot, _message, *, text: str, scope_key: str):  # noqa: ARG001
        return None

    async def _render_snapshot(*, compact_mode: bool = False) -> str:
        assert compact_mode is True
        return "compact funnel snapshot"

    monkeypatch.setattr("app.bot.handlers.moderation._ensure_moderation_topic", _ensure_topic)
    monkeypatch.setattr("app.bot.handlers.moderation._require_moderator", _require_moderator)
    monkeypatch.setattr("app.bot.handlers.moderation.send_progress_draft", _send_progress)
    monkeypatch.setattr("app.bot.handlers.moderation._render_bot_funnel_snapshot_text", _render_snapshot)

    await mod_funnel_stats(message, bot=SimpleNamespace())

    assert message.answers == ["compact funnel snapshot"]
