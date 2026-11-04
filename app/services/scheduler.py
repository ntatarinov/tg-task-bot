from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Chat
from app.db.repositories import ChatRepository, DigestRepository
from app.db.session import async_session_factory
from app.llm.base import BaseLLMClient, LLMUnavailableError
from app.llm.openai_client import create_llm_client, llm_block_reason
from app.services.analysis_runner import flush_pending, run_batch
from app.services.digest_service import DigestService
from app.services.retention import RetentionService
from app.utils import metrics
from app.utils.logging import get_logger
from app.utils.time import chat_timezone, ensure_aware_utc, now_utc

logger = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None
_last_block_reason: str | None = None


def _make_llm():
    return create_llm_client(
        provider=settings.llm_provider,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )


def due_slot(digest_time: str, now_local: datetime, catchup_hours: int = 0) -> date | None:
    """
    The day whose digest should go out now, if any: the latest occurrence of digest_time,
    while it is within the catch-up window. A restart at 18:00 therefore delays the digest
    instead of skipping the day, and a 23:00 slot can be caught up after midnight.
    """
    hour, minute = map(int, digest_time.split(":"))
    slot = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now_local:
        slot -= timedelta(days=1)
    if now_local - slot < timedelta(hours=catchup_hours, minutes=1):
        return slot.date()
    return None


def weekly_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


async def _allowed_chat_ids(session: AsyncSession) -> list[int]:
    """Ids rather than objects: a rollback after one chat's failure expires every loaded
    object, so each chat has to be fetched afresh when its turn comes."""
    chats = await ChatRepository(session).get_all_active()
    return [c.id for c in chats if c.telegram_chat_id in settings.allowed_chats]


async def run_scheduled_digest(
    bot: Bot, session: AsyncSession, llm: BaseLLMClient, chat: Chat, kind: str, period_key: str
) -> None:
    """Build once per slot, then deliver until Telegram has accepted all of it."""
    digest_repo = DigestRepository(session)
    service = DigestService(session, llm)

    digest = await digest_repo.get_slot(chat.id, kind, period_key)
    if digest is not None and digest.sent_at is not None:
        return

    if digest is None:
        await flush_pending(bot, session, llm, chat)
        end = now_utc()
        previous_end = await digest_repo.last_period_end(chat.id, kind)
        fallback = end - timedelta(days=7 if kind == "weekly" else 1)
        start = ensure_aware_utc(previous_end) if previous_end else fallback
        try:
            digest = await service.build_digest(chat, start, end, kind, period_key)
            await session.commit()
        except IntegrityError:
            # Another process claimed this slot first.
            await session.rollback()
            return

    target = chat.digest_chat_id or chat.telegram_chat_id
    if await service.deliver(bot, digest, target):
        metrics.digests_sent_total.inc()
        logger.info("scheduler.digest_sent", chat_id=chat.id, kind=kind, period=period_key)
    else:
        logger.error("scheduler.digest_undelivered", chat_id=chat.id, kind=kind, period=period_key)


async def digest_tick() -> None:
    """Runs every minute; sends each chat's digests once their local time has come."""
    from app.bot.dispatcher import get_bot

    bot = get_bot()
    if bot is None:
        return

    async with async_session_factory() as session:
        llm = _make_llm()
        chat_repo = ChatRepository(session)
        try:
            chat_ids = await _allowed_chat_ids(session)
        except Exception as e:
            logger.error("scheduler.digest_tick.load_chats_failed", error=type(e).__name__)
            return

        for chat_id in chat_ids:
            chat = await chat_repo.get_by_id(chat_id)
            if chat is None or not chat.digest_enabled:
                continue
            now_local = datetime.now(chat_timezone(chat.timezone))
            slot = due_slot(chat.digest_time, now_local, settings.digest_catchup_hours)
            if slot is None:
                continue

            planned = [("daily", slot.isoformat())]
            if chat.weekly_digest_enabled and slot.weekday() == chat.weekly_digest_day:
                planned.append(("weekly", weekly_key(slot)))

            for kind, period_key in planned:
                try:
                    await run_scheduled_digest(bot, session, llm, chat, kind, period_key)
                except Exception as e:
                    logger.error(
                        "scheduler.digest_failed", chat_id=chat_id, kind=kind, error=type(e).__name__
                    )
                    await session.rollback()
                    break


async def analysis_tick() -> None:
    """Take one batch per chat. An outage just leaves messages queued for the next tick."""
    from app.bot.dispatcher import get_bot

    global _last_block_reason
    blocked = llm_block_reason()
    if blocked != _last_block_reason:
        _last_block_reason = blocked
        if blocked:
            logger.warning("analysis.paused", reason=blocked)
    if blocked:
        return

    async with async_session_factory() as session:
        llm = _make_llm()
        chat_repo = ChatRepository(session)
        try:
            chat_ids = await _allowed_chat_ids(session)
        except Exception as e:
            logger.error("scheduler.analysis.load_chats_failed", error=type(e).__name__)
            return

        for chat_id in chat_ids:
            try:
                chat = await chat_repo.get_by_id(chat_id)
                if chat is not None:
                    await run_batch(get_bot(), session, llm, chat)
            except LLMUnavailableError as e:
                # Every chat uses the same model, so the others would fail the same way.
                logger.warning("analysis.llm_unavailable", reason=str(e))
                return
            except Exception as e:
                logger.error("analysis.failed", chat_id=chat_id, error=type(e).__name__)
                await session.rollback()


async def retention_tick() -> None:
    async with async_session_factory() as session:
        try:
            await RetentionService(session).apply_policy()
        except Exception as e:
            logger.error("retention.failed", error=type(e).__name__)
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
            analysis_tick,
            IntervalTrigger(seconds=settings.analysis_interval_seconds),
            id="analysis_tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _scheduler.add_job(
            retention_tick,
            IntervalTrigger(hours=1),
            id="retention_tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    return _scheduler
