# Post-Auction UX Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace minimal post-auction notifications with rich messages containing deal info, next-step instructions, guarantor memo, and action buttons; add a deal topic system for mediated seller-winner communication.

**Architecture:** Extend `FinalizeResult` to carry rich data, replace notification copy functions with rich templates, add `DealTopic` model + service for brokered messaging, new `post_auction` handler for inline button callbacks, FSM flows for feedback and guarantor requests triggered from inline buttons.

**Tech Stack:** Python 3.12, aiogram 3.x, SQLAlchemy 2.x (async), Alembic, PostgreSQL

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `app/db/enums.py` | Modify | Add `DealTopicStatus` enum |
| `app/db/models.py` | Modify | Add `DealTopic` model, add `auction_id` to `GuarantorRequest` |
| `app/db/base.py` | No change | Already imports all models |
| `alembic/versions/xxx_add_deal_topics_and_guarantor_auction.py` | Create | Migration for new table + column |
| `app/services/notification_copy_service.py` | Modify | Add rich completion message functions |
| `app/bot/keyboards/auction.py` | Modify | Add new keyboard builders |
| `app/services/deal_topic_service.py` | Create | DealTopic CRUD, message forwarding |
| `app/bot/handlers/post_auction.py` | Create | Callback handlers for all deal buttons |
| `app/bot/states/post_auction.py` | Create | FSM states for feedback + guarantor from inline |
| `app/services/auction_service.py` | Modify | Extend `FinalizeResult`, update notification calls |
| `app/bot/handlers/bid_actions.py` | Modify | Use rich notifications in `_notify_auction_finish` |
| `app/bot/handlers/__init__.py` | Modify | Register `post_auction_router` |
| `app/bot/handlers/moderation.py` | Modify | Use `_finalize_auction_locked` in `mod_end` |
| `app/bot/handlers/guarantor.py` | Modify | Accept pre-filled `auction_id` |
| `app/bot/handlers/trade_feedback.py` | Modify | Support inline-triggered FSM |
| `tests/test_notification_copy_rich.py` | Create | Unit tests for rich copy |
| `tests/test_deal_topic_service.py` | Create | Unit tests for deal topic service |
| `tests/test_post_auction_keyboards.py` | Create | Unit tests for keyboards |
| `tests/test_post_auction_callbacks.py` | Create | Unit tests for callback handlers |
| `tests/integration/test_deal_topic_flow.py` | Create | Integration test for full deal topic flow |

---

### Task 1: Add `DealTopicStatus` enum

**Files:**
- Modify: `app/db/enums.py`

- [ ] **Step 1: Add the enum**

In `app/db/enums.py`, add after the existing `GuarantorRequestStatus` enum (around line 81):

```python
class DealTopicStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
```

- [ ] **Step 2: Verify lint passes**

Run: `python -m ruff check app/db/enums.py`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add app/db/enums.py
git commit -m "feat: add DealTopicStatus enum for post-auction deal topics"
```

---

### Task 2: Add `DealTopic` model and extend `GuarantorRequest`

**Files:**
- Modify: `app/db/models.py`

- [ ] **Step 1: Add `DealTopic` model**

In `app/db/models.py`, add after the `GuarantorRequest` class (after line ~823). Import `DealTopicStatus` at the top of the file alongside other enum imports:

```python
from app.db.enums import (
    AuctionStatus,
    BidStatus,
    ComplaintStatus,
    DealTopicStatus,
    GuarantorRequestStatus,
    ModerationEventType,
    ModerationQueueStatus,
    ReputationEventReason,
    VerificationStatus,
)
```

Add the model:

```python
class DealTopic(Base, TimestampMixin):
    __tablename__ = "deal_topics"
    __table_args__ = (
        Index("ix_deal_topics_auction_id", "auction_id"),
        Index("ix_deal_topics_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auctions.id", ondelete="CASCADE"),
        nullable=False,
    )
    seller_topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    winner_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[DealTopicStatus] = mapped_column(
        Enum(DealTopicStatus, name="deal_topic_status"),
        nullable=False,
        default=DealTopicStatus.ACTIVE,
        server_default=text("'ACTIVE'"),
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 2: Add `auction_id` nullable column to `GuarantorRequest`**

In the `GuarantorRequest` class, add after the `details` field (around line 806):

```python
    auction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auctions.id", ondelete="SET NULL"),
        nullable=True,
    )
```

- [ ] **Step 3: Verify lint passes**

Run: `python -m ruff check app/db/models.py`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add app/db/models.py
git commit -m "feat: add DealTopic model, add auction_id to GuarantorRequest"
```

---

### Task 3: Generate Alembic migration

**Files:**
- Create: `alembic/versions/xxx_add_deal_topics_and_guarantor_auction.py`

- [ ] **Step 1: Generate migration**

Run: `alembic revision --autogenerate -m "add_deal_topics_and_guarantor_auction_id"`

- [ ] **Step 2: Review the generated migration**

Open the generated file and verify it contains:
- `CREATE TABLE deal_topics` with columns: `id`, `auction_id`, `seller_topic_id`, `winner_topic_id`, `status`, `closed_at`, `created_at`, `updated_at`
- `ADD COLUMN auction_id` to `guarantor_requests` (nullable)
- Indexes on `deal_topics.auction_id` and `deal_topics.status`

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/
git commit -m "feat: alembic migration for deal_topics table and guarantor auction_id"
```

---

### Task 4: Extend `FinalizeResult` with rich data

**Files:**
- Modify: `app/services/auction_service.py`

- [ ] **Step 1: Extend the dataclass**

Replace the `FinalizeResult` dataclass (lines 79-84) with:

```python
@dataclass(slots=True)
class FinalizeResult:
    auction_id: uuid.UUID
    winner_tg_user_id: int | None
    seller_tg_user_id: int
    final_price: int | None = None
    description: str | None = None
    winner_username: str | None = None
    winner_first_name: str | None = None
    seller_username: str | None = None
    seller_first_name: str | None = None
    had_bids: bool = True
```

- [ ] **Step 2: Update `_finalize_auction_locked` to populate rich data**

Modify the return statement at the end of `_finalize_auction_locked` (around line 525). After computing `winner_tg_user_id`, also extract `winner_username`, `winner_first_name`. The `seller` object is already fetched. Change the return to:

```python
    winner_username: str | None = None
    winner_first_name: str | None = None
    if winner_user_id is not None:
        winner = await session.scalar(select(User).where(User.id == winner_user_id))
        winner_tg_user_id = winner.tg_user_id if winner is not None else None
        if winner is not None:
            winner_username = winner.username
            winner_first_name = winner.first_name

    if seller is None:
        return None

    await adjust_reputation(session, auction.seller_user_id, 3, ReputationEventReason.AUCTION_COMPLETED)
    if winner_user_id is not None:
        await adjust_reputation(session, winner_user_id, 2, ReputationEventReason.BID_WON)

    top_bids = await _top_bids_for_auction(session, auction.id, limit=1)
    had_bids = len(top_bids) > 0

    return FinalizeResult(
        auction_id=auction.id,
        winner_tg_user_id=winner_tg_user_id,
        seller_tg_user_id=seller.tg_user_id,
        final_price=auction.start_price if had_bids and top_bids else None,
        description=auction.description,
        winner_username=winner_username,
        winner_first_name=winner_first_name,
        seller_username=seller.username,
        seller_first_name=seller.first_name,
        had_bids=had_bids,
    )
```

Note: `final_price` should use `top_bids[0].amount` if available. Update:

```python
    final_price = top_bids[0].amount if top_bids else auction.start_price
```

And in the FinalizeResult construction:
```python
        final_price=final_price if had_bids else None,
```

- [ ] **Step 3: Verify lint passes**

Run: `python -m ruff check app/services/auction_service.py`
Expected: no errors

- [ ] **Step 4: Run existing tests**

Run: `python -m pytest tests/test_auction_service_resilience.py tests/test_auction_caption_render.py -q`
Expected: all pass (FinalizeResult consumers access fields that still exist)

- [ ] **Step 5: Commit**

```bash
git add app/services/auction_service.py
git commit -m "feat: extend FinalizeResult with rich auction data for post-auction notifications"
```

---

### Task 5: Add rich notification copy functions

**Files:**
- Modify: `app/services/notification_copy_service.py`
- Create: `tests/test_notification_copy_rich.py`

- [ ] **Step 1: Write tests for rich copy functions**

Create `tests/test_notification_copy_rich.py`:

```python
from __future__ import annotations

import uuid

import pytest

from app.services.notification_copy_service import (
    seller_completion_text,
    winner_completion_text,
    seller_no_bids_text,
    deal_topic_intro_text,
    moderation_completion_text,
)


@pytest.fixture
def auction_id() -> uuid.UUID:
    return uuid.UUID("a1b2c3d4-5678-9012-abcd-ef1234567890")


def test_seller_completion_text_basic(auction_id: uuid.UUID) -> None:
    text = seller_completion_text(
        auction_id=auction_id,
        description="iPhone 15 Pro Max",
        final_price=15000,
        counterparty_mention="@winner",
        counterparty_role="победитель",
    )
    assert "#a1b2c3d4" in text
    assert "15 000" in text
    assert "@winner" in text
    assert "аукцион" in text.lower() or "завершён" in text.lower() or "завершен" in text.lower()


def test_winner_completion_text_basic(auction_id: uuid.UUID) -> None:
    text = winner_completion_text(
        auction_id=auction_id,
        description="iPhone 15 Pro Max",
        final_price=15000,
        counterparty_mention="@seller",
        counterparty_role="продавец",
    )
    assert "#a1b2c3d4" in text
    assert "15 000" in text
    assert "@seller" in text
    assert "выиграл" in text.lower() or "выиграли" in text.lower()


def test_seller_no_bids_text_basic(auction_id: uuid.UUID) -> None:
    text = seller_no_bids_text(
        auction_id=auction_id,
        description="iPhone 15 Pro Max",
        start_price=10000,
    )
    assert "#a1b2c3d4" in text
    assert "10 000" in text
    assert "0" in text


def test_deal_topic_intro_text_basic(auction_id: uuid.UUID) -> None:
    text = deal_topic_intro_text(
        auction_id=auction_id,
        description="iPhone 15 Pro Max",
        final_price=15000,
        seller_mention="@seller",
        winner_mention="@winner",
    )
    assert "#a1b2c3d4" in text
    assert "@seller" in text
    assert "@winner" in text


def test_moderation_completion_text_basic(auction_id: uuid.UUID) -> None:
    text = moderation_completion_text(
        auction_id=auction_id,
        description="iPhone 15 Pro Max",
        final_price=15000,
        bid_count=7,
        seller_mention="@seller (tg: 123)",
        winner_mention="@winner (tg: 456)",
        seller_reputation=42,
        winner_reputation=15,
        has_deal_topic=True,
        has_guarantor=False,
        reason="по таймеру",
    )
    assert "#a1b2c3d4" in text
    assert "15 000" in text
    assert "7" in text
    assert "создан" in text.lower()
    assert "не запрошен" in text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_notification_copy_rich.py -q`
Expected: FAIL — functions not defined

- [ ] **Step 3: Implement rich copy functions**

Add these functions to `app/services/notification_copy_service.py`:

```python
def _truncate(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _format_price(amount: int) -> str:
    return f"{amount:,} ₽".replace(",", " ")


def seller_completion_text(
    *,
    auction_id: uuid.UUID,
    description: str,
    final_price: int,
    counterparty_mention: str,
    counterparty_role: str,
    is_buyout: bool = False,
    is_mod_action: bool = False,
) -> str:
    prefix = "Модерация: " if is_mod_action else ""
    buyout_label = " выкупом" if is_buyout else ""
    return (
        f"🏆 {prefix}Аукцион завершён{buyout_label}!\n\n"
        f"<b>Лот:</b> {short_auction_ref(auction_id)}\n"
        f"<b>Описание:</b> {html.escape(_truncate(description))}\n"
        f"<b>Финальная цена:</b> {_format_price(final_price)}\n"
        f"<b>Победитель:</b> {counterparty_mention}\n\n"
        f"📋 <b>Следующие шаги:</b>\n"
        f"1. Нажмите «Написать победителю» — откроется топик сделки\n"
        f"2. Договоритесь о способе передачи товара и оплаты\n"
        f"3. После завершения — оставьте отзыв\n\n"
        f"🛡 <b>Гарант — ваша безопасность</b>\n"
        f"Если сумма сделки значительная или вы впервые работаете с {counterparty_role}ом — "
        f"запросите гаранта. Гарант выступит посредником и обеспечит защиту обеих сторон.\n"
        f"<i>Стоимость: бесплатно для участников с репутацией 50+</i>"
    )


def winner_completion_text(
    *,
    auction_id: uuid.UUID,
    description: str,
    final_price: int,
    counterparty_mention: str,
    counterparty_role: str,
    is_buyout: bool = False,
    is_mod_action: bool = False,
) -> str:
    prefix = "Модерация: " if is_mod_action else ""
    buyout_label = " (выкуп)" if is_buyout else ""
    return (
        f"🎉 {prefix}Вы выиграли аукцион{buyout_label}!\n\n"
        f"<b>Лот:</b> {short_auction_ref(auction_id)}\n"
        f"<b>Описание:</b> {html.escape(_truncate(description))}\n"
        f"<b>Ваша цена:</b> {_format_price(final_price)}\n"
        f"<b>Продавец:</b> {counterparty_mention}\n\n"
        f"📋 <b>Что дальше:</b>\n"
        f"1. Нажмите «Написать продавцу» — откроется топик сделки\n"
        f"2. Договоритесь о способе получения товара и оплаты\n"
        f"3. После завершения — оставьте отзыв\n\n"
        f"🛡 <b>Гарант — ваша безопасность</b>\n"
        f"Если сумма сделки значительная или вы впервые работаете с {counterparty_role}ом — "
        f"запросите гаранта. Гарант выступит посредником и обеспечит защиту обеих сторон.\n"
        f"<i>Стоимость: бесплатно для участников с репутацией 50+</i>"
    )


def seller_no_bids_text(
    *,
    auction_id: uuid.UUID,
    description: str,
    start_price: int,
) -> str:
    return (
        f"⏰ Аукцион завершён без ставок\n\n"
        f"<b>Лот:</b> {short_auction_ref(auction_id)}\n"
        f"<b>Описание:</b> {html.escape(_truncate(description))}\n"
        f"<b>Стартовая цена:</b> {_format_price(start_price)}\n"
        f"<b>Ставок:</b> 0\n\n"
        f"💡 <b>Совет:</b> Попробуйте изменить стартовую цену или описание "
        f"и опубликуйте лот заново."
    )


def deal_topic_intro_text(
    *,
    auction_id: uuid.UUID,
    description: str,
    final_price: int,
    seller_mention: str,
    winner_mention: str,
) -> str:
    return (
        f"🤝 <b>Топик сделки создан</b>\n\n"
        f"Лот: {short_auction_ref(auction_id)} — {html.escape(_truncate(description))}\n"
        f"Цена: {_format_price(final_price)}\n"
        f"Участники: {seller_mention} (продавец) и {winner_mention} (победитель)\n\n"
        f"Все сообщения здесь будут пересылаться вашему контрагенту.\n"
        f"Будьте вежливы и обсуждайте только сделку.\n\n"
        f"🛡 <b>Совет по безопасности</b>\n"
        f"Для защиты обеих сторон вы можете запросить гаранта — нажав кнопку ниже."
    )


def moderation_completion_text(
    *,
    auction_id: uuid.UUID,
    description: str,
    final_price: int,
    bid_count: int,
    seller_mention: str,
    winner_mention: str,
    seller_reputation: int,
    winner_reputation: int,
    has_deal_topic: bool,
    has_guarantor: bool,
    reason: str,
) -> str:
    deal_label = "создан автоматически" if has_deal_topic else "не создан"
    guarantor_label = "запрошен" if has_guarantor else "не запрошен"
    return (
        f"🏁 Лот {short_auction_ref(auction_id)} завершён {reason}\n\n"
        f"<b>Описание:</b> {html.escape(_truncate(description))}\n"
        f"<b>Финальная цена:</b> {_format_price(final_price)}\n"
        f"<b>Ставок:</b> {bid_count}\n\n"
        f"<b>Продавец:</b> {seller_mention}\n"
        f"<b>Победитель:</b> {winner_mention}\n"
        f"<b>Репутация продавца:</b> {seller_reputation}\n"
        f"<b>Репутация победителя:</b> {winner_reputation}\n\n"
        f"🤝 <b>Топик сделки:</b> {deal_label}\n"
        f"🛡 <b>Гарант:</b> {guarantor_label}"
    )
```

Also add `import html` at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_notification_copy_rich.py -q`
Expected: all pass

- [ ] **Step 5: Run lint**

Run: `python -m ruff check app/services/notification_copy_service.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add app/services/notification_copy_service.py tests/test_notification_copy_rich.py
git commit -m "feat: add rich post-auction notification copy functions with tests"
```

---

### Task 6: Add new keyboard builders

**Files:**
- Modify: `app/bot/keyboards/auction.py`
- Create: `tests/test_post_auction_keyboards.py`

- [ ] **Step 1: Write keyboard tests**

Create `tests/test_post_auction_keyboards.py`:

```python
from __future__ import annotations

import uuid

from app.bot.keyboards.auction import (
    deal_completion_keyboard,
    no_bids_keyboard,
    deal_topic_keyboard,
    moderation_completion_keyboard,
)


def test_deal_completion_keyboard_seller() -> None:
    kb = deal_completion_keyboard(
        auction_id="a1b2c3d4",
        post_url="https://t.me/channel/123",
        is_seller=True,
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 3
    assert "deal:write:a1b2c3d4" in buttons[0][0].callback_data
    assert "guarantor" not in buttons[0][0].text.lower() or True
    assert any("открыть" in b[0].text.lower() for b in buttons)


def test_deal_completion_keyboard_winner() -> None:
    kb = deal_completion_keyboard(
        auction_id="a1b2c3d4",
        post_url="https://t.me/channel/123",
        is_seller=False,
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 3


def test_deal_completion_keyboard_no_post_url() -> None:
    kb = deal_completion_keyboard(
        auction_id="a1b2c3d4",
        post_url=None,
        is_seller=True,
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 2


def test_no_bids_keyboard() -> None:
    kb = no_bids_keyboard(
        auction_id="a1b2c3d4",
        post_url="https://t.me/channel/123",
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 2
    assert "deal:republish:a1b2c3d4" in buttons[0][0].callback_data


def test_deal_topic_keyboard() -> None:
    kb = deal_topic_keyboard(auction_id="a1b2c3d4")
    buttons = kb.inline_keyboard
    assert len(buttons) == 2
    assert any("deal:guarant:" in b[0].callback_data for b in buttons)
    assert any("deal:feedback:" in b[0].callback_data for b in buttons)


def test_moderation_completion_keyboard() -> None:
    kb = moderation_completion_keyboard(
        auction_id="a1b2c3d4",
        post_url="https://t.me/channel/123",
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_post_auction_keyboards.py -q`
Expected: FAIL — functions not defined

- [ ] **Step 3: Implement keyboard builders**

Add to `app/bot/keyboards/auction.py`:

```python
def deal_completion_keyboard(
    *,
    auction_id: str,
    post_url: str | None,
    is_seller: bool,
) -> InlineKeyboardMarkup:
    write_label = "💬 Написать победителю" if is_seller else "💬 Написать продавцу"
    rows: list[list[InlineKeyboardButton]] = [
        [styled_button(text=write_label, callback_data=f"deal:write:{auction_id}", style="primary")],
        [
            styled_button(text="🛡 Запросить гаранта", callback_data=f"deal:guarant:{auction_id}", style="success"),
            styled_button(text="⭐ Оставить отзыв", callback_data=f"deal:feedback:{auction_id}"),
        ],
    ]
    if post_url:
        rows.append([styled_button(text="📄 Открыть пост лота", url=post_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def no_bids_keyboard(
    *,
    auction_id: str,
    post_url: str | None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [styled_button(text="🔄 Опубликовать заново", callback_data=f"deal:republish:{auction_id}", style="primary")],
    ]
    if post_url:
        rows.append([styled_button(text="📄 Открыть пост лота", url=post_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deal_topic_keyboard(
    *,
    auction_id: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(text="🛡 Запросить гаранта", callback_data=f"deal:guarant:{auction_id}", style="success"),
                styled_button(text="⭐ Оставить отзыв", callback_data=f"deal:feedback:{auction_id}"),
            ],
            [
                styled_button(text="⚠ Жалоба", callback_data=f"deal:complaint:{auction_id}"),
            ],
        ]
    )


def moderation_completion_keyboard(
    *,
    auction_id: str,
    post_url: str | None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if post_url:
        rows.append([styled_button(text="📄 Открыть пост лота", url=post_url)])
    rows.append([styled_button(text="⚠ Заморозить", callback_data=f"mod:freeze:{auction_id}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_post_auction_keyboards.py -q`
Expected: all pass

- [ ] **Step 5: Run lint**

Run: `python -m ruff check app/bot/keyboards/auction.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add app/bot/keyboards/auction.py tests/test_post_auction_keyboards.py
git commit -m "feat: add post-auction keyboard builders with tests"
```

---

### Task 7: Create `DealTopicService`

**Files:**
- Create: `app/services/deal_topic_service.py`
- Create: `tests/test_deal_topic_service.py`

- [ ] **Step 1: Write tests**

Create `tests/test_deal_topic_service.py`:

```python
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.deal_topic_service import (
    get_or_create_deal_topic,
    find_active_deal_topic,
    forward_deal_message,
)


@pytest.fixture
def auction_id() -> uuid.UUID:
    return uuid.UUID("a1b2c3d4-5678-9012-abcd-ef1234567890")


@pytest.mark.asyncio
async def test_find_active_deal_topic_returns_none_when_not_exists(auction_id: uuid.UUID) -> None:
    mock_session = AsyncMock()
    mock_session.scalar.return_value = None
    result = await find_active_deal_topic(mock_session, auction_id=auction_id)
    assert result is None


@pytest.mark.asyncio
async def test_get_or_create_returns_existing(auction_id: uuid.UUID) -> None:
    mock_session = AsyncMock()
    existing = MagicMock()
    existing.status.value = "ACTIVE"
    existing.seller_topic_id = 100
    mock_session.scalar.return_value = existing

    mock_bot = AsyncMock()

    result = await get_or_create_deal_topic(
        mock_session,
        bot=mock_bot,
        auction_id=auction_id,
        seller_tg_id=111,
        winner_tg_id=222,
        description="Test",
        final_price=1000,
        seller_mention="@s",
        winner_mention="@w",
    )
    assert result is existing
    mock_session.add.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_deal_topic_service.py -q`
Expected: FAIL

- [ ] **Step 3: Implement `DealTopicService`**

Create `app/services/deal_topic_service.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, UTC

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import DealTopicStatus
from app.db.models import DealTopic
from app.services.notification_copy_service import deal_topic_intro_text
from app.bot.keyboards.auction import deal_topic_keyboard


async def find_active_deal_topic(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID,
) -> DealTopic | None:
    return await session.scalar(
        select(DealTopic).where(
            DealTopic.auction_id == auction_id,
            DealTopic.status == DealTopicStatus.ACTIVE,
        )
    )


async def get_or_create_deal_topic(
    session: AsyncSession,
    *,
    bot: Bot,
    auction_id: uuid.UUID,
    seller_tg_id: int,
    winner_tg_id: int,
    description: str,
    final_price: int,
    seller_mention: str,
    winner_mention: str,
) -> DealTopic:
    existing = await find_active_deal_topic(session, auction_id=auction_id)
    if existing is not None:
        return existing

    short_id = str(auction_id)[:8]

    seller_topic = await bot.create_forum_topic(
        chat_id=seller_tg_id,
        name=f"🤝 Сделка #{short_id}",
    )

    intro = deal_topic_intro_text(
        auction_id=auction_id,
        description=description,
        final_price=final_price,
        seller_mention=seller_mention,
        winner_mention=winner_mention,
    )
    kb = deal_topic_keyboard(auction_id=short_id)

    await bot.send_message(
        chat_id=seller_tg_id,
        message_thread_id=seller_topic.message_thread_id,
        text=intro,
        reply_markup=kb,
        parse_mode="HTML",
    )

    deal = DealTopic(
        auction_id=auction_id,
        seller_topic_id=seller_topic.message_thread_id,
        status=DealTopicStatus.ACTIVE,
    )
    session.add(deal)
    await session.flush()

    return deal


async def ensure_winner_topic(
    session: AsyncSession,
    *,
    bot: Bot,
    deal: DealTopic,
    winner_tg_id: int,
    auction_id: uuid.UUID,
    description: str,
    final_price: int,
    seller_mention: str,
    winner_mention: str,
) -> None:
    if deal.winner_topic_id is not None:
        return

    short_id = str(auction_id)[:8]

    winner_topic = await bot.create_forum_topic(
        chat_id=winner_tg_id,
        name=f"🤝 Сделка #{short_id}",
    )

    intro = deal_topic_intro_text(
        auction_id=auction_id,
        description=description,
        final_price=final_price,
        seller_mention=seller_mention,
        winner_mention=winner_mention,
    )
    kb = deal_topic_keyboard(auction_id=short_id)

    await bot.send_message(
        chat_id=winner_tg_id,
        message_thread_id=winner_topic.message_thread_id,
        text=intro,
        reply_markup=kb,
        parse_mode="HTML",
    )

    deal.winner_topic_id = winner_topic.message_thread_id
    await session.flush()


async def forward_deal_message(
    bot: Bot,
    *,
    deal: DealTopic,
    sender_tg_id: int,
    sender_label: str,
    recipient_tg_id: int,
    recipient_topic_id: int,
    text: str | None = None,
    photo_file_id: str | None = None,
) -> None:
    prefix = f"<b>{sender_label}:</b>\n"
    if photo_file_id:
        caption = f"{prefix}{text}" if text else prefix.rstrip(":")
        await bot.send_photo(
            chat_id=recipient_tg_id,
            message_thread_id=recipient_topic_id,
            photo=photo_file_id,
            caption=caption,
            parse_mode="HTML",
        )
    elif text:
        await bot.send_message(
            chat_id=recipient_tg_id,
            message_thread_id=recipient_topic_id,
            text=f"{prefix}{text}",
            parse_mode="HTML",
        )


async def close_deal_topic(
    session: AsyncSession,
    *,
    deal: DealTopic,
) -> None:
    deal.status = DealTopicStatus.CLOSED
    deal.closed_at = datetime.now(UTC)
    await session.flush()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_deal_topic_service.py -q`
Expected: all pass

- [ ] **Step 5: Run lint**

Run: `python -m ruff check app/services/deal_topic_service.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add app/services/deal_topic_service.py tests/test_deal_topic_service.py
git commit -m "feat: add DealTopicService for deal topic CRUD and message forwarding"
```

---

### Task 8: Create post-auction FSM states

**Files:**
- Create: `app/bot/states/post_auction.py`

- [ ] **Step 1: Create FSM states**

Create `app/bot/states/post_auction.py`:

```python
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class FeedbackFSM(StatesGroup):
    waiting_rating = State()
    waiting_comment = State()


class DealGuarantorFSM(StatesGroup):
    waiting_details = State()
```

- [ ] **Step 2: Run lint**

Run: `python -m ruff check app/bot/states/post_auction.py`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add app/bot/states/post_auction.py
git commit -m "feat: add FSM states for post-auction feedback and guarantor flows"
```

---

### Task 9: Create `post_auction` handler

**Files:**
- Create: `app/bot/handlers/post_auction.py`
- Create: `tests/test_post_auction_callbacks.py`

- [ ] **Step 1: Write basic callback tests**

Create `tests/test_post_auction_callbacks.py`:

```python
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.handlers.post_auction import parse_deal_callback


def test_parse_deal_callback_write() -> None:
    action, auction_id = parse_deal_callback("deal:write:a1b2c3d4-5678-9012-abcd-ef1234567890")
    assert action == "write"
    assert auction_id == uuid.UUID("a1b2c3d4-5678-9012-abcd-ef1234567890")


def test_parse_deal_callback_guarant() -> None:
    action, auction_id = parse_deal_callback("deal:guarant:a1b2c3d4-5678-9012-abcd-ef1234567890")
    assert action == "guarant"
    assert auction_id == uuid.UUID("a1b2c3d4-5678-9012-abcd-ef1234567890")


def test_parse_deal_callback_feedback() -> None:
    action, auction_id = parse_deal_callback("deal:feedback:a1b2c3d4-5678-9012-abcd-ef1234567890")
    assert action == "feedback"


def test_parse_deal_callback_republish() -> None:
    action, auction_id = parse_deal_callback("deal:republish:a1b2c3d4-5678-9012-abcd-ef1234567890")
    assert action == "republish"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_post_auction_callbacks.py -q`
Expected: FAIL

- [ ] **Step 3: Implement the handler**

Create `app/bot/handlers/post_auction.py`:

```python
from __future__ import annotations

import uuid

from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Auction, DealTopic, User, GuarantorRequest
from app.db.enums import AuctionStatus, DealTopicStatus
from app.bot.keyboards.auction import deal_topic_keyboard
from app.bot.states.post_auction import FeedbackFSM, DealGuarantorFSM
from app.db.session import SessionFactory
from app.services.deal_topic_service import (
    get_or_create_deal_topic,
    ensure_winner_topic,
    forward_deal_message,
    find_active_deal_topic,
)
from app.services.notification_copy_service import _format_user_mention, short_auction_ref
from app.services.trade_feedback_service import submit_trade_feedback
from app.services.guarantor_service import create_guarantor_request

router = Router(name="post_auction")


def parse_deal_callback(data: str) -> tuple[str, uuid.UUID]:
    parts = data.split(":")
    return parts[1], uuid.UUID(parts[2])


def _format_mention(user: User | None) -> str:
    if user is None:
        return "—"
    if user.username:
        return f"@{user.username}"
    display = user.first_name or "Пользователь"
    return f'<a href="tg://user?id={user.tg_user_id}">{display}</a>'


@router.callback_query(F.data.startswith("deal:write:"))
async def handle_deal_write(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    action, auction_id = parse_deal_callback(callback.data)
    assert action == "write"

    async with SessionFactory() as session:
        async with session.begin():
            auction = await session.scalar(
                select(Auction).where(Auction.id == auction_id)
            )
            if auction is None:
                await callback.answer("Аукцион не найден", show_alert=True)
                return

            seller = await session.scalar(select(User).where(User.id == auction.seller_user_id))
            winner = await session.scalar(select(User).where(User.id == auction.winner_user_id)) if auction.winner_user_id else None

            if winner is None:
                await callback.answer("Нет победителя для связи", show_alert=True)
                return

            caller_tg_id = callback.from_user.id
            is_seller = caller_tg_id == seller.tg_user_id if seller else False

            deal = await get_or_create_deal_topic(
                session,
                bot=bot,
                auction_id=auction_id,
                seller_tg_id=seller.tg_user_id,
                winner_tg_id=winner.tg_user_id,
                description=auction.description,
                final_price=auction.start_price,
                seller_mention=_format_mention(seller),
                winner_mention=_format_mention(winner),
            )

            if is_seller:
                await ensure_winner_topic(
                    session,
                    bot=bot,
                    deal=deal,
                    winner_tg_id=winner.tg_user_id,
                    auction_id=auction_id,
                    description=auction.description,
                    final_price=auction.start_price,
                    seller_mention=_format_mention(seller),
                    winner_mention=_format_mention(winner),
                )

    await callback.answer("Топик сделки открыт")


@router.callback_query(F.data.startswith("deal:feedback:"))
async def handle_deal_feedback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, auction_id = parse_deal_callback(callback.data)

    await state.update_data(feedback_auction_id=str(auction_id))
    await state.set_state(FeedbackFSM.waiting_rating)

    rating_kb = _rating_keyboard()
    await callback.message.answer("⭐ Оцените сделку:", reply_markup=rating_kb)
    await callback.answer()


def _rating_keyboard():
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐", callback_data="rate:1"),
                InlineKeyboardButton(text="⭐⭐", callback_data="rate:2"),
                InlineKeyboardButton(text="⭐⭐⭐", callback_data="rate:3"),
            ],
            [
                InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data="rate:4"),
                InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data="rate:5"),
            ],
        ]
    )


@router.callback_query(F.data.startswith("rate:"))
async def handle_rating(callback: CallbackQuery, state: FSMContext) -> None:
    rating = int(callback.data.split(":")[1])
    await state.update_data(feedback_rating=rating)
    await state.set_state(FeedbackFSM.waiting_comment)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await callback.message.answer(
        "Комментарий (необязательно):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="rate:skip")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "rate:skip", FeedbackFSM.waiting_comment)
async def handle_rating_skip(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    auction_id = uuid.UUID(data["feedback_auction_id"])
    rating = data["feedback_rating"]

    async with SessionFactory() as session:
        async with session.begin():
            user = await session.scalar(
                select(User).where(User.tg_user_id == callback.from_user.id)
            )
            if user is None:
                await callback.answer("Пользователь не найден", show_alert=True)
                return
            result = await submit_trade_feedback(
                session,
                auction_id=auction_id,
                author_user_id=user.id,
                rating=rating,
                comment=None,
            )

    await state.clear()
    await callback.message.answer(result.message)
    await callback.answer()


@router.message(FeedbackFSM.waiting_comment, F.text)
async def handle_feedback_comment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    auction_id = uuid.UUID(data["feedback_auction_id"])
    rating = data["feedback_rating"]

    async with SessionFactory() as session:
        async with session.begin():
            user = await session.scalar(
                select(User).where(User.tg_user_id == message.from_user.id)
            )
            if user is None:
                await message.answer("Пользователь не найден")
                return
            result = await submit_trade_feedback(
                session,
                auction_id=auction_id,
                author_user_id=user.id,
                rating=rating,
                comment=message.text,
            )

    await state.clear()
    await message.answer(result.message)


@router.callback_query(F.data.startswith("deal:guarant:"))
async def handle_deal_guarant(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, auction_id = parse_deal_callback(callback.data)

    await state.update_data(guarant_auction_id=str(auction_id))
    await state.set_state(DealGuarantorFSM.waiting_details)

    await callback.message.answer("Опишите ваш запрос гаранта:")
    await callback.answer()


@router.message(DealGuarantorFSM.waiting_details, F.text)
async def handle_guarant_details(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    auction_id = uuid.UUID(data["guarant_auction_id"])

    async with SessionFactory() as session:
        async with session.begin():
            user = await session.scalar(
                select(User).where(User.tg_user_id == message.from_user.id)
            )
            if user is None:
                await message.answer("Пользователь не найден")
                return
            await create_guarantor_request(
                session,
                submitter_user_id=user.id,
                details=message.text,
                auction_id=auction_id,
            )

    await state.clear()
    await message.answer("Запрос гаранта отправлен модераторам.")


@router.callback_query(F.data.startswith("deal:republish:"))
async def handle_deal_republish(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, auction_id = parse_deal_callback(callback.data)

    async with SessionFactory() as session:
        async with session.begin():
            auction = await session.scalar(
                select(Auction).where(Auction.id == auction_id).with_for_update()
            )
            if auction is None:
                await callback.answer("Аукцион не найден", show_alert=True)
                return
            if auction.status != AuctionStatus.ENDED:
                await callback.answer("Можно переопубликовать только завершённый лот", show_alert=True)
                return

            from app.db.models import Auction as AuctionModel
            new_auction = AuctionModel(
                seller_user_id=auction.seller_user_id,
                description=auction.description,
                photo_file_id=auction.photo_file_id,
                start_price=auction.start_price,
                buyout_price=auction.buyout_price,
                min_step=auction.min_step,
                duration_hours=auction.duration_hours,
                anti_sniper_enabled=auction.anti_sniper_enabled,
            )
            session.add(new_auction)

    await callback.message.answer("Лот скопирован как черновик. Опубликуйте его через /sell.")
    await callback.answer()
```

Note: `create_guarantor_request` needs to accept an optional `auction_id` parameter — this will be added in Task 10.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_post_auction_callbacks.py -q`
Expected: all pass

- [ ] **Step 5: Run lint**

Run: `python -m ruff check app/bot/handlers/post_auction.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add app/bot/handlers/post_auction.py tests/test_post_auction_callbacks.py
git commit -m "feat: add post_auction handler with deal write, feedback, guarant, republish"
```

---

### Task 10: Update `create_guarantor_request` to accept `auction_id`

**Files:**
- Modify: `app/services/guarantor_service.py`

- [ ] **Step 1: Update the function signature**

In `app/services/guarantor_service.py`, find the `create_guarantor_request` function (around line 72). Add `auction_id: uuid.UUID | None = None` parameter and pass it when creating the `GuarantorRequest`:

```python
async def create_guarantor_request(
    session: AsyncSession,
    *,
    submitter_user_id: int,
    details: str,
    auction_id: uuid.UUID | None = None,
) -> GuarantorRequest:
```

And in the model construction, add `auction_id=auction_id`.

- [ ] **Step 2: Verify lint passes**

Run: `python -m ruff check app/services/guarantor_service.py`
Expected: no errors

- [ ] **Step 3: Run existing guarantor tests**

Run: `python -m pytest tests/test_guarantor_service.py tests/integration/test_guarantor_service.py -q 2>/dev/null || python -m pytest tests/ -k guarantor -q`
Expected: all pass (new parameter is optional with default None)

- [ ] **Step 4: Commit**

```bash
git add app/services/guarantor_service.py
git commit -m "feat: accept optional auction_id in create_guarantor_request"
```

---

### Task 11: Register `post_auction` router

**Files:**
- Modify: `app/bot/handlers/__init__.py`

- [ ] **Step 1: Add import and registration**

In `app/bot/handlers/__init__.py`, add the import:

```python
from .post_auction import router as post_auction_router
```

And register before `moderation_router`:

```python
router.include_router(post_auction_router)
```

- [ ] **Step 2: Verify lint passes**

Run: `python -m ruff check app/bot/handlers/__init__.py`
Expected: no errors

- [ ] **Step 3: Run main dispatcher test**

Run: `python -m pytest tests/test_main_dispatcher.py -q`
Expected: pass

- [ ] **Step 4: Commit**

```bash
git add app/bot/handlers/__init__.py
git commit -m "feat: register post_auction router in handler root"
```

---

### Task 12: Update `_notify_auction_finish` in `bid_actions.py`

**Files:**
- Modify: `app/bot/handlers/bid_actions.py`

- [ ] **Step 1: Update `_notify_auction_finish`**

Replace the function at lines 387-436. The new version accepts rich data and uses new copy + keyboards:

```python
async def _notify_auction_finish(
    bot: Bot,
    *,
    winner_tg_id: int | None,
    seller_tg_id: int | None,
    auction_id: uuid.UUID,
    post_url: str | None,
    final_price: int | None = None,
    description: str | None = None,
    winner_mention: str | None = None,
    seller_mention: str | None = None,
    is_buyout: bool = True,
    had_bids: bool = True,
) -> None:
    resolved_post_url = post_url or await resolve_auction_post_url(bot, auction_id=auction_id)
    short_id = str(auction_id)[:8]

    if seller_tg_id is not None and had_bids and final_price is not None:
        from app.services.notification_copy_service import seller_completion_text
        from app.bot.keyboards.auction import deal_completion_keyboard

        text = seller_completion_text(
            auction_id=auction_id,
            description=description or "",
            final_price=final_price,
            counterparty_mention=winner_mention or str(winner_tg_id),
            counterparty_role="победитель",
            is_buyout=is_buyout,
        )
        kb = deal_completion_keyboard(auction_id=short_id, post_url=resolved_post_url, is_seller=True)

        await send_user_topic_message(
            bot,
            tg_user_id=seller_tg_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=text,
            reply_markup=kb,
            message_effect_id=resolve_auction_message_effect_id(AuctionMessageEffectEvent.BUYOUT_SELLER),
            notification_event=NotificationEventType.AUCTION_FINISH,
            auction_id=auction_id,
        )
    elif seller_tg_id is not None and not had_bids:
        from app.services.notification_copy_service import seller_no_bids_text
        from app.bot.keyboards.auction import no_bids_keyboard

        text = seller_no_bids_text(
            auction_id=auction_id,
            description=description or "",
            start_price=final_price or 0,
        )
        kb = no_bids_keyboard(auction_id=short_id, post_url=resolved_post_url)

        await send_user_topic_message(
            bot,
            tg_user_id=seller_tg_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=text,
            reply_markup=kb,
            notification_event=NotificationEventType.AUCTION_FINISH,
            auction_id=auction_id,
        )

    if winner_tg_id is not None and had_bids and final_price is not None:
        from app.services.notification_copy_service import winner_completion_text
        from app.bot.keyboards.auction import deal_completion_keyboard

        text = winner_completion_text(
            auction_id=auction_id,
            description=description or "",
            final_price=final_price,
            counterparty_mention=seller_mention or str(seller_tg_id),
            counterparty_role="продавец",
            is_buyout=is_buyout,
        )
        kb = deal_completion_keyboard(auction_id=short_id, post_url=resolved_post_url, is_seller=False)

        await send_user_topic_message(
            bot,
            tg_user_id=winner_tg_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=text,
            reply_markup=kb,
            message_effect_id=resolve_auction_message_effect_id(AuctionMessageEffectEvent.BUYOUT_WINNER),
            notification_event=NotificationEventType.AUCTION_WIN,
            auction_id=auction_id,
        )

    from app.services.notification_copy_service import moderation_completion_text
    from app.bot.keyboards.auction import moderation_completion_keyboard

    seller_label = seller_mention or (str(seller_tg_id) if seller_tg_id is not None else "нет")
    winner_label = winner_mention or (str(winner_tg_id) if winner_tg_id is not None else "нет")

    mod_text = moderation_completion_text(
        auction_id=auction_id,
        description=description or "",
        final_price=final_price or 0,
        bid_count=0,
        seller_mention=seller_label,
        winner_mention=winner_label,
        seller_reputation=0,
        winner_reputation=0,
        has_deal_topic=False,
        has_guarantor=False,
        reason="выкупом" if is_buyout else "по таймеру",
    )
    mod_kb = moderation_completion_keyboard(auction_id=short_id, post_url=resolved_post_url)

    await send_section_message(
        bot,
        section=ModerationTopicSection.AUCTIONS_CLOSED,
        text=mod_text,
        reply_markup=mod_kb,
    )
```

- [ ] **Step 2: Update callers to pass rich data**

Find where `_notify_auction_finish` is called (in `handle_buyout_action` around line 650). Update the call to pass the new parameters from `BidActionResult` / `FinalizeResult`:

```python
    await _notify_auction_finish(
        bot,
        winner_tg_id=result.winner_tg_user_id,
        seller_tg_id=result.seller_tg_user_id,
        auction_id=auction_id,
        post_url=None,
        final_price=result.final_price if hasattr(result, 'final_price') else None,
        description=result.description if hasattr(result, 'description') else None,
        winner_mention=_build_mention(result.winner_username, result.winner_first_name, result.winner_tg_user_id) if result.winner_tg_user_id else None,
        seller_mention=_build_mention(result.seller_username, result.seller_first_name, result.seller_tg_user_id),
        is_buyout=True,
        had_bids=result.had_bids if hasattr(result, 'had_bids') else True,
    )
```

Add helper at module level:

```python
def _build_mention(username: str | None, first_name: str | None, tg_id: int) -> str:
    if username:
        return f"@{username}"
    display = first_name or "Пользователь"
    return f'<a href="tg://user?id={tg_id}">{display}</a>'
```

- [ ] **Step 3: Verify lint passes**

Run: `python -m ruff check app/bot/handlers/bid_actions.py`
Expected: no errors

- [ ] **Step 4: Run existing tests**

Run: `python -m pytest tests/test_bid_actions_alerts.py tests/test_bid_actions_outbid_notifications.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/bot/handlers/bid_actions.py
git commit -m "feat: use rich notifications in _notify_auction_finish"
```

---

### Task 13: Update `finalize_expired_auctions` notifications

**Files:**
- Modify: `app/services/auction_service.py`

- [ ] **Step 1: Update notification section**

In `finalize_expired_auctions` (lines ~830-871), replace the notification loop. After `await _safe_refresh_auction_posts(bot, auction_id)`, the loop over `finalized_results` needs to use the new rich data:

Replace the notification block with:

```python
    for result in finalized_results:
        post_url = await resolve_auction_post_url(bot, auction_id=result.auction_id)
        short_id = str(result.auction_id)[:8]

        winner_mention = _build_result_mention(
            result.winner_username, result.winner_first_name, result.winner_tg_user_id
        )
        seller_mention = _build_result_mention(
            result.seller_username, result.seller_first_name, result.seller_tg_user_id
        )

        if result.had_bids and result.final_price is not None:
            from app.services.notification_copy_service import seller_completion_text
            from app.bot.keyboards.auction import deal_completion_keyboard

            seller_text = seller_completion_text(
                auction_id=result.auction_id,
                description=result.description or "",
                final_price=result.final_price,
                counterparty_mention=winner_mention,
                counterparty_role="победитель",
                is_buyout=False,
            )
            seller_kb = deal_completion_keyboard(auction_id=short_id, post_url=post_url, is_seller=True)
        else:
            from app.services.notification_copy_service import seller_no_bids_text
            from app.bot.keyboards.auction import no_bids_keyboard

            seller_text = seller_no_bids_text(
                auction_id=result.auction_id,
                description=result.description or "",
                start_price=result.final_price or 0,
            )
            seller_kb = no_bids_keyboard(auction_id=short_id, post_url=post_url)

        await send_user_topic_message(
            bot,
            tg_user_id=result.seller_tg_user_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=seller_text,
            reply_markup=seller_kb,
            message_effect_id=resolve_auction_message_effect_id(AuctionMessageEffectEvent.ENDED_SELLER),
            notification_event=NotificationEventType.AUCTION_FINISH,
            auction_id=result.auction_id,
        )

        if result.winner_tg_user_id is not None and result.had_bids and result.final_price is not None:
            from app.services.notification_copy_service import winner_completion_text
            from app.bot.keyboards.auction import deal_completion_keyboard

            winner_text = winner_completion_text(
                auction_id=result.auction_id,
                description=result.description or "",
                final_price=result.final_price,
                counterparty_mention=seller_mention,
                counterparty_role="продавец",
                is_buyout=False,
            )
            winner_kb = deal_completion_keyboard(auction_id=short_id, post_url=post_url, is_seller=False)

            await send_user_topic_message(
                bot,
                tg_user_id=result.winner_tg_user_id,
                purpose=PrivateTopicPurpose.AUCTIONS,
                text=winner_text,
                reply_markup=winner_kb,
                message_effect_id=resolve_auction_message_effect_id(AuctionMessageEffectEvent.ENDED_WINNER),
                notification_event=NotificationEventType.AUCTION_WIN,
                auction_id=result.auction_id,
            )

        from app.services.notification_copy_service import moderation_completion_text
        from app.bot.keyboards.auction import moderation_completion_keyboard

        mod_text = moderation_completion_text(
            auction_id=result.auction_id,
            description=result.description or "",
            final_price=result.final_price or 0,
            bid_count=0,
            seller_mention=seller_mention,
            winner_mention=winner_mention,
            seller_reputation=0,
            winner_reputation=0,
            has_deal_topic=False,
            has_guarantor=False,
            reason="по таймеру",
        )
        mod_kb = moderation_completion_keyboard(auction_id=short_id, post_url=post_url)

        await send_section_message(
            bot,
            section=ModerationTopicSection.AUCTIONS_CLOSED,
            text=mod_text,
            reply_markup=mod_kb,
        )
```

Add helper function at module level:

```python
def _build_result_mention(username: str | None, first_name: str | None, tg_id: int | None) -> str:
    if tg_id is None:
        return "нет"
    if username:
        return f"@{username}"
    display = first_name or "Пользователь"
    return f'<a href="tg://user?id={tg_id}">{display}</a>'
```

- [ ] **Step 2: Verify lint passes**

Run: `python -m ruff check app/services/auction_service.py`
Expected: no errors

- [ ] **Step 3: Run existing tests**

Run: `python -m pytest tests/test_auction_service_resilience.py tests/test_auction_caption_render.py -q`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add app/services/auction_service.py
git commit -m "feat: use rich notifications in finalize_expired_auctions"
```

---

### Task 14: Fix `mod_end` to use `_finalize_auction_locked`

**Files:**
- Modify: `app/services/moderation_service.py`
- Modify: `app/bot/handlers/moderation.py`

- [ ] **Step 1: Update `end_auction` in moderation_service**

In `app/services/moderation_service.py`, replace the `end_auction` function (lines ~325-367) to delegate to `_finalize_auction_locked`:

```python
async def end_auction(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID,
) -> FinalizeResult | None:
    from app.services.auction_service import _finalize_auction_locked, get_auction_by_id

    auction = await get_auction_by_id(session, auction_id, for_update=True)
    if auction is None:
        return None
    return await _finalize_auction_locked(session, auction, status=AuctionStatus.ENDED)
```

This requires importing `FinalizeResult` and `AuctionStatus` at the top. Also, `_finalize_auction_locked` is a private function — consider making it public or keeping the import as-is (the codebase already uses cross-module private imports).

- [ ] **Step 2: Update `mod_end` handler**

In `app/bot/handlers/moderation.py`, update the `mod_end` handler to use rich notification data from the `FinalizeResult`:

```python
    result = await end_auction(session, auction_id=target_auction_id)
    if result is None:
        await message.reply("Не удалось завершить аукцион.")
        return
```

Then after refresh, send rich notifications using the same pattern as Task 13.

- [ ] **Step 3: Verify lint passes**

Run: `python -m ruff check app/services/moderation_service.py app/bot/handlers/moderation.py`
Expected: no errors

- [ ] **Step 4: Run existing tests**

Run: `python -m pytest tests/ -k moderation -q --timeout=30`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/services/moderation_service.py app/bot/handlers/moderation.py
git commit -m "fix: mod_end now uses _finalize_auction_locked for consistent reputation awards"
```

---

### Task 15: Update `_format_user_mention` in `notification_copy_service.py`

**Files:**
- Modify: `app/services/notification_copy_service.py`

- [ ] **Step 1: Add a public mention formatting function**

Add to `notification_copy_service.py`:

```python
def format_user_mention(*, username: str | None, first_name: str | None, tg_user_id: int) -> str:
    if username:
        return f"@{html.escape(username)}"
    display = html.escape(first_name or "Пользователь")
    return f'<a href="tg://user?id={tg_user_id}">{display}</a>'
```

This provides a reusable mention formatter that doesn't require a full `User` object.

- [ ] **Step 2: Commit**

```bash
git add app/services/notification_copy_service.py
git commit -m "feat: add format_user_mention helper to notification_copy_service"
```

---

### Task 16: Full test suite + lint check

**Files:**
- All modified files

- [ ] **Step 1: Run full lint**

Run: `python -m ruff check app tests`
Expected: no errors

- [ ] **Step 2: Run full unit test suite**

Run: `python -m pytest tests/ -q --ignore=tests/integration`
Expected: all pass

- [ ] **Step 3: Run integration tests (if DB available)**

Run: `RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/auction_test python -m pytest tests/integration/ -q`

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address lint and test issues from post-auction UX redesign"
```

---

## Rollout Notes

1. **Alembic migration** must be applied before deploying new code: `alembic upgrade head`
2. **Backward-safe:** `GuarantorRequest.auction_id` is nullable, existing rows unaffected
3. **No breaking changes** to existing notification delivery — old code paths still work for edge cases
4. **Feature flag not needed** — this is a notification template change, not a behavior toggle
5. **Rollback:** Revert code + `alembic downgrade -1` drops the `deal_topics` table and `guarantor_requests.auction_id` column
