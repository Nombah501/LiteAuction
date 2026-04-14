# Reputation Score + Progressive Trust — Design Spec (Sub-project 1 of 3)

**Date:** 2026-04-11
**Status:** Approved
**Scope:** Sub-project 1 — Reputation Score Foundation
**Parent:** Anti-fraud system improvement for LiteAuction Telegram bot

## Problem

Current anti-fraud system has 14 identified vulnerabilities. Three are critical: (H) fraud detection doesn't block bids, (C) no seller shill detection, (A) no cross-auction collusion tracking. The system uses binary heuristics with no user-level trust model — a brand new account has the same privileges as a verified seller with 50 completed deals.

## Decision: Reputation Score + Progressive Trust (Approach C)

Build a reputation system where every user has a score (0-100) and trust tier. Trust level determines what actions are available. This sub-project establishes the foundation — model, calculator, publish gate integration, and event log.

**Sub-project decomposition:**
1. **This spec:** Reputation Score Foundation — model, calculator, publish gate, UI
2. **Next spec:** Progressive Trust + Pre-bid Block — tier-based restrictions, bid blocking, auto-freeze
3. **Future spec:** Advanced Heuristics — seller shill, cross-auction collusion, reputation decay

---

## Section 1: Data Model

### New table: `user_reputations`

```sql
CREATE TABLE user_reputations (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    score           INTEGER NOT NULL DEFAULT 0 CHECK (score >= 0 AND score <= 100),
    tier            VARCHAR(16) NOT NULL DEFAULT 'NEW',
    last_event_at   TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_reputations_user_id UNIQUE (user_id)
);
```

### New table: `user_reputation_events`

```sql
CREATE TABLE user_reputation_events (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta           INTEGER NOT NULL,
    reason          VARCHAR(32) NOT NULL,
    source_id       BIGINT,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_reputation_events_user_created
    ON user_reputation_events (user_id, created_at DESC);
```

### Trust tiers

| Tier | Score range | Meaning |
|---|---|---|
| NEW | 0-20 | New/untrusted user |
| BRONZE | 21-40 | Limited access |
| SILVER | 41-60 | Standard user |
| GOLD | 61-80 | Reliable seller |
| PLATINUM | 81-100 | Verified participant |

Tier is derived from score — not stored independently. Updated on every score change.

---

## Section 2: Initial Score Calculation

Calculated once when `UserReputation` record is first created (lazy — on first interaction requiring reputation).

**Base score: 10** (new account < 24h) or **25** (account > 24h)

**Adjustments (applied once at creation):**

| Factor | Delta | Max | Source |
|---|---|---|---|
| Completed auctions as seller | +5 each | +30 | `auctions` where seller_user_id and status=CLOSED |
| Won auctions as buyer | +3 each | +30 | `bids` where user is winner |
| Complaints against user | -10 each | -30 | `complaints` count targeting user |
| Confirmed fraud signals | -20 each | -40 | `fraud_signals` where status=CONFIRMED |
| Verified user | +15 | +15 | `verified_users` table |
| Has assigned guarantor | +10 | +10 | `guarantor_requests` where status=ASSIGNED |

Final score clamped to [0, 100].

---

## Section 3: Reputation Events

### Positive events

| Event | Delta | Trigger |
|---|---|---|
| `auction_completed` | +3 | Auction closes with seller's status=ACTIVE, no fraud signals |
| `bid_won` | +2 | Auction finalizes with user as winner |
| `guarantor_assigned` | +10 | Moderator assigns guarantor request |
| `user_verified` | +15 | User passes verification |
| `mod_adjust` | +N | Manual moderator correction |

### Negative events

| Event | Delta | Trigger |
|---|---|---|
| `complaint_filed` | -8 | New complaint targeting user |
| `fraud_signal` | -15 | New fraud signal (OPEN) created |
| `fraud_confirmed` | -10 | Fraud signal status → CONFIRMED by moderator |
| `bid_removed` | -5 | Moderator removes user's bid |
| `temp_ban` | -20 | User receives temporary ban |
| `perm_ban` | score = 0 | User receives permanent ban (full reset) |
| `mod_adjust` | -N | Manual moderator correction |
| `decay` | -2 | 30+ days of inactivity (per 30-day period) |

### Protections

- Max negative delta per 24h: **-40** (except `perm_ban`)
- Max positive delta per 24h: **+15**
- Score clamped to **[0, 100]**
- Decay floor: **10** (previously active users don't decay below 10)

### Decay

- Triggered by auction_watcher once per day (batch process)
- Users with `last_event_at` older than 30 days: score -= 2 per 30-day gap
- Decay stops at score=10
- Each decay creates a `user_reputation_events` record with reason=`decay`

---

## Section 4: Publish Gate Integration

### New logic (replaces `evaluate_seller_publish_gate`)

```
PLATINUM / GOLD / SILVER  → publish allowed
BRONZE                    → publish allowed only with assigned guarantor
NEW                       → publish blocked (need guarantor + 1 completed deal)
```

**Blocking message:**
```
Публикация ограничена. Ваш уровень: {tier} (score: {score}).
{reason_specific_advice}
Повысить уровень: завершите сделки без жалоб.
```

Where `reason_specific_advice`:
- BRONZE without guarantor: "Для публикации нужен назначенный гарант → /guarant"
- NEW: "Для публикации нужен гарант и хотя бы 1 завершённая сделка (проданный или выигранный аукцион)."

### Implementation

Modify `publish_gate_service.py`:
1. Load `UserReputation` for seller
2. If reputation doesn't exist yet, call `recalculate_user_reputation()` to create it
3. Apply tier-based rules above
4. Keep existing guarantor check as BRONZE requirement

**Fallback:** If `UserReputation` record doesn't exist and recalculation fails, use existing `evaluate_user_risk_snapshot` as safety net.

---

## Section 5: API — `reputation_service.py`

### Functions

```python
async def get_or_create_reputation(session, user_id: int) -> UserReputation
    # Lazy creation with initial score calculation

async def recalculate_user_reputation(session, user_id: int) -> UserReputation
    # Full recalculation from historical data

async def adjust_reputation(session, user_id: int, delta: int, reason: str, source_id: int | None = None) -> UserReputation
    # Apply delta with 24h cap enforcement, update tier

async def get_user_tier(session, user_id: int) -> str
    # Returns tier string

async def get_reputation_history(session, user_id: int, limit: int = 20) -> list[UserReputationEvent]
    # Recent events for mod display

async def run_reputation_decay(session) -> int
    # Batch decay process, returns count of affected users
```

### Integration points (add calls in existing services)

| File | Location | Call |
|---|---|---|
| `complaint_service.py` | After complaint creation | `adjust_reputation(session, target_user_id, -8, "complaint_filed", complaint.id)` |
| `fraud_service.py` | After signal creation (score >= threshold) | `adjust_reputation(session, user_id, -15, "fraud_signal", signal.id)` |
| `moderation_service.py` | On signal CONFIRMED | `adjust_reputation(session, user_id, -10, "fraud_confirmed", signal.id)` |
| `moderation_service.py` | On bid removal | `adjust_reputation(session, user_id, -5, "bid_removed", bid.id)` |
| `moderation_service.py` | On temp ban | `adjust_reputation(session, user_id, -20, "temp_ban")` |
| `moderation_service.py` | On perm ban | `adjust_reputation(session, user_id, -score, "perm_ban")` |
| `auction_service.py` | On auction close (seller) | `adjust_reputation(session, seller_user_id, +3, "auction_completed", auction.id)` |
| `auction_service.py` | On auction close (winner) | `adjust_reputation(session, winner_user_id, +2, "bid_won", auction.id)` |
| `guarantor_service.py` | On guarantor assigned | `adjust_reputation(session, submitter_user_id, +10, "guarantor_assigned", request.id)` |
| `verification_service.py` | On user verified | `adjust_reputation(session, user_id, +15, "user_verified")` |

---

## Section 6: Moderation UI

### New command: `/reputation <user_id_or_username>`

Shows:
- Current tier and score
- Score history (last 10 events)
- Active restrictions
- Recommendations

### New mod action: `/modrepadjust <user_id> <delta> <reason>`

Manual reputation adjustment by moderator. Requires `trust:manage` scope.

### Modpanel addition

New "Репутация" section listing NEW and BRONZE tier users (paginated).

---

## Section 7: User Notifications

On tier change (upgrade or downgrade), send message to user's private topic:

**Upgrade:**
```
🎉 Ваш уровень повышен: {old_tier} → {new_tier} (score: {score})
```

**Downgrade:**
```
⚠️ Ваш уровень понижен: {old_tier} → {new_tier} (score: {score})
Причина: {reason_label}
```

Sent via existing `send_user_topic_message` with `purpose=NOTIFICATIONS`.

---

## Section 8: Scope — What This Sub-project Does NOT Include

- Bid blocking based on reputation (Sub-project 2)
- Auto-freeze auctions on high fraud score (Sub-project 2)
- Tier-based bid/lot limits (Sub-project 2)
- Seller shill detection heuristic (Sub-project 3)
- Cross-auction collusion detection (Sub-project 3)
- IP/device fingerprinting (out of scope for Telegram)
- Existing fraud heuristics remain unchanged

---

## Success Criteria

1. `user_reputations` and `user_reputation_events` tables created via Alembic migration
2. Existing users have reputation calculated (backfill script)
3. New events (complaint, fraud, ban, auction complete, etc.) automatically adjust reputation
4. Publish gate uses tier-based rules instead of old risk_eval
5. Moderators can view and adjust reputation
6. Users receive notifications on tier changes
7. Daily decay process runs via auction_watcher
