from __future__ import annotations

import asyncio
from collections import defaultdict

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.utils import safe_send_message
from app.config import settings
from app.db.models import Chat
from app.i18n import chat_language, t
from app.llm.base import BaseLLMClient, LLMUnavailableError
from app.llm.openai_client import llm_block_reason
from app.services.clarification_service import ClarificationService
from app.services.conversation_analyzer import BatchResult, ConversationAnalyzer
from app.utils.logging import get_logger
from app.utils.task_labels import format_clarification_question

logger = get_logger(__name__)

# The scheduler and a manual /digest can both reach for the same chat's queue;
# without this they would analyze the same batch twice.
_chat_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


async def send_followups(bot: Bot | None, session: AsyncSession, chat: Chat, result: BatchResult) -> None:
    if bot is None:
        return
    if result.clarification:
        sent = await safe_send_message(
            bot,
            chat.telegram_chat_id,
            format_clarification_question(result.clarification.question, chat_language(chat)),
            parse_mode=None,
        )
        # Remember which Telegram message carried the question so a reply to it
        # can later be matched as the answer (see ClarificationService).
        if sent is not None:
            await ClarificationService(session).set_question_message_id(
                result.clarification.clarification_id, sent.message_id
            )
            await session.commit()
    for notify in result.notifications:
        # Most often this fails because the assignee never started a DM with the
        # bot; safe_send_message logs it and the task itself is unaffected.
        await safe_send_message(
            bot,
            notify.telegram_user_id,
            t(
                chat_language(chat),
                "bot.assigned",
                task=notify.task_title,
                chat=notify.chat_title,
            ),
            parse_mode=None,
        )


async def run_batch(
    bot: Bot | None, session: AsyncSession, llm: BaseLLMClient, chat: Chat, only_if_due: bool = True
) -> BatchResult | None:
    """One batch for one chat under its lock. LLMUnavailableError propagates to the caller."""
    async with _chat_locks[chat.id]:
        analyzer = ConversationAnalyzer(session, llm)
        if only_if_due and not await analyzer.is_due(chat):
            return None
        result = await analyzer.analyze_next_batch(chat)
    if result is not None:
        await send_followups(bot, session, chat, result)
    return result


async def flush_pending(bot: Bot | None, session: AsyncSession, llm: BaseLLMClient, chat: Chat) -> None:
    """Best-effort catch-up before a digest. Whatever remains is reported in the digest itself."""
    if llm_block_reason():
        return
    for _ in range(settings.digest_flush_batches):
        try:
            result = await run_batch(bot, session, llm, chat, only_if_due=False)
        except LLMUnavailableError as e:
            logger.warning("analysis.flush_stopped", chat_id=chat.id, reason=str(e))
            return
        if result is None:
            return
