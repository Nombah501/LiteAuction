from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.types import ChosenInlineResult, InlineQuery, InlineQueryResultCachedPhoto

from app.bot.keyboards.auction import auction_active_keyboard
from app.db.enums import AuctionStatus
from app.db.session import SessionFactory
from app.services.auction_service import (
    activate_auction_inline_post,
    load_auction_view,
    parse_auction_uuid,
    refresh_auction_posts,
    render_auction_caption,
)
from app.services.private_topics_service import PrivateTopicPurpose, send_user_topic_message
from app.services.publish_gate_service import evaluate_seller_publish_gate
from app.services.user_service import upsert_user

router = Router(name="inline_auction")


def _extract_auction_id(raw_query: str) -> str | None:
    query = raw_query.strip()
    if not query.startswith("auc_"):
        return None
    return query[4:]


@router.inline_query()
async def handle_inline_auction_query(inline_query: InlineQuery) -> None:
    auction_id_raw = _extract_auction_id(inline_query.query)
    if auction_id_raw is None:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    auction_uuid = parse_auction_uuid(auction_id_raw)
    if auction_uuid is None:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    async with SessionFactory() as session:
        view = await load_auction_view(session, auction_uuid)
        publish_gate = None
        if view is not None:
            publish_gate = await evaluate_seller_publish_gate(session, seller_user_id=view.seller.id)

    if view is None:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    if publish_gate is not None and not publish_gate.allowed:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    if inline_query.from_user.id != view.seller.tg_user_id:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    if view.auction.status != AuctionStatus.DRAFT:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    result = InlineQueryResultCachedPhoto(
        id=f"auc:{view.auction.id}",
        photo_file_id=view.auction.photo_file_id,
        caption=render_auction_caption(view, publish_pending=True),
        parse_mode=ParseMode.HTML,
        reply_markup=auction_active_keyboard(
            auction_id=str(view.auction.id),
            min_step=view.auction.min_step,
            has_buyout=view.auction.buyout_price is not None,
            photo_count=view.photo_count,
        ),
        title=f"Аукцион #{str(view.auction.id)[:8]}",
        description="Опубликовать аукцион",
    )

    await inline_query.answer([result], cache_time=1, is_personal=True)


@router.chosen_inline_result(F.result_id.startswith("auc:"))
async def handle_chosen_inline_result(chosen: ChosenInlineResult, bot: Bot) -> None:
    if chosen.from_user is None or chosen.inline_message_id is None:
        return

    auction_id_raw = chosen.result_id.split(":", maxsplit=1)[1]
    auction_uuid = parse_auction_uuid(auction_id_raw)
    if auction_uuid is None:
        return

    blocked_message: str | None = None
    async with SessionFactory() as session:
        async with session.begin():
            publisher = await upsert_user(session, chosen.from_user)
            publish_gate = await evaluate_seller_publish_gate(session, seller_user_id=publisher.id)
            if not publish_gate.allowed:
                blocked_message = publish_gate.block_message
                auction = None
            else:
                auction = await activate_auction_inline_post(
                    session,
                    auction_id=auction_uuid,
                    publisher_user_id=publisher.id,
                    inline_message_id=chosen.inline_message_id,
                )

    if blocked_message:
        await send_user_topic_message(
            bot,
            tg_user_id=chosen.from_user.id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=blocked_message,
        )
        return

    if auction is None:
        return

    await refresh_auction_posts(bot, auction_uuid)
