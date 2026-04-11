from __future__ import annotations

import uuid
from datetime import datetime, UTC

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import DealTopicStatus
from app.db.models import DealTopic
from app.services.notification_copy_service import deal_topic_intro_text
from app.bot.keyboards.auction import deal_topic_keyboard


async def find_active_deal_topic(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID,
) -> DealTopic | None:
    return await session.scalar(
        select(DealTopic).where(
            DealTopic.auction_id == auction_id,
            DealTopic.status == DealTopicStatus.ACTIVE,
        )
    )


async def get_or_create_deal_topic(
    session: AsyncSession,
    *,
    bot: Bot,
    auction_id: uuid.UUID,
    seller_tg_id: int,
    winner_tg_id: int,
    description: str,
    final_price: int,
    seller_mention: str,
    winner_mention: str,
) -> DealTopic:
    existing = await find_active_deal_topic(session, auction_id=auction_id)
    if existing is not None:
        return existing

    short_id = str(auction_id)[:8]

    seller_topic = await bot.create_forum_topic(
        chat_id=seller_tg_id,
        name=f"🤝 Сделка #{short_id}",
    )

    intro = deal_topic_intro_text(
        auction_id=auction_id,
        description=description,
        final_price=final_price,
        seller_mention=seller_mention,
        winner_mention=winner_mention,
    )
    kb = deal_topic_keyboard(auction_id=short_id)

    await bot.send_message(
        chat_id=seller_tg_id,
        message_thread_id=seller_topic.message_thread_id,
        text=intro,
        reply_markup=kb,
        parse_mode="HTML",
    )

    deal = DealTopic(
        auction_id=auction_id,
        seller_topic_id=seller_topic.message_thread_id,
        status=DealTopicStatus.ACTIVE,
    )
    session.add(deal)
    await session.flush()

    return deal


async def ensure_winner_topic(
    session: AsyncSession,
    *,
    bot: Bot,
    deal: DealTopic,
    winner_tg_id: int,
    auction_id: uuid.UUID,
    description: str,
    final_price: int,
    seller_mention: str,
    winner_mention: str,
) -> None:
    if deal.winner_topic_id is not None:
        return

    short_id = str(auction_id)[:8]

    winner_topic = await bot.create_forum_topic(
        chat_id=winner_tg_id,
        name=f"🤝 Сделка #{short_id}",
    )

    intro = deal_topic_intro_text(
        auction_id=auction_id,
        description=description,
        final_price=final_price,
        seller_mention=seller_mention,
        winner_mention=winner_mention,
    )
    kb = deal_topic_keyboard(auction_id=short_id)

    await bot.send_message(
        chat_id=winner_tg_id,
        message_thread_id=winner_topic.message_thread_id,
        text=intro,
        reply_markup=kb,
        parse_mode="HTML",
    )

    deal.winner_topic_id = winner_topic.message_thread_id
    await session.flush()


async def forward_deal_message(
    bot: Bot,
    *,
    deal: DealTopic,
    sender_label: str,
    recipient_tg_id: int,
    recipient_topic_id: int,
    text: str | None = None,
    photo_file_id: str | None = None,
) -> None:
    prefix = f"<b>{sender_label}:</b>\n"
    if photo_file_id:
        caption = f"{prefix}{text}" if text else prefix.rstrip(":")
        await bot.send_photo(
            chat_id=recipient_tg_id,
            message_thread_id=recipient_topic_id,
            photo=photo_file_id,
            caption=caption,
            parse_mode="HTML",
        )
    elif text:
        await bot.send_message(
            chat_id=recipient_tg_id,
            message_thread_id=recipient_topic_id,
            text=f"{prefix}{text}",
            parse_mode="HTML",
        )


async def close_deal_topic(
    session: AsyncSession,
    *,
    deal: DealTopic,
) -> None:
    deal.status = DealTopicStatus.CLOSED
    deal.closed_at = datetime.now(UTC)
    await session.flush()
