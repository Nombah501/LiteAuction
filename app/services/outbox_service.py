from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.enums import FeedbackStatus, IntegrationOutboxStatus, ModerationAction
from app.db.models import FeedbackItem, IntegrationOutbox, User
from app.db.session import SessionFactory
from app.services.github_automation_service import (
    FeedbackIssueClient,
    GitHubApiIssueClient,
    build_feedback_issue_draft,
)
from app.services.moderation_service import log_moderation_action

OUTBOX_EVENT_FEEDBACK_APPROVED = "feedback.approved"


def feedback_issue_dedupe_key(feedback_id: int) -> str:
    return f"feedback:{feedback_id}:github-issue"


async def enqueue_outbox_event(
    session: AsyncSession,
    *,
    event_type: str,
    payload: dict,
    dedupe_key: str,
) -> bool:
    now = datetime.now(UTC)
    stmt = (
        insert(IntegrationOutbox)
        .values(
            event_type=event_type,
            payload=payload,
            dedupe_key=dedupe_key,
            attempts=0,
            next_retry_at=now,
            status=IntegrationOutboxStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[IntegrationOutbox.dedupe_key])
        .returning(IntegrationOutbox.id)
    )
    inserted_id = await session.scalar(stmt)
    return inserted_id is not None


async def enqueue_feedback_issue_event(session: AsyncSession, *, feedback_id: int) -> bool:
    return await enqueue_outbox_event(
        session,
        event_type=OUTBOX_EVENT_FEEDBACK_APPROVED,
        payload={"feedback_id": feedback_id},
        dedupe_key=feedback_issue_dedupe_key(feedback_id),
    )


async def _resolve_feedback_issue_actor_user_id(session: AsyncSession) -> int:
    actor_tg_user_id = settings.feedback_github_actor_tg_user_id
    actor = await session.scalar(select(User).where(User.tg_user_id == actor_tg_user_id))
    if actor is not None:
        return actor.id

    try:
        async with session.begin_nested():
            actor = User(tg_user_id=actor_tg_user_id, username="system_feedback_github")
            session.add(actor)
            await session.flush()
            return actor.id
    except IntegrityError:
        existing_actor = await session.scalar(select(User).where(User.tg_user_id == actor_tg_user_id))
        if existing_actor is None:
            raise
        return existing_actor.id


def _backoff_seconds(attempt_number: int) -> int:
    base = max(settings.outbox_retry_base_seconds, 1)
    cap = max(settings.outbox_retry_max_seconds, base)
    power = max(attempt_number - 1, 0)
    return min(base * (2**power), cap)


def _mark_outbox_done(event: IntegrationOutbox, *, now: datetime) -> None:
    event.attempts += 1
    event.status = IntegrationOutboxStatus.DONE
    event.next_retry_at = now
    event.last_error = None
    event.updated_at = now


def _mark_outbox_retry_or_fail(event: IntegrationOutbox, *, now: datetime, error: Exception) -> None:
    max_attempts = max(settings.outbox_max_attempts, 1)
    event.attempts += 1
    event.last_error = str(error)[:1000]
    event.updated_at = now

    if event.attempts >= max_attempts:
        event.status = IntegrationOutboxStatus.FAILED
        event.next_retry_at = now
        return

    event.status = IntegrationOutboxStatus.PENDING
    event.next_retry_at = now + timedelta(seconds=_backoff_seconds(event.attempts))


async def _handle_feedback_approved_event(
    session: AsyncSession,
    *,
    event: IntegrationOutbox,
    issue_client: FeedbackIssueClient,
) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw_feedback_id = payload.get("feedback_id")
    if not isinstance(raw_feedback_id, int):
        raise RuntimeError("Outbox payload has no valid feedback_id")

    item = await session.scalar(
        select(FeedbackItem).where(FeedbackItem.id == raw_feedback_id).with_for_update()
    )
    if item is None:
        raise RuntimeError(f"Feedback item #{raw_feedback_id} not found")

    if FeedbackStatus(item.status) != FeedbackStatus.APPROVED:
        return

    if item.github_issue_url:
        return

    submitter = await session.scalar(select(User).where(User.id == item.submitter_user_id))
    moderator = None
    if item.moderator_user_id is not None:
        moderator = await session.scalar(select(User).where(User.id == item.moderator_user_id))

    draft = build_feedback_issue_draft(item=item, submitter=submitter, moderator=moderator)
    issue_ref = await issue_client.create_issue(
        title=draft.title,
        body=draft.body,
        labels=draft.labels,
    )

    now = datetime.now(UTC)
    item.github_issue_url = issue_ref.url
    item.updated_at = now

    actor_user_id = await _resolve_feedback_issue_actor_user_id(session)
    await log_moderation_action(
        session,
        actor_user_id=actor_user_id,
        action=ModerationAction.CREATE_FEEDBACK_GITHUB_ISSUE,
        reason="Создан GitHub issue по одобренному фидбеку",
        target_user_id=item.submitter_user_id,
        payload={
            "feedback_id": item.id,
            "feedback_type": str(item.type),
            "github_issue_number": issue_ref.number,
            "github_issue_url": issue_ref.url,
            "outbox_event_id": event.id,
            "outbox_dedupe_key": event.dedupe_key,
        },
    )


async def _handle_outbox_event(
    session: AsyncSession,
    *,
    event: IntegrationOutbox,
    issue_client: FeedbackIssueClient,
) -> None:
    if event.event_type == OUTBOX_EVENT_FEEDBACK_APPROVED:
        await _handle_feedback_approved_event(session, event=event, issue_client=issue_client)
        return
    raise RuntimeError(f"Unsupported outbox event type: {event.event_type}")


async def _process_next_outbox_event(*, issue_client: FeedbackIssueClient) -> bool:
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        async with session.begin():
            event = await session.scalar(
                select(IntegrationOutbox)
                .where(
                    IntegrationOutbox.status == IntegrationOutboxStatus.PENDING,
                    IntegrationOutbox.next_retry_at <= now,
                )
                .order_by(IntegrationOutbox.next_retry_at.asc(), IntegrationOutbox.id.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if event is None:
                return False

            try:
                await _handle_outbox_event(session, event=event, issue_client=issue_client)
                _mark_outbox_done(event, now=now)
            except Exception as exc:
                _mark_outbox_retry_or_fail(event, now=now, error=exc)
            return True


async def process_pending_outbox_events(*, issue_client: FeedbackIssueClient | None = None) -> int:
    if not settings.github_automation_enabled:
        return 0

    batch_size = max(settings.outbox_batch_size, 1)
    client = issue_client or GitHubApiIssueClient.from_settings()

    processed = 0
    for _ in range(batch_size):
        handled = await _process_next_outbox_event(issue_client=client)
        if not handled:
            break
        processed += 1

    return processed
