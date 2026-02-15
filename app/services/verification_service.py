from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TelegramChatVerification, TelegramUserVerification, User


@dataclass(slots=True)
class VerificationStatus:
    is_verified: bool
    custom_description: str | None
    updated_at: datetime | None


@dataclass(slots=True)
class VerificationUpdateResult:
    ok: bool
    message: str
    is_verified: bool


def _normalize_description(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return text[:70]


async def _load_or_create_user_verification(
    session: AsyncSession,
    *,
    tg_user_id: int,
) -> TelegramUserVerification:
    row = await session.scalar(
        select(TelegramUserVerification)
        .where(TelegramUserVerification.tg_user_id == tg_user_id)
        .with_for_update()
    )
    if row is not None:
        return row

    now = datetime.now(UTC)
    row = TelegramUserVerification(
        tg_user_id=tg_user_id,
        is_verified=False,
        custom_description=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def _load_or_create_chat_verification(
    session: AsyncSession,
    *,
    chat_id: int,
) -> TelegramChatVerification:
    row = await session.scalar(
        select(TelegramChatVerification)
        .where(TelegramChatVerification.chat_id == chat_id)
        .with_for_update()
    )
    if row is not None:
        return row

    now = datetime.now(UTC)
    row = TelegramChatVerification(
        chat_id=chat_id,
        is_verified=False,
        custom_description=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def set_user_verification(
    session: AsyncSession,
    bot: Bot,
    *,
    actor_user_id: int,
    target_tg_user_id: int,
    verify: bool,
    custom_description: str | None = None,
) -> VerificationUpdateResult:
    description = _normalize_description(custom_description)

    try:
        if verify:
            success = await bot.verify_user(user_id=target_tg_user_id, custom_description=description)
            if not success:
                return VerificationUpdateResult(False, "Telegram API не подтвердила верификацию", False)
        else:
            success = await bot.remove_user_verification(user_id=target_tg_user_id)
            if not success:
                return VerificationUpdateResult(False, "Telegram API не сняла верификацию", True)
    except TelegramAPIError as exc:
        return VerificationUpdateResult(False, f"Ошибка Telegram API: {exc}", not verify)

    row = await _load_or_create_user_verification(session, tg_user_id=target_tg_user_id)
    now = datetime.now(UTC)
    row.is_verified = verify
    row.custom_description = description if verify else None
    row.updated_by_user_id = actor_user_id
    row.updated_at = now

    return VerificationUpdateResult(
        True,
        "Верификация пользователя обновлена" if verify else "Верификация пользователя снята",
        verify,
    )


async def set_chat_verification(
    session: AsyncSession,
    bot: Bot,
    *,
    actor_user_id: int,
    chat_id: int,
    verify: bool,
    custom_description: str | None = None,
) -> VerificationUpdateResult:
    description = _normalize_description(custom_description)

    try:
        if verify:
            success = await bot.verify_chat(chat_id=chat_id, custom_description=description)
            if not success:
                return VerificationUpdateResult(False, "Telegram API не подтвердила верификацию чата", False)
        else:
            success = await bot.remove_chat_verification(chat_id=chat_id)
            if not success:
                return VerificationUpdateResult(False, "Telegram API не сняла верификацию чата", True)
    except TelegramAPIError as exc:
        return VerificationUpdateResult(False, f"Ошибка Telegram API: {exc}", not verify)

    row = await _load_or_create_chat_verification(session, chat_id=chat_id)
    now = datetime.now(UTC)
    row.is_verified = verify
    row.custom_description = description if verify else None
    row.updated_by_user_id = actor_user_id
    row.updated_at = now

    return VerificationUpdateResult(
        True,
        "Верификация чата обновлена" if verify else "Верификация чата снята",
        verify,
    )


async def get_user_verification_status(session: AsyncSession, *, tg_user_id: int) -> VerificationStatus:
    row = await session.scalar(
        select(TelegramUserVerification).where(TelegramUserVerification.tg_user_id == tg_user_id)
    )
    if row is None:
        return VerificationStatus(is_verified=False, custom_description=None, updated_at=None)
    return VerificationStatus(
        is_verified=bool(row.is_verified),
        custom_description=row.custom_description,
        updated_at=row.updated_at,
    )


async def get_chat_verification_status(session: AsyncSession, *, chat_id: int) -> VerificationStatus:
    row = await session.scalar(
        select(TelegramChatVerification).where(TelegramChatVerification.chat_id == chat_id)
    )
    if row is None:
        return VerificationStatus(is_verified=False, custom_description=None, updated_at=None)
    return VerificationStatus(
        is_verified=bool(row.is_verified),
        custom_description=row.custom_description,
        updated_at=row.updated_at,
    )


async def is_user_verified(session: AsyncSession, *, tg_user_id: int) -> bool:
    row = await session.scalar(
        select(TelegramUserVerification.is_verified).where(TelegramUserVerification.tg_user_id == tg_user_id)
    )
    return bool(row)


async def load_verified_user_ids(
    session: AsyncSession,
    *,
    user_ids: list[int],
) -> set[int]:
    unique_user_ids = sorted(set(user_ids))
    if not unique_user_ids:
        return set()

    rows = (
        await session.execute(
            select(User.id)
            .join(TelegramUserVerification, TelegramUserVerification.tg_user_id == User.tg_user_id)
            .where(
                User.id.in_(unique_user_ids),
                TelegramUserVerification.is_verified.is_(True),
            )
        )
    ).scalars().all()
    return {int(user_id) for user_id in rows}


async def load_verified_tg_user_ids(
    session: AsyncSession,
    *,
    tg_user_ids: list[int],
) -> set[int]:
    unique_tg_user_ids = sorted(set(tg_user_ids))
    if not unique_tg_user_ids:
        return set()

    rows = (
        await session.execute(
            select(TelegramUserVerification.tg_user_id).where(
                TelegramUserVerification.tg_user_id.in_(unique_tg_user_ids),
                TelegramUserVerification.is_verified.is_(True),
            )
        )
    ).scalars().all()
    return {int(tg_user_id) for tg_user_id in rows}
