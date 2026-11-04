from __future__ import annotations

import hmac
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
from app.startup import run_startup_checks
from app.utils import metrics
from app.utils.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

_bot = None
_dp = None


def webhook_secret_valid(received: str) -> bool:
    if not settings.webhook_secret:
        # Without a secret anyone who finds the URL can forge chat messages.
        return settings.app_env == "local"
    return hmac.compare_digest(settings.webhook_secret, received)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot, _dp
    logger.info("app.startup", env=settings.app_env)

    await run_startup_checks()
    _bot, _dp = await setup_bot()

    if settings.webhook_url:
        await _bot.set_webhook(
            url=settings.webhook_url,
            drop_pending_updates=False,
            secret_token=settings.webhook_secret or None,
            allowed_updates=_dp.resolve_used_update_types(),
        )
        logger.info("bot.webhook_set", url=settings.webhook_url)
    else:
        logger.warning("bot.no_webhook_url", hint="Set WEBHOOK_URL in .env to use webhook mode")

    scheduler = None
    if settings.enable_scheduler:
        scheduler = get_scheduler()
        scheduler.start()
        logger.info("scheduler.started")
    else:
        logger.info("scheduler.disabled", hint="ENABLE_SCHEDULER=false — assuming app.polling runs it")

    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)
    if _bot:
        await _bot.session.close()
    from app.db.redis import close_redis
    await close_redis()
    await engine.dispose()
    logger.info("app.shutdown")


app = FastAPI(title="Digest", lifespan=lifespan)
app.include_router(api_router)


@app.get("/metrics")
async def prometheus_metrics() -> Response:
    return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE)


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> Response:
    if _bot is None or _dp is None:
        return Response(content="Bot not initialized", status_code=503)

    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not webhook_secret_valid(received):
        logger.warning("webhook.bad_secret")
        return Response(content="Forbidden", status_code=403)

    try:
        body = await request.body()
        data: dict[str, Any] = json.loads(body)
        update = Update(**data)
        await _dp.feed_update(_bot, update)
    except Exception as e:
        logger.error("webhook.error", error=type(e).__name__)
        # A non-2xx makes Telegram redeliver, and saving is idempotent, so a retry is safe.
        return Response(content="error", status_code=500)

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
