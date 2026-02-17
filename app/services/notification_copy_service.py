from __future__ import annotations

import uuid


def short_auction_ref(auction_id: uuid.UUID) -> str:
    return f"#{str(auction_id)[:8]}"


def outbid_notification_text(auction_id: uuid.UUID) -> str:
    return f"Лот {short_auction_ref(auction_id)}: вашу ставку перебили."


def outbid_digest_text(*, auction_id: uuid.UUID, suppressed_count: int, window_label: str) -> str:
    return (
        f"Дайджест по лоту {short_auction_ref(auction_id)}: "
        f"за {window_label} ставку перебивали {suppressed_count} раз."
    )


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
