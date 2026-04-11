from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from app.bot.handlers import router as start_router
from app.config import settings
from app.db.session import dispose_database, ping_database
from app.infra.redis_client import close_redis, ping_redis
from app.logging_setup import configure_logging
from app.services.appeal_escalation_watcher import run_appeal_escalation_watcher
from app.services.auction_watcher import cancel_watcher, run_auction_watcher
from app.services.outbox_watcher import run_outbox_watcher

logger = logging.getLogger(__name__)


async def startup_checks() -> None:
    await ping_database()
    await ping_redis()
    logger.info("Startup checks passed: database and redis are available")


async def configure_bot_commands(bot: Bot) -> None:
    private_commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="newauction", description="Создать лот"),
        BotCommand(command="guarant", description="Запрос гаранта"),
        BotCommand(command="cancel", description="Отменить действие"),
    ]
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())

    group_commands = [
        BotCommand(command="publish", description="Опубликовать лот"),
    ]
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(
        storage=RedisStorage.from_url(settings.redis_url),
        events_isolation=RedisEventIsolation.from_url(settings.redis_url),
    )
    dp.include_router(start_router)
    return dp


async def run() -> None:
    configure_logging(settings.log_level)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()

    await startup_checks()
    await configure_bot_commands(bot)
    watcher_task: asyncio.Task[None] | None = asyncio.create_task(run_auction_watcher(bot))
    escalation_task: asyncio.Task[None] | None = asyncio.create_task(run_appeal_escalation_watcher(bot))
    outbox_task: asyncio.Task[None] | None = asyncio.create_task(run_outbox_watcher())

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await cancel_watcher(watcher_task)
        await cancel_watcher(escalation_task)
        await cancel_watcher(outbox_task)
        await dp.fsm.close()
        await bot.session.close()
        await close_redis()
        await dispose_database()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
