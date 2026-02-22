from __future__ import annotations

import pytest
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from app.main import configure_bot_commands


class _DummyBot:
    def __init__(self) -> None:
        self.commands: list[BotCommand] = []
        self.scope: BotCommandScopeAllPrivateChats | None = None

    async def set_my_commands(self, commands, *, scope) -> None:  # noqa: ANN001
        self.commands = list(commands)
        self.scope = scope


@pytest.mark.asyncio
async def test_configure_bot_commands_includes_core_user_flows() -> None:
    bot = _DummyBot()

    await configure_bot_commands(bot)

    assert isinstance(bot.scope, BotCommandScopeAllPrivateChats)
    command_map = {item.command: item.description for item in bot.commands}
    assert "start" in command_map
    assert "newauction" in command_map
    assert "cancel" in command_map
    assert "settings" in command_map
    assert "points" in command_map
    assert "tradefeedback" in command_map
    assert "boostfeedback" in command_map
    assert "bug" in command_map
    assert "suggest" in command_map
