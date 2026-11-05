from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand

from app.i18n import FALLBACK_LANGUAGE, LANGUAGES, t
from app.utils.logging import get_logger

logger = get_logger(__name__)

COMMANDS = (
    "start", "help", "tasks", "digest", "task", "clarify", "settings", "status", "chatid",
    "set_timezone", "set_digest_time", "set_weekly", "set_digest_chat", "digest_on", "digest_off",
    "language", "reanalyze", "purge_history", "pin_dashboard",
)


async def set_bot_commands(bot: Bot) -> None:
    """Telegram shows each person the version for their app language. The one without a
    language code is what everyone else sees, so English goes there."""
    for language in LANGUAGES:
        code = None if language == FALLBACK_LANGUAGE else language
        commands = [BotCommand(command=name, description=t(language, f"cmd.{name}")) for name in COMMANDS]
        await bot.set_my_commands(commands, language_code=code)
        try:
            await bot.set_my_description(t(language, "bot.description"), language_code=code)
            await bot.set_my_short_description(t(language, "bot.short_description"), language_code=code)
        except TelegramAPIError as e:
            # Only the profile text: not worth keeping the bot from starting.
            logger.warning("bot.description_failed", language=language, error=type(e).__name__)
