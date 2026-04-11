# Command Reorganization: Hub-and-Spoke Design

**Date:** 2026-04-11
**Status:** Approved
**Scope:** Slash commands in private chat + Telegram menu

## Problem

~30+ slash commands in private chat create chaos. Users see a long command list in Telegram menu with irrelevant options (emoji tools, feedback, trade feedback, points). Moderators have 20+ commands that clutter the interface. The bot is pivoting to focus on PC hardware auctions published via `/publish` in a single trading chat.

## Decision: Hub-and-Spoke

Approach A (Hub-and-Spoke): `/start` is the single navigation hub. All user actions accessible via inline buttons. Minimal commands registered in Telegram menu.

## Section 1: Command Map

### Public commands in Telegram menu (4 commands)

| Command | Where | Purpose |
|---|---|---|
| `/start` | Private chat | Main hub: inline buttons for all actions |
| `/newauction` | Private, supergroup | Shortcut to create auction |
| `/guarant` | Private chat | Shortcut to guarantor request |
| `/publish` | Group, supergroup | Publish lot to trading chat |
| `/cancel` | Private, supergroup | Cancel current FSM action |

### Hidden commands (handlers stay, removed from menu)

All existing command handlers remain functional. Users who know the commands can type them manually.

**User commands hidden from menu:**
- `/points` — points system accessible but not promoted
- `/bug`, `/suggest`, `/boostfeedback` — feedback accessible but not promoted
- `/boostappeal` — appeal boost accessible but not promoted
- `/tradefeedback` — trade feedback accessible but not promoted
- `/emojiid`, `/effectid` — emoji tools accessible but not promoted
- `/topics`, `/settings` — replaced by buttons in `/start` hub

**Moderator commands (already restricted to mod-chat topics):**
- All 20+ mod commands (`/freeze`, `/ban`, `/end`, `/modpanel`, `/modstats`, etc.) remain as hidden shortcuts for experienced moderators
- `/modpanel` also accessible via button in `/start` for moderators

### Commands NOT removed

No handlers are deleted. No service files are touched. All existing functionality remains operational.

## Section 2: `/start` Hub Structure

### Main menu keyboard

```
+---------------------+
|  📦 Создать лот       |  -> callback: create:new
+---------------------+
|  📋 Мои лоты          |  -> callback: dash:my_auctions
+---------------------+
|  🛡 Гарант            |  -> callback: dash:guarant
|  🔔 Уведомления       |  -> callback: dash:notifications
|  ⚙️ Настройки          |  -> callback: dash:settings
+---------------------+
|  🛠 Модерация          |  -> callback: mod:panel  (moderators only)
+---------------------+
```

### Button behaviors

- **Создать лот** (`create:new`) — existing handler, starts auction creation FSM
- **Мои лоты** (`dash:my_auctions`) — existing handler, shows seller's auction list with statuses
- **Гарант** (`dash:guarant`) — NEW callback, triggers guarantor request flow (same as `/guarant`)
- **Уведомления** (`dash:notifications`) — NEW callback, shows notification settings (quiet hours, snooze, event toggles)
- **Настройки** (`dash:settings`) — existing callback, shows topic management and settings
- **Модерация** (`mod:panel`) — existing callback, only shown when `show_moderation_button=True`

### Removed button

- **Баланс** (`dash:balance`) — removed from main menu keyboard. Points system not promoted.

### Sub-menu navigation

All sub-menus use callback buttons with "← Назад" to return to the previous level.

- **Уведомления** sub-menu: current notification settings, quiet hours toggle, snooze, per-event toggles. Reuses logic from existing `/settings` and notification callback handlers.
- **Гарант** sub-menu: submit request, view current request status. Reuses logic from `handlers/guarantor.py`.
- **Настройки** sub-menu: private topics (on/off), topic management. Reuses logic from existing `/topics` and `/settings` handlers.

## Section 3: Implementation Scope

### What changes

1. **`app/bot/keyboards/auction.py`** — `start_private_keyboard()`:
   - Remove "Баланс" button
   - Add "Гарант" button (`dash:guarant`)
   - Add "Уведомления" button (`dash:notifications`)
   - Rename "Создать аукцион" → "Создать лот"
   - Rename "Мои аукционы" → "Мои лоты"

2. **`app/bot/handlers/start.py`** — add 2 new callback handlers:
   - `dash:guarant` — invokes guarantor intake flow (reuse logic from `handlers/guarantor.py` service layer)
   - `dash:notifications` — renders notification settings view (reuse logic from `start_notification_views.py`)

3. **Telegram menu commands** — update via `BotFather` or `bot.set_my_commands()`:
   - Register only: `/start`, `/newauction`, `/guarant`, `/publish`, `/cancel`
   - All other commands become unlisted but functional

### What does NOT change

- All existing command handlers remain registered and functional
- No service files are modified
- No handler files are deleted
- All moderator commands stay as-is
- Inline query publication stays as-is
- Channel DM intake stays as-is
- Suggested posts stays as-is

## Section 4: Risk Assessment

- **Low risk:** Adding buttons to keyboard — purely additive
- **Low risk:** Adding callback handlers — new code, doesn't touch existing
- **Low risk:** Hiding commands from menu — handlers still work
- **No risk:** No deletions, no service layer changes

## Success Criteria

1. `/start` shows new hub keyboard with 5-6 buttons
2. All new buttons navigate correctly to their targets
3. Existing commands (`/points`, `/bug`, etc.) still work when typed manually
4. Only 5 commands appear in Telegram menu
5. Moderator button only visible to moderators
