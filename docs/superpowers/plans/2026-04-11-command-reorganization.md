# Command Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize bot commands into a hub-and-spoke model with `/start` as the main navigation hub, hiding secondary commands from Telegram menu while keeping all handlers functional.

**Architecture:** Expand the existing `start_private_keyboard` with new buttons (Гарант, Уведомления), add 2 new callback handlers in `start.py`, and update Telegram bot menu via `set_my_commands`. No deletions.

**Tech Stack:** Python 3.12, aiogram 3.x, existing codebase patterns.

---

### Task 1: Update `start_private_keyboard` — add buttons, remove Баланс

**Files:**
- Modify: `app/bot/keyboards/auction.py:48-91`

- [ ] **Step 1: Edit `start_private_keyboard` function**

Replace the function body with:

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
            )
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

Key changes:
- "Создать аукцион" → "Создать лот"
- "Мои аукционы" → "Мои лоты"
- "Баланс" button removed entirely
- "Гарант" button added (`dash:guarant`)
- "Уведомления" button added (`dash:notifications`)
- "Мод-панель" → "Модерация"
- Гарант + Уведомления on same row (2 buttons per row)

- [ ] **Step 2: Verify it compiles**

Run: `docker compose exec bot python -c "from app.bot.keyboards.auction import start_private_keyboard; kb = start_private_keyboard(show_moderation_button=False); print('OK:', len(kb.inline_keyboard), 'rows')"`

Expected: `OK: 4 rows` (5 rows with `show_moderation_button=True`)

- [ ] **Step 3: Commit**

```bash
git add app/bot/keyboards/auction.py
git commit -m "feat: reorganize start keyboard — add guarant/notifications, remove balance"
```

---

### Task 2: Add `dash:guarant` callback handler

**Files:**
- Modify: `app/bot/handlers/start.py` (add new callback handler after `dash:home` handler around line 988)

This callback reuses the guarantor intake flow. When user taps "Гарант", it starts the same FSM as `/guarant`.

- [ ] **Step 1: Add the callback handler**

Insert after the `callback_dashboard_home` handler (after line 988):

```python
@router.callback_query(F.data == "dash:guarant")
async def callback_dashboard_guarant(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if callback.from_user is None:
        return
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("Не удалось открыть раздел «Гарант»", show_alert=True)
        return

    await callback.answer()

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            if not await enforce_callback_topic(
                callback,
                bot=bot,
                session=session,
                user=user,
                purpose=PrivateTopicPurpose.SUPPORT,
                command_hint="/guarant",
            ):
                return

    await state.set_state(GuarantorIntakeStates.waiting_request_text)
    if isinstance(callback.message.message_thread_id, int):
        await state.update_data(expected_thread_id=callback.message.message_thread_id)
    await callback.message.answer("Опишите запрос на гаранта одним сообщением. Для отмены используйте /cancel")
```

- [ ] **Step 2: Add required imports at top of `start.py`**

Add to the existing imports:

```python
from aiogram.fsm.context import FSMContext
from app.bot.states.guarantor_intake import GuarantorIntakeStates
```

Note: `enforce_callback_topic` and `PrivateTopicPurpose` are already imported. `SessionFactory`, `upsert_user` are already imported.

- [ ] **Step 3: Verify it compiles**

Run: `docker compose exec bot python -c "from app.bot.handlers.start import router; print('OK')"`

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app/bot/handlers/start.py
git commit -m "feat: add dash:guarant callback handler for hub navigation"
```

---

### Task 3: Add `dash:notifications` callback handler

**Files:**
- Modify: `app/bot/handlers/start.py` (add after the new guarant handler)

This callback shows the notification settings card — same view as `dash:settings` but focused only on notifications.

- [ ] **Step 1: Add the callback handler**

```python
@router.callback_query(F.data == "dash:notifications")
async def callback_dashboard_notifications(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        return
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("Не удалось открыть уведомления", show_alert=True)
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            snapshot = await load_notification_settings(session, user_id=user.id)
            snoozes = await list_active_auction_notification_snoozes(session, user_id=user.id)

    if snapshot is None:
        await callback.answer("Настройки уведомлений недоступны", show_alert=True)
        return

    text = _render_settings_text(snapshot, snoozes=snoozes)
    keyboard = _settings_keyboard(snapshot, snoozes=snoozes)
    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
        return
    except TelegramBadRequest:
        pass

    await callback.message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)
```

Note: All imports (`_render_settings_text`, `_settings_keyboard`, `load_notification_settings`, `list_active_auction_notification_snoozes`, `TelegramBadRequest`) are already present in the file.

- [ ] **Step 2: Verify it compiles**

Run: `docker compose exec bot python -c "from app.bot.handlers.start import router; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/bot/handlers/start.py
git commit -m "feat: add dash:notifications callback handler for hub navigation"
```

---

### Task 4: Update Telegram bot menu commands via `set_my_commands`

**Files:**
- Modify: `app/bot/handlers/start.py` or add a startup hook

The bot should register only 5 commands in its Telegram menu. This is best done at startup.

- [ ] **Step 1: Check if there is an existing startup hook or `set_my_commands` call**

Search for `set_my_commands` in the codebase. If found, modify it. If not, add it.

Run: `grep -rn "set_my_commands" app/`

- [ ] **Step 2: Add or update `set_my_commands` in startup**

Find the bot startup location (likely `app/bot/__init__.py` or `app/main.py`). Add this after bot initialization:

```python
from aiogram.types import BotCommand

await bot.set_my_commands([
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="newauction", description="Создать лот"),
    BotCommand(command="guarant", description="Запрос гаранта"),
    BotCommand(command="publish", description="Опубликовать лот (в чате торговли)"),
    BotCommand(command="cancel", description="Отменить действие"),
])
```

For group/supergroup scope, register only `/publish`:

```python
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats

await bot.set_my_commands(
    [
        BotCommand(command="publish", description="Опубликовать лот"),
    ],
    scope=BotCommandScopeAllGroupChats(),
)
```

- [ ] **Step 3: Verify commands registered**

Run: `docker compose exec bot python -c "import asyncio; from app.config import settings; from aiogram import Bot; from aiogram.types import BotCommand; bot = Bot(token=settings.bot_token); cmds = asyncio.run(bot.get_my_commands()); print([c.command for c in cmds])"`

Expected: `['start', 'newauction', 'guarant', 'publish', 'cancel']`

- [ ] **Step 4: Commit**

```bash
git add app/
git commit -m "feat: register only hub commands in Telegram menu via set_my_commands"
```

---

### Task 5: End-to-end verification

- [ ] **Step 1: Restart bot**

Run: `docker compose restart bot`

- [ ] **Step 2: Check logs for clean startup**

Run: `docker compose logs bot --tail=20`

Expected: No errors, "Start polling" message visible.

- [ ] **Step 3: Verify keyboard structure**

Run: `docker compose exec bot python -c "
from app.bot.keyboards.auction import start_private_keyboard
kb = start_private_keyboard(show_moderation_button=False)
for row in kb.inline_keyboard:
    for btn in row:
        print(f'{btn.text} -> {btn.callback_data}')
print('---')
kb_mod = start_private_keyboard(show_moderation_button=True)
for row in kb_mod.inline_keyboard:
    for btn in row:
        print(f'{btn.text} -> {btn.callback_data}')
"`

Expected (without mod):
```
Создать лот -> create:new
Мои лоты -> dash:my_auctions
Гарант -> dash:guarant | Уведомления -> dash:notifications
Настройки -> dash:settings
```

Expected (with mod): same + `Модерация -> mod:panel`

- [ ] **Step 4: Final commit (if any remaining fixes)**

```bash
git add -A
git commit -m "chore: finalize command reorganization"
```
