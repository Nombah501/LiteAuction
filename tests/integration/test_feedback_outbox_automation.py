from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import FeedbackStatus, FeedbackType, IntegrationOutboxStatus, ModerationAction
from app.db.models import FeedbackItem, IntegrationOutbox, ModerationLog, User
from app.services.github_automation_service import GitHubIssueRef
from app.services.outbox_service import enqueue_feedback_issue_event, process_pending_outbox_events


class _StaticIssueClient:
    def __init__(self, *, issue_number: int, issue_url: str) -> None:
        self.issue_number = issue_number
        self.issue_url = issue_url
        self.calls = 0

    async def create_issue(self, *, title: str, body: str, labels: list[str]) -> GitHubIssueRef:
        self.calls += 1
        assert title
        assert body
        assert labels
        return GitHubIssueRef(number=self.issue_number, url=self.issue_url)


class _FlakyIssueClient:
    def __init__(self) -> None:
        self.calls = 0

    async def create_issue(self, *, title: str, body: str, labels: list[str]) -> GitHubIssueRef:
        self.calls += 1
        assert title
        assert body
        assert labels
        if self.calls == 1:
            raise RuntimeError("temporary github outage")
        return GitHubIssueRef(number=7002, url="https://github.com/Nombah501/LiteAuction/issues/7002")


class _AlwaysFailIssueClient:
    def __init__(self) -> None:
        self.calls = 0

    async def create_issue(self, *, title: str, body: str, labels: list[str]) -> GitHubIssueRef:
        self.calls += 1
        assert title
        assert body
        assert labels
        raise RuntimeError("permanent github failure")


@pytest.mark.asyncio
async def test_outbox_worker_creates_issue_once_and_is_idempotent(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.services.outbox_service.SessionFactory", session_factory)
    monkeypatch.setattr(settings, "github_automation_enabled", True)
    monkeypatch.setattr(settings, "outbox_batch_size", 10)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93201, username="issue_submitter")
            moderator = User(tg_user_id=93202, username="issue_moderator")
            session.add_all([submitter, moderator])
            await session.flush()

            item = FeedbackItem(
                type=FeedbackType.BUG,
                status=FeedbackStatus.APPROVED,
                submitter_user_id=submitter.id,
                moderator_user_id=moderator.id,
                content="При подтверждении ставки кнопка зависает",
                resolution_note="Подтверждено и принято",
                reward_points=30,
                resolved_at=datetime.now(UTC),
            )
            session.add(item)
            await session.flush()
            feedback_id = item.id

            inserted = await enqueue_feedback_issue_event(session, feedback_id=feedback_id)
            assert inserted is True

    client = _StaticIssueClient(
        issue_number=7001,
        issue_url="https://github.com/Nombah501/LiteAuction/issues/7001",
    )
    first_run = await process_pending_outbox_events(issue_client=client)
    second_run = await process_pending_outbox_events(issue_client=client)

    assert first_run == 1
    assert second_run == 0
    assert client.calls == 1

    async with session_factory() as session:
        item_row = await session.scalar(select(FeedbackItem).where(FeedbackItem.id == feedback_id))
        outbox_row = await session.scalar(select(IntegrationOutbox).where(IntegrationOutbox.dedupe_key == f"feedback:{feedback_id}:github-issue"))
        logs = (
            await session.execute(
                select(ModerationLog)
                .where(ModerationLog.action == ModerationAction.CREATE_FEEDBACK_GITHUB_ISSUE)
                .order_by(ModerationLog.id.asc())
            )
        ).scalars().all()

    assert item_row is not None
    assert item_row.github_issue_url == "https://github.com/Nombah501/LiteAuction/issues/7001"
    assert outbox_row is not None
    assert outbox_row.status == IntegrationOutboxStatus.DONE
    assert outbox_row.attempts == 1
    assert outbox_row.last_error is None
    assert len(logs) == 1
    assert logs[0].payload is not None
    assert logs[0].payload.get("feedback_id") == feedback_id
    assert logs[0].payload.get("github_issue_url") == "https://github.com/Nombah501/LiteAuction/issues/7001"


@pytest.mark.asyncio
async def test_outbox_worker_retries_after_failure_and_then_succeeds(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.services.outbox_service.SessionFactory", session_factory)
    monkeypatch.setattr(settings, "github_automation_enabled", True)
    monkeypatch.setattr(settings, "outbox_batch_size", 10)
    monkeypatch.setattr(settings, "outbox_max_attempts", 3)
    monkeypatch.setattr(settings, "outbox_retry_base_seconds", 60)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93211, username="retry_submitter")
            session.add(submitter)
            await session.flush()

            item = FeedbackItem(
                type=FeedbackType.SUGGESTION,
                status=FeedbackStatus.APPROVED,
                submitter_user_id=submitter.id,
                content="Сделать историю ставок более наглядной",
                reward_points=20,
                resolved_at=datetime.now(UTC),
            )
            session.add(item)
            await session.flush()
            feedback_id = item.id
            await enqueue_feedback_issue_event(session, feedback_id=feedback_id)

    client = _FlakyIssueClient()
    first_run = await process_pending_outbox_events(issue_client=client)
    assert first_run == 1

    async with session_factory() as session:
        outbox_after_first = await session.scalar(
            select(IntegrationOutbox).where(IntegrationOutbox.dedupe_key == f"feedback:{feedback_id}:github-issue")
        )
        assert outbox_after_first is not None
        assert outbox_after_first.status == IntegrationOutboxStatus.PENDING
        assert outbox_after_first.attempts == 1
        assert outbox_after_first.last_error is not None

    async with session_factory() as session:
        async with session.begin():
            locked = await session.scalar(
                select(IntegrationOutbox)
                .where(IntegrationOutbox.dedupe_key == f"feedback:{feedback_id}:github-issue")
                .with_for_update()
            )
            assert locked is not None
            locked.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)

    second_run = await process_pending_outbox_events(issue_client=client)
    assert second_run == 1
    assert client.calls == 2

    async with session_factory() as session:
        item_row = await session.scalar(select(FeedbackItem).where(FeedbackItem.id == feedback_id))
        outbox_row = await session.scalar(
            select(IntegrationOutbox).where(IntegrationOutbox.dedupe_key == f"feedback:{feedback_id}:github-issue")
        )

    assert item_row is not None
    assert item_row.github_issue_url == "https://github.com/Nombah501/LiteAuction/issues/7002"
    assert outbox_row is not None
    assert outbox_row.status == IntegrationOutboxStatus.DONE
    assert outbox_row.attempts == 2
    assert outbox_row.last_error is None


@pytest.mark.asyncio
async def test_outbox_worker_marks_event_failed_after_max_attempts(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.services.outbox_service.SessionFactory", session_factory)
    monkeypatch.setattr(settings, "github_automation_enabled", True)
    monkeypatch.setattr(settings, "outbox_batch_size", 10)
    monkeypatch.setattr(settings, "outbox_max_attempts", 2)
    monkeypatch.setattr(settings, "outbox_retry_base_seconds", 1)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93221)
            session.add(submitter)
            await session.flush()

            item = FeedbackItem(
                type=FeedbackType.BUG,
                status=FeedbackStatus.APPROVED,
                submitter_user_id=submitter.id,
                content="Падение на пустом описании",
                reward_points=30,
                resolved_at=datetime.now(UTC),
            )
            session.add(item)
            await session.flush()
            feedback_id = item.id
            await enqueue_feedback_issue_event(session, feedback_id=feedback_id)

    client = _AlwaysFailIssueClient()
    run_one = await process_pending_outbox_events(issue_client=client)
    assert run_one == 1

    async with session_factory() as session:
        async with session.begin():
            locked = await session.scalar(
                select(IntegrationOutbox)
                .where(IntegrationOutbox.dedupe_key == f"feedback:{feedback_id}:github-issue")
                .with_for_update()
            )
            assert locked is not None
            locked.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)

    run_two = await process_pending_outbox_events(issue_client=client)
    assert run_two == 1
    assert client.calls == 2

    async with session_factory() as session:
        outbox_row = await session.scalar(
            select(IntegrationOutbox).where(IntegrationOutbox.dedupe_key == f"feedback:{feedback_id}:github-issue")
        )
        item_row = await session.scalar(select(FeedbackItem).where(FeedbackItem.id == feedback_id))

    assert outbox_row is not None
    assert outbox_row.status == IntegrationOutboxStatus.FAILED
    assert outbox_row.attempts == 2
    assert outbox_row.last_error is not None
    assert item_row is not None
    assert item_row.github_issue_url is None
