from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from app.bot.keyboards.auction import styled_button


def complaint_actions_keyboard(
    complaint_id: int,
    *,
    back_callback: str | None = None,
) -> InlineKeyboardMarkup:
    rows = [
        [
            styled_button(
                text="Заморозить",
                callback_data=f"modrep:freeze:{complaint_id}",
                style="primary",
            ),
            styled_button(
                text="Снять топ-ставку",
                callback_data=f"modrep:rm_top:{complaint_id}",
                style="danger",
            ),
        ],
        [
            styled_button(
                text="Бан + снять",
                callback_data=f"modrep:ban_top:{complaint_id}",
                style="danger",
            ),
            styled_button(
                text="Отклонить",
                callback_data=f"modrep:dismiss:{complaint_id}",
            ),
        ],
    ]
    if back_callback is not None:
        rows.append([styled_button(text="Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fraud_actions_keyboard(
    signal_id: int,
    *,
    back_callback: str | None = None,
) -> InlineKeyboardMarkup:
    rows = [
        [
            styled_button(
                text="Заморозить",
                callback_data=f"modrisk:freeze:{signal_id}",
                style="primary",
            ),
            styled_button(
                text="Бан пользователя",
                callback_data=f"modrisk:ban:{signal_id}",
                style="danger",
            ),
        ],
        [
            styled_button(
                text="Игнор",
                callback_data=f"modrisk:ignore:{signal_id}",
            )
        ],
    ]
    if back_callback is not None:
        rows.append([styled_button(text="Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(
                    text="Открытые жалобы",
                    callback_data="modui:complaints:0",
                    style="primary",
                )
            ],
            [
                styled_button(
                    text="Фрод-сигналы",
                    callback_data="modui:signals:0",
                    style="danger",
                )
            ],
            [
                styled_button(
                    text="Замороженные аукционы",
                    callback_data="modui:frozen:0",
                    style="success",
                )
            ],
            [
                styled_button(
                    text="Апелляции",
                    callback_data="modui:appeals:0",
                    style="primary",
                )
            ],
            [
                styled_button(
                    text="Статистика",
                    callback_data="modui:stats",
                    style="success",
                )
            ],
            [styled_button(text="Обновить", callback_data="modui:home")],
        ]
    )


def moderation_complaints_list_keyboard(
    *,
    items: list[tuple[int, str]],
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [styled_button(text=label, callback_data=f"modui:complaint:{item_id}:{page}")]
        for item_id, label in items
    ]

    nav_row = []
    if page > 0:
        nav_row.append(styled_button(text="<-", callback_data=f"modui:complaints:{page - 1}"))
    nav_row.append(styled_button(text="Меню", callback_data="modui:home"))
    if has_next:
        nav_row.append(styled_button(text="->", callback_data=f"modui:complaints:{page + 1}"))
    rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_signals_list_keyboard(
    *,
    items: list[tuple[int, str]],
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [styled_button(text=label, callback_data=f"modui:signal:{item_id}:{page}")]
        for item_id, label in items
    ]

    nav_row = []
    if page > 0:
        nav_row.append(styled_button(text="<-", callback_data=f"modui:signals:{page - 1}"))
    nav_row.append(styled_button(text="Меню", callback_data="modui:home"))
    if has_next:
        nav_row.append(styled_button(text="->", callback_data=f"modui:signals:{page + 1}"))
    rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_frozen_list_keyboard(
    *,
    items: list[tuple[str, str]],
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [styled_button(text=label, callback_data=f"modui:frozen_auction:{auction_id}:{page}")]
        for auction_id, label in items
    ]

    nav_row = []
    if page > 0:
        nav_row.append(styled_button(text="<-", callback_data=f"modui:frozen:{page - 1}"))
    nav_row.append(styled_button(text="Меню", callback_data="modui:home"))
    if has_next:
        nav_row.append(styled_button(text="->", callback_data=f"modui:frozen:{page + 1}"))
    rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_frozen_actions_keyboard(*, auction_id: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(
                    text="Разморозить",
                    callback_data=f"modui:unfreeze:{auction_id}:{page}",
                    style="success",
                )
            ],
            [styled_button(text="Назад", callback_data=f"modui:frozen:{page}")],
        ]
    )


def moderation_appeals_list_keyboard(
    *,
    items: list[tuple[int, str]],
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows = [[styled_button(text=label, callback_data=f"modui:appeal:{appeal_id}:{page}")] for appeal_id, label in items]

    nav_row = []
    if page > 0:
        nav_row.append(styled_button(text="<-", callback_data=f"modui:appeals:{page - 1}"))
    nav_row.append(styled_button(text="Меню", callback_data="modui:home"))
    if has_next:
        nav_row.append(styled_button(text="->", callback_data=f"modui:appeals:{page + 1}"))
    rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_appeal_actions_keyboard(*, appeal_id: int, page: int, show_take: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if show_take:
        rows.append(
            [
                styled_button(
                    text="В работу",
                    callback_data=f"modui:appeal_review:{appeal_id}:{page}",
                    style="primary",
                )
            ]
        )

    rows.append(
        [
            styled_button(
                text="Удовлетворить",
                callback_data=f"modui:appeal_resolve:{appeal_id}:{page}",
                style="success",
            ),
            styled_button(
                text="Отклонить",
                callback_data=f"modui:appeal_reject:{appeal_id}:{page}",
                style="danger",
            ),
        ]
    )
    rows.append([styled_button(text="Назад", callback_data=f"modui:appeals:{page}")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_appeal_back_keyboard(*, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[styled_button(text="Назад", callback_data=f"modui:appeals:{page}")]]
    )
