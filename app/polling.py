from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiogram import Dispatcher
from apscheduler.triggers.interval import IntervalTrigger

from app.bot.dispatcher import setup_bot
from app.config import settings
from app.db.redis import close_redis
from app.db.session import engine
from app.services.scheduler import get_scheduler
from app.startup import run_startup_checks
from app.utils.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

# Asks the supervisor for a new process: Docker restarts on any exit, and
# scripts/start_local.ps1 loops on this code.
RESTART_EXIT_CODE = 75
RESTART_CHECK_SECONDS = 5


async def watch_restart_flag(dp: Dispatcher, state: dict[str, bool]) -> None:
    """Stop polling cleanly when the settings panel asks for a restart."""
    flag = Path(settings.restart_flag_path)
    if flag.exists():
        flag.unlink(missing_ok=True)
        state["restart"] = True
        logger.info("polling.restart_requested")
        await dp.stop_polling()


async def main() -> None:
    """
    Long-polling entrypoint for local development — no public HTTPS / webhook needed.
    Run with:  python -m app.polling
    """
    logger.info("polling.startup", env=settings.app_env)
    await run_startup_checks()
    # A request made while the bot was down is already satisfied by this start.
    Path(settings.restart_flag_path).unlink(missing_ok=True)
    bot, dp = await setup_bot()

    # Polling and webhooks are mutually exclusive, so remove any webhook. Updates that
    # arrived while the bot was down are kept: Telegram holds them for 24 hours.
    await bot.delete_webhook(drop_pending_updates=False)

    state = {"restart": False}
    scheduler = get_scheduler()
    scheduler.add_job(
        watch_restart_flag,
        IntervalTrigger(seconds=RESTART_CHECK_SECONDS),
        args=[dp, state],
        id="restart_watch",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("polling.started")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await close_redis()
        await engine.dispose()
        logger.info("polling.shutdown")
    if state["restart"]:
        sys.exit(RESTART_EXIT_CODE)


if __name__ == "__main__":
    asyncio.run(main())
