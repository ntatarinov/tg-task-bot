from __future__ import annotations

from aiogram import Bot
from aiogram.types import BotCommand


async def set_bot_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Описание бота"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="tasks", description="Показать активные задачи"),
        BotCommand(command="digest", description="Сформировать дайджест прямо сейчас"),
        BotCommand(command="task", description="Создать задачу вручную: /task <текст>"),
        BotCommand(command="clarify", description="Задачи, требующие уточнения"),
        BotCommand(command="settings", description="Настройки чата"),
        BotCommand(command="set_timezone", description="Часовой пояс: /set_timezone <TZ>"),
        BotCommand(command="set_digest_time", description="Время дайджеста: /set_digest_time <HH:MM>"),
        BotCommand(command="digest_on", description="Включить дайджест в этом чате"),
        BotCommand(command="digest_off", description="Выключить дайджест в этом чате"),
    ]
    await bot.set_my_commands(commands)
