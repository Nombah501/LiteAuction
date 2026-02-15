from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from app.bot.keyboards.auction import styled_button
from app.config import settings
from app.db.enums import FeedbackStatus, GuarantorRequestStatus


def _icon(value: str) -> str | None:
    raw = value.strip()
    return raw if raw else None


def _icon_fallback(*values: str) -> str | None:
    for value in values:
        icon = _icon(value)
        if icon is not None:
            return icon
    return None


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
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_freeze_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            ),
            styled_button(
                text="Снять топ-ставку",
                callback_data=f"modrep:rm_top:{complaint_id}",
                style="danger",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_remove_top_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            ),
        ],
        [
            styled_button(
                text="Бан + снять",
                callback_data=f"modrep:ban_top:{complaint_id}",
                style="danger",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_ban_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            ),
            styled_button(
                text="Отклонить",
                callback_data=f"modrep:dismiss:{complaint_id}",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_reject_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            ),
        ],
    ]
    if back_callback is not None:
        rows.append(
            [
                styled_button(
                    text="Назад",
                    callback_data=back_callback,
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_back_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ]
        )
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
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_freeze_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            ),
            styled_button(
                text="Бан пользователя",
                callback_data=f"modrisk:ban:{signal_id}",
                style="danger",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_ban_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            ),
        ],
        [
            styled_button(
                text="Игнор",
                callback_data=f"modrisk:ignore:{signal_id}",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_ignore_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            )
        ],
    ]
    if back_callback is not None:
        rows.append(
            [
                styled_button(
                    text="Назад",
                    callback_data=back_callback,
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_back_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(
                    text="Открытые жалобы",
                    callback_data="modui:complaints:0",
                    style="primary",
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_complaints_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ],
            [
                styled_button(
                    text="Фрод-сигналы",
                    callback_data="modui:signals:0",
                    style="danger",
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_signals_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ],
            [
                styled_button(
                    text="Замороженные аукционы",
                    callback_data="modui:frozen:0",
                    style="success",
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_frozen_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ],
            [
                styled_button(
                    text="Апелляции",
                    callback_data="modui:appeals:0",
                    style="primary",
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_appeals_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ],
            [
                styled_button(
                    text="Статистика",
                    callback_data="modui:stats",
                    style="success",
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_stats_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ],
            [
                styled_button(
                    text="Обновить",
                    callback_data="modui:home",
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_refresh_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ],
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
        nav_row.append(
            styled_button(
                text="<-",
                callback_data=f"modui:complaints:{page - 1}",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_prev_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            )
        )
    nav_row.append(
        styled_button(
            text="Меню",
            callback_data="modui:home",
            icon_custom_emoji_id=_icon_fallback(
                settings.ui_emoji_mod_menu_id,
                settings.ui_emoji_mod_panel_id,
            ),
        )
    )
    if has_next:
        nav_row.append(
            styled_button(
                text="->",
                callback_data=f"modui:complaints:{page + 1}",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_next_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            )
        )
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
        nav_row.append(
            styled_button(
                text="<-",
                callback_data=f"modui:signals:{page - 1}",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_prev_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            )
        )
    nav_row.append(
        styled_button(
            text="Меню",
            callback_data="modui:home",
            icon_custom_emoji_id=_icon_fallback(
                settings.ui_emoji_mod_menu_id,
                settings.ui_emoji_mod_panel_id,
            ),
        )
    )
    if has_next:
        nav_row.append(
            styled_button(
                text="->",
                callback_data=f"modui:signals:{page + 1}",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_next_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            )
        )
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
        nav_row.append(
            styled_button(
                text="<-",
                callback_data=f"modui:frozen:{page - 1}",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_prev_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            )
        )
    nav_row.append(
        styled_button(
            text="Меню",
            callback_data="modui:home",
            icon_custom_emoji_id=_icon_fallback(
                settings.ui_emoji_mod_menu_id,
                settings.ui_emoji_mod_panel_id,
            ),
        )
    )
    if has_next:
        nav_row.append(
            styled_button(
                text="->",
                callback_data=f"modui:frozen:{page + 1}",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_next_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            )
        )
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
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_unfreeze_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ],
            [
                styled_button(
                    text="Назад",
                    callback_data=f"modui:frozen:{page}",
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_back_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ],
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
        nav_row.append(
            styled_button(
                text="<-",
                callback_data=f"modui:appeals:{page - 1}",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_prev_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            )
        )
    nav_row.append(
        styled_button(
            text="Меню",
            callback_data="modui:home",
            icon_custom_emoji_id=_icon_fallback(
                settings.ui_emoji_mod_menu_id,
                settings.ui_emoji_mod_panel_id,
            ),
        )
    )
    if has_next:
        nav_row.append(
            styled_button(
                text="->",
                callback_data=f"modui:appeals:{page + 1}",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_next_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            )
        )
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
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_take_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ]
        )

    rows.append(
        [
            styled_button(
                text="Удовлетворить",
                callback_data=f"modui:appeal_resolve:{appeal_id}:{page}",
                style="success",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_approve_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            ),
            styled_button(
                text="Отклонить",
                callback_data=f"modui:appeal_reject:{appeal_id}:{page}",
                style="danger",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_reject_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            ),
        ]
    )
    rows.append(
        [
            styled_button(
                text="Назад",
                callback_data=f"modui:appeals:{page}",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_back_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderation_appeal_back_keyboard(*, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(
                    text="Назад",
                    callback_data=f"modui:appeals:{page}",
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_back_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ]
        ]
    )


def feedback_actions_keyboard(*, feedback_id: int, status: FeedbackStatus) -> InlineKeyboardMarkup | None:
    if status in {FeedbackStatus.APPROVED, FeedbackStatus.REJECTED}:
        return None

    rows = []
    if status == FeedbackStatus.NEW:
        rows.append(
            [
                styled_button(
                    text="В работу",
                    callback_data=f"modfb:take:{feedback_id}",
                    style="primary",
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_take_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                )
            ]
        )

    rows.append(
        [
            styled_button(
                text="Одобрить",
                callback_data=f"modfb:approve:{feedback_id}",
                style="success",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_approve_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            ),
            styled_button(
                text="Отклонить",
                callback_data=f"modfb:reject:{feedback_id}",
                style="danger",
                icon_custom_emoji_id=_icon_fallback(
                    settings.ui_emoji_mod_reject_id,
                    settings.ui_emoji_mod_panel_id,
                ),
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def guarantor_actions_keyboard(*, request_id: int, status: GuarantorRequestStatus) -> InlineKeyboardMarkup | None:
    if status in {GuarantorRequestStatus.ASSIGNED, GuarantorRequestStatus.REJECTED}:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(
                    text="Взять гарантом",
                    callback_data=f"modgr:assign:{request_id}",
                    style="success",
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_assign_guarantor_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                ),
                styled_button(
                    text="Отклонить",
                    callback_data=f"modgr:reject:{request_id}",
                    style="danger",
                    icon_custom_emoji_id=_icon_fallback(
                        settings.ui_emoji_mod_reject_id,
                        settings.ui_emoji_mod_panel_id,
                    ),
                ),
            ]
        ]
    )
