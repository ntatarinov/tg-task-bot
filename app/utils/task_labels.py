from __future__ import annotations

from app.db.models import TaskStatus
from app.i18n import t


def status_labels(language: str) -> dict[str, str]:
    return {status.value: t(language, f"status.{status.value}") for status in TaskStatus}


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


def format_clarification_question(question: str, language: str) -> str:
    """The hint is plain text (no markup, safe under the bot's default HTML parse_mode without
    escaping) and makes a reply count as the answer."""
    return f"{question}{t(language, 'clarify.reply_hint')}"
