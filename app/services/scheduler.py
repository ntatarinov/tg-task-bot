from __future__ import annotations

from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.db.redis import cache_set_if_absent
from app.db.repositories import ChatRepository, DigestRepository, MessageRepository
from app.db.session import async_session_factory
from app.llm.openai_client import create_llm_client
from app.services.digest_service import DigestService
from app.services.task_analyzer import TaskAnalyzer
from app.utils import metrics
from app.utils.logging import get_logger
from app.utils.time import ensure_aware_utc

logger = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _make_llm():
    return create_llm_client(
        provider=settings.llm_provider,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )


def chat_is_due(digest_time: str, now_local: datetime) -> bool:
    """True when the chat's local HH:MM equals its configured digest_time."""
    return now_local.strftime("%H:%M") == digest_time


async def _already_sent_today(
    digest_repo: DigestRepository, chat_id: int, now_local: datetime, tz: pytz.BaseTzInfo
) -> bool:
    """Dedup guard: Redis SET NX claim, falling back to the digests table when Redis is down."""
    key = f"digest:sent:{chat_id}:{now_local.date().isoformat()}"
    acquired = await cache_set_if_absent(key, "1", ttl_seconds=36 * 3600)
    if acquired is True:
        return False
    if acquired is False:
        return True
    # Redis unavailable — read the last sent digest from the database.
    last_sent = await digest_repo.get_last_sent_at(chat_id)
    if last_sent is None:
        return False
    return ensure_aware_utc(last_sent).astimezone(tz).date() == now_local.date()


async def digest_tick() -> None:
    """Runs every minute; sends each chat's digest when its local time hits digest_time."""
    from app.bot.dispatcher import get_bot
    from app.bot.utils import safe_send_message

    bot = get_bot()
    if bot is None:
        return

    async with async_session_factory() as session:
        chat_repo = ChatRepository(session)
        digest_repo = DigestRepository(session)
        llm = _make_llm()
        digest_svc = DigestService(session, llm)

        try:
            chats = await chat_repo.get_all_active()
        except Exception as e:
            logger.error("scheduler.digest_tick.load_chats_failed", error=str(e))
            return

        for chat in chats:
            if not chat.digest_enabled:
                continue
            try:
                tz = pytz.timezone(chat.timezone)
            except Exception:
                tz = pytz.timezone(settings.default_timezone)
            now_local = datetime.now(tz)

            if not chat_is_due(chat.digest_time, now_local):
                continue
            if await _already_sent_today(digest_repo, chat.id, now_local, tz):
                continue

            try:
                text = await digest_svc.build_digest(chat)
                await session.commit()
            except Exception as e:
                logger.error("scheduler.build_digest_failed", chat_id=chat.id, error=str(e))
                await session.rollback()
                continue

            sent = await safe_send_message(
                bot,
                chat_id=chat.telegram_chat_id,
                text=text,
                parse_mode="Markdown",
            )
            if sent is not None:
                metrics.digests_sent_total.inc()
                logger.info("scheduler.digest_sent", chat_id=chat.telegram_chat_id)


async def retry_pending_analysis() -> None:
    """Re-run analysis for messages saved while the LLM was unavailable."""
    from app.bot.dispatcher import get_bot
    from app.bot.utils import safe_send_message
    from app.services.clarification_service import ClarificationService

    async with async_session_factory() as session:
        message_repo = MessageRepository(session)
        try:
            pending = await message_repo.get_pending_analysis()
        except Exception as e:
            logger.error("scheduler.retry.load_failed", error=str(e))
            return

        if not pending:
            return

        bot = get_bot()
        llm = _make_llm()
        analyzer = TaskAnalyzer(session, llm)
        logger.info("scheduler.retry.start", count=len(pending))

        for message in pending:
            try:
                to_ask = await analyzer.analyze_and_process(
                    message=message,
                    chat=message.chat,
                    user=message.user,
                )
                await message_repo.mark_analysis_done(message.id)
                await session.commit()
            except Exception as e:
                logger.error("scheduler.retry.message_failed", message_id=message.id, error=str(e))
                await session.rollback()
                continue

            if to_ask and bot is not None:
                sent = await safe_send_message(
                    bot,
                    chat_id=message.chat.telegram_chat_id,
                    text=to_ask.question,
                    reply_to_message_id=message.telegram_message_id,
                )
                if sent is not None:
                    try:
                        svc = ClarificationService(session)
                        await svc.set_question_message_id(to_ask.clarification_id, sent.message_id)
                        await session.commit()
                    except Exception as e:
                        logger.error("scheduler.retry.set_qmid_failed", error=str(e))
                        await session.rollback()


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=settings.default_timezone)
        _scheduler.add_job(
            digest_tick,
            IntervalTrigger(seconds=60),
            id="digest_tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _scheduler.add_job(
            retry_pending_analysis,
            IntervalTrigger(seconds=settings.pending_retry_interval_seconds),
            id="retry_pending_analysis",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    return _scheduler
