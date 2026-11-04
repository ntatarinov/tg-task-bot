from __future__ import annotations

import re

from app.llm.base import BaseLLMClient
from app.llm.schemas import (
    BatchMessage,
    ConversationAnalysis,
    ExtractedItem,
    ExtractedTopic,
    TopicContext,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

TASK_KEYWORDS = [
    "сделать", "нужно", "задача", "реализовать", "добавить", "исправить",
    "починить", "написать", "разработать", "внедрить", "надо", "необходимо",
    "доделать", "завершить", "запустить", "настроить", "проверить",
    "task", "todo", "fix", "implement", "add", "create", "build",
]

NOISE_PATTERNS = [
    r"^[😀-🙏👀-🙌🎉-🎊]+$",
    r"^\+\d*$",
    r"^(ок|окей|ладно|хорошо|понял|спасибо|thanks|ok|lol|😂|👍)+$",
]

MOCK_TOPIC_TITLE = "Рабочие задачи"


def _is_noise(text: str) -> bool:
    lowered = text.lower().strip()
    if len(lowered) < 3:
        return True
    for pattern in NOISE_PATTERNS:
        if re.fullmatch(pattern, lowered, re.IGNORECASE):
            return True
    return False


def _detect_task_signal(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in TASK_KEYWORDS)


def _extract_assignee(text: str) -> str | None:
    matches = re.findall(r"@(\w+)", text)
    if matches:
        return "@" + matches[0]
    return None


class MockLLMClient(BaseLLMClient):
    """Rule-based stand-in for development without a model. It does not understand
    anything: every task-looking message lands in one catch-all topic."""

    async def analyze_conversation(
        self,
        messages: list[BatchMessage],
        topics: list[TopicContext],
        older_topics: list[dict],
    ) -> ConversationAnalysis:
        logger.debug("mock_llm.analyze_conversation", messages=len(messages))
        items: list[ExtractedItem] = []
        for message in messages:
            if _is_noise(message.text) or not _detect_task_signal(message.text):
                continue
            title = re.split(r"[.!?\n]", message.text)[0].strip()[:200]
            items.append(
                ExtractedItem(
                    kind="task",
                    text=title or message.text[:200],
                    quote=message.text[:200],
                    owner=_extract_assignee(message.text),
                    source_ids=[message.id],
                )
            )
        if not items:
            return ConversationAnalysis()

        known = next((t for t in topics if t.title == MOCK_TOPIC_TITLE), None)
        return ConversationAnalysis(
            topics=[
                ExtractedTopic(
                    topic_id=known.id if known else None,
                    title=MOCK_TOPIC_TITLE,
                    summary=f"Выявлено задач: {len(items)}.",
                    message_ids=[i for item in items for i in item.source_ids],
                    items=items,
                )
            ]
        )

    async def merge_summaries(self, fragments: dict[int, list[str]]) -> dict[int, str]:
        return {topic_id: " ".join(parts) for topic_id, parts in fragments.items()}
