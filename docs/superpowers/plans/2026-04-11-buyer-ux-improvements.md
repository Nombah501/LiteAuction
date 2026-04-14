# Buyer UX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 5 high-friction UX points for buyers: missing /help, no bid history, broken deal topic forwarding, gallery soft-gate, and invisible reputation.

**Architecture:** 5 independent tasks, each adding a new handler or modifying an existing one. All follow the established pattern: `Router(name="...")` in handler file, register in `app/bot/handlers/__init__.py`, unit tests with dummy classes and monkeypatched `SessionFactory`.

**Tech Stack:** Python 3.12, aiogram 3.x, SQLAlchemy async, pytest-asyncio

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `app/bot/handlers/deal_topic_chat.py` | Create | Deal topic message forwarding + close flow |
| `app/bot/handlers/help.py` | Create | `/help` command handler |
| `app/bot/handlers/my_bids.py` | Create | `/mybids` command + callback handler |
| `app/bot/handlers/my_reputation.py` | Create | `/myrep` command + callback handler |
| `app/bot/handlers/start.py` | Modify | Add gallery deep-link + onboarding message |
| `app/bot/handlers/bid_actions.py` | Modify | Gallery deep-link for non-private users |
| `app/bot/handlers/__init__.py` | Modify | Register 4 new routers |
| `app/bot/keyboards/auction.py` | Modify | Add dashboard buttons + deal close button |
| `app/services/auction_service.py` | Modify | Add reputation badge to caption |
| `tests/test_deal_topic_chat.py` | Create | Tests for deal forwarding + close |
| `tests/test_help_handler.py` | Create | Tests for /help |
| `tests/test_my_bids_handler.py` | Create | Tests for /mybids |
| `tests/test_my_reputation_handler.py` | Create | Tests for /myrep |
| `tests/test_gallery_deep_link.py` | Create | Tests for gallery deep-link |

---

### Task 1: Deal Topic Message Forwarding + Close Flow

**Files:**
- Create: `app/bot/handlers/deal_topic_chat.py`
- Modify: `app/bot/handlers/__init__.py`
- Modify: `app/bot/keyboards/auction.py`
- Create: `tests/test_deal_topic_chat.py`

- [ ] **Step 1: Write the failing test for message forwarding**

```python
# tests/test_deal_topic_chat.py
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.bot.handlers.deal_topic_chat import parse_close_payload


def test_parse_close_payload_valid() -> None:
    auction_id = uuid.uuid4()
    result = parse_close_payload(f"deal:close:{auction_id}")
    assert result == auction_id


def test_parse_close_payload_invalid() -> None:
    result = parse_close_payload("deal:close:not-a-uuid")
    assert result is None


def test_parse_close_payload_wrong_prefix() -> None:
    result = parse_close_payload("deal:write:abc")
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deal_topic_chat.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create the deal_topic_chat handler**

```python
# app/bot/handlers/deal_topic_chat.py
from __future__ import annotations

import uuid

from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
from aiogram.filters import Command
from sqlalchemy import select

from app.db.models import DealTopic, User, Auction
from app.db.enums import DealTopicStatus
from app.db.session import SessionFactory
from app.services.deal_topic_service import forward_deal_message, close_deal_topic
from app.services.notification_copy_service import format_user_mention
from app.bot.keyboards.auction import styled_button

router = Router(name="deal_topic_chat")


def parse_close_payload(data: str) -> uuid.UUID | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[1] != "close":
        return None
    try:
        return uuid.UUID(parts[2])
    except ValueError:
        return None


@router.message(F.chat.type == "private", F.message_thread_id)
async def handle_deal_topic_message(message: Message, bot: Bot) -> None:
    if message.from_user is None or message.message_thread_id is None:
        return
    if message.is_automatic_forward:
        return

    sender_tg_id = message.from_user.id
    thread_id = message.message_thread_id

    async with SessionFactory() as session:
        deal = await session.scalar(
            select(DealTopic).where(
                DealTopic.seller_topic_id == thread_id,
                DealTopic.status == DealTopicStatus.ACTIVE,
            )
        )

        if deal is None:
            deal = await session.scalar(
                select(DealTopic).where(
                    DealTopic.winner_topic_id == thread_id,
                    DealTopic.status == DealTopicStatus.ACTIVE,
                )
            )

        if deal is None:
            return

        is_seller_thread = deal.seller_topic_id == thread_id

        auction = await session.scalar(
            select(Auction).where(Auction.id == deal.auction_id)
        )
        if auction is None:
            return

        seller = await session.scalar(
            select(User).where(User.id == auction.seller_user_id)
        )
        winner = (
            await session.scalar(select(User).where(User.id == auction.winner_user_id))
            if auction.winner_user_id
            else None
        )

        if is_seller_thread:
            recipient_tg_id = winner.tg_user_id if winner else None
            recipient_topic_id = deal.winner_topic_id
            sender_label = format_user_mention(
                username=seller.username if seller else None,
                first_name=seller.first_name if seller else None,
                tg_user_id=seller.tg_user_id if seller else 0,
            )
        else:
            recipient_tg_id = seller.tg_user_id if seller else None
            recipient_topic_id = deal.seller_topic_id
            sender_label = format_user_mention(
                username=winner.username if winner else None,
                first_name=winner.first_name if winner else None,
                tg_user_id=winner.tg_user_id if winner else 0,
            )

        if recipient_tg_id is None or recipient_topic_id is None:
            await message.answer("Контрагент не найден.")
            return

    photo_file_id = None
    text = None
    if message.photo:
        photo_file_id = message.photo[-1].file_id
        text = message.caption
    elif message.text:
        text = message.text
    elif message.document:
        photo_file_id = message.document.thumb.file_id if message.document.thumb else None
        text = message.document.file_name
    elif message.voice:
        text = "🎤 Голосовое сообщение"
    else:
        await message.answer("Этот тип сообщения не поддерживается.")
        return

    await forward_deal_message(
        bot,
        deal=deal,
        sender_label=sender_label,
        recipient_tg_id=recipient_tg_id,
        recipient_topic_id=recipient_topic_id,
        text=text,
        photo_file_id=photo_file_id,
    )


@router.callback_query(F.data.startswith("deal:close:"))
async def handle_deal_close(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None:
        return

    auction_id = parse_close_payload(callback.data)
    if auction_id is None:
        await callback.answer("Некорректный запрос", show_alert=True)
        return

    async with SessionFactory() as session:
        async with session.begin():
            deal = await session.scalar(
                select(DealTopic).where(
                    DealTopic.auction_id == auction_id,
                    DealTopic.status == DealTopicStatus.ACTIVE,
                )
            )
            if deal is None:
                await callback.answer("Топик сделки не найден или уже закрыт", show_alert=True)
                return

            await close_deal_topic(session, deal=deal)

            short_id = str(auction_id)[:8]
            close_text = f"✅ Сделка #{short_id} закрыта.\nНе забудьте оставить отзыв — /tradefeedback"

            if deal.seller_topic_id:
                try:
                    await bot.send_message(
                        chat_id=callback.from_user.id,
                        message_thread_id=deal.seller_topic_id,
                        text=close_text,
                    )
                except Exception:
                    pass

            if deal.winner_topic_id:
                auction = await session.scalar(
                    select(Auction).where(Auction.id == auction_id)
                )
                if auction and auction.winner_user_id:
                    winner = await session.scalar(
                        select(User).where(User.id == auction.winner_user_id)
                    )
                    if winner:
                        try:
                            await bot.send_message(
                                chat_id=winner.tg_user_id,
                                message_thread_id=deal.winner_topic_id,
                                text=close_text,
                            )
                        except Exception:
                            pass

    await callback.answer("Сделка закрыта")


@router.callback_query(F.data.startswith("deal:closerequest:"))
async def handle_deal_close_request(callback: CallbackQuery) -> None:
    auction_id_str = callback.data.split(":")[-1]
    short_id = auction_id_str[:8]

    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(text="✅ Да, закрыть", callback_data=f"deal:close:{auction_id_str}"),
                styled_button(text="❌ Отмена", callback_data="deal:closecancel"),
            ]
        ]
    )
    await callback.message.answer(
        f"Вы уверены, что хотите закрыть сделку #{short_id}?",
        reply_markup=confirm_kb,
    )
    await callback.answer()


@router.callback_query(F.data == "deal:closecancel")
async def handle_deal_close_cancel(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_deal_topic_chat.py -v`
Expected: 3 passed

- [ ] **Step 5: Add close button to deal topic keyboard**

In `app/bot/keyboards/auction.py`, modify `deal_topic_keyboard` function. Find the existing function and add a close button row:

```python
# In deal_topic_keyboard, add this row before the return:
            [
                styled_button(text="🔒 Закрыть сделку", callback_data=f"deal:closerequest:{auction_id}"),
            ],
```

The full function becomes:

```python
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
            [
                styled_button(text="🔒 Закрыть сделку", callback_data=f"deal:closerequest:{auction_id}"),
            ],
        ]
    )
```

- [ ] **Step 6: Register the router**

In `app/bot/handlers/__init__.py`, add the import and registration:

```python
from .deal_topic_chat import router as deal_topic_chat_router
```

Add before `post_auction_router`:

```python
router.include_router(deal_topic_chat_router)
```

- [ ] **Step 7: Commit**

```bash
git add app/bot/handlers/deal_topic_chat.py app/bot/handlers/__init__.py app/bot/keyboards/auction.py tests/test_deal_topic_chat.py
git commit -m "feat: add deal topic message forwarding and close flow"
```

---

### Task 2: `/help` Command + First-Time Onboarding

**Files:**
- Create: `app/bot/handlers/help.py`
- Modify: `app/bot/handlers/__init__.py`
- Modify: `app/bot/handlers/start.py`
- Create: `tests/test_help_handler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_help_handler.py
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.bot.handlers.help import build_help_text, build_group_help_text


def test_build_help_text_contains_commands() -> None:
    text = build_help_text()
    assert "/mybids" in text
    assert "/points" in text
    assert "/myrep" in text
    assert "/guarant" in text


def test_build_help_text_mentions_bidding() -> None:
    text = build_help_text()
    assert "ставк" in text.lower() or "bid" in text.lower()


def test_build_group_help_text_is_shorter() -> None:
    group_text = build_group_help_text()
    full_text = build_help_text()
    assert len(group_text) < len(full_text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_help_handler.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create the help handler**

```python
# app/bot/handlers/help.py
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ChatType

router = Router(name="help")


def build_help_text() -> str:
    return (
        "📖 <b>Как работает LiteAuction</b>\n\n"
        "🔍 <b>Ставки</b> — нажимайте кнопки +/- под лотом в чате/канале\n"
        "🏆 <b>Победа</b> — если ваша ставка осталась верхней к финишу, вы выиграли\n"
        "💬 <b>Сделка</b> — бот создаст приватный топик для связи с продавцом\n"
        "🛡 <b>Гарант</b> — безопасная сделка через модератора\n"
        "⭐ <b>Отзыв</b> — оцените сделку после завершения\n\n"
        "<b>Команды:</b>\n"
        "/mybids — мои ставки и аукционы\n"
        "/points — баланс и бонусы\n"
        "/myrep — моя репутация\n"
        "/guarant — запросить гаранта\n"
        "/bug /suggest — сообщить о проблеме"
    )


def build_group_help_text() -> str:
    return (
        "📖 Полная справка доступна в личных сообщениях с ботом.\n"
        "Нажмите /help в приватном чате."
    )


@router.message(Command("help"), F.chat.type == ChatType.PRIVATE)
async def handle_help_private(message: Message) -> None:
    await message.answer(build_help_text(), parse_mode="HTML")


@router.message(Command("help"))
async def handle_help_group(message: Message) -> None:
    await message.answer(build_group_help_text())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_help_handler.py -v`
Expected: 3 passed

- [ ] **Step 5: Add onboarding message for new users in start.py**

In `app/bot/handlers/start.py`, after the dashboard message is sent in `handle_start_private`, add a check for new users. Find the section after `dashboard_keyboard` is built and the dashboard message is sent. Add after the existing dashboard send:

```python
    bid_count = await session.scalar(
        select(func.count()).select_from(Bid).where(Bid.user_id == user.id)
    )
    auction_count = await session.scalar(
        select(func.count()).select_from(Auction).where(Auction.seller_user_id == user.id)
    )
    if bid_count == 0 and auction_count == 0:
        await message.answer(
            "👋 <b>Добро пожаловать!</b>\n\n"
            "Вы можете:\n"
            "• Искать лоты в подключённых чатах и каналах\n"
            "• Делать ставки кнопками под лотом\n"
            "• Следить за своими ставками — /mybids\n\n"
            "Начните с просмотра активных аукционов в чате!",
            parse_mode="HTML",
        )
```

Add the needed imports at the top of `start.py`:

```python
from sqlalchemy import func
from app.db.models import Bid, Auction
```

Note: Only add these imports if they are not already present in the file.

- [ ] **Step 6: Register the help router**

In `app/bot/handlers/__init__.py`, add:

```python
from .help import router as help_router
```

Register after `start_router`:

```python
router.include_router(help_router)
```

- [ ] **Step 7: Commit**

```bash
git add app/bot/handlers/help.py app/bot/handlers/__init__.py app/bot/handlers/start.py tests/test_help_handler.py
git commit -m "feat: add /help command and first-time user onboarding"
```

---

### Task 3: `/mybids` — Buyer Bid History

**Files:**
- Create: `app/bot/handlers/my_bids.py`
- Modify: `app/bot/handlers/__init__.py`
- Modify: `app/bot/keyboards/auction.py`
- Create: `tests/test_my_bids_handler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_my_bids_handler.py
from __future__ import annotations

import uuid

from app.bot.handlers.my_bids import parse_mybids_page_payload, format_time_left
from datetime import datetime, timezone, timedelta


def test_parse_mybids_page_payload_valid() -> None:
    result = parse_mybids_page_payload("mybids:page:3")
    assert result == 3


def test_parse_mybids_page_payload_default() -> None:
    result = parse_mybids_page_payload("mybids:page:0")
    assert result == 0


def test_parse_mybids_page_payload_invalid() -> None:
    result = parse_mybids_page_payload("mybids:page:abc")
    assert result == 0


def test_format_time_left_hours() -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=2, minutes=30)
    result = format_time_left(future)
    assert "2ч" in result
    assert "30м" in result


def test_format_time_left_minutes_only() -> None:
    future = datetime.now(timezone.utc) + timedelta(minutes=45)
    result = format_time_left(future)
    assert "45м" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_my_bids_handler.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create the my_bids handler**

```python
# app/bot/handlers/my_bids.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
from aiogram.filters import Command
from sqlalchemy import select, func, desc

from app.db.models import Bid, Auction, User, AuctionPost
from app.db.enums import AuctionStatus
from app.db.session import SessionFactory
from app.bot.keyboards.auction import styled_button

router = Router(name="my_bids")

PAGE_SIZE = 5


def parse_mybids_page_payload(data: str) -> int:
    parts = data.split(":")
    if len(parts) != 3:
        return 0
    try:
        return int(parts[2])
    except ValueError:
        return 0


def format_time_left(ends_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = ends_at - now
    if delta.total_seconds() <= 0:
        return "скоро"
    total_seconds = int(delta.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


def _mybids_list_keyboard(
    *,
    page: int,
    has_prev: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    nav_row: list = []
    if has_prev:
        nav_row.append(styled_button(text="<-", callback_data=f"mybids:page:{page - 1}"))
    nav_row.append(styled_button(text=f"Стр. {page + 1}", callback_data=f"mybids:page:{page}"))
    if has_next:
        nav_row.append(styled_button(text="->", callback_data=f"mybids:page:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[nav_row])


@router.message(Command("mybids"))
async def handle_mybids_command(message: Message) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.tg_user_id == message.from_user.id)
        )
        if user is None:
            await message.answer("Сначала откройте бота через /start")
            return

        text = await _build_mybids_text(session, user.id, page=0)
        kb = await _build_mybids_keyboard(session, user.id, page=0)

    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("mybids:page:"))
async def handle_mybids_page(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return

    page = parse_mybids_page_payload(callback.data)

    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.tg_user_id == callback.from_user.id)
        )
        if user is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        text = await _build_mybids_text(session, user.id, page=page)
        kb = await _build_mybids_keyboard(session, user.id, page=page)

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


async def _build_mybids_text(session, user_id: int, page: int) -> str:
    active_statuses = {AuctionStatus.ACTIVE, AuctionStatus.FROZEN}
    finished_statuses = {AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}

    latest_bid_subq = (
        select(Bid.auction_id, func.max(Bid.created_at).label("last_bid_at"))
        .where(Bid.user_id == user_id, Bid.is_removed == False)
        .group_by(Bid.auction_id)
        .subquery()
    )

    active_query = (
        select(Auction, latest_bid_subq.c.last_bid_at)
        .join(latest_bid_subq, Auction.id == latest_bid_subq.c.auction_id)
        .where(Auction.status.in_(active_statuses))
        .order_by(Auction.ends_at.asc())
    )
    active_result = (await session.execute(active_query)).all()

    finished_query = (
        select(Auction, latest_bid_subq.c.last_bid_at)
        .join(latest_bid_subq, Auction.id == latest_bid_subq.c.auction_id)
        .where(Auction.status.in_(finished_statuses))
        .order_by(desc(latest_bid_subq.c.last_bid_at))
    )
    finished_result = (await session.execute(finished_query)).all()

    lines: list[str] = []

    if active_result:
        lines.append(f"🔥 <b>Активные ставки ({len(active_result)})</b>\n")
        for auction, _last_bid_at in active_result:
            short_id = str(auction.id)[:8]
            desc = auction.description[:40] + ("..." if len(auction.description) > 40 else "")

            top_bid = await session.scalar(
                select(func.max(Bid.amount))
                .where(Bid.auction_id == auction.id, Bid.is_removed == False)
            )
            my_max = await session.scalar(
                select(func.max(Bid.amount))
                .where(Bid.auction_id == auction.id, Bid.user_id == user_id, Bid.is_removed == False)
            )

            is_top = my_max is not None and my_max >= (top_bid or 0)
            status_icon = "✅ ТОП" if is_top else "⚠️ Перебили"

            time_str = format_time_left(auction.ends_at) if auction.ends_at else "—"

            lines.append(f"Лот #{short_id} • {desc}")
            lines.append(f"💰 Ваша ставка: ${my_max} ({status_icon}) | До финиша: {time_str}")
            lines.append("")

    if finished_result:
        lines.append(f"✅ <b>Завершённые ({len(finished_result)})</b>")
        for auction, _last_bid_at in finished_result[:5]:
            short_id = str(auction.id)[:8]
            desc = auction.description[:40] + ("..." if len(auction.description) > 40 else "")

            won = auction.winner_user_id == user_id
            result_text = "ВЫИГРАЛИ" if won else "Проиграли"

            lines.append(f"Лот #{short_id} • {desc} — {result_text}")

    if not active_result and not finished_result:
        lines.append("У вас пока нет ставок.")
        lines.append("Ищите лоты в подключённых чатах и каналах!")

    return "\n".join(lines)


async def _build_mybids_keyboard(session, user_id: int, page: int) -> InlineKeyboardMarkup:
    total = await session.scalar(
        select(func.count(func.distinct(Bid.auction_id)))
        .where(Bid.user_id == user_id, Bid.is_removed == False)
    )
    total = total or 0
    has_prev = page > 0
    has_next = (page + 1) * PAGE_SIZE < total

    if not has_prev and not has_next:
        return InlineKeyboardMarkup(inline_keyboard=[])

    return _mybids_list_keyboard(page=page, has_prev=has_prev, has_next=has_next)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_my_bids_handler.py -v`
Expected: 5 passed

- [ ] **Step 5: Add "Мои ставки" button to dashboard keyboard**

In `app/bot/keyboards/auction.py`, modify `start_private_keyboard`. Add a new row after the "Мои лоты" row:

```python
        [
            styled_button(
                text="Мои ставки",
                callback_data="dash:my_bids",
            ),
        ],
```

The full keyboard becomes:

```python
def start_private_keyboard(*, show_moderation_button: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            styled_button(
                text="Создать лот",
                callback_data="create:new",
                style="primary",
                icon_custom_emoji_id=_icon(settings.ui_emoji_create_auction_id),
            )
        ],
        [
            styled_button(
                text="Мои лоты",
                callback_data="dash:my_auctions",
                style="primary",
            ),
        ],
        [
            styled_button(
                text="Мои ставки",
                callback_data="dash:my_bids",
            ),
        ],
        [
            styled_button(
                text="Гарант",
                callback_data="dash:guarant",
            ),
            styled_button(
                text="Уведомления",
                callback_data="dash:notifications",
            ),
        ],
        [
            styled_button(
                text="Настройки",
                callback_data="dash:settings",
            )
        ],
    ]

    if show_moderation_button:
        rows.append(
            [
                styled_button(
                    text="Модерация",
                    callback_data="mod:panel",
                    style="success",
                    icon_custom_emoji_id=_icon(settings.ui_emoji_mod_panel_id),
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)
```

- [ ] **Step 6: Add `dash:my_bids` callback redirect in start.py**

In `app/bot/handlers/start.py`, add a callback handler that redirects `dash:my_bids` to the mybids handler. Find the existing dashboard callback handlers and add:

```python
@router.callback_query(F.data == "dash:my_bids")
async def callback_dashboard_my_bids(callback: CallbackQuery) -> None:
    await handle_mybids_command_from_callback(callback)
```

This requires importing and creating a wrapper. Instead, the simplest approach is to add in `my_bids.py`:

```python
@router.callback_query(F.data == "dash:my_bids")
async def handle_mybids_dashboard_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return

    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.tg_user_id == callback.from_user.id)
        )
        if user is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        text = await _build_mybids_text(session, user.id, page=0)
        kb = await _build_mybids_keyboard(session, user.id, page=0)

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()
```

- [ ] **Step 7: Register the router**

In `app/bot/handlers/__init__.py`, add:

```python
from .my_bids import router as my_bids_router
```

Register after `help_router`:

```python
router.include_router(my_bids_router)
```

- [ ] **Step 8: Commit**

```bash
git add app/bot/handlers/my_bids.py app/bot/handlers/__init__.py app/bot/keyboards/auction.py tests/test_my_bids_handler.py
git commit -m "feat: add /mybids command for buyer bid history"
```

---

### Task 4: Gallery Deep-Link for Non-Private Users

**Files:**
- Modify: `app/bot/handlers/bid_actions.py`
- Modify: `app/bot/handlers/start.py`
- Create: `tests/test_gallery_deep_link.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gallery_deep_link.py
from __future__ import annotations

import uuid

from app.bot.handlers.start import _extract_gallery_auction_id


def test_extract_gallery_auction_id_valid() -> None:
    uid = uuid.uuid4()
    result = _extract_gallery_auction_id(f"gallery_{uid}")
    assert result == uid


def test_extract_gallery_auction_id_invalid() -> None:
    result = _extract_gallery_auction_id("gallery_not-a-uuid")
    assert result is None


def test_extract_gallery_auction_id_wrong_prefix() -> None:
    result = _extract_gallery_auction_id("report_abc")
    assert result is None


def test_extract_gallery_auction_id_none() -> None:
    result = _extract_gallery_auction_id(None)
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gallery_deep_link.py -v`
Expected: FAIL — name not defined

- [ ] **Step 3: Add gallery deep-link parser to start.py**

In `app/bot/handlers/start.py`, add the new parser function near the existing `_extract_report_auction_id`:

```python
def _extract_gallery_auction_id(payload: str | None) -> uuid.UUID | None:
    if payload is None or not payload.startswith("gallery_"):
        return None
    auction_raw = payload[len("gallery_"):].strip()
    if not auction_raw:
        return None
    try:
        return uuid.UUID(auction_raw)
    except ValueError:
        return None
```

- [ ] **Step 4: Add gallery handling in handle_start_private**

In `app/bot/handlers/start.py`, inside `handle_start_private`, after the appeal deep-link block and before the dashboard keyboard is sent, add gallery deep-link handling:

```python
    gallery_auction_id = _extract_gallery_auction_id(payload)
    if gallery_auction_id is not None:
        async with SessionFactory() as g_session:
            from app.services.auction_service import load_auction_view, load_auction_photo_ids
            g_view = await load_auction_view(g_session, gallery_auction_id)
            if g_view is not None:
                g_photos = await load_auction_photo_ids(g_session, gallery_auction_id)
                if not g_photos:
                    g_photos = [g_view.auction.photo_file_id]
                caption = f"📸 Фото лота #{str(gallery_auction_id)[:8]}"
                if len(g_photos) == 1:
                    await bot.send_photo(
                        chat_id=message.from_user.id,
                        photo=g_photos[0],
                        caption=caption,
                    )
                else:
                    from aiogram.types import InputMediaPhoto
                    for chunk_start in range(0, len(g_photos), 10):
                        chunk = g_photos[chunk_start:chunk_start + 10]
                        media = [
                            InputMediaPhoto(
                                media=file_id,
                                caption=caption if chunk_start == 0 and idx == 0 else None,
                            )
                            for idx, file_id in enumerate(chunk)
                        ]
                        await bot.send_media_group(
                            chat_id=message.from_user.id,
                            media=media,
                        )
```

- [ ] **Step 5: Modify gallery handler for non-private users**

In `app/bot/handlers/bid_actions.py`, modify `handle_gallery_action`. Replace the `TelegramForbiddenError` handler with a deep-link button:

Find this block:
```python
    except TelegramForbiddenError:
        await callback.answer(_soft_gate_alert_text(), show_alert=True)
        return
```

Replace with:
```python
    except TelegramForbiddenError:
        from aiogram.types import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
        from app.config import settings
        bot_username = (await bot.get_me()).username
        deep_link = f"https://t.me/{bot_username}?start=gallery_{auction_id}"
        await callback.message.answer(
            "📸 Для просмотра фото откройте бота:",
            reply_markup=IKM(
                inline_keyboard=[
                    [IKB(text="📸 Смотреть все фото", url=deep_link)]
                ]
            ),
        )
        await callback.answer()
        return
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_gallery_deep_link.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add app/bot/handlers/start.py app/bot/handlers/bid_actions.py tests/test_gallery_deep_link.py
git commit -m "feat: gallery deep-link for non-private users"
```

---

### Task 5: `/myrep` — User Reputation Display

**Files:**
- Create: `app/bot/handlers/my_reputation.py`
- Modify: `app/bot/handlers/__init__.py`
- Modify: `app/bot/keyboards/auction.py`
- Modify: `app/services/auction_service.py`
- Create: `tests/test_my_reputation_handler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_my_reputation_handler.py
from __future__ import annotations

from app.bot.handlers.my_reputation import format_tier_badge, build_reputation_text
from app.db.enums import ReputationTier


def test_format_tier_badge_new() -> None:
    assert format_tier_badge(ReputationTier.NEW) == ""


def test_format_tier_badge_bronze() -> None:
    assert "🥉" in format_tier_badge(ReputationTier.BRONZE)


def test_format_tier_badge_silver() -> None:
    assert "🥈" in format_tier_badge(ReputationTier.SILVER)


def test_format_tier_badge_gold() -> None:
    assert "🥇" in format_tier_badge(ReputationTier.GOLD)


def test_format_tier_badge_platinum() -> None:
    assert "💎" in format_tier_badge(ReputationTier.PLATINUM)


def test_build_reputation_text_shows_score() -> None:
    text = build_reputation_text(
        tier=ReputationTier.GOLD,
        score=1250,
        deals_count=18,
        feedback_received=15,
        avg_rating=4.7,
        feedback_given=12,
        next_tier=ReputationTier.PLATINUM,
        next_tier_threshold=3000,
    )
    assert "1250" in text
    assert "GOLD" in text
    assert "18" in text
    assert "1750" in text


def test_build_reputation_text_max_tier() -> None:
    text = build_reputation_text(
        tier=ReputationTier.PLATINUM,
        score=5000,
        deals_count=100,
        feedback_received=90,
        avg_rating=4.9,
        feedback_given=80,
        next_tier=None,
        next_tier_threshold=None,
    )
    assert "PLATINUM" in text
    assert "Максимальный" in text or "максимальн" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_my_reputation_handler.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create the my_reputation handler**

```python
# app/bot/handlers/my_reputation.py
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from sqlalchemy import select, func

from app.db.models import UserReputation, UserReputationEvent, TradeFeedback, User, Auction
from app.db.enums import ReputationTier, AuctionStatus
from app.db.session import SessionFactory

router = Router(name="my_reputation")

TIER_THRESHOLDS: dict[ReputationTier, int] = {
    ReputationTier.NEW: 0,
    ReputationTier.BRONZE: 100,
    ReputationTier.SILVER: 500,
    ReputationTier.GOLD: 1000,
    ReputationTier.PLATINUM: 3000,
}

TIER_ORDER = [
    ReputationTier.NEW,
    ReputationTier.BRONZE,
    ReputationTier.SILVER,
    ReputationTier.GOLD,
    ReputationTier.PLATINUM,
]


def format_tier_badge(tier: ReputationTier) -> str:
    badges = {
        ReputationTier.NEW: "",
        ReputationTier.BRONZE: "🥉 BRONZE",
        ReputationTier.SILVER: "🥈 SILVER",
        ReputationTier.GOLD: "🥇 GOLD",
        ReputationTier.PLATINUM: "💎 PLATINUM",
    }
    return badges.get(tier, "")


def build_reputation_text(
    *,
    tier: ReputationTier,
    score: int,
    deals_count: int,
    feedback_received: int,
    avg_rating: float | None,
    feedback_given: int,
    next_tier: ReputationTier | None,
    next_tier_threshold: int | None,
) -> str:
    badge = format_tier_badge(tier)
    tier_display = f"{badge}" if badge else tier.value

    lines = [
        f"⭐ <b>Ваша репутация: {tier_display}</b>",
        f"Баллы: <b>{score}</b>",
        "",
        f"Завершённых сделок: {deals_count}",
        f"Отзывов получено: {feedback_received}",
    ]

    if avg_rating is not None:
        lines.append(f"Средняя оценка: {avg_rating:.1f}⭐")

    lines.append(f"Отзывов оставлено: {feedback_given}")

    if next_tier is not None and next_tier_threshold is not None:
        remaining = next_tier_threshold - score
        lines.extend([
            "",
            f"Следующий tier: {format_tier_badge(next_tier)} ({next_tier_threshold} баллов)",
            f"Осталось: {remaining}",
        ])
    elif tier == ReputationTier.PLATINUM:
        lines.extend(["", "🏆 Максимальный tier достигнут!"])

    return "\n".join(lines)


@router.message(Command("myrep"))
async def handle_myrep_command(message: Message) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.tg_user_id == message.from_user.id)
        )
        if user is None:
            await message.answer("Сначала откройте бота через /start")
            return

        text = await _build_myrep_text(session, user.id)

    await message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "dash:reputation")
async def handle_myrep_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return

    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.tg_user_id == callback.from_user.id)
        )
        if user is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        text = await _build_myrep_text(session, user.id)

    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


async def _build_myrep_text(session, user_id: int) -> str:
    rep = await session.scalar(
        select(UserReputation).where(UserReputation.user_id == user_id)
    )

    if rep is None:
        return (
            "⭐ <b>Ваша репутация: NEW</b>\n"
            "Баллы: 0\n\n"
            "Пока нет данных. Совершайте сделки и оставляйте отзывы!"
        )

    finished_statuses = {AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}
    deals_count = await session.scalar(
        select(func.count())
        .select_from(Auction)
        .where(
            Auction.winner_user_id == user_id,
            Auction.status.in_(finished_statuses),
        )
    ) or 0

    feedback_received = await session.scalar(
        select(func.count())
        .select_from(TradeFeedback)
        .where(TradeFeedback.target_user_id == user_id)
    ) or 0

    avg_rating = await session.scalar(
        select(func.avg(TradeFeedback.rating))
        .where(TradeFeedback.target_user_id == user_id)
    )

    feedback_given = await session.scalar(
        select(func.count())
        .select_from(TradeFeedback)
        .where(TradeFeedback.author_user_id == user_id)
    ) or 0

    current_tier = ReputationTier(rep.tier)
    current_idx = TIER_ORDER.index(current_tier)
    next_tier = TIER_ORDER[current_idx + 1] if current_idx + 1 < len(TIER_ORDER) else None
    next_threshold = TIER_THRESHOLDS.get(next_tier) if next_tier else None

    recent_events = (await session.execute(
        select(UserReputationEvent)
        .where(UserReputationEvent.user_id == user_id)
        .order_by(UserReputationEvent.created_at.desc())
        .limit(5)
    )).scalars().all()

    text = build_reputation_text(
        tier=current_tier,
        score=rep.score,
        deals_count=deals_count,
        feedback_received=feedback_received,
        avg_rating=float(avg_rating) if avg_rating else None,
        feedback_given=feedback_given,
        next_tier=next_tier,
        next_tier_threshold=next_threshold,
    )

    if recent_events:
        text += "\n\n📊 <b>Последние события:</b>"
        for event in recent_events:
            delta_str = f"+{event.delta}" if event.delta > 0 else str(event.delta)
            text += f"\n{delta_str} — {event.reason}"

    return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_my_reputation_handler.py -v`
Expected: 7 passed

- [ ] **Step 5: Add reputation button to dashboard keyboard**

In `app/bot/keyboards/auction.py`, modify `start_private_keyboard`. Add a new row for reputation alongside settings:

```python
        [
            styled_button(
                text="Репутация",
                callback_data="dash:reputation",
            ),
            styled_button(
                text="Настройки",
                callback_data="dash:settings",
            ),
        ],
```

- [ ] **Step 6: Add reputation badge to auction caption**

In `app/services/auction_service.py`, modify `render_auction_caption`. Import the badge formatter:

```python
from app.db.enums import ReputationTier
```

Find the seller mention line:
```python
f"👤 Продавец: {_format_user_mention(view.seller)}",
```

Replace with:
```python
        seller_badge = ""
        if hasattr(view, 'seller_reputation_tier') and view.seller_reputation_tier:
            from app.bot.handlers.my_reputation import format_tier_badge
            badge = format_tier_badge(ReputationTier(view.seller_reputation_tier))
            if badge:
                seller_badge = f" [{badge}]"
        f"👤 Продавец: {_format_user_mention(view.seller)}{seller_badge}",
```

Note: This requires `AuctionView` to carry the seller's reputation tier. If `AuctionView` is a NamedTuple/dataclass, add `seller_reputation_tier: str | None = None` to it. If the view loads the seller's user record already, the reputation tier can be joined in the query. This step may require a small modification to `load_auction_view` in `auction_service.py` to join `UserReputation`.

A simpler alternative that avoids modifying `AuctionView`: add the badge only when rendering, by fetching reputation inline:

```python
        seller_badge = ""
        if view.seller and hasattr(view.seller, 'reputation_tier'):
            badge = format_tier_badge(ReputationTier(view.seller.reputation_tier))
            if badge:
                seller_badge = f" [{badge}]"
```

This assumes the `User` model or view's seller object carries the tier. If not, skip the badge in caption for now and only add the `/myrep` command. The badge can be added in a follow-up PR when `AuctionView` is extended.

- [ ] **Step 7: Register the router**

In `app/bot/handlers/__init__.py`, add:

```python
from .my_reputation import router as my_reputation_router
```

Register after `my_bids_router`:

```python
router.include_router(my_reputation_router)
```

- [ ] **Step 8: Commit**

```bash
git add app/bot/handlers/my_reputation.py app/bot/handlers/__init__.py app/bot/keyboards/auction.py app/services/auction_service.py tests/test_my_reputation_handler.py
git commit -m "feat: add /myrep command for user reputation display"
```

---

### Task 6: Lint + Full Test Suite Validation

- [ ] **Step 1: Run ruff check**

Run: `python -m ruff check app tests`
Expected: No errors

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest -q tests`
Expected: All tests pass

- [ ] **Step 3: Fix any lint or test failures and re-run**

If ruff reports issues, fix them. If tests fail, investigate and fix. Re-run both commands until clean.
