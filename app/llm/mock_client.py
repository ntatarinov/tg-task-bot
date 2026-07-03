from __future__ import annotations

import re
from typing import Literal

from app.llm.base import BaseLLMClient
from app.llm.schemas import DigestSummary, MessageAnalysis
from app.utils.logging import get_logger

Priority = Literal["low", "medium", "high", "critical"]

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
    for phrase in ["коля", "коле", "петя", "пете", "ваня", "ване"]:
        if phrase in text.lower():
            return phrase.capitalize()
    return None


def _detect_priority(text: str) -> Priority:
    lowered = text.lower()
    if any(w in lowered for w in ["срочно", "критично", "asap", "горит", "критический"]):
        return "critical"
    if any(w in lowered for w in ["важно", "высокий", "приоритет"]):
        return "high"
    if any(w in lowered for w in ["потом", "когда-нибудь", "не срочно"]):
        return "low"
    return "medium"


class MockLLMClient(BaseLLMClient):
    """Rule-based mock LLM for development without an API key."""

    async def analyze_message(
        self,
        message_text: str,
        context_messages: list[dict],
        active_tasks: list[dict],
        reply_to_text: str | None = None,
    ) -> MessageAnalysis:
        logger.debug("mock_llm.analyze_message", text_len=len(message_text))

        if _is_noise(message_text):
            return MessageAnalysis(message_type="noise", contains_task=False, confidence=0.9)

        has_task = _detect_task_signal(message_text)
        if not has_task:
            return MessageAnalysis(message_type="noise", contains_task=False, confidence=0.6)

        assignee = _extract_assignee(message_text)
        priority = _detect_priority(message_text)

        # Build a simple title from first sentence
        sentences = re.split(r"[.!?\n]", message_text)
        title = sentences[0].strip()[:200] if sentences else message_text[:200]

        open_questions: list[str] = []
        should_ask = False
        clarification_question: str | None = None

        if not assignee:
            open_questions.append("Кто будет выполнять эту задачу?")
            should_ask = True
            clarification_question = "Уточните, пожалуйста, кто возьмёт эту задачу?"

        confidence = 0.75 if has_task else 0.4

        return MessageAnalysis(
            message_type="task_signal",
            contains_task=True,
            new_task=True,
            task_title=title,
            summary=message_text[:500],
            assignee=assignee,
            priority=priority,
            open_questions=open_questions,
            should_ask_clarification=should_ask,
            clarification_question=clarification_question,
            confidence=confidence,
        )

    async def summarize_for_digest(
        self,
        messages: list[dict],
        tasks: list[dict],
    ) -> DigestSummary:
        key_points: list[str] = []
        for task in tasks[:5]:
            key_points.append(f"Задача: {task.get('title', '—')}")

        summary = f"За период поступило {len(messages)} сообщений, выявлено {len(tasks)} задач."
        return DigestSummary(key_points=key_points, summary=summary)
