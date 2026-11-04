from __future__ import annotations

from aiogram import Bot
from aiogram.types import BotCommand


async def set_bot_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Описание бота"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="tasks", description="Показать активные задачи"),
        BotCommand(command="digest", description="Дайджест: /digest [today|yesterday|week|3d|дата дата]"),
        BotCommand(command="task", description="Создать задачу вручную: /task <текст>"),
        BotCommand(command="clarify", description="Задачи, требующие уточнения"),
        BotCommand(command="settings", description="Настройки чата"),
        BotCommand(command="status", description="Состояние анализа"),
        BotCommand(command="chatid", description="Показать ID чата (для администраторов)"),
        BotCommand(command="set_timezone", description="Часовой пояс: /set_timezone <TZ>"),
        BotCommand(command="set_digest_time", description="Время дайджеста: /set_digest_time <HH:MM>"),
        BotCommand(command="set_weekly", description="Еженедельный дайджест: /set_weekly <пн..вс|off>"),
        BotCommand(command="set_digest_chat", description="Куда слать отчёты: /set_digest_chat <id|here>"),
        BotCommand(command="digest_on", description="Включить дайджест в этом чате"),
        BotCommand(command="digest_off", description="Выключить дайджест в этом чате"),
        BotCommand(command="reanalyze", description="Повторить анализ: /reanalyze <дней>"),
        BotCommand(command="purge_history", description="Удалить историю: /purge_history <дней> confirm"),
        BotCommand(command="pin_dashboard", description="Закрепить ссылку на сайт с задачами"),
    ]
    await bot.set_my_commands(commands)
