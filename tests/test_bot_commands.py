from __future__ import annotations

import pytest
from aiogram.types import BotCommand

from app.main import configure_bot_commands


class _DummyBot:
    def __init__(self) -> None:
        self.commands_by_scope: dict[str, list[BotCommand]] = {}

    async def set_my_commands(self, commands, *, scope) -> None:  # noqa: ANN001
        self.commands_by_scope[scope.type] = list(commands)


@pytest.mark.asyncio
async def test_configure_bot_commands_includes_core_user_flows() -> None:
    bot = _DummyBot()

    await configure_bot_commands(bot)

    assert "all_private_chats" in bot.commands_by_scope
    command_map = {item.command: item.description for item in bot.commands_by_scope["all_private_chats"]}
    assert "start" in command_map
    assert "help" in command_map
    assert "newauction" in command_map
    assert "mybids" in command_map
    assert "myrep" in command_map
    assert "cancel" in command_map
    assert "settings" in command_map
    assert "points" in command_map
    assert "tradefeedback" in command_map
    assert "boostfeedback" in command_map
    assert "bug" in command_map
    assert "suggest" in command_map
