from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot.handlers.publish_auction import publish_auction_to_current_chat
from app.db.enums import AuctionStatus
from app.db.models import Auction, AuctionPhoto, AuctionPost, Bid, Complaint, User
from app.services.auction_service import create_draft_auction


@pytest_asyncio.fixture
async def publish_flow_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    db_url = (os.getenv("TEST_DATABASE_URL") or "").strip()
    if not db_url:
        pytest.skip("No TEST_DATABASE_URL set")

    parsed = urlsplit(db_url)
    db_name = parsed.path.lstrip("/").lower()
    if "test" not in db_name:
        pytest.skip("TEST_DATABASE_URL must target test database")

    engine = create_async_engine(db_url, future=True)
    tables: list[Table] = [
        cast(Table, User.__table__),
        cast(Table, Auction.__table__),
        cast(Table, AuctionPhoto.__table__),
        cast(Table, Bid.__table__),
        cast(Table, Complaint.__table__),
        cast(Table, AuctionPost.__table__),
    ]
    try:
        async with engine.begin() as conn:
            for table in reversed(tables):
                await conn.run_sync(lambda sync_conn, table=table: table.drop(sync_conn, checkfirst=True))
            for table in tables:
                await conn.run_sync(
                    lambda sync_conn, table=table: table.create(sync_conn, checkfirst=True)
                )
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Integration database is unavailable: {exc}")

    yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        for table in reversed(tables):
            await conn.run_sync(lambda sync_conn, table=table: table.drop(sync_conn, checkfirst=True))
    await engine.dispose()


class _DummyFromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = f"seller{user_id}"
        self.first_name = "Seller"
        self.last_name = "Test"


class _DummyChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class _DummyMessage:
    def __init__(self, *, text: str, from_user_id: int, chat_id: int, message_id: int) -> None:
        self.text = text
        self.from_user = _DummyFromUser(from_user_id)
        self.chat = _DummyChat(chat_id)
        self.message_thread_id: int | None = None
        self.message_id = message_id
        self.answers: list[str] = []

    async def answer(self, text: str, **_kwargs) -> None:
        self.answers.append(text)


class _DummyBot:
    def __init__(
        self,
        *,
        album_message_ids: list[int],
        post_message_id: int,
        fail_send_photo: bool = False,
    ) -> None:
        self.album_message_ids = album_message_ids
        self.post_message_id = post_message_id
        self.fail_send_photo = fail_send_photo
        self.deleted_messages: list[tuple[int, int]] = []
        self.sent_media_group_calls = 0
        self.sent_photo_calls = 0

    async def send_media_group(self, *, chat_id: int, media, message_thread_id: int | None = None):
        _ = media
        _ = message_thread_id
        self.sent_media_group_calls += 1
        return [
            SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=message_id)
            for message_id in self.album_message_ids
        ]

    async def send_photo(self, *, chat_id: int, photo: str, **_kwargs):
        self.sent_photo_calls += 1
        if self.fail_send_photo:
            raise TelegramBadRequest(
                method=SendPhoto(chat_id=chat_id, photo=photo),
                message="Bad Request: cannot send photo",
            )
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=self.post_message_id)

    async def delete_message(self, *, chat_id: int, message_id: int, **_kwargs) -> None:
        self.deleted_messages.append((chat_id, message_id))


async def _seed_draft_auction(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    seller_tg_user_id: int,
) -> tuple[int, uuid.UUID]:
    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=seller_tg_user_id, username=f"seller{seller_tg_user_id}")
            session.add(seller)
            await session.flush()

            auction = await create_draft_auction(
                session,
                seller_user_id=seller.id,
                photo_file_id="photo-main",
                photo_file_ids=["photo-main", "photo-2", "photo-3"],
                description="Draft lot",
                start_price=100,
                buyout_price=None,
                min_step=5,
                duration_hours=6,
                anti_sniper_enabled=True,
            )

            return seller.tg_user_id, auction.id


@pytest.mark.asyncio
async def test_publish_command_activates_auction_and_persists_post(
    monkeypatch,
    publish_flow_session_factory,
) -> None:
    session_factory = publish_flow_session_factory
    monkeypatch.setattr("app.bot.handlers.publish_auction.SessionFactory", session_factory)

    async def _allow_publish(*_args, **_kwargs):
        return SimpleNamespace(allowed=True, block_message=None)

    refresh_calls: list[str] = []

    async def _refresh_stub(_bot, auction_id):
        refresh_calls.append(str(auction_id))

    monkeypatch.setattr("app.bot.handlers.publish_auction.evaluate_seller_publish_gate", _allow_publish)
    monkeypatch.setattr("app.bot.handlers.publish_auction.refresh_auction_posts", _refresh_stub)

    seller_tg_user_id, auction_id = await _seed_draft_auction(session_factory, seller_tg_user_id=94501)
    chat_id = -10094501
    command_message_id = 81

    message = _DummyMessage(
        text=f"/publish {auction_id}",
        from_user_id=seller_tg_user_id,
        chat_id=chat_id,
        message_id=command_message_id,
    )
    bot = _DummyBot(album_message_ids=[601, 602, 603], post_message_id=701)

    await publish_auction_to_current_chat(message, bot)

    assert message.answers == []
    assert bot.sent_media_group_calls == 1
    assert bot.sent_photo_calls == 1
    assert bot.deleted_messages == [(chat_id, command_message_id)]
    assert refresh_calls == [str(auction_id)]

    async with session_factory() as session:
        auction = await session.scalar(select(Auction).where(Auction.id == auction_id))
        post = await session.scalar(select(AuctionPost).where(AuctionPost.auction_id == auction_id))

    assert auction is not None
    assert auction.status == AuctionStatus.ACTIVE
    assert post is not None
    assert post.chat_id == chat_id
    assert post.message_id == 701


@pytest.mark.asyncio
async def test_publish_command_rolls_back_album_when_send_photo_fails(
    monkeypatch,
    publish_flow_session_factory,
) -> None:
    session_factory = publish_flow_session_factory
    monkeypatch.setattr("app.bot.handlers.publish_auction.SessionFactory", session_factory)

    async def _allow_publish(*_args, **_kwargs):
        return SimpleNamespace(allowed=True, block_message=None)

    monkeypatch.setattr("app.bot.handlers.publish_auction.evaluate_seller_publish_gate", _allow_publish)

    seller_tg_user_id, auction_id = await _seed_draft_auction(session_factory, seller_tg_user_id=94511)
    chat_id = -10094511

    message = _DummyMessage(
        text=f"/publish {auction_id}",
        from_user_id=seller_tg_user_id,
        chat_id=chat_id,
        message_id=82,
    )
    bot = _DummyBot(album_message_ids=[611, 612], post_message_id=711, fail_send_photo=True)

    await publish_auction_to_current_chat(message, bot)

    assert message.answers
    assert "Не удалось опубликовать лот" in message.answers[-1]
    assert bot.deleted_messages == [(chat_id, 611), (chat_id, 612)]

    async with session_factory() as session:
        auction = await session.scalar(select(Auction).where(Auction.id == auction_id))
        post = await session.scalar(select(AuctionPost).where(AuctionPost.auction_id == auction_id))

    assert auction is not None
    assert auction.status == AuctionStatus.DRAFT
    assert post is None


@pytest.mark.asyncio
async def test_publish_command_rolls_back_album_and_post_when_activation_fails(
    monkeypatch,
    publish_flow_session_factory,
) -> None:
    session_factory = publish_flow_session_factory
    monkeypatch.setattr("app.bot.handlers.publish_auction.SessionFactory", session_factory)

    async def _allow_publish(*_args, **_kwargs):
        return SimpleNamespace(allowed=True, block_message=None)

    async def _activate_none(*_args, **_kwargs):
        return None

    refresh_calls: list[str] = []

    async def _refresh_stub(_bot, auction_id):
        refresh_calls.append(str(auction_id))

    monkeypatch.setattr("app.bot.handlers.publish_auction.evaluate_seller_publish_gate", _allow_publish)
    monkeypatch.setattr("app.bot.handlers.publish_auction.activate_auction_chat_post", _activate_none)
    monkeypatch.setattr("app.bot.handlers.publish_auction.refresh_auction_posts", _refresh_stub)

    seller_tg_user_id, auction_id = await _seed_draft_auction(session_factory, seller_tg_user_id=94521)
    chat_id = -10094521

    message = _DummyMessage(
        text=f"/publish {auction_id}",
        from_user_id=seller_tg_user_id,
        chat_id=chat_id,
        message_id=83,
    )
    bot = _DummyBot(album_message_ids=[621, 622], post_message_id=721)

    await publish_auction_to_current_chat(message, bot)

    assert message.answers
    assert "Лот уже был опубликован" in message.answers[-1]
    assert bot.deleted_messages == [(chat_id, 721), (chat_id, 621), (chat_id, 622)]
    assert refresh_calls == []

    async with session_factory() as session:
        auction = await session.scalar(select(Auction).where(Auction.id == auction_id))
        post = await session.scalar(select(AuctionPost).where(AuctionPost.auction_id == auction_id))

    assert auction is not None
    assert auction.status == AuctionStatus.DRAFT
    assert post is None
