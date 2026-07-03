from __future__ import annotations

import asyncio

from app.bot.dispatcher import setup_bot
from app.config import settings
from app.db.redis import close_redis
from app.db.session import engine
from app.services.scheduler import get_scheduler
from app.utils.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


async def main() -> None:
    """
    Long-polling entrypoint for local development — no public HTTPS / webhook needed.
    Run with:  python -m app.polling
    """
    logger.info("polling.startup", env=settings.app_env)
    bot, dp = await setup_bot()

    # Polling and webhooks are mutually exclusive; drop any existing webhook first.
    await bot.delete_webhook(drop_pending_updates=True)

    scheduler = get_scheduler()
    scheduler.start()
    logger.info("polling.started")

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await close_redis()
        await engine.dispose()
        logger.info("polling.shutdown")


if __name__ == "__main__":
    asyncio.run(main())
