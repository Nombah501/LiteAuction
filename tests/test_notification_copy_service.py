from __future__ import annotations

from uuid import UUID

from app.services.notification_copy_service import (
    auction_buyout_finished_text,
    auction_buyout_winner_text,
    auction_finished_text,
    auction_winner_text,
    moderation_bid_removed_text,
    moderation_ended_text,
    moderation_frozen_text,
    moderation_unfrozen_text,
    moderation_winner_text,
    outbid_digest_text,
    outbid_notification_text,
    quiet_hours_deferred_summary_text,
    russian_count_label,
    russian_plural_form,
)


_AID = UUID("12345678-1234-5678-1234-567812345678")


def test_outbid_copy_templates_are_concise_and_consistent() -> None:
    assert outbid_notification_text(_AID) == "Лот #12345678: вашу ставку перебили."
    assert outbid_digest_text(auction_id=_AID, suppressed_count=3, window_label="3 мин") == (
        "Дайджест по лоту #12345678: за 3 мин ставку перебивали 3 раза."
    )


def test_russian_plural_helpers_cover_common_edge_cases() -> None:
    assert russian_plural_form(count=1, one="раз", few="раза", many="раз") == "раз"
    assert russian_plural_form(count=2, one="раз", few="раза", many="раз") == "раза"
    assert russian_plural_form(count=4, one="раз", few="раза", many="раз") == "раза"
    assert russian_plural_form(count=5, one="раз", few="раза", many="раз") == "раз"
    assert russian_plural_form(count=11, one="раз", few="раза", many="раз") == "раз"
    assert russian_plural_form(count=14, one="раз", few="раза", many="раз") == "раз"


def test_quiet_hours_deferred_summary_uses_pluralization() -> None:
    assert quiet_hours_deferred_summary_text(deferred_count=1) == (
        "Тихие часы завершены: пропущено 1 уведомление этого типа."
    )
    assert quiet_hours_deferred_summary_text(deferred_count=3) == (
        "Тихие часы завершены: пропущено 3 уведомления этого типа."
    )
    assert quiet_hours_deferred_summary_text(deferred_count=11) == (
        "Тихие часы завершены: пропущено 11 уведомлений этого типа."
    )
    assert quiet_hours_deferred_summary_text(deferred_count=25) == (
        "Тихие часы завершены: пропущено 25 уведомлений этого типа."
    )


def test_russian_count_label_combines_number_and_plural_form() -> None:
    assert russian_count_label(count=1, one="уведомление", few="уведомления", many="уведомлений") == (
        "1 уведомление"
    )
    assert russian_count_label(count=22, one="уведомление", few="уведомления", many="уведомлений") == (
        "22 уведомления"
    )


def test_finish_and_win_copy_templates_are_consistent() -> None:
    assert auction_buyout_finished_text(_AID) == "Лот #12345678 завершен выкупом."
    assert auction_buyout_winner_text(_AID) == "Вы выиграли лот #12345678 (выкуп)."
    assert auction_finished_text(_AID) == "Лот #12345678 завершен."
    assert auction_winner_text(_AID) == "Вы выиграли лот #12345678."


def test_moderation_copy_templates_are_consistent() -> None:
    assert moderation_frozen_text(_AID) == "Модерация: лот #12345678 заморожен."
    assert moderation_unfrozen_text(_AID) == "Модерация: лот #12345678 разморожен."
    assert moderation_ended_text(_AID) == "Модерация: лот #12345678 завершен."
    assert moderation_winner_text(_AID) == "Модерация: вы признаны победителем в лоте #12345678."
    assert moderation_bid_removed_text() == "Модерация: ваша ставка по лоту была снята."
