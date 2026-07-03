from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from aiogram.types import Update
from fastapi import FastAPI, Request, Response

from app.api.routes import router as api_router
from app.bot.dispatcher import setup_bot
from app.config import settings
from app.db.session import engine
from app.services.scheduler import get_scheduler
from app.utils import metrics
from app.utils.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

_bot = None
_dp = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot, _dp
    logger.info("app.startup", env=settings.app_env)

    _bot, _dp = await setup_bot()

    if settings.webhook_url:
        await _bot.set_webhook(
            url=settings.webhook_url,
            drop_pending_updates=True,
        )
        logger.info("bot.webhook_set", url=settings.webhook_url)
    else:
        logger.warning("bot.no_webhook_url", hint="Set WEBHOOK_URL in .env to use webhook mode")

    scheduler = get_scheduler()
    scheduler.start()
    logger.info("scheduler.started")

    yield

    scheduler.shutdown(wait=False)
    if _bot:
        await _bot.session.close()
    from app.db.redis import close_redis
    await close_redis()
    await engine.dispose()
    logger.info("app.shutdown")


app = FastAPI(title="Тудушка", lifespan=lifespan)
app.include_router(api_router)


@app.get("/metrics")
async def prometheus_metrics() -> Response:
    return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE)


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> Response:
    if _bot is None or _dp is None:
        return Response(content="Bot not initialized", status_code=503)

    try:
        body = await request.body()
        data: dict[str, Any] = json.loads(body)
        update = Update(**data)
        await _dp.feed_update(_bot, update)
    except Exception as e:
        logger.error("webhook.error", error=str(e))

    return Response(content="ok")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    try:
        from sqlalchemy import text

        from app.db.session import async_session_factory
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        logger.error("readiness.db_check_failed", error=str(e))
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Database unavailable")
