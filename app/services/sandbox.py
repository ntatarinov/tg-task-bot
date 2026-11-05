from __future__ import annotations

import html
import re
import time
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Topic, TopicItem
from app.db.repositories import ChatRepository
from app.i18n import t
from app.llm.base import BaseLLMClient
from app.services.conversation_analyzer import ConversationAnalyzer
from app.services.digest_service import DigestService
from app.services.message_ingestor import MessageIngestor
from app.utils.time import now_utc

MAX_SANDBOX_CHARS = 20000
MAX_BATCHES = 50
SANDBOX_CHAT_ID = -1

_LINE_RE = re.compile(r"^\s*([^:\n]{1,40}):\s*(.+)$")
_TAG_RE = re.compile(r"<[^>]+>")


def plain_text(digest_html: str) -> str:
    """A digest as text, for showing outside Telegram."""
    return html.unescape(_TAG_RE.sub("", digest_html))


def parse_chat(text: str, language: str = "en") -> list[tuple[str, str]]:
    """Read pasted lines of "Author: message". A line without an author continues the previous one."""
    messages: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _LINE_RE.match(line)
        if match:
            messages.append((match.group(1).strip(), match.group(2).strip()))
        elif messages:
            author, body = messages[-1]
            messages[-1] = (author, f"{body}\n{line.strip()}")
        else:
            messages.append((t(language, "sandbox.author"), line.strip()))
    return messages


async def run_sandbox(llm: BaseLLMClient, text: str, language: str = "en") -> dict[str, Any]:
    """
    Run a pasted conversation through the real pipeline in a throwaway in-memory database:
    the same analyzer and digest as production, with nothing written to the bot's data
    and no questions asked in any chat. LLMError propagates to the caller.
    """
    messages = parse_chat(text, language)
    chat_title = t(language, "sandbox.chat")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    started = time.monotonic()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            chat_repo = ChatRepository(session)
            chat = await chat_repo.get_or_create(SANDBOX_CHAT_ID, chat_title)
            await chat_repo.set_language(chat, language)
            await session.commit()

            ingestor = MessageIngestor(session)
            first_sent = now_utc() - timedelta(minutes=len(messages) + 1)
            for index, (author, body) in enumerate(messages, 1):
                await ingestor.ingest(
                    telegram_chat_id=SANDBOX_CHAT_ID,
                    chat_title=chat_title,
                    telegram_user_id=abs(hash(author)) % 1_000_000 + 1,
                    username=None,
                    display_name=author,
                    telegram_message_id=index,
                    text=body,
                    raw_update_json=None,
                    sent_at=first_sent + timedelta(minutes=index),
                )
            await session.commit()

            analyzer = ConversationAnalyzer(session, llm, ask_clarifications=False)
            rejected = failed = 0
            for _ in range(MAX_BATCHES):
                result = await analyzer.analyze_next_batch(chat)
                if result is None:
                    break
                rejected += result.items_rejected
                failed += result.failed_messages

            topics = await analyzer.topic_repo.list_recent(chat.id, 100)
            view = [_topic_view(t) for t in topics]
            digest = await DigestService(session, llm).build_digest(
                chat, first_sent - timedelta(minutes=1), now_utc() + timedelta(minutes=1), kind="manual"
            )
            await session.rollback()
            return {
                "messages": len(messages),
                "seconds": round(time.monotonic() - started, 1),
                "rejected": rejected,
                "failed": failed,
                "topics": view,
                "digest": plain_text(digest.text),
            }
    finally:
        await engine.dispose()


def _topic_view(topic: Topic) -> dict[str, Any]:
    return {
        "title": topic.title,
        "project": topic.project,
        "summary": topic.summary,
        "items": [_item_view(i) for i in topic.items],
    }


def _item_view(item: TopicItem) -> dict[str, Any]:
    return {
        "kind": item.kind.value,
        "status": item.status.value,
        "text": item.text,
        "owner": item.owner,
        "deadline": item.deadline,
    }
