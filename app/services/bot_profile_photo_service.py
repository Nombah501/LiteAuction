from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InputProfilePhotoStatic

from app.config import settings
from app.db.enums import ModerationAction


@dataclass(slots=True)
class BotProfilePhotoResult:
    ok: bool
    message: str
    action: ModerationAction | None = None
    reason: str | None = None
    payload: dict[str, str] | None = None


def list_bot_profile_photo_presets() -> list[str]:
    return sorted(settings.parsed_bot_profile_photo_presets().keys())


async def apply_bot_profile_photo_preset(bot: Bot, *, preset: str) -> BotProfilePhotoResult:
    normalized_preset = preset.strip().lower()
    if not normalized_preset:
        return BotProfilePhotoResult(ok=False, message="Укажите preset")

    presets = settings.parsed_bot_profile_photo_presets()
    file_id = presets.get(normalized_preset)
    if file_id is None:
        return BotProfilePhotoResult(ok=False, message=f"Preset '{normalized_preset}' не найден")

    try:
        changed = await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=file_id))
    except TelegramAPIError as exc:
        return BotProfilePhotoResult(ok=False, message=f"Не удалось обновить фото бота: {exc}")

    if not changed:
        return BotProfilePhotoResult(ok=False, message="Telegram не подтвердил смену фото")

    return BotProfilePhotoResult(
        ok=True,
        message=f"Фото бота переключено на preset '{normalized_preset}'",
        action=ModerationAction.SET_BOT_PROFILE_PHOTO,
        reason=f"set bot profile photo preset: {normalized_preset}",
        payload={"preset": normalized_preset},
    )


async def rollback_bot_profile_photo(bot: Bot) -> BotProfilePhotoResult:
    default_preset = settings.parsed_bot_profile_photo_default_preset()
    presets = settings.parsed_bot_profile_photo_presets()
    if default_preset is not None and default_preset in presets:
        result = await apply_bot_profile_photo_preset(bot, preset=default_preset)
        if not result.ok:
            return result
        return BotProfilePhotoResult(
            ok=True,
            message=f"Фото бота сброшено на default preset '{default_preset}'",
            action=ModerationAction.SET_BOT_PROFILE_PHOTO,
            reason=f"reset bot profile photo to default preset: {default_preset}",
            payload={"preset": default_preset, "rollback": "default_preset"},
        )

    try:
        removed = await bot.remove_my_profile_photo()
    except TelegramAPIError as exc:
        return BotProfilePhotoResult(ok=False, message=f"Не удалось удалить фото бота: {exc}")

    if not removed:
        return BotProfilePhotoResult(ok=False, message="Telegram не подтвердил удаление фото")

    return BotProfilePhotoResult(
        ok=True,
        message="Фото бота сброшено (кастомное фото удалено)",
        action=ModerationAction.REMOVE_BOT_PROFILE_PHOTO,
        reason="reset bot profile photo by removing custom photo",
        payload={"rollback": "remove"},
    )
