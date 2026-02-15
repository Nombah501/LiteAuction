from __future__ import annotations

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings


def _icon(value: str) -> str | None:
    raw = value.strip()
    return raw if raw else None


def styled_button(
    *,
    text: str,
    callback_data: str | None = None,
    style: str | None = None,
    icon_custom_emoji_id: str | None = None,
    switch_inline_query: str | None = None,
    copy_text: str | None = None,
    url: str | None = None,
) -> InlineKeyboardButton:
    payload: dict[str, object] = {"text": text}
    if callback_data is not None:
        payload["callback_data"] = callback_data
    if style is not None:
        payload["style"] = style
    if icon_custom_emoji_id is not None:
        payload["icon_custom_emoji_id"] = icon_custom_emoji_id
    if switch_inline_query is not None:
        payload["switch_inline_query"] = switch_inline_query
    if copy_text is not None:
        payload["copy_text"] = CopyTextButton(text=copy_text)
    if url is not None:
        payload["url"] = url
    return InlineKeyboardButton.model_validate(payload)


def start_private_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(
                    text="Создать аукцион",
                    callback_data="create:new",
                    style="primary",
                    icon_custom_emoji_id=_icon(settings.ui_emoji_create_auction_id),
                )
            ],
            [
                styled_button(
                    text="Мод-панель",
                    callback_data="mod:panel",
                    style="success",
                    icon_custom_emoji_id=_icon(settings.ui_emoji_mod_panel_id),
                )
            ],
        ]
    )


def buyout_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [styled_button(text="Пропустить", callback_data="create:buyout:skip")],
        ]
    )


def photos_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [styled_button(text="Готово", callback_data="create:photos:done", style="success")],
        ]
    )


def duration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(text="6 ч", callback_data="create:duration:6", style="primary"),
                styled_button(text="12 ч", callback_data="create:duration:12", style="primary"),
            ],
            [
                styled_button(text="18 ч", callback_data="create:duration:18", style="primary"),
                styled_button(text="24 ч", callback_data="create:duration:24", style="primary"),
            ],
        ]
    )


def anti_sniper_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(text="Включить", callback_data="create:antisniper:1", style="success"),
                styled_button(text="Выключить", callback_data="create:antisniper:0", style="danger"),
            ]
        ]
    )


def draft_publish_keyboard(auction_id: str, photo_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(
                    text="Опубликовать в чате/канале",
                    switch_inline_query=f"auc_{auction_id}",
                    style="primary",
                    icon_custom_emoji_id=_icon(settings.ui_emoji_publish_id),
                )
            ],
            [
                styled_button(
                    text="Скопировать /publish",
                    copy_text=f"/publish {auction_id}",
                    style="success",
                )
            ],
            [
                styled_button(
                    text=f"Все фото ({photo_count})",
                    callback_data=f"gallery:{auction_id}",
                    style="primary",
                )
            ],
            [styled_button(text="Создать новый лот", callback_data="create:new")],
        ]
    )


def auction_active_keyboard(
    auction_id: str,
    min_step: int,
    has_buyout: bool,
    photo_count: int = 1,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            styled_button(
                text=f"Все фото ({photo_count})",
                callback_data=f"gallery:{auction_id}",
                style="primary",
            )
        ],
        [
            styled_button(
                text=f"+ ${min_step} x1",
                callback_data=f"bid:{auction_id}:1",
                style="primary",
                icon_custom_emoji_id=_icon(settings.ui_emoji_bid_id),
            ),
            styled_button(
                text=f"+ ${min_step * 3} x3",
                callback_data=f"bid:{auction_id}:3",
                style="success",
                icon_custom_emoji_id=_icon(settings.ui_emoji_bid_id),
            ),
            styled_button(
                text=f"+ ${min_step * 5} x5",
                callback_data=f"bid:{auction_id}:5",
                style="success",
                icon_custom_emoji_id=_icon(settings.ui_emoji_bid_id),
            ),
        ]
    ]

    if has_buyout:
        rows.append(
            [
                styled_button(
                    text="Выкупить",
                    callback_data=f"buy:{auction_id}",
                    style="danger",
                    icon_custom_emoji_id=_icon(settings.ui_emoji_buyout_id),
                )
            ]
        )

    rows.append(
        [
            styled_button(
                text="Пожаловаться",
                callback_data=f"report:{auction_id}",
                style="danger",
                icon_custom_emoji_id=_icon(settings.ui_emoji_report_id),
            )
        ]
    )

    username = settings.bot_username.strip().lstrip("@")
    if username:
        rows.append(
            [
                styled_button(
                    text="Открыть бота",
                    url=f"https://t.me/{username}?start=auction_gate",
                    style="primary",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)
