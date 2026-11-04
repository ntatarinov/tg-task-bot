from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytz

from app.db.models import Chat
from app.db.repositories import ChatRepository
from app.llm.base import BaseLLMClient
from app.llm.schemas import BatchMessage, ConversationAnalysis, TopicContext
from app.services.conversation_analyzer import BatchResult, ConversationAnalyzer
from app.services.message_ingestor import MessageIngestor

BASE_TIME = pytz.utc.localize(datetime(2026, 11, 4, 6, 0))  # Wednesday, 11:00 in Almaty


class ScriptedLLM(BaseLLMClient):
    """Plays back prepared answers: a dict, an exception to raise, or a function of the call."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.merge_calls: list[dict[int, list[str]]] = []

    async def analyze_conversation(
        self,
        messages: list[BatchMessage],
        topics: list[TopicContext],
        older_topics: list[dict],
    ) -> ConversationAnalysis:
        self.calls.append({"messages": messages, "topics": topics, "older_topics": older_topics})
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(messages, topics)
        return ConversationAnalysis(**response)

    async def merge_summaries(self, fragments: dict[int, list[str]]) -> dict[int, str]:
        self.merge_calls.append(fragments)
        return {k: " ".join(v) for k, v in fragments.items()}


class FakeBot:
    """Records outgoing messages; `failures` holds exceptions to raise on upcoming sends."""

    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.sent: list[dict] = []
        self.failures = failures or []
        self._next_id = 9000

    async def send_message(self, chat_id: int, text: str, **kwargs):
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        self._next_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return SimpleNamespace(message_id=self._next_id)


async def make_chat(session, telegram_chat_id: int = -1001234567890, title: str = "Team") -> Chat:
    chat = await ChatRepository(session).get_or_create(telegram_chat_id, title)
    await session.commit()
    return chat


async def feed(
    session,
    chat: Chat,
    lines: list[tuple],
    start_id: int = 1,
    start: datetime = BASE_TIME,
) -> list:
    """Ingest (author, text) or (author, text, reply_to_telegram_id) tuples, one minute apart."""
    ingestor = MessageIngestor(session)
    saved = []
    for offset, line in enumerate(lines):
        author, text = line[0], line[1]
        message, _ = await ingestor.ingest(
            telegram_chat_id=chat.telegram_chat_id,
            chat_title=chat.title,
            telegram_user_id=abs(hash(author)) % 1_000_000 + 1,
            username=None,
            display_name=author,
            telegram_message_id=start_id + offset,
            text=text,
            raw_update_json={},
            reply_to_message_id=line[2] if len(line) > 2 else None,
            sent_at=start + timedelta(minutes=offset),
        )
        saved.append(message)
    await session.commit()
    return saved


async def analyze(session, chat: Chat, llm: BaseLLMClient) -> list[BatchResult]:
    """Drain the queue the way the scheduler does, batch by batch."""
    analyzer = ConversationAnalyzer(session, llm)
    results = []
    while (result := await analyzer.analyze_next_batch(chat)) is not None:
        results.append(result)
    return results
