from __future__ import annotations

import json
import re

from openai import AsyncOpenAI

from app.config import settings
from app.llm.base import BaseLLMClient
from app.llm.schemas import DigestSummary, MessageAnalysis
from app.utils import metrics
from app.utils.logging import get_logger
from app.utils.time import now_in_tz

logger = get_logger(__name__)

_WEEKDAYS_RU = [
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"
]


def _today_str() -> str:
    """Current date for the prompt — the model has no idea what 'today' is."""
    try:
        dt = now_in_tz(settings.default_timezone)
    except Exception:
        from app.utils.time import now_utc

        dt = now_utc()
    return f"{dt.strftime('%Y-%m-%d')} ({_WEEKDAYS_RU[dt.weekday()]})"

ANALYZE_SYSTEM_PROMPT = """Ты — ассистент для анализа РАБОЧИХ командных чатов команды разработки.
В чате намешано всё: рабочие обсуждения, личные темы, шутки, оффтоп.
Твоя задача — выделять ТОЛЬКО реальные рабочие задачи по проекту.

ИГНОРИРУЙ (message_type="noise", contains_task=false, низкий confidence):
- личные и бытовые темы (обед, кофе, выходные, погода, семья, спорт);
- юмор, шутки, мемы, эмоциональные реакции;
- общий трёп, не относящийся к работе.

ВЫДЕЛЯЙ задачу (contains_task=true) только если есть конкретное рабочее действие:
что-то нужно сделать / исправить / реализовать / настроить / проверить по проекту.
Если сомневаешься, рабочая ли это задача — ставь contains_task=false и низкий confidence.

Верни ТОЛЬКО валидный JSON-объект, без markdown-блоков и пояснений.

Формат ответа:
{
  "message_type": "task_signal|requirement|decision|clarification|question|noise|blocker",
  "contains_task": true/false,
  "new_task": true/false,
  "related_task_id": null или число,
  "task_title": "Краткое название задачи",
  "summary": "Описание задачи",
  "assignee": "Имя или @username исполнителя",
  "deadline": null или строка с датой,
  "priority": "low|medium|high|critical",
  "requirements": ["требование 1", "требование 2"],
  "open_questions": ["вопрос 1"],
  "should_ask_clarification": true/false,
  "clarification_question": "Текст вопроса для чата",
  "confidence": 0.0-1.0
}"""

DIGEST_SYSTEM_PROMPT = """Ты — ассистент для формирования дайджеста командного чата.
На основе списка сообщений и задач составь краткую выжимку.
Верни ТОЛЬКО валидный JSON без markdown-блоков:
{
  "key_points": ["пункт 1", "пункт 2"],
  "summary": "Краткий итог"
}"""


def _extract_json(raw: str) -> dict:
    """
    Parse a JSON object out of an LLM response. Local models often wrap it in
    ```json ... ``` fences or add commentary, so we strip fences and, failing
    that, grab the outermost {...} block.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


class OpenAILLMClient(BaseLLMClient):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: str | None = None) -> None:
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**kwargs)
        self.model = model

    async def analyze_message(
        self,
        message_text: str,
        context_messages: list[dict],
        active_tasks: list[dict],
        reply_to_text: str | None = None,
    ) -> MessageAnalysis:
        context_str = "\n".join(
            f"[{m.get('username', 'unknown')}]: {m.get('text', '')}"
            for m in context_messages[-10:]
        )
        tasks_str = json.dumps(
            [{"id": t.get("id"), "title": t.get("title"), "status": t.get("status")} for t in active_tasks[:10]],
            ensure_ascii=False,
        )
        reply_str = f"\nОтвет на сообщение: {reply_to_text}" if reply_to_text else ""

        user_content = f"""Сегодняшняя дата: {_today_str()}.
Используй её для вычисления дедлайнов (например «до пятницы»). Не выдумывай год.

Контекст последних сообщений:
{context_str}

Активные задачи: {tasks_str}

Новое сообщение для анализа:{reply_str}
{message_text}"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
                max_tokens=800,
            )
            raw = response.choices[0].message.content or "{}"
            data = _extract_json(raw)
            return MessageAnalysis(**data)
        except Exception as e:
            logger.error("openai_client.analyze_message.error", error=str(e))
            metrics.llm_errors_total.inc()
            return MessageAnalysis(message_type="noise", contains_task=False, confidence=0.0)

    async def summarize_for_digest(
        self,
        messages: list[dict],
        tasks: list[dict],
    ) -> DigestSummary:
        messages_str = "\n".join(
            f"[{m.get('username', '?')}]: {m.get('text', '')}" for m in messages[-50:]
        )
        tasks_str = json.dumps(
            [{"title": t.get("title"), "status": t.get("status"), "assignee": t.get("assignee_text")} for t in tasks],
            ensure_ascii=False,
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": DIGEST_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Сегодняшняя дата: {_today_str()}.\n\n"
                            f"Сообщения:\n{messages_str}\n\nЗадачи: {tasks_str}"
                        ),
                    },
                ],
                temperature=0.3,
                max_tokens=500,
            )
            raw = response.choices[0].message.content or "{}"
            data = _extract_json(raw)
            return DigestSummary(**data)
        except Exception as e:
            logger.error("openai_client.summarize_for_digest.error", error=str(e))
            metrics.llm_errors_total.inc()
            return DigestSummary(summary="Не удалось сформировать выжимку.")


def create_llm_client(provider: str, api_key: str = "", model: str = "gpt-4o-mini", base_url: str = "") -> BaseLLMClient:
    if provider == "openai" and api_key:
        return OpenAILLMClient(api_key=api_key, model=model, base_url=base_url or None)
    from app.llm.mock_client import MockLLMClient
    return MockLLMClient()
