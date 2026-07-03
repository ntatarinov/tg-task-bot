from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.commands import set_bot_commands
from app.bot.handlers import router
from app.config import settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

_bot: Bot | None = None
_dp: Dispatcher | None = None


def get_bot() -> Bot | None:
    return _bot


def create_bot() -> Bot:
    global _bot
    _bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    return _bot


def create_dispatcher() -> Dispatcher:
    global _dp
    _dp = Dispatcher()
    _dp.include_router(router)
    return _dp


async def setup_bot() -> tuple[Bot, Dispatcher]:
    bot = create_bot()
    dp = create_dispatcher()
    await set_bot_commands(bot)
    logger.info("bot.setup_complete")
    return bot, dp
