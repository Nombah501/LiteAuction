from __future__ import annotations

import html
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


def _truncate(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _format_price(amount: int) -> str:
    return f"{amount:,} ₽".replace(",", " ")


def seller_completion_text(
    *,
    auction_id: uuid.UUID,
    description: str,
    final_price: int,
    counterparty_mention: str,
    counterparty_role: str,
    is_buyout: bool = False,
    is_mod_action: bool = False,
) -> str:
    prefix = "Модерация: " if is_mod_action else ""
    buyout_label = " выкупом" if is_buyout else ""
    return (
        f"🏆 {prefix}Аукцион завершён{buyout_label}!\n\n"
        f"<b>Лот:</b> {short_auction_ref(auction_id)}\n"
        f"<b>Описание:</b> {html.escape(_truncate(description))}\n"
        f"<b>Финальная цена:</b> {_format_price(final_price)}\n"
        f"<b>Победитель:</b> {counterparty_mention}\n\n"
        f"📋 <b>Следующие шаги:</b>\n"
        f"1. Нажмите «Написать победителю» — откроется топик сделки\n"
        f"2. Договоритесь о способе передачи товара и оплаты\n"
        f"3. После завершения — оставьте отзыв\n\n"
        f"🛡 <b>Гарант — ваша безопасность</b>\n"
        f"Если сумма сделки значительная или вы впервые работаете с {counterparty_role}ом — "
        f"запросите гаранта. Гарант выступит посредником и обеспечит защиту обеих сторон.\n"
        f"<i>Стоимость: бесплатно для участников с репутацией 50+</i>"
    )


def winner_completion_text(
    *,
    auction_id: uuid.UUID,
    description: str,
    final_price: int,
    counterparty_mention: str,
    counterparty_role: str,
    is_buyout: bool = False,
    is_mod_action: bool = False,
) -> str:
    prefix = "Модерация: " if is_mod_action else ""
    buyout_label = " (выкуп)" if is_buyout else ""
    return (
        f"🎉 {prefix}Вы выиграли аукцион{buyout_label}!\n\n"
        f"<b>Лот:</b> {short_auction_ref(auction_id)}\n"
        f"<b>Описание:</b> {html.escape(_truncate(description))}\n"
        f"<b>Ваша цена:</b> {_format_price(final_price)}\n"
        f"<b>Продавец:</b> {counterparty_mention}\n\n"
        f"📋 <b>Что дальше:</b>\n"
        f"1. Нажмите «Написать продавцу» — откроется топик сделки\n"
        f"2. Договоритесь о способе получения товара и оплаты\n"
        f"3. После завершения — оставьте отзыв\n\n"
        f"🛡 <b>Гарант — ваша безопасность</b>\n"
        f"Если сумма сделки значительная или вы впервые работаете с {counterparty_role}ом — "
        f"запросите гаранта. Гарант выступит посредником и обеспечит защиту обеих сторон.\n"
        f"<i>Стоимость: бесплатно для участников с репутацией 50+</i>"
    )


def seller_no_bids_text(
    *,
    auction_id: uuid.UUID,
    description: str,
    start_price: int,
) -> str:
    return (
        f"⏰ Аукцион завершён без ставок\n\n"
        f"<b>Лот:</b> {short_auction_ref(auction_id)}\n"
        f"<b>Описание:</b> {html.escape(_truncate(description))}\n"
        f"<b>Стартовая цена:</b> {_format_price(start_price)}\n"
        f"<b>Ставок:</b> 0\n\n"
        f"💡 <b>Совет:</b> Попробуйте изменить стартовую цену или описание "
        f"и опубликуйте лот заново."
    )


def deal_topic_intro_text(
    *,
    auction_id: uuid.UUID,
    description: str,
    final_price: int,
    seller_mention: str,
    winner_mention: str,
) -> str:
    return (
        f"🤝 <b>Топик сделки создан</b>\n\n"
        f"Лот: {short_auction_ref(auction_id)} — {html.escape(_truncate(description))}\n"
        f"Цена: {_format_price(final_price)}\n"
        f"Участники: {seller_mention} (продавец) и {winner_mention} (победитель)\n\n"
        f"Все сообщения здесь будут пересылаться вашему контрагенту.\n"
        f"Будьте вежливы и обсуждайте только сделку.\n\n"
        f"🛡 <b>Совет по безопасности</b>\n"
        f"Для защиты обеих сторон вы можете запросить гаранта — нажав кнопку ниже."
    )


def moderation_completion_text(
    *,
    auction_id: uuid.UUID,
    description: str,
    final_price: int,
    bid_count: int,
    seller_mention: str,
    winner_mention: str,
    seller_reputation: int,
    winner_reputation: int,
    has_deal_topic: bool,
    has_guarantor: bool,
    reason: str,
) -> str:
    deal_label = "создан автоматически" if has_deal_topic else "не создан"
    guarantor_label = "запрошен" if has_guarantor else "не запрошен"
    return (
        f"🏁 Лот {short_auction_ref(auction_id)} завершён {reason}\n\n"
        f"<b>Описание:</b> {html.escape(_truncate(description))}\n"
        f"<b>Финальная цена:</b> {_format_price(final_price)}\n"
        f"<b>Ставок:</b> {bid_count}\n\n"
        f"<b>Продавец:</b> {seller_mention}\n"
        f"<b>Победитель:</b> {winner_mention}\n"
        f"<b>Репутация продавца:</b> {seller_reputation}\n"
        f"<b>Репутация победителя:</b> {winner_reputation}\n\n"
        f"🤝 <b>Топик сделки:</b> {deal_label}\n"
        f"🛡 <b>Гарант:</b> {guarantor_label}"
    )


def format_user_mention(*, username: str | None, first_name: str | None, tg_user_id: int) -> str:
    if username:
        return f"@{html.escape(username)}"
    display = html.escape(first_name or "Пользователь")
    return f'<a href="tg://user?id={tg_user_id}">{display}</a>'
