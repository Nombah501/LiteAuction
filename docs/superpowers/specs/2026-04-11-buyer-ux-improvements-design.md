# Buyer UX Improvements — Surgical Fixes

**Date:** 2026-04-11
**Status:** Approved
**Audience:** Buyers (bidders), with seller/moderator secondary benefit
**Approach:** 5 targeted fixes with maximum impact on buyer experience

---

## Overview

Analysis of the full codebase revealed 13 UX gaps. This spec addresses the 5 highest-impact improvements for the buyer persona, covering both onboarding (new users) and existing flow friction (registered users).

---

## 1. `/help` Command + First-Time Onboarding

### Problem
New users see a dashboard with "Create lot" as the primary action — irrelevant for buyers. No `/help` handler exists. No explanation of the auction lifecycle, guarantor system, or points.

### Solution

#### 1a. `/help` Command
- **New file:** `app/bot/handlers/help.py`
- **Trigger:** `/help` in private or group chat
- **Behavior:**
  - **Private chat:** Full help text with all commands and flow explanation
  - **Group chat:** Short version with link "Full guide in private messages"
- **Content:**
  ```
  How LiteAuction works

  Bidding — press +/- buttons under any lot in chat/channel
  Winning — if your bid is top when timer ends, you win
  Deal — bot creates a private topic to connect you with the seller
  Guarantor — safe deal through a moderator
  Feedback — rate the deal after completion

  Commands:
  /mybids — my bids and auctions
  /points — balance and bonuses
  /myrep — my reputation
  /guarant — request a guarantor
  /bug /suggest — report a problem
  ```
- **Register:** Add `BotCommand(command="help", description="How to use the bot")` to bot menu commands

#### 1b. Role-Oriented `/start`
- **Modify:** `app/bot/handlers/start.py` :: `handle_start_private`
- **Logic:** After sending the dashboard, check if user has `0 bids` and `0 auctions` (new user)
  - If new user: send a follow-up onboarding message:
    ```
    Welcome! You can:
    - Find lots in connected chats and channels
    - Place bids with buttons under each lot
    - Track your bids — /mybids

    Start by browsing active auctions in the chat!
    ```
  - If existing user: current dashboard behavior unchanged
- **Query:** Check `Bid` and `Auction` tables for user activity (single combined query for performance)

### Out of Scope
Tutorial mode, forced onboarding wizard, separate onboarding FSM states.

---

## 2. `/mybids` — Buyer Bid History

### Problem
After placing a bid, the buyer has no way to return to their auctions. Cannot see if outbid, won, or lost. No persistent view of bidding activity.

### Solution

#### New Handler
- **New file:** `app/bot/handlers/my_bids.py`
- **Trigger:** `/mybids` command or `dash:my_bids` callback
- **Register:** Add `BotCommand(command="mybids", description="My bids and auctions")` to bot menu

#### Data Query
- Query `Bid` table filtered by `user_id`, grouped by `auction_id` (only latest bid per auction)
- Join with `Auction` to get status, current price, end time
- Split into two groups:
  - **Active:** `Auction.status` is ACTIVE/FROZEN
  - **Completed:** `Auction.status` is FINISHED

#### Active Bids Display
```
Active bids (3)

Lot #a1b2c3 — iPhone 15 Pro
Your bid: $500 (TOP) | Time left: 2h 15m
[Open post] [View photos]

Lot #e4f5g6 — AirPods Pro
Your bid: $120 (OUTBID — $150)
[Open post] [View photos]
```

- Status indicator: `TOP` or `OUTBID` based on whether user's bid equals current highest
- Time remaining calculated from `Auction.ends_at`
- Buttons:
  - `Open post` — deep link to auction post (existing `post_url` from `AuctionPost`)
  - `View photos` — existing `gallery:<auction_id>` callback

#### Completed Bids Display
```
Completed (2)
Lot #x1y2z3 — MacBook Air — WON $1200
Lot #a4b5c6 — PS5 — Lost ($800 vs $850)
```

- Won/Lost based on whether user was the winning bidder
- For won auctions, additional buttons:
  - `Message seller` — `deal:write:<auction_id>` callback
  - `Leave feedback` — `deal:feedback:<auction_id>` callback

#### Pagination
- 5 items per page (matching `dash:my_auctions` pattern)
- Callback prefix: `mybids:page:<N>` for pagination
- Same pagination keyboard builder pattern as existing auction list

#### Dashboard Integration
- Add `My bids` button to main dashboard keyboard (callback `dash:my_bids`)

### Out of Scope
Push reminders about outbid status (already handled by outbid notification), complex filters, sorting options.

---

## 3. Deal Topic Message Forwarding

### Problem
The "Message seller/winner" button creates forum topics for both parties, but message forwarding between them is not implemented. Users write messages into their topic — nothing happens.

### Solution

#### New Handler
- **New file:** `app/bot/handlers/deal_topic_chat.py`
- **Register:** In `app/bot/handlers/__init__.py` with high priority, filtered by `message_thread_id`

#### Forwarding Logic
1. Listen for all messages in private chat forum topics (messages with `message_thread_id`)
2. Look up `DealTopic` by `(chat_id, message_thread_id)`. If no record found — message is not in a deal topic, skip (pass to other handlers)
3. If found and status is `OPEN`:
   - Find counterparty `DealTopic` (same `auction_id`, different `user_id`)
   - Forward message to counterparty topic with prefix:
     ```
     @sender_username:
     <original message content>
     ```
4. If counterparty topic not found or status is `CLOSED`:
   - Reply: `Deal topic is closed. Create a new request.`

#### Supported Message Types
- Text (preserve formatting)
- Photo (with caption)
- Document
- Voice message

#### Excluded
- Service messages, bot messages, callback queries

#### Deal Topic Close Flow
- Add `Close deal` button to deal topic keyboard
- Two-step confirmation (arm/confirm pattern with TTL, matching buyout flow)
- Both parties receive: `Deal closed. Don't forget to leave feedback — /tradefeedback`
- Status transitions to `DealTopicStatus.CLOSED`

### Out of Scope
Read receipts, typing indicators, message editing/deletion forwarding, file size limits beyond Telegram defaults.

---

## 4. Gallery via Deep-Link

### Problem
Buyer presses "Photos" on an auction but hasn't done `/start` in private chat. Gets a soft-gate error message instead of photos. Blocks lot inspection entirely.

### Solution

#### Modified Handler
- **Modify:** `app/bot/handlers/bid_actions.py` — gallery callback handler

#### Flow for Non-Private-Started Users
1. User presses gallery button (`gallery:<auction_id>`)
2. Check `user.private_started`
3. If `False`:
   - Send callback alert: `Press to view all photos`
   - Send inline button in current chat:
     `[View all photos]` → URL `t.me/{bot_username}?start=gallery_<auction_id>`
   - User clicks → transitions to private chat with bot
4. Bot receives `/start gallery_<auction_id>`:
   - Execute `upsert_user` + `mark_private_started=True`
   - Immediately send all auction photos as media group
   - Then send standard dashboard
5. If `True`: current behavior unchanged (photos sent to private chat)

#### Deep-Link Registration
- **Modify:** `app/bot/handlers/start.py` :: `handle_start_private`
- Add `gallery_<auction_uuid>` payload alongside existing `appeal_<ref>` and `report_<uuid>`
- Parse UUID, fetch `Auction` + `AuctionPhoto`, send photos before dashboard

### Out of Scope
Sending photos in group chats, auto-start without user consent, changing other soft-gate checks (bid/buyout gates remain).

---

## 5. User Reputation Display (`/myrep`)

### Problem
Reputation system exists in DB (`UserReputation` + `UserReputationEvent`, tiers: NEW/BRONZE/SILVER/GOLD/PLATINUM) but users cannot see their own reputation anywhere. `/reputation` is a moderator-only command for adjusting others' reputation.

### Solution

#### New Handler
- **New file:** `app/bot/handlers/my_reputation.py`
- **Trigger:** `/myrep` command or `dash:reputation` callback
- **Register:** Add `BotCommand(command="myrep", description="My reputation")` to bot menu

#### Display Format
```
Your reputation: GOLD
Points: 1 250

Completed deals: 18
Feedback received: 15 (avg: 4.7 stars)
Feedback given: 12

Recent activity:
+50 — Feedback for deal #x1y2z3 (2h ago)
+30 — Feedback for deal #a4b5c6 (1d ago)
+100 — Moderator adjustment (3d ago)

Next tier: PLATINUM (3 000 pts)
Remaining: 1 750
```

#### Data Sources
- `UserReputation` — current score and tier
- `UserReputationEvent` — last 5 events with descriptions
- `TradeFeedback` — count received, count given, average rating
- Auction count where user was winner — completed deals count

#### Tier Progress Bar
- Calculate threshold for next tier from tier definitions
- Show points remaining to next tier
- If at max tier (PLATINUM): show `Maximum tier reached`

#### Dashboard Integration
- Add `Reputation` button to main dashboard keyboard (callback `dash:reputation`)

#### Public Reputation Badge
- **Modify:** `render_auction_caption` in `app/bot/services/auction_service.py`
- Show seller tier badge next to seller username: `Seller: @name [GOLD]`
- Tier-to-badge mapping:
  - NEW: no badge
  - BRONZE: `🥉`
  - SILVER: `🥈`
  - GOLD: `🥇`
  - PLATINUM: `💎`

### Out of Scope
Leaderboard, detailed reputation analytics, viewing other users' reputation, reputation comparison.

---

## Implementation Order

| Priority | Item | Depends On | Est. Complexity |
|----------|------|------------|-----------------|
| 1 | Deal topic message forwarding (Sec 3) | None | Medium — new handler + close flow |
| 2 | `/help` + onboarding (Sec 1) | None | Small — new handler + minor start.py change |
| 3 | `/mybids` (Sec 2) | None | Medium — new handler + dashboard button |
| 4 | Gallery deep-link (Sec 4) | None | Small — modify existing handler + start.py |
| 5 | `/myrep` reputation display (Sec 5) | None | Small — new handler + caption change |

Items 1-4 are independent and can be parallelized. Item 5 is also independent.

---

## Files Changed Summary

| File | Action | Section |
|------|--------|---------|
| `app/bot/handlers/help.py` | New | 1 |
| `app/bot/handlers/start.py` | Modify | 1, 4 |
| `app/bot/handlers/my_bids.py` | New | 2 |
| `app/bot/handlers/deal_topic_chat.py` | New | 3 |
| `app/bot/handlers/post_auction.py` | Modify | 3 (add close button) |
| `app/bot/handlers/bid_actions.py` | Modify | 4 |
| `app/bot/handlers/my_reputation.py` | New | 5 |
| `app/bot/handlers/__init__.py` | Modify | 3, register new routers |
| `app/bot/keyboards/` | Modify | 2, 3, 5 (dashboard buttons) |
| `app/bot/services/auction_service.py` | Modify | 5 (caption badge) |

---

## Testing Strategy

Each section gets:
- Unit tests for handler logic (FSM, callback parsing, message formatting)
- Unit tests for new services (bid history query, reputation aggregation)
- Integration test for deal topic forwarding (two users, message exchange)
- Integration test for deep-link gallery flow
- Bot smoke test checklist for each new command
