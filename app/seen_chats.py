from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


def load() -> dict[str, dict[str, Any]]:
    path = Path(settings.seen_chats_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(
    telegram_chat_id: int, title: str | None, chat_type: str, present: bool = True
) -> None:
    """
    Note a group the bot was added to but may not work in, so the panel can offer to allow it
    without anyone looking up an id. Only the id and the title are kept, never a message.
    Private chats are skipped: anyone can write to a bot, and those are not work chats.
    """
    if chat_type not in ("group", "supergroup"):
        return
    known = load()
    key = str(telegram_chat_id)
    entry = {"title": title, "type": chat_type, "present": present}
    if known.get(key) == entry:
        return
    known[key] = entry
    try:
        Path(settings.seen_chats_path).write_text(json.dumps(known, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning("seen_chats.write_failed", error=type(e).__name__)
