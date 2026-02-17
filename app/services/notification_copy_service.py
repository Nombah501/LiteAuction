from __future__ import annotations

import uuid


def short_auction_ref(auction_id: uuid.UUID) -> str:
    return f"#{str(auction_id)[:8]}"


def russian_plural_form(*, count: int, one: str, few: str, many: str) -> str:
    safe_count = abs(int(count))
    last_two = safe_count % 100
    if 11 <= last_two <= 14:
        return many

    last_one = safe_count % 10
    if last_one == 1:
        return one
    if 2 <= last_one <= 4:
        return few
    return many


def russian_count_label(*, count: int, one: str, few: str, many: str) -> str:
    return f"{count} {russian_plural_form(count=count, one=one, few=few, many=many)}"


def outbid_notification_text(auction_id: uuid.UUID) -> str:
    return f"Лот {short_auction_ref(auction_id)}: вашу ставку перебили."


def outbid_digest_text(*, auction_id: uuid.UUID, suppressed_count: int, window_label: str) -> str:
    repeated_count = russian_count_label(
        count=suppressed_count,
        one="раз",
        few="раза",
        many="раз",
    )
    return (
        f"Дайджест по лоту {short_auction_ref(auction_id)}: "
        f"за {window_label} ставку перебивали {repeated_count}."
    )


def quiet_hours_deferred_summary_text(*, deferred_count: int) -> str:
    deferred_label = russian_count_label(
        count=deferred_count,
        one="уведомление",
        few="уведомления",
        many="уведомлений",
    )
    return f"Тихие часы завершены: пропущено {deferred_label} этого типа."


def auction_buyout_finished_text(auction_id: uuid.UUID) -> str:
    return f"Лот {short_auction_ref(auction_id)} завершен выкупом."


def auction_buyout_winner_text(auction_id: uuid.UUID) -> str:
    return f"Вы выиграли лот {short_auction_ref(auction_id)} (выкуп)."


def auction_finished_text(auction_id: uuid.UUID) -> str:
    return f"Лот {short_auction_ref(auction_id)} завершен."


def auction_winner_text(auction_id: uuid.UUID) -> str:
    return f"Вы выиграли лот {short_auction_ref(auction_id)}."


def moderation_frozen_text(auction_id: uuid.UUID) -> str:
    return f"Модерация: лот {short_auction_ref(auction_id)} заморожен."


def moderation_unfrozen_text(auction_id: uuid.UUID) -> str:
    return f"Модерация: лот {short_auction_ref(auction_id)} разморожен."


def moderation_ended_text(auction_id: uuid.UUID) -> str:
    return f"Модерация: лот {short_auction_ref(auction_id)} завершен."


def moderation_winner_text(auction_id: uuid.UUID) -> str:
    return f"Модерация: вы признаны победителем в лоте {short_auction_ref(auction_id)}."


def moderation_bid_removed_text() -> str:
    return "Модерация: ваша ставка по лоту была снята."
