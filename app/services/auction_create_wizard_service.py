from __future__ import annotations

import hashlib
import html
import json
from typing import Any, Mapping

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message

from app.bot.keyboards.auction import (
    anti_sniper_keyboard,
    buyout_choice_keyboard,
    duration_keyboard,
    photos_done_keyboard,
)

WIZARD_STEP_WAITING_PHOTO = "waiting_photo"
WIZARD_STEP_WAITING_DESCRIPTION = "waiting_description"
WIZARD_STEP_WAITING_START_PRICE = "waiting_start_price"
WIZARD_STEP_WAITING_BUYOUT_PRICE = "waiting_buyout_price"
WIZARD_STEP_WAITING_MIN_STEP = "waiting_min_step"
WIZARD_STEP_WAITING_DURATION = "waiting_duration"
WIZARD_STEP_WAITING_ANTI_SNIPER = "waiting_anti_sniper"

WIZARD_STEP_SEQUENCE: tuple[str, ...] = (
    WIZARD_STEP_WAITING_PHOTO,
    WIZARD_STEP_WAITING_DESCRIPTION,
    WIZARD_STEP_WAITING_START_PRICE,
    WIZARD_STEP_WAITING_BUYOUT_PRICE,
    WIZARD_STEP_WAITING_MIN_STEP,
    WIZARD_STEP_WAITING_DURATION,
    WIZARD_STEP_WAITING_ANTI_SNIPER,
)

_PROGRESS_CHAT_ID_KEY = "create_wizard_progress_chat_id"
_PROGRESS_MESSAGE_ID_KEY = "create_wizard_progress_message_id"
_PROGRESS_FINGERPRINT_KEY = "create_wizard_progress_fingerprint"
_LAST_EVENT_KEY = "create_wizard_last_event"

_STEP_LABELS: tuple[tuple[str, str], ...] = (
    (WIZARD_STEP_WAITING_PHOTO, "Фото"),
    (WIZARD_STEP_WAITING_DESCRIPTION, "Описание"),
    (WIZARD_STEP_WAITING_START_PRICE, "Старт"),
    (WIZARD_STEP_WAITING_BUYOUT_PRICE, "Выкуп"),
    (WIZARD_STEP_WAITING_MIN_STEP, "Мин. шаг"),
    (WIZARD_STEP_WAITING_DURATION, "Длительность"),
    (WIZARD_STEP_WAITING_ANTI_SNIPER, "Антиснайпер"),
)


def _is_not_modified_error(exc: TelegramBadRequest) -> bool:
    return "message is not modified" in str(exc).lower()


def _photo_count(data: Mapping[str, Any]) -> int:
    photo_ids_raw = data.get("photo_file_ids")
    if isinstance(photo_ids_raw, list):
        return len([item for item in photo_ids_raw if str(item)])
    fallback = data.get("photo_file_id")
    if isinstance(fallback, str) and fallback:
        return 1
    return 0


def _format_usd(amount: Any) -> str:
    if isinstance(amount, int) and amount > 0:
        return f"${amount}"
    return "-"


def _short_description_preview(raw: Any) -> str:
    if not isinstance(raw, str):
        return "-"
    cleaned = " ".join(raw.strip().split())
    if not cleaned:
        return "-"
    if len(cleaned) > 72:
        cleaned = f"{cleaned[:69]}..."
    return html.escape(cleaned)


def _progress_step_index(step_name: str, *, finished: bool) -> int:
    if finished:
        return len(WIZARD_STEP_SEQUENCE)
    try:
        return WIZARD_STEP_SEQUENCE.index(step_name) + 1
    except ValueError:
        return 1


def _ascii_meter(*, step_index: int, total_steps: int, width: int = 16) -> tuple[str, int]:
    percent = int(round((max(step_index, 0) / max(total_steps, 1)) * 100))
    filled = int(round((percent / 100) * width))
    bar = f"[{'=' * filled}{'.' * (width - filled)}]"
    return bar, percent


def _buyout_status(data: Mapping[str, Any]) -> str:
    if "buyout_price" not in data:
        return "-"
    buyout_price = data.get("buyout_price")
    if buyout_price is None:
        return "Пропущен"
    return _format_usd(buyout_price)


def _duration_status(data: Mapping[str, Any]) -> str:
    duration_hours = data.get("duration_hours")
    if isinstance(duration_hours, int) and duration_hours > 0:
        return f"{duration_hours} ч"
    return "-"


def _anti_sniper_status(data: Mapping[str, Any]) -> str:
    if "anti_sniper_enabled" not in data:
        return "-"
    return "Включен" if bool(data.get("anti_sniper_enabled")) else "Выключен"


def _description_status(data: Mapping[str, Any]) -> str:
    description = data.get("description")
    if isinstance(description, str) and description.strip():
        return "готово"
    return "-"


def _step_value(step_name: str, data: Mapping[str, Any]) -> str:
    if step_name == WIZARD_STEP_WAITING_PHOTO:
        return f"{_photo_count(data)}/10"
    if step_name == WIZARD_STEP_WAITING_DESCRIPTION:
        return _description_status(data)
    if step_name == WIZARD_STEP_WAITING_START_PRICE:
        return _format_usd(data.get("start_price"))
    if step_name == WIZARD_STEP_WAITING_BUYOUT_PRICE:
        return _buyout_status(data)
    if step_name == WIZARD_STEP_WAITING_MIN_STEP:
        return _format_usd(data.get("min_step"))
    if step_name == WIZARD_STEP_WAITING_DURATION:
        return _duration_status(data)
    if step_name == WIZARD_STEP_WAITING_ANTI_SNIPER:
        return _anti_sniper_status(data)
    return "-"


def _step_marker(*, item_index: int, current_index: int, finished: bool) -> str:
    if finished or item_index < current_index:
        return "[x]"
    if item_index == current_index:
        return "[>]"
    return "[ ]"


def _progress_board(*, data: Mapping[str, Any], step_index: int, total_steps: int, finished: bool) -> str:
    meter, percent = _ascii_meter(step_index=step_index, total_steps=total_steps)
    lines = [f"{meter} {percent:>3}%"]
    for index, (step_name, label) in enumerate(_STEP_LABELS, start=1):
        marker = _step_marker(item_index=index, current_index=step_index, finished=finished)
        value = _step_value(step_name, data)
        lines.append(f"{marker} {label:<11} {value}")
    return "\n".join(lines)


def render_create_wizard_text(
    *,
    data: Mapping[str, Any],
    step_name: str,
    hint: str,
    error: str | None = None,
    finished: bool = False,
) -> str:
    total_steps = len(WIZARD_STEP_SEQUENCE)
    step_index = _progress_step_index(step_name, finished=finished)
    title = "Создание лота завершено" if finished else "Создание лота"
    board = _progress_board(data=data, step_index=step_index, total_steps=total_steps, finished=finished)
    last_event = data.get(_LAST_EVENT_KEY)
    lines = [
        f"<b>{title} · шаг {step_index}/{total_steps}</b>",
        f"<pre>{board}</pre>",
        f"<b>Описание:</b> {_short_description_preview(data.get('description'))}",
    ]

    if isinstance(last_event, str) and last_event.strip():
        lines.append(f"<b>Изменение:</b> {html.escape(last_event.strip())}")
    if error:
        lines.append(f"<b>Ошибка:</b> {html.escape(error)}")
    lines.append(f"<b>Сейчас:</b> {html.escape(hint)}")
    return "\n".join(lines)


def _keyboard_for_step(step_name: str) -> InlineKeyboardMarkup | None:
    if step_name == WIZARD_STEP_WAITING_PHOTO:
        return photos_done_keyboard()
    if step_name == WIZARD_STEP_WAITING_BUYOUT_PRICE:
        return buyout_choice_keyboard()
    if step_name == WIZARD_STEP_WAITING_DURATION:
        return duration_keyboard()
    if step_name == WIZARD_STEP_WAITING_ANTI_SNIPER:
        return anti_sniper_keyboard()
    return None


def _fingerprint_payload(text: str, keyboard: InlineKeyboardMarkup | None) -> str:
    payload = {
        "text": text,
        "reply_markup": keyboard.model_dump(mode="json", exclude_none=True) if keyboard else None,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()


async def upsert_create_wizard_progress(
    *,
    state: FSMContext,
    bot: Bot | None,
    anchor_message: Message | None,
    step_name: str,
    hint: str,
    error: str | None = None,
    finished: bool = False,
    last_event: str | None = None,
    force_repost: bool = False,
) -> None:
    data = await state.get_data()
    render_data: Mapping[str, Any]
    if last_event is None:
        render_data = data
    else:
        next_data = dict(data)
        next_data[_LAST_EVENT_KEY] = last_event
        render_data = next_data

    keyboard = None if finished else _keyboard_for_step(step_name)
    text = render_create_wizard_text(
        data=render_data,
        step_name=step_name,
        hint=hint,
        error=error,
        finished=finished,
    )
    fingerprint = _fingerprint_payload(text, keyboard)
    if data.get(_PROGRESS_FINGERPRINT_KEY) == fingerprint:
        return

    stored_chat_id = data.get(_PROGRESS_CHAT_ID_KEY)
    stored_message_id = data.get(_PROGRESS_MESSAGE_ID_KEY)
    state_updates: dict[str, Any] = {_PROGRESS_FINGERPRINT_KEY: fingerprint}
    if last_event is not None:
        state_updates[_LAST_EVENT_KEY] = last_event

    if (
        force_repost
        and bot is not None
        and anchor_message is not None
        and isinstance(stored_chat_id, int)
        and isinstance(stored_message_id, int)
    ):
        try:
            sent = await anchor_message.answer(
                text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception:
            sent = None
        if sent is not None:
            sent_chat_id = getattr(getattr(sent, "chat", None), "id", None)
            sent_message_id = getattr(sent, "message_id", None)
            if isinstance(sent_chat_id, int) and isinstance(sent_message_id, int):
                state_updates[_PROGRESS_CHAT_ID_KEY] = sent_chat_id
                state_updates[_PROGRESS_MESSAGE_ID_KEY] = sent_message_id
            try:
                await bot.delete_message(chat_id=stored_chat_id, message_id=stored_message_id)
            except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError):
                pass
            await state.update_data(data=state_updates)
            return

    if bot is not None and isinstance(stored_chat_id, int) and isinstance(stored_message_id, int):
        try:
            await bot.edit_message_text(
                chat_id=stored_chat_id,
                message_id=stored_message_id,
                text=text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            await state.update_data(data=state_updates)
            return
        except TelegramBadRequest as exc:
            if _is_not_modified_error(exc):
                await state.update_data(data=state_updates)
                return
        except (TelegramForbiddenError, TelegramAPIError):
            pass

    if anchor_message is None:
        await state.update_data(data=state_updates)
        return

    try:
        sent = await anchor_message.answer(
            text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception:
        await state.update_data(data=state_updates)
        return

    update_payload = dict(state_updates)
    sent_chat_id = getattr(getattr(sent, "chat", None), "id", None)
    sent_message_id = getattr(sent, "message_id", None)
    if isinstance(sent_chat_id, int) and isinstance(sent_message_id, int):
        update_payload[_PROGRESS_CHAT_ID_KEY] = sent_chat_id
        update_payload[_PROGRESS_MESSAGE_ID_KEY] = sent_message_id
    await state.update_data(data=update_payload)


async def delete_numeric_input_message(message: Message) -> None:
    bot: Bot | None = getattr(message, "bot", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    message_id = getattr(message, "message_id", None)
    if bot is None or not isinstance(chat_id, int) or not isinstance(message_id, int):
        return

    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError):
        return
