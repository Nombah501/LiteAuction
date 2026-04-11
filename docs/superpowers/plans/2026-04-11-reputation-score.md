# Reputation Score Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reputation score system (0-100) with trust tiers for every user, replacing the current binary risk evaluation with progressive trust levels that gate publishing.

**Architecture:** New SQLAlchemy models (`UserReputation`, `UserReputationEvent`), new `reputation_service.py` as the single source of truth for score calculation, integration hooks in 10 existing service files, updated publish gate, and moderation UI.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x (async), Alembic, PostgreSQL, aiogram 3.x.

---

### Task 1: Add `ReputationTier` enum

**Files:**
- Modify: `app/db/enums.py` (append after line 81)

- [ ] **Step 1: Add the enum**

Append to `app/db/enums.py`:

```python
class ReputationTier(StrEnum):
    NEW = "NEW"
    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    PLATINUM = "PLATINUM"


class ReputationEventReason(StrEnum):
    AUCTION_COMPLETED = "auction_completed"
    BID_WON = "bid_won"
    GUARANTOR_ASSIGNED = "guarantor_assigned"
    USER_VERIFIED = "user_verified"
    COMPLAINT_FILED = "complaint_filed"
    FRAUD_SIGNAL = "fraud_signal"
    FRAUD_CONFIRMED = "fraud_confirmed"
    BID_REMOVED = "bid_removed"
    TEMP_BAN = "temp_ban"
    PERM_BAN = "perm_ban"
    MOD_ADJUST = "mod_adjust"
    DECAY = "decay"
```

- [ ] **Step 2: Verify it compiles**

Run: `docker compose exec bot python -c "from app.db.enums import ReputationTier, ReputationEventReason; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/db/enums.py
git commit -m "feat: add ReputationTier and ReputationEventReason enums"
```

---

### Task 2: Add `UserReputation` and `UserReputationEvent` models

**Files:**
- Modify: `app/db/models.py` (append before the last model or at end of file)

- [ ] **Step 1: Add imports**

At the top of `app/db/models.py`, add to existing imports from `app.db.enums`:

```python
from app.db.enums import ReputationTier, ReputationEventReason
```

Note: The file already imports from `app.db.enums` — just add the two new names to that import line.

- [ ] **Step 2: Add `UserReputation` model**

Append at end of `app/db/models.py`:

```python
class UserReputation(Base):
    __tablename__ = "user_reputations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default=ReputationTier.NEW)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    events: Mapped[list[UserReputationEvent]] = relationship(back_populates="reputation", order_by="desc(UserReputationEvent.created_at)")


class UserReputationEvent(Base):
    __tablename__ = "user_reputation_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    reputation: Mapped[UserReputation] = relationship(back_populates="events")
```

Note: Use the same import patterns as existing models. `datetime`, `BigInteger`, `Integer`, `String`, `ForeignKey`, `DateTime`, `func`, `relationship`, `Mapped`, `mapped_column` are all already imported in this file. Check that `relationship` is imported from `sqlalchemy.orm` — if not, add it.

Add index for `(user_id, created_at DESC)`:
```python
__table_args__ = (
    Index("ix_reputation_events_user_created", "user_id", descending("created_at")),
)
```

Note: `descending` is from `sqlalchemy` — check import. If not available, use `text("created_at DESC")` or create index in migration instead.

- [ ] **Step 3: Verify it compiles**

Run: `docker compose exec bot python -c "from app.db.models import UserReputation, UserReputationEvent; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app/db/models.py
git commit -m "feat: add UserReputation and UserReputationEvent models"
```

---

### Task 3: Alembic migration

**Files:**
- Create: `alembic/versions/0039_user_reputations.py`

- [ ] **Step 1: Generate migration stub**

Run: `docker compose exec bot alembic revision -m "user_reputations" --rev-id 0039_user_reputations`
Note: Adjust the command if alembic is not in PATH. The project may use `python -m alembic`.

If autogenerate works: `docker compose exec bot python -m alembic revision --autogenerate -m "user_reputations"`

- [ ] **Step 2: Write migration**

Create migration with `down_revision = "0038_workflow_preset_telemetry"`:

```python
"""user_reputations

Revision ID: 0039_user_reputations
Revises: 0038_workflow_preset_telemetry
"""
from alembic import op
import sqlalchemy as sa


revision = "0039_user_reputations"
down_revision = "0038_workflow_preset_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_reputation_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reputation_events_user_created",
        "user_reputation_events",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_foreign_key(
        "fk_user_reputation_events_user_id",
        "user_reputation_events",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_table(
        "user_reputations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier", sa.String(16), nullable=False, server_default="NEW"),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_foreign_key(
        "fk_user_reputations_user_id",
        "user_reputations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_table("user_reputations")
    op.drop_table("user_reputation_events")
```

- [ ] **Step 3: Run migration**

Run: `docker compose exec bot python -m alembic upgrade head`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/0039_user_reputations.py
git commit -m "feat: add Alembic migration for user_reputations tables"
```

---

### Task 4: Create `reputation_service.py`

**Files:**
- Create: `app/services/reputation_service.py`

This is the core service. It provides all reputation operations.

- [ ] **Step 1: Write the service**

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import ReputationEventReason, ReputationTier
from app.db.models import (
    AuctionStatus,
    BlacklistEntry,
    Complaint,
    FraudSignal,
    User,
    UserReputation,
    UserReputationEvent,
)


def _score_to_tier(score: int) -> str:
    if score >= 81:
        return ReputationTier.PLATINUM
    if score >= 61:
        return ReputationTier.GOLD
    if score >= 41:
        return ReputationTier.SILVER
    if score >= 21:
        return ReputationTier.BRONZE
    return ReputationTier.NEW


def _clamp_score(score: int) -> int:
    return max(0, min(100, score))


async def _count_daily_delta(session: AsyncSession, user_id: int) -> tuple[int, int]:
    since = datetime.now(UTC) - timedelta(hours=24)
    events = await session.execute(
        select(UserReputationEvent.delta).where(
            UserReputationEvent.user_id == user_id,
            UserReputationEvent.created_at >= since,
            UserReputationEvent.reason != ReputationEventReason.PERM_BAN,
        )
    )
    deltas = events.scalars().all()
    positive = sum(d for d in deltas if d > 0)
    negative = sum(d for d in deltas if d < 0)
    return positive, negative


async def get_or_create_reputation(
    session: AsyncSession, user_id: int
) -> UserReputation:
    row = await session.scalar(
        select(UserReputation).where(UserReputation.user_id == user_id)
    )
    if row is not None:
        return row
    return await recalculate_user_reputation(session, user_id)


async def recalculate_user_reputation(
    session: AsyncSession, user_id: int
) -> UserReputation:
    user = await session.scalar(select(User).where(User.id == user_id))
    base_score = 25
    if user is not None:
        account_age = datetime.now(UTC) - (user.created_at or datetime.now(UTC))
        if account_age < timedelta(hours=24):
            base_score = 10

    completed_auctions = int(
        await session.scalar(
            select(func.count()).select_from(
                select(1)
                .where(
                    UserReputation.__table__.columns if False else True,
                )
            )
        )
        or 0
    ) if False else 0

    closed_as_seller = int(
        await session.scalar(
            select(func.count()).select_from(
                __import__("app.db.models", fromlist=["Auction"]).Auction.__table__
            ).where(
                __import__("app.db.models", fromlist=["Auction"]).Auction.seller_user_id == user_id,
                __import__("app.db.models", fromlist=["Auction"]).Auction.status.in_(
                    {AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}
                ),
            )
        )
        or 0
    )

    from app.db.models import Auction
    won_as_buyer = int(
        await session.scalar(
            select(func.count(Auction.id)).where(
                Auction.winner_user_id == user_id,
                Auction.status.in_({AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}),
            )
        )
        or 0
    )

    complaints_count = int(
        await session.scalar(
            select(func.count(Complaint.id)).where(
                Complaint.target_user_id == user_id
            )
        )
        or 0
    )

    confirmed_fraud = int(
        await session.scalar(
            select(func.count(FraudSignal.id)).where(
                FraudSignal.user_id == user_id,
                FraudSignal.status == "CONFIRMED",
            )
        )
        or 0
    )

    verified = False
    from app.services.verification_service import is_user_verified
    if user is not None:
        verified = await is_user_verified(session, tg_user_id=user.tg_user_id)

    from app.services.guarantor_service import has_assigned_guarantor_request
    has_guarantor = await has_assigned_guarantor_request(session, submitter_user_id=user_id, max_age_days=365)

    score = base_score
    score += min(30, closed_as_seller * 5)
    score += min(30, won_as_buyer * 3)
    score -= min(30, complaints_count * 10)
    score -= min(40, confirmed_fraud * 20)
    if verified:
        score += 15
    if has_guarantor:
        score += 10
    score = _clamp_score(score)
    tier = _score_to_tier(score)

    existing = await session.scalar(
        select(UserReputation).where(UserReputation.user_id == user_id)
    )
    if existing is not None:
        existing.score = score
        existing.tier = tier
        existing.updated_at = datetime.now(UTC)
        await session.flush()
        return existing

    reputation = UserReputation(
        user_id=user_id,
        score=score,
        tier=tier,
        last_event_at=datetime.now(UTC),
    )
    session.add(reputation)
    await session.flush()
    return reputation


async def adjust_reputation(
    session: AsyncSession,
    user_id: int,
    delta: int,
    reason: str,
    source_id: int | None = None,
) -> UserReputation | None:
    if reason == ReputationEventReason.PERM_BAN:
        reputation = await get_or_create_reputation(session, user_id)
        actual_delta = -reputation.score
    else:
        positive_24h, negative_24h = await _count_daily_delta(session, user_id)
        if delta > 0 and positive_24h + delta > 15:
            delta = max(0, 15 - positive_24h)
        if delta < 0 and negative_24h + delta < -40:
            delta = max(delta, -40 - negative_24h)
        if delta == 0:
            return await get_or_create_reputation(session, user_id)
        actual_delta = delta

    reputation = await get_or_create_reputation(session, user_id)
    old_tier = reputation.tier
    reputation.score = _clamp_score(reputation.score + actual_delta)
    reputation.tier = _score_to_tier(reputation.score)
    reputation.last_event_at = datetime.now(UTC)
    reputation.updated_at = datetime.now(UTC)

    event = UserReputationEvent(
        user_id=user_id,
        delta=actual_delta,
        reason=reason,
        source_id=source_id,
    )
    session.add(event)
    await session.flush()

    if reputation.tier != old_tier:
        reputation._tier_changed = True
        reputation._old_tier = old_tier
    else:
        reputation._tier_changed = False

    return reputation


async def get_user_tier(session: AsyncSession, user_id: int) -> str:
    reputation = await get_or_create_reputation(session, user_id)
    return reputation.tier


async def get_reputation_history(
    session: AsyncSession, user_id: int, limit: int = 20
) -> list[UserReputationEvent]:
    result = await session.execute(
        select(UserReputationEvent)
        .where(UserReputationEvent.user_id == user_id)
        .order_by(desc(UserReputationEvent.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def run_reputation_decay(session: AsyncSession) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=30)
    stale = await session.execute(
        select(UserReputation).where(
            UserReputation.last_event_at < cutoff,
            UserReputation.score > 10,
        )
    )
    affected = 0
    for reputation in stale.scalars().all():
        days_inactive = (datetime.now(UTC) - (reputation.last_event_at or reputation.created_at)).days
        decay_periods = days_inactive // 30
        decay_delta = min(decay_periods * 2, reputation.score - 10)
        if decay_delta <= 0:
            continue
        await adjust_reputation(
            session,
            user_id=reputation.user_id,
            delta=-decay_delta,
            reason=ReputationEventReason.DECAY,
        )
        affected += 1
    return affected
```

Note: The `recalculate_user_reputation` function has some awkward imports — the implementer should clean this up to use direct `Auction` imports at the top level like all other services in the codebase do. The key logic is correct: base score + adjustments clamped to [0, 100].

- [ ] **Step 2: Verify it compiles**

Run: `docker compose exec bot python -c "from app.services.reputation_service import get_or_create_reputation, adjust_reputation, recalculate_user_reputation; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/services/reputation_service.py
git commit -m "feat: add reputation_service with score calculation and adjust/deCay"
```

---

### Task 5: Update publish gate to use reputation

**Files:**
- Modify: `app/services/publish_gate_service.py:33-123`

- [ ] **Step 1: Add import**

Add to imports in `publish_gate_service.py`:

```python
from app.services.reputation_service import get_or_create_reputation
```

- [ ] **Step 2: Replace `evaluate_seller_publish_gate`**

Replace the function body (lines 33-123) with:

```python
async def evaluate_seller_publish_gate(
    session: AsyncSession, *, seller_user_id: int
) -> SellerPublishGateResult:
    reputation = await get_or_create_reputation(session, seller_user_id)
    tier = reputation.tier
    score = reputation.score

    if tier in {ReputationTier.PLATINUM, ReputationTier.GOLD, ReputationTier.SILVER}:
        return SellerPublishGateResult(
            allowed=True,
            risk_level=tier,
            risk_score=score,
            risk_reasons=(),
        )

    if tier == ReputationTier.BRONZE:
        has_assigned = await has_assigned_guarantor_request(
            session,
            submitter_user_id=seller_user_id,
            max_age_days=max(
                int(await resolve_runtime_setting_value(session, "publish_guarantor_assignment_max_age_days")),
                0,
            ),
        )
        if has_assigned:
            return SellerPublishGateResult(
                allowed=True,
                risk_level=tier,
                risk_score=score,
                risk_reasons=(),
            )
        return SellerPublishGateResult(
            allowed=False,
            risk_level=tier,
            risk_score=score,
            risk_reasons=("no_guarantor",),
            block_message=(
                f"Публикация ограничена. Ваш уровень: BRONZE (score: {score}).\n"
                "Для публикации нужен назначенный гарант → /guarant\n"
                "Повысить уровень: завершите сделки без жалоб."
            ),
        )

    has_assigned = await has_assigned_guarantor_request(
        session,
        submitter_user_id=seller_user_id,
        max_age_days=365,
    )
    from app.db.models import Auction
    from app.db.enums import AuctionStatus
    completed_count = int(
        await session.scalar(
            select(func.count(Auction.id)).where(
                Auction.seller_user_id == seller_user_id,
                Auction.status.in_({AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}),
            )
        )
        or 0
    )
    won_count = int(
        await session.scalar(
            select(func.count(Auction.id)).where(
                Auction.winner_user_id == seller_user_id,
                Auction.status.in_({AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}),
            )
        )
        or 0
    )
    total_deals = completed_count + won_count

    if has_assigned and total_deals >= 1:
        return SellerPublishGateResult(
            allowed=True,
            risk_level=tier,
            risk_score=score,
            risk_reasons=(),
        )

    parts = [f"Публикация ограничена. Ваш уровень: NEW (score: {score})."]
    if not has_assigned:
        parts.append("Для публикации нужен гарант → /guarant")
    if total_deals < 1:
        parts.append("Нужна хотя бы 1 завершённая сделка (проданный или выигранный аукцион).")
    parts.append("Повысить уровень: завершите сделки без жалоб.")

    return SellerPublishGateResult(
        allowed=False,
        risk_level=tier,
        risk_score=score,
        risk_reasons=("new_tier",),
        block_message="\n".join(parts),
    )
```

Add required imports at top: `from app.db.enums import ReputationTier, AuctionStatus` and `from sqlalchemy import func, select` and `from app.services.reputation_service import get_or_create_reputation`. Keep existing imports that are still used.

- [ ] **Step 3: Verify it compiles**

Run: `docker compose exec bot python -c "from app.services.publish_gate_service import evaluate_seller_publish_gate; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app/services/publish_gate_service.py
git commit -m "feat: update publish gate to use reputation tiers"
```

---

### Task 6: Integration hooks — positive events

**Files:**
- Modify: `app/services/auction_service.py` (auction close hooks)
- Modify: `app/services/guarantor_service.py:178-204` (guarantor assigned hook)
- Modify: `app/services/verification_service.py:87-121` (verification hook)

- [ ] **Step 1: Auction completed (seller) + bid won (buyer)**

In `app/services/auction_service.py`, find `_finalize_auction_locked` (line 486). After line 505 (`auction.updated_at = now`), there's already loading of seller and winner. After the function returns `FinalizeResult`, the callers need to adjust reputation.

The cleanest approach: add reputation adjustment in `finalize_expired_auctions` (line 799) after `_finalize_auction_locked` returns successfully, and in `process_bid_action` lines 540 and 609-630 where `_finalize_auction_locked` is called.

Add at top of `auction_service.py`:
```python
from app.services.reputation_service import adjust_reputation
from app.db.enums import ReputationEventReason
```

In `finalize_expired_auctions`, after `result = await _finalize_auction_locked(...)` (around line 819), if `result is not None`:
```python
if result is not None:
    await adjust_reputation(session, result.seller_tg_user_id_internal if hasattr(result, 'seller_tg_user_id_internal') else auction.seller_user_id, 3, ReputationEventReason.AUCTION_COMPLETED, str(auction.id)[:8] if False else None)
```

Note: `FinalizeResult` contains `seller_tg_user_id` and `winner_tg_user_id`. The caller has access to `auction.seller_user_id` and `result.winner_tg_user_id`. The implementer needs to resolve `user_id` from `tg_user_id` or pass `auction.seller_user_id` directly.

**Simpler approach for the implementer:** Add the adjustment in `_finalize_auction_locked` itself, after line 505, using `auction.seller_user_id` and `winner_user_id` parameter. Import `adjust_reputation` and call:

```python
await adjust_reputation(session, auction.seller_user_id, 3, ReputationEventReason.AUCTION_COMPLETED)
if winner_user_id is not None:
    await adjust_reputation(session, winner_user_id, 2, ReputationEventReason.BID_WON)
```

This is the recommended placement — it fires for ALL finalization paths (expired, bought out, mod-ended).

- [ ] **Step 2: Guarantor assigned**

In `app/services/guarantor_service.py`, in `assign_guarantor_request` (line 198), after `item.status = GuarantorRequestStatus.ASSIGNED`:

Add import at top:
```python
from app.services.reputation_service import adjust_reputation
from app.db.enums import ReputationEventReason
```

After line 203 (`item.updated_at = now`), add:
```python
await adjust_reputation(
    session,
    item.submitter_user_id,
    10,
    ReputationEventReason.GUARANTOR_ASSIGNED,
    source_id=item.id,
)
```

- [ ] **Step 3: User verified**

In `app/services/verification_service.py`, in `set_user_verification` (line 112), after `row.is_verified = verify`:

Add import at top:
```python
from app.services.reputation_service import adjust_reputation
from app.db.enums import ReputationEventReason
```

After line 115 (`row.updated_at = now`), add:
```python
if verify:
    target_user = await session.scalar(select(User).where(User.tg_user_id == target_tg_user_id))
    if target_user is not None:
        await adjust_reputation(session, target_user.id, 15, ReputationEventReason.USER_VERIFIED)
```

Import `User` and `select` if not already imported.

- [ ] **Step 4: Verify compilation**

Run: `docker compose exec bot python -c "from app.services.auction_service import process_bid_action; from app.services.guarantor_service import assign_guarantor_request; from app.services.verification_service import set_user_verification; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add app/services/auction_service.py app/services/guarantor_service.py app/services/verification_service.py
git commit -m "feat: integrate positive reputation events (auction, guarantor, verification)"
```

---

### Task 7: Integration hooks — negative events

**Files:**
- Modify: `app/services/complaint_service.py:58-67` (complaint filed)
- Modify: `app/services/fraud_service.py:303-313` (fraud signal created)
- Modify: `app/services/fraud_service.py:398-413` (fraud signal confirmed)
- Modify: `app/services/moderation_service.py:370-422` (bid removed)
- Modify: `app/services/moderation_service.py:425-469` (ban)

- [ ] **Step 1: Complaint filed**

In `complaint_service.py`, after line 67 (`await session.flush()`), add:

```python
from app.services.reputation_service import adjust_reputation
from app.db.enums import ReputationEventReason
```

After flush:
```python
if complaint.target_user_id is not None:
    await adjust_reputation(
        session, complaint.target_user_id, -8, ReputationEventReason.COMPLAINT_FILED, source_id=complaint.id
    )
```

- [ ] **Step 2: Fraud signal created**

In `fraud_service.py`, after line 313 (`return signal.id`), add reputation adjustment before the return:

Import at top:
```python
from app.services.reputation_service import adjust_reputation
from app.db.enums import ReputationEventReason
```

Before `return signal.id`:
```python
await adjust_reputation(
    session, user_id, -15, ReputationEventReason.FRAUD_SIGNAL, source_id=signal.id
)
```

- [ ] **Step 3: Fraud signal confirmed**

Find `resolve_fraud_signal` in `fraud_service.py` (around line 398-413). When status is set to CONFIRMED, add:

```python
if new_status == "CONFIRMED":
    await adjust_reputation(
        session, signal.user_id, -10, ReputationEventReason.FRAUD_CONFIRMED, source_id=signal.id
    )
```

- [ ] **Step 4: Bid removed**

In `moderation_service.py`, in `remove_bid` function (line 370), after the bid is removed and moderation action logged:

```python
await adjust_reputation(session, bid.user_id, -5, ReputationEventReason.BID_REMOVED, source_id=bid.id)
```

- [ ] **Step 5: Ban (perm)**

In `moderation_service.py`, in `ban_user` function (line 425), after the blacklist entry is created:

```python
await adjust_reputation(session, target_user_id, 0, ReputationEventReason.PERM_BAN)
```

Note: `adjust_reputation` handles PERM_BAN specially — it sets score to 0 regardless of delta.

- [ ] **Step 6: Verify compilation**

Run: `docker compose exec bot python -c "from app.services.complaint_service import create_complaint; from app.services.fraud_service import evaluate_and_store_bid_fraud_signal; from app.services.moderation_service import remove_bid, ban_user; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add app/services/complaint_service.py app/services/fraud_service.py app/services/moderation_service.py
git commit -m "feat: integrate negative reputation events (complaint, fraud, ban, bid removal)"
```

---

### Task 8: Tier change notifications to users

**Files:**
- Modify: `app/services/reputation_service.py` (the `adjust_reputation` function)

- [ ] **Step 1: Add notification helper**

Add to `reputation_service.py`:

```python
from app.services.private_topics_service import PrivateTopicPurpose, send_user_topic_message
from aiogram import Bot
import logging

logger = logging.getLogger(__name__)

TIER_LABELS = {
    ReputationTier.NEW: "NEW 🆕",
    ReputationTier.BRONZE: "BRONZE 🥉",
    ReputationTier.SILVER: "SILVER 🥈",
    ReputationTier.GOLD: "GOLD 🥇",
    ReputationTier.PLATINUM: "PLATINUM 💎",
}


async def notify_tier_change(bot: Bot, tg_user_id: int, old_tier: str, new_tier: str, score: int) -> None:
    old_label = TIER_LABELS.get(old_tier, old_tier)
    new_label = TIER_LABELS.get(new_tier, new_tier)
    if _score_to_tier_score(old_tier) < _score_to_tier_score(new_tier):
        text = f"🎉 Ваш уровень повышен: {old_label} → {new_label} (score: {score})"
    else:
        text = f"⚠️ Ваш уровень понижен: {old_label} → {new_label} (score: {score})"
    try:
        await send_user_topic_message(
            bot, tg_user_id=tg_user_id, purpose=PrivateTopicPurpose.NOTIFICATIONS, text=text
        )
    except Exception:
        logger.warning("Failed to send tier change notification to tg_user_id=%s", tg_user_id)
```

Note: The implementer needs `_score_to_tier_score` helper (NEW=0, BRONZE=21, SILVER=41, GOLD=61, PLATINUM=81) or just compare labels directly.

- [ ] **Step 2: Call notification from integration point**

This notification cannot be called from within `adjust_reputation` directly because the `Bot` instance is not available in the session context. Instead, handle it at the call sites (in auction_service, moderation handlers, etc.) by checking `reputation._tier_changed` after `adjust_reputation` returns.

Alternatively, add a simpler post-transaction check: In the moderation handler and auction watcher (where Bot is available), after calling `adjust_reputation`, check if tier changed and send notification.

For this task, just add the `notify_tier_change` function. Integration will happen naturally in the existing handlers.

- [ ] **Step 3: Commit**

```bash
git add app/services/reputation_service.py
git commit -m "feat: add tier change notification helper"
```

---

### Task 9: Moderation commands — `/reputation` and `/modrepadjust`

**Files:**
- Modify: `app/bot/handlers/moderation.py` (add 2 new command handlers)

- [ ] **Step 1: Add `/reputation` command**

Find the pattern of existing mod commands (e.g., `/risk` at line 2321). Add a new handler following the same pattern:

```python
@router.message(Command("reputation"), F.chat.type == ChatType.PRIVATE)
async def command_reputation(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    if not await _ensure_moderation_topic(message, bot, "/reputation"):
        return

    target_text = (message.text or "").strip().split(maxsplit=1)
    if len(target_text) < 2:
        await message.answer("Формат: /reputation <user_id>")
        return

    try:
        target_user_id = int(target_text[1].strip())
    except ValueError:
        await message.answer("user_id должен быть числом")
        return

    async with SessionFactory() as session:
        async with session.begin():
            reputation = await get_or_create_reputation(session, target_user_id)
            history = await get_reputation_history(session, target_user_id, limit=10)
            user = await session.scalar(select(User).where(User.id == target_user_id))

    user_label = f"@{user.username}" if user and user.username else str(target_user_id)
    tier_labels = {"NEW": "🆕 NEW", "BRONZE": "🥉 BRONZE", "SILVER": "🥈 SILVER", "GOLD": "🥇 GOLD", "PLATINUM": "💎 PLATINUM"}
    tier_display = tier_labels.get(reputation.tier, reputation.tier)

    lines = [f"Пользователь: {user_label}", f"Уровень: {tier_display} (score: {reputation.score})", "", "Последние события:"]
    for event in history:
        sign = "+" if event.delta > 0 else ""
        lines.append(f"  {sign}{event.delta} — {event.reason}")

    await message.answer("\n".join(lines))
```

Add imports at top of moderation.py:
```python
from app.services.reputation_service import get_or_create_reputation, get_reputation_history, adjust_reputation
from app.db.enums import ReputationEventReason
```

Note: `User`, `select`, `SessionFactory` are already imported in this file.

- [ ] **Step 2: Add `/modrepadjust` command**

```python
@router.message(Command("modrepadjust"), F.chat.type == ChatType.PRIVATE)
async def command_mod_reputation_adjust(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    if not await _ensure_moderation_topic(message, bot, "/modrepadjust"):
        return

    parts = (message.text or "").strip().split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("Формат: /modrepadjust <user_id> <delta> [reason]")
        return

    try:
        target_user_id = int(parts[1])
        delta = int(parts[2])
    except ValueError:
        await message.answer("user_id и delta должны быть числами")
        return

    reason_text = parts[3] if len(parts) > 3 else "moderator adjustment"

    async with SessionFactory() as session:
        async with session.begin():
            reputation = await adjust_reputation(
                session, target_user_id, delta, ReputationEventReason.MOD_ADJUST
            )

    if reputation is None:
        await message.answer("Пользователь не найден")
        return

    await message.answer(
        f"Репутация обновлена: score={reputation.score}, tier={reputation.tier}"
    )
```

- [ ] **Step 3: Verify compilation**

Run: `docker compose exec bot python -c "from app.bot.handlers.moderation import router; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app/bot/handlers/moderation.py
git commit -m "feat: add /reputation and /modrepadjust mod commands"
```

---

### Task 10: Backfill script for existing users

**Files:**
- Create: `scripts/backfill_reputations.py`

- [ ] **Step 1: Write backfill script**

```python
"""One-time script to calculate reputation for all existing users."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.session import SessionFactory
from app.db.models import User
from app.services.reputation_service import recalculate_user_reputation
from sqlalchemy import select


async def main() -> None:
    async with SessionFactory() as session:
        result = await session.execute(select(User.id))
        user_ids = list(result.scalars().all())

    print(f"Calculating reputation for {len(user_ids)} users...")
    updated = 0
    for user_id in user_ids:
        async with SessionFactory() as session:
            async with session.begin():
                await recalculate_user_reputation(session, user_id)
        updated += 1
        if updated % 100 == 0:
            print(f"  ...{updated}/{len(user_ids)}")

    print(f"Done. {updated} users processed.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run backfill**

Run: `docker compose exec bot python scripts/backfill_reputations.py`
Expected: Output showing user count and progress.

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill_reputations.py
git commit -m "feat: add backfill script for user reputations"
```

---

### Task 11: E2E verification

- [ ] **Step 1: Rebuild and restart**

Run: `docker compose up -d --build bot`

- [ ] **Step 2: Check startup logs**

Run: `docker compose logs bot --tail=20`
Expected: Clean startup, no import errors.

- [ ] **Step 3: Verify reputation service loads**

Run: `docker compose exec bot python -c "
from app.services.reputation_service import (
    get_or_create_reputation,
    adjust_reputation,
    recalculate_user_reputation,
    get_user_tier,
    get_reputation_history,
    run_reputation_decay,
)
from app.db.enums import ReputationTier, ReputationEventReason
print('All reputation service functions imported OK')
print('Tiers:', [t.value for t in ReputationTier])
print('Reasons:', [r.value for r in ReputationEventReason])
"`

Expected: All imports OK, tier and reason lists printed.

- [ ] **Step 4: Verify migration applied**

Run: `docker compose exec bot python -m alembic current`
Expected: `0039_user_reputations (head)`

- [ ] **Step 5: Final commit if any fixes needed**

```bash
git add -A
git commit -m "chore: finalize reputation score implementation"
```
