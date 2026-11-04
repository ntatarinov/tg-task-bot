from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

from app.config import settings
from app.db.session import async_session_factory, engine
from app.services.message_ingestor import MessageIngestor
from app.utils.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


def _flatten_text(raw: Any) -> str:
    """Telegram Desktop stores formatted text as a list of strings and entity objects."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "".join(p if isinstance(p, str) else str(p.get("text", "")) for p in raw)
    return ""


def _user_id(from_id: Any) -> int | None:
    # "user123456" is a person; "channel123" is a post made on behalf of a chat.
    if isinstance(from_id, str) and from_id.startswith("user") and from_id[4:].isdigit():
        return int(from_id[4:])
    return None


def _sent_at(entry: dict) -> datetime:
    if "date_unixtime" in entry:
        return datetime.fromtimestamp(int(entry["date_unixtime"]), tz=pytz.utc)
    # Older exports only carry the exporting computer's local time.
    return pytz.utc.localize(datetime.fromisoformat(entry["date"]))


def iter_export(export: dict) -> Iterator[dict[str, Any]]:
    """Yield ingest() arguments for each real message in a parsed result.json."""
    for entry in export.get("messages", []):
        if entry.get("type") != "message":
            continue
        text = _flatten_text(entry.get("text"))
        yield {
            "telegram_user_id": _user_id(entry.get("from_id")),
            "display_name": entry.get("from"),
            "telegram_message_id": int(entry["id"]),
            "text": text,
            "reply_to_message_id": entry.get("reply_to_message_id"),
            "sent_at": _sent_at(entry),
            "content_type": entry.get("media_type") or ("photo" if "photo" in entry else "text"),
            "forward_from": entry.get("forwarded_from"),
        }


async def import_export(export: dict, telegram_chat_id: int, analyze: bool = True) -> tuple[int, int]:
    """Returns (imported, skipped as already present). Safe to run repeatedly."""
    imported = skipped = 0
    async with async_session_factory() as session:
        ingestor = MessageIngestor(session)
        for fields in iter_export(export):
            message, created = await ingestor.ingest(
                telegram_chat_id=telegram_chat_id,
                chat_title=export.get("name"),
                username=None,
                raw_update_json=None,
                source="import",
                **fields,
            )
            if not created:
                skipped += 1
                continue
            imported += 1
            if not analyze:
                message.pending_analysis = False
            if imported % 500 == 0:
                await session.commit()
        await session.commit()
    return imported, skipped


async def main() -> None:
    """
    Load chat history from a Telegram Desktop export, since the Bot API cannot read the past.
    In Telegram Desktop: chat menu → Export chat history → format JSON, then:
        python -m app.importer path/to/result.json --chat-id -1001234567890
    """
    parser = argparse.ArgumentParser(description="Import a Telegram Desktop JSON export")
    parser.add_argument("path", type=Path, help="path to result.json")
    parser.add_argument("--chat-id", type=int, required=True, help="Telegram chat id (see /chatid)")
    parser.add_argument("--no-analyze", action="store_true", help="store without queuing analysis")
    args = parser.parse_args()

    if args.chat_id not in settings.allowed_chats:
        raise SystemExit(f"Чата {args.chat_id} нет в ALLOWED_CHAT_IDS")

    export = json.loads(args.path.read_text(encoding="utf-8"))
    imported, skipped = await import_export(export, args.chat_id, analyze=not args.no_analyze)
    await engine.dispose()
    logger.info("import.done", imported=imported, skipped=skipped)


if __name__ == "__main__":
    asyncio.run(main())
