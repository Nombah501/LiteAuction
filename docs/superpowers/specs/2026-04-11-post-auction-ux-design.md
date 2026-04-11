# Post-Auction UX Redesign

**Date:** 2026-04-11
**Status:** Approved
**Scope:** Improve notifications, communication, and post-auction flow after deal completion.

## Problem

When an auction completes, users receive minimal messages with almost no actionable information:

- Seller gets: `"Лот #{id} завершен."`
- Winner gets: `"Вы выиграли лот #{id}."`
- Only button: "Открыть пост лота" (links back to ended auction post)

No way for seller and winner to contact each other. No guarantor reminder. No feedback prompt. No deal details (price, counterparty).

## Solution Overview

Redesign all post-auction notifications using "Approach A: Everything in one message" — show full deal info, next-step instructions, guarantor memo, and action buttons in a single rich notification. Introduce a new **Deal Topic** system for mediated communication between seller and winner through the bot.

## Scenarios

### Scenario 1: Seller — Auction Ended with Winner

**Recipient:** Seller (via private topic in bot)
**Trigger:** Timer expiration, buyout, or moderator `/end`

**Message structure:**

```
🏆 Аукцион завершён!

Лот: #a1b2c3d4
Описание: <truncated to ~80 chars>
Финальная цена: 15 000 ₽
Победитель: @winner_username

📋 Следующие шаги:
1. Нажмите «Написать победителю» — откроется топик сделки
2. Договоритесь о способе передачи товара и оплаты
3. После завершения — оставьте отзыв

🛡 Гарант — ваша безопасность
Если сумма сделки значительная или вы впервые работаете с покупателем —
запросите гаранта. Гарант выступит посредником и обеспечит защиту обеих сторон.
Стоимость: бесплатно для участников с репутацией 50+

[💬 Написать победителю]
[🛡 Запросить гаранта] [⭐ Оставить отзыв]
[📄 Открыть пост лота]
```

**Keyboard:**
- Row 1: `💬 Написать победителю` — creates deal topic, opens it for seller
- Row 2: `🛡 Запросить гаранта` — starts guarantor FSM with pre-filled auction_id
- Row 2: `⭐ Оставить отзыв` — starts feedback FSM
- Row 3: `📄 Открыть пост лота` — URL button (existing)

### Scenario 2: Winner — You Won

**Recipient:** Winner (via private topic in bot)
**Trigger:** Same as Scenario 1

Identical structure to Scenario 1, with differences:
- Title: `🎉 Вы выиграли аукцион!`
- Price label: `Ваша цена:` instead of `Финальная цена:`
- Counterparty: `Продавец: @seller_username` instead of `Победитель`
- Button: `💬 Написать продавцу`

### Scenario 3: Seller — No Bids

**Recipient:** Seller
**Trigger:** Auction expires with 0 bids

```
⏰ Аукцион завершён без ставок

Лот: #a1b2c3d4
Описание: <truncated>
Стартовая цена: 10 000 ₽
Ставок: 0

💡 Совет: Попробуйте изменить стартовую цену или описание
и опубликуйте лот заново.

[🔄 Опубликовать заново] [📄 Открыть пост лота]
```

**Keyboard:**
- `🔄 Опубликовать заново` — re-creates auction with same data, resets status to DRAFT
- `📄 Открыть пост лота` — existing URL button

### Scenario 4: Deal Topic

**What:** A new forum topic created in each participant's private chat with the bot, enabling mediated communication.

**Creation trigger:** Either participant clicks `💬 Написать ...` button from Scenario 1 or 2.

**Mechanics:**
1. Bot creates a forum topic in the seller's private chat: `🤝 Сделка #a1b2c3d4`
2. Bot creates a forum topic in the winner's private chat: `🤝 Сделка #a1b2c3d4`
3. Both topics get an intro message with deal details and a safety reminder
4. When seller writes in their deal topic → message is forwarded to winner's deal topic (prefixed with sender identity)
5. When winner writes → forwarded to seller's deal topic

**Intro message in each topic:**
```
🤝 Топик сделки создан

Лот: #a1b2c3d4 — iPhone 15 Pro Max 256GB
Цена: 15 000 ₽
Участники: @seller (продавец) и @winner (победитель)

Все сообщения здесь будут пересылаться вашему контрагенту.
Будьте вежливы и обсуждайте только сделку.

🛡 Совет по безопасности
Для защиты обеих сторон вы можете запросить гаранта — нажав кнопку ниже.
```

**Buttons on intro message:**
- `🛡 Запросить гаранта`
- `⭐ Оставить отзыв`
- `⚠ Жалоба`

**Message forwarding format:**
- From seller → winner: `@seller_username (продавец):\n<text>`
- From winner → seller: `@winner_username (победитель):\n<text>`
- Photos/files forwarded as-is with caption prefix

### Scenario 5: Moderation Channel

**Recipient:** Moderation topic section

Enhanced version of current moderation notification:

```
🏁 Лот #a1b2c3d4 завершён по таймеру

Описание: iPhone 15 Pro Max 256GB
Финальная цена: 15 000 ₽
Ставок: 7

Продавец: @seller_username (tg: 123456789)
Победитель: @winner_username (tg: 987654321)
Репутация продавца: 42
Репутация победителя: 15

🤝 Топик сделки: создан автоматически
🛡 Гарант: не запрошен

[📄 Открыть пост лота] [⚠ Заморозить]
```

## Data Model Changes

### New model: `DealTopic`

```python
class DealTopic(Base):
    __tablename__ = "deal_topics"

    id: Mapped[uuid.UUID]          # PK
    auction_id: Mapped[uuid.UUID]  # FK -> auctions.id
    seller_topic_id: Mapped[int]   # forum topic ID in seller's private chat
    winner_topic_id: Mapped[int | None]  # forum topic ID in winner's chat (None until winner opens)
    status: Mapped[DealTopicStatus]  # ACTIVE, CLOSED
    created_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
```

### New enum: `DealTopicStatus`

```python
class DealTopicStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
```

### Modified model: `GuarantorRequest`

Add optional `auction_id` field:

```python
auction_id: Mapped[uuid.UUID | None]  # FK -> auctions.id, nullable
```

Alembic migration required. Backward-safe: existing rows get NULL.

## Notification Copy Changes

All templates in `app/services/notification_copy_service.py` are replaced with rich versions. The copy service gains new parameters:

- `auction_id: str` — full short ID
- `description: str` — truncated to ~80 chars
- `final_price: int` — winning bid amount
- `counterparty_mention: str` — formatted mention of the other party
- `counterparty_role: str` — "продавец" or "победитель"
- `auction_title: str | None` — for deal topic header

New functions:
- `seller_completion_message(...)` — Scenario 1
- `winner_completion_message(...)` — Scenario 2
- `seller_no_bids_message(...)` — Scenario 3
- `deal_topic_intro(...)` — Scenario 4 intro
- `moderation_completion_message(...)` — Scenario 5

## Keyboard Changes

New keyboards in `app/bot/keyboards/auction.py`:

- `deal_completion_keyboard(auction_id: str, post_url: str | None, is_seller: bool)` — Scenarios 1, 2
- `no_bids_keyboard(auction_id: str, post_url: str | None)` — Scenario 3
- `deal_topic_keyboard(auction_id: str)` — Scenario 4 buttons on intro message
- `moderation_completion_keyboard(auction_id: str, post_url: str | None)` — Scenario 5

Callback data prefixes:
- `deal:write:<auction_id>` — open/create deal topic
- `deal:guarant:<auction_id>` — start guarantor request
- `deal:feedback:<auction_id>` — start feedback FSM
- `deal:republish:<auction_id>` — republish auction without bids

## Handler Changes

### New handler: `deal_topic.py`

- `handle_deal_write` — callback `deal:write:*`, creates DealTopic + forum topics, sends intro, opens topic for user
- `handle_deal_message` — listens for messages in deal topics, forwards to counterparty
- `handle_deal_guarant` — callback `deal:guarant:*`, starts guarantor FSM with pre-filled auction_id
- `handle_deal_feedback` — callback `deal:feedback:*`, starts feedback FSM
- `handle_deal_republish` — callback `deal:republish:*`, clones auction to DRAFT

### Modified handlers

- `bid_actions.py:_notify_auction_finish` — use new rich templates + keyboards
- `auction_service.py:finalize_expired_auctions` — pass AuctionView data to notifications
- `auction_service.py:_finalize_auction_locked` — return rich FinalizeResult with price + description
- `moderation.py:mod_end` — use `_finalize_auction_locked` (fixes missing reputation award)

## FSM Flows

### Feedback FSM (existing mechanism, triggered by inline button)

1. User clicks `⭐ Оставить отзыв`
2. Bot asks: "Оцените сделку" with inline buttons: ⭐⭐⭐⭐⭐ (1-5)
3. User selects rating
4. Bot asks: "Комментарий (необязательно)" — text input or skip button
5. Bot saves via `trade_feedback_service.submit_trade_feedback`
6. Confirmation message

### Guarantor FSM (modified to accept auction_id)

1. User clicks `🛡 Запросить гаранта`
2. If triggered from deal completion — auction_id is pre-filled, bot asks "Опишите запрос" (or uses default text)
3. If triggered from deal topic — same, with auction_id pre-filled
4. Creates `GuarantorRequest` with `auction_id` set

## Edge Cases

- **Winner has no username:** Use `tg://user?id=<tg_id>` mention format (existing `_format_user_mention`)
- **Auction ended by moderator:** Same rich notification, with `Модерация:` prefix in title
- **Buyout vs timer:** Same notification template, buyout adds `(выкуп)` to title
- **Deal topic already exists:** `deal:write:*` opens existing topic instead of creating new one
- **User blocks bot:** Private topic delivery fails silently (existing behavior)
- **One party closes topic:** DealTopic status → CLOSED, both parties notified

## Files Affected

| File | Change |
|------|--------|
| `app/services/notification_copy_service.py` | New rich message templates |
| `app/bot/keyboards/auction.py` | New keyboard builders |
| `app/bot/handlers/deal_topic.py` | **New** — deal topic handler |
| `app/bot/handlers/bid_actions.py` | Updated `_notify_auction_finish` |
| `app/services/auction_service.py` | Rich FinalizeResult, updated notifications |
| `app/services/deal_topic_service.py` | **New** — deal topic CRUD + forwarding |
| `app/db/models.py` | New `DealTopic` model, `GuarantorRequest.auction_id` |
| `app/db/enums.py` | New `DealTopicStatus` enum |
| `app/bot/handlers/guarantor.py` | Accept pre-filled auction_id |
| `app/bot/handlers/trade_feedback.py` | FSM trigger from inline button |
| `app/bot/handlers/moderation.py` | Use `_finalize_auction_locked` for `/end` |
| `alembic/versions/xxx_add_deal_topics.py` | **New** migration |
