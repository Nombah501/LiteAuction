from __future__ import annotations

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings


def _icon(value: str) -> str | None:
    raw = value.strip()
    return raw if raw else None


def _first_icon(*values: str) -> str | None:
    for value in values:
        icon = _icon(value)
        if icon is not None:
            return icon
    return None


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


def start_private_keyboard(*, show_moderation_button: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
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
                text="Мои аукционы",
                callback_data="dash:my_auctions",
                style="primary",
            )
        ],
        [
            styled_button(
                text="Настройки",
                callback_data="dash:settings",
            )
        ],
        [
            styled_button(
                text="Баланс",
                callback_data="dash:balance",
            )
        ],
    ]

    if show_moderation_button:
        rows.append(
            [
                styled_button(
                    text="Мод-панель",
                    callback_data="mod:panel",
                    style="success",
                    icon_custom_emoji_id=_icon(settings.ui_emoji_mod_panel_id),
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _filter_button_text(*, filter_key: str, current_filter: str, label: str) -> str:
    if filter_key == current_filter:
        return f"[{label}]"
    return label


def my_auctions_list_keyboard(
    *,
    auctions: list[tuple[str, str]],
    current_filter: str,
    page: int,
    has_prev: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            styled_button(
                text=_filter_button_text(filter_key="a", current_filter=current_filter, label="Активные"),
                callback_data="dash:my:list:a:0",
            ),
            styled_button(
                text=_filter_button_text(filter_key="f", current_filter=current_filter, label="Завершенные"),
                callback_data="dash:my:list:f:0",
            ),
        ],
        [
            styled_button(
                text=_filter_button_text(filter_key="d", current_filter=current_filter, label="Черновики"),
                callback_data="dash:my:list:d:0",
            ),
            styled_button(
                text=_filter_button_text(filter_key="l", current_filter=current_filter, label="Все"),
                callback_data="dash:my:list:l:0",
            ),
        ],
    ]

    for auction_id, label in auctions:
        rows.append(
            [
                styled_button(
                    text=label,
                    callback_data=f"dash:my:view:{auction_id}:{current_filter}:{page}",
                    style="primary",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if has_prev:
        nav_row.append(styled_button(text="<-", callback_data=f"dash:my:list:{current_filter}:{page - 1}"))
    nav_row.append(styled_button(text=f"Стр. {page + 1}", callback_data=f"dash:my:list:{current_filter}:{page}"))
    if has_next:
        nav_row.append(styled_button(text="->", callback_data=f"dash:my:list:{current_filter}:{page + 1}"))
    rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def my_auction_detail_keyboard(
    *,
    auction_id: str,
    filter_key: str,
    page: int,
    first_post_url: str | None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            styled_button(
                text="Ставки",
                callback_data=f"dash:my:bids:{auction_id}:{filter_key}:{page}",
                style="primary",
            ),
            styled_button(
                text="Посты",
                callback_data=f"dash:my:posts:{auction_id}:{filter_key}:{page}",
                style="primary",
            ),
        ],
        [
            styled_button(
                text="Фото",
                callback_data=f"gallery:{auction_id}",
            ),
            styled_button(
                text="Назад к списку",
                callback_data=f"dash:my:list:{filter_key}:{page}",
            ),
        ],
    ]
    if first_post_url:
        rows.insert(
            1,
            [
                styled_button(
                    text="Открыть пост",
                    url=first_post_url,
                    style="success",
                )
            ],
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def my_auction_subview_keyboard(*, auction_id: str, filter_key: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(
                    text="Карточка лота",
                    callback_data=f"dash:my:view:{auction_id}:{filter_key}:{page}",
                    style="primary",
                ),
                styled_button(
                    text="Назад к списку",
                    callback_data=f"dash:my:list:{filter_key}:{page}",
                ),
            ]
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
            [
                styled_button(
                    text="Готово",
                    callback_data="create:photos:done",
                    style="success",
                    icon_custom_emoji_id=_first_icon(
                        settings.ui_emoji_photos_done_id,
                        settings.ui_emoji_create_auction_id,
                    ),
                )
            ],
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
                    icon_custom_emoji_id=_first_icon(
                        settings.ui_emoji_copy_publish_id,
                        settings.ui_emoji_publish_id,
                    ),
                )
            ],
            [
                styled_button(
                    text=f"Все фото ({photo_count})",
                    callback_data=f"gallery:{auction_id}",
                    style="primary",
                    icon_custom_emoji_id=_first_icon(
                        settings.ui_emoji_gallery_id,
                        settings.ui_emoji_publish_id,
                    ),
                )
            ],
            [
                styled_button(
                    text="Создать новый лот",
                    callback_data="create:new",
                    icon_custom_emoji_id=_first_icon(
                        settings.ui_emoji_new_lot_id,
                        settings.ui_emoji_create_auction_id,
                    ),
                )
            ],
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
                text=f"+${min_step}",
                callback_data=f"bid:{auction_id}:1",
                style="success",
                icon_custom_emoji_id=_first_icon(
                    settings.ui_emoji_bid_x1_id,
                    settings.ui_emoji_bid_id,
                ),
            ),
            styled_button(
                text=f"+${min_step * 3}",
                callback_data=f"bid:{auction_id}:3",
                style="success",
                icon_custom_emoji_id=_first_icon(
                    settings.ui_emoji_bid_x3_id,
                    settings.ui_emoji_bid_id,
                ),
            ),
            styled_button(
                text=f"+${min_step * 5}",
                callback_data=f"bid:{auction_id}:5",
                style="success",
                icon_custom_emoji_id=_first_icon(
                    settings.ui_emoji_bid_x5_id,
                    settings.ui_emoji_bid_id,
                ),
            ),
        ]
    ]

    if has_buyout:
        rows.append(
            [
                styled_button(
                    text="Выкуп",
                    callback_data=f"buy:{auction_id}",
                    style="danger",
                    icon_custom_emoji_id=_icon(settings.ui_emoji_buyout_id),
                )
            ]
        )

    utility_row: list[InlineKeyboardButton] = [
        styled_button(
            text=f"Фото {photo_count}",
            callback_data=f"gallery:{auction_id}",
            style="primary",
            icon_custom_emoji_id=_first_icon(
                settings.ui_emoji_gallery_id,
                settings.ui_emoji_publish_id,
            ),
        ),
        styled_button(
            text="Жалоба",
            callback_data=f"report:{auction_id}",
            style="danger",
            icon_custom_emoji_id=_icon(settings.ui_emoji_report_id),
        ),
    ]

    username = settings.bot_username.strip().lstrip("@")
    if username:
        utility_row.append(
            styled_button(
                text="Бот",
                url=f"https://t.me/{username}?start=auction_gate",
                style="primary",
            )
        )

    rows.append(utility_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)
