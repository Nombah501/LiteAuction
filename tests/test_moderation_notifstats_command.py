from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.bot.handlers.moderation import (
    _render_notification_metrics_snapshot_text,
    mod_help,
    mod_notification_stats,
)
from app.services.notification_metrics_service import (
    NotificationAlertCode,
    NotificationAlertHint,
    NotificationAlertSeverity,
    NotificationMetricDelta,
    NotificationMetricBucket,
    NotificationMetricsSnapshot,
    NotificationMetricTotals,
)
from app.services.notification_policy_service import NotificationEventType


class _DummyFromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _DummyMessage:
    def __init__(self, *, text: str = "/notifstats", user_id: int = 42) -> None:
        self.text = text
        self.from_user = _DummyFromUser(user_id)
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


class _DummySessionFactoryCtx:
    async def __aenter__(self):  # noqa: ANN204
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001, ANN204
        return False


class _DummySessionFactory:
    def __call__(self) -> _DummySessionFactoryCtx:
        return _DummySessionFactoryCtx()


@pytest.mark.asyncio
async def test_render_notification_metrics_snapshot_text_includes_top_reasons(monkeypatch) -> None:
    captured_filters: list[tuple[int, NotificationEventType | None, str | None]] = []

    async def _snapshot_loader(
        *,
        top_limit: int = 5,
        event_type_filter: NotificationEventType | None = None,
        reason_filter: str | None = None,
    ) -> NotificationMetricsSnapshot:  # noqa: ARG001
        captured_filters.append((top_limit, event_type_filter, reason_filter))
        return NotificationMetricsSnapshot(
            all_time=NotificationMetricTotals(sent_total=11, suppressed_total=7, aggregated_total=5),
            last_24h=NotificationMetricTotals(sent_total=4, suppressed_total=2, aggregated_total=1),
            previous_24h=NotificationMetricTotals(sent_total=2, suppressed_total=3, aggregated_total=1),
            delta_24h_vs_previous_24h=NotificationMetricDelta(
                sent_delta=2,
                suppressed_delta=-1,
                aggregated_delta=0,
            ),
            last_7d=NotificationMetricTotals(sent_total=9, suppressed_total=5, aggregated_total=3),
            top_suppressed_24h=(
                NotificationMetricBucket(
                    event_type=NotificationEventType.AUCTION_OUTBID,
                    reason="blocked_master",
                    total=2,
                ),
            ),
            top_suppressed_7d=(
                NotificationMetricBucket(
                    event_type=NotificationEventType.AUCTION_OUTBID,
                    reason="blocked_master",
                    total=4,
                ),
                NotificationMetricBucket(
                    event_type=NotificationEventType.SUPPORT,
                    reason="forbidden",
                    total=3,
                ),
            ),
            top_suppressed=(
                NotificationMetricBucket(
                    event_type=NotificationEventType.AUCTION_OUTBID,
                    reason="blocked_master",
                    total=4,
                ),
                NotificationMetricBucket(
                    event_type=NotificationEventType.SUPPORT,
                    reason="forbidden",
                    total=3,
                ),
            ),
            alert_hints=(
                NotificationAlertHint(
                    severity=NotificationAlertSeverity.HIGH,
                    code=NotificationAlertCode.SUPPRESSED_DELTA_HIGH,
                    message="suppressed delta +80 >= +80",
                ),
            ),
        )

    monkeypatch.setattr("app.bot.handlers.moderation.load_notification_metrics_snapshot", _snapshot_loader)

    text = await _render_notification_metrics_snapshot_text()

    assert "Notification metrics snapshot" in text
    assert "sent total: 11" in text
    assert "suppressed total: 7" in text
    assert "aggregated total: 5" in text
    assert "sent total (24h): 4" in text
    assert "suppressed total (24h): 2" in text
    assert "aggregated total (24h): 1" in text
    assert "sent delta: +2" in text
    assert "suppressed delta: -1" in text
    assert "aggregated delta: 0" in text
    assert "sent total (7d): 9" in text
    assert "suppressed total (7d): 5" in text
    assert "aggregated total (7d): 3" in text
    assert "Alert hints:" in text
    assert "HIGH: suppressed delta +80 >= +80" in text
    assert "Top suppression reasons (24h):" in text
    assert "Top suppression reasons (7d):" in text
    assert "Перебили ставку / blocked_master: 2 (share=100%)" in text
    assert "Перебили ставку / blocked_master: 4 (share=57%)" in text
    assert "Поддержка / forbidden: 3 (share=43%)" in text
    assert captured_filters == [(5, None, None)]


@pytest.mark.asyncio
async def test_render_notification_metrics_snapshot_text_compact_mode(monkeypatch) -> None:
    captured_filters: list[tuple[int, NotificationEventType | None, str | None]] = []

    async def _snapshot_loader(
        *,
        top_limit: int = 5,
        event_type_filter: NotificationEventType | None = None,
        reason_filter: str | None = None,
    ) -> NotificationMetricsSnapshot:  # noqa: ARG001
        captured_filters.append((top_limit, event_type_filter, reason_filter))
        return NotificationMetricsSnapshot(
            all_time=NotificationMetricTotals(sent_total=20, suppressed_total=9, aggregated_total=4),
            last_24h=NotificationMetricTotals(sent_total=6, suppressed_total=3, aggregated_total=1),
            previous_24h=NotificationMetricTotals(sent_total=5, suppressed_total=2, aggregated_total=1),
            delta_24h_vs_previous_24h=NotificationMetricDelta(sent_delta=1, suppressed_delta=1, aggregated_delta=0),
            last_7d=NotificationMetricTotals(sent_total=15, suppressed_total=8, aggregated_total=3),
            top_suppressed_24h=(
                NotificationMetricBucket(
                    event_type=NotificationEventType.SUPPORT,
                    reason="forbidden",
                    total=2,
                ),
            ),
            top_suppressed_7d=(),
            top_suppressed=(),
            alert_hints=(),
        )

    monkeypatch.setattr("app.bot.handlers.moderation.load_notification_metrics_snapshot", _snapshot_loader)

    text = await _render_notification_metrics_snapshot_text(compact_mode=True)

    assert "Compact totals:" in text
    assert "all-time: sent=20, suppressed=9, aggregated=4" in text
    assert "delta24h: sent=+1, suppressed=+1, aggregated=0" in text
    assert "top-1 suppression: 24h Поддержка/forbidden: 2" in text
    assert captured_filters == [(1, None, None)]


@pytest.mark.asyncio
async def test_render_notification_metrics_snapshot_text_handles_zero_suppressed_totals(monkeypatch) -> None:
    async def _snapshot_loader(
        *,
        top_limit: int = 5,
        event_type_filter: NotificationEventType | None = None,
        reason_filter: str | None = None,
    ) -> NotificationMetricsSnapshot:  # noqa: ARG001
        return NotificationMetricsSnapshot(
            all_time=NotificationMetricTotals(sent_total=3, suppressed_total=0, aggregated_total=1),
            last_24h=NotificationMetricTotals(sent_total=1, suppressed_total=0, aggregated_total=0),
            previous_24h=NotificationMetricTotals(sent_total=1, suppressed_total=0, aggregated_total=0),
            delta_24h_vs_previous_24h=NotificationMetricDelta(sent_delta=0, suppressed_delta=0, aggregated_delta=0),
            last_7d=NotificationMetricTotals(sent_total=2, suppressed_total=0, aggregated_total=1),
            top_suppressed_24h=(
                NotificationMetricBucket(
                    event_type=NotificationEventType.SUPPORT,
                    reason="forbidden",
                    total=1,
                ),
            ),
            top_suppressed_7d=(),
            top_suppressed=(
                NotificationMetricBucket(
                    event_type=NotificationEventType.AUCTION_OUTBID,
                    reason="blocked_master",
                    total=2,
                ),
            ),
            alert_hints=(),
        )

    monkeypatch.setattr("app.bot.handlers.moderation.load_notification_metrics_snapshot", _snapshot_loader)

    text = await _render_notification_metrics_snapshot_text()

    assert "Top suppression reasons (24h):" in text
    assert "Поддержка / forbidden: 1 (share=n/a)" in text
    assert "Top suppression reasons (event/reason, all-time):" in text
    assert "Перебили ставку / blocked_master: 2 (share=n/a)" in text


@pytest.mark.asyncio
async def test_mod_notification_stats_sends_snapshot(monkeypatch) -> None:
    message = _DummyMessage()
    progress_calls: list[tuple[str, str]] = []
    captured_render_filters: list[tuple[NotificationEventType | None, str | None, bool]] = []

    async def _ensure_topic(_message, _bot, _command_hint):
        return True

    async def _require_moderator(_message):
        return True

    async def _send_progress(bot, _message, *, text: str, scope_key: str):  # noqa: ARG001
        progress_calls.append((text, scope_key))

    async def _render_snapshot(
        *,
        event_type_filter: NotificationEventType | None = None,
        reason_filter: str | None = None,
        compact_mode: bool = False,
    ) -> str:
        captured_render_filters.append((event_type_filter, reason_filter, compact_mode))
        return "snapshot text"

    monkeypatch.setattr("app.bot.handlers.moderation._ensure_moderation_topic", _ensure_topic)
    monkeypatch.setattr("app.bot.handlers.moderation._require_moderator", _require_moderator)
    monkeypatch.setattr("app.bot.handlers.moderation.send_progress_draft", _send_progress)
    monkeypatch.setattr("app.bot.handlers.moderation._render_notification_metrics_snapshot_text", _render_snapshot)

    await mod_notification_stats(message, bot=SimpleNamespace())

    assert progress_calls == [("Собираю snapshot по метрикам уведомлений...", "notifstats")]
    assert message.answers == ["snapshot text"]
    assert captured_render_filters == [(None, None, False)]


@pytest.mark.asyncio
async def test_mod_notification_stats_accepts_event_and_reason_filters(monkeypatch) -> None:
    message = _DummyMessage(text="/notifstats auction_outbid quiet")
    captured_render_filters: list[tuple[NotificationEventType | None, str | None, bool]] = []

    async def _ensure_topic(_message, _bot, _command_hint):
        return True

    async def _require_moderator(_message):
        return True

    async def _send_progress(bot, _message, *, text: str, scope_key: str):  # noqa: ARG001
        return None

    async def _render_snapshot(
        *,
        event_type_filter: NotificationEventType | None = None,
        reason_filter: str | None = None,
        compact_mode: bool = False,
    ) -> str:
        captured_render_filters.append((event_type_filter, reason_filter, compact_mode))
        return "filtered snapshot"

    monkeypatch.setattr("app.bot.handlers.moderation._ensure_moderation_topic", _ensure_topic)
    monkeypatch.setattr("app.bot.handlers.moderation._require_moderator", _require_moderator)
    monkeypatch.setattr("app.bot.handlers.moderation.send_progress_draft", _send_progress)
    monkeypatch.setattr("app.bot.handlers.moderation._render_notification_metrics_snapshot_text", _render_snapshot)

    await mod_notification_stats(message, bot=SimpleNamespace())

    assert message.answers == ["filtered snapshot"]
    assert captured_render_filters == [(NotificationEventType.AUCTION_OUTBID, "quiet", False)]


@pytest.mark.asyncio
async def test_mod_notification_stats_accepts_compact_mode(monkeypatch) -> None:
    message = _DummyMessage(text="/notifstats compact auction_outbid")
    captured_render_filters: list[tuple[NotificationEventType | None, str | None, bool]] = []

    async def _ensure_topic(_message, _bot, _command_hint):
        return True

    async def _require_moderator(_message):
        return True

    async def _send_progress(bot, _message, *, text: str, scope_key: str):  # noqa: ARG001
        return None

    async def _render_snapshot(
        *,
        event_type_filter: NotificationEventType | None = None,
        reason_filter: str | None = None,
        compact_mode: bool = False,
    ) -> str:
        captured_render_filters.append((event_type_filter, reason_filter, compact_mode))
        return "compact snapshot"

    monkeypatch.setattr("app.bot.handlers.moderation._ensure_moderation_topic", _ensure_topic)
    monkeypatch.setattr("app.bot.handlers.moderation._require_moderator", _require_moderator)
    monkeypatch.setattr("app.bot.handlers.moderation.send_progress_draft", _send_progress)
    monkeypatch.setattr("app.bot.handlers.moderation._render_notification_metrics_snapshot_text", _render_snapshot)

    await mod_notification_stats(message, bot=SimpleNamespace())

    assert message.answers == ["compact snapshot"]
    assert captured_render_filters == [(NotificationEventType.AUCTION_OUTBID, None, True)]


@pytest.mark.asyncio
async def test_mod_notification_stats_rejects_invalid_filters_with_help(monkeypatch) -> None:
    message = _DummyMessage(text="/notifstats event=unknown")

    async def _ensure_topic(_message, _bot, _command_hint):
        return True

    async def _require_moderator(_message):
        return True

    monkeypatch.setattr("app.bot.handlers.moderation._ensure_moderation_topic", _ensure_topic)
    monkeypatch.setattr("app.bot.handlers.moderation._require_moderator", _require_moderator)

    await mod_notification_stats(message, bot=SimpleNamespace())

    assert len(message.answers) == 1
    assert "Формат: /notifstats" in message.answers[0]
    assert "Неизвестный event-фильтр" in message.answers[0]


@pytest.mark.asyncio
async def test_mod_notification_stats_rejects_unknown_mode(monkeypatch) -> None:
    message = _DummyMessage(text="/notifstats mode=short")

    async def _ensure_topic(_message, _bot, _command_hint):
        return True

    async def _require_moderator(_message):
        return True

    monkeypatch.setattr("app.bot.handlers.moderation._ensure_moderation_topic", _ensure_topic)
    monkeypatch.setattr("app.bot.handlers.moderation._require_moderator", _require_moderator)

    await mod_notification_stats(message, bot=SimpleNamespace())

    assert len(message.answers) == 1
    assert "Неизвестный mode" in message.answers[0]


@pytest.mark.asyncio
async def test_mod_help_lists_notifstats_command(monkeypatch) -> None:
    message = _DummyMessage(text="/mod")

    async def _ensure_topic(_message, _bot, _command_hint):
        return True

    async def _require_moderator(_message):
        return True

    async def _scopes(_session, _tg_user_id: int) -> frozenset[str]:
        return frozenset()

    monkeypatch.setattr("app.bot.handlers.moderation._ensure_moderation_topic", _ensure_topic)
    monkeypatch.setattr("app.bot.handlers.moderation._require_moderator", _require_moderator)
    monkeypatch.setattr("app.bot.handlers.moderation.get_moderation_scopes", _scopes)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", _DummySessionFactory())

    await mod_help(message, bot=SimpleNamespace())

    assert message.answers
    assert "/notifstats [compact] [event] [reason]" in message.answers[-1]
