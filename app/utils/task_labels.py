from __future__ import annotations

from app.db.models import TaskStatus

STATUS_LABELS: dict[str, str] = {
    TaskStatus.new.value: "🆕 Новая",
    TaskStatus.needs_clarification.value: "❓ Нужно уточнение",
    TaskStatus.ready.value: "✅ Готова",
    TaskStatus.in_progress.value: "🔄 В работе",
    TaskStatus.done.value: "✔️ Выполнена",
    TaskStatus.cancelled.value: "❌ Отменена",
}

PRIORITY_EMOJI: dict[str, str] = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🟠",
    "critical": "🔴",
}

# Action name -> resulting status. Shared between the bot's inline buttons
# (callback_data) and the web dashboard's status buttons.
TASK_ACTIONS: dict[str, TaskStatus] = {
    "progress": TaskStatus.in_progress,
    "done": TaskStatus.done,
    "cancel": TaskStatus.cancelled,
    "reopen": TaskStatus.new,
}

# Plain text (no markup — safe under the bot's default HTML parse_mode without escaping)
# appended to clarification questions so a reply is understood as the answer.
CLARIFICATION_REPLY_HINT = "\n\n↩️ Ответьте на это сообщение (Reply), чтобы я учёл ваш ответ."


def format_clarification_question(question: str) -> str:
    return f"{question}{CLARIFICATION_REPLY_HINT}"
