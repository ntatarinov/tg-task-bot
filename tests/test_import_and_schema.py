from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime

import pytest
import pytz
from sqlalchemy import create_engine, func, inspect, select

from app import importer
from app.db.models import Message
from app.db.schema import schema_is_current, upgrade_schema
from app.init_db import backup_sqlite
from tests.helpers import make_chat

EXPORT = {
    "name": "Команда",
    "messages": [
        {"id": 10, "type": "service", "action": "create_group", "date_unixtime": "1762236000"},
        {
            "id": 11, "type": "message", "date_unixtime": "1762236000", "from": "Аня",
            "from_id": "user555", "text": "Обычный текст",
        },
        {
            "id": 12, "type": "message", "date_unixtime": "1762236060", "from": "Борис",
            "from_id": "user777", "reply_to_message_id": 11,
            "text": ["Смотри ", {"type": "link", "text": "https://example.com"}, " тут"],
        },
        {
            "id": 13, "type": "message", "date_unixtime": "1762236120", "from": "Канал",
            "from_id": "channel42", "text": "От имени канала",
        },
        {
            "id": 14, "type": "message", "date_unixtime": "1762236180", "from": "Аня",
            "from_id": "user555", "text": "", "photo": "photos/1.jpg",
        },
    ],
}


@pytest.fixture
def importer_session(monkeypatch, session):
    @asynccontextmanager
    async def shared():
        yield session

    monkeypatch.setattr(importer, "async_session_factory", shared)
    return session


def test_export_parsing_flattens_text_and_skips_service_events():
    parsed = list(importer.iter_export(EXPORT))

    assert [p["telegram_message_id"] for p in parsed] == [11, 12, 13, 14]
    assert parsed[1]["text"] == "Смотри https://example.com тут"
    assert parsed[1]["reply_to_message_id"] == 11
    assert parsed[0]["telegram_user_id"] == 555
    assert parsed[2]["telegram_user_id"] is None
    assert parsed[0]["sent_at"] == datetime.fromtimestamp(1762236000, tz=pytz.utc)
    assert parsed[3]["content_type"] == "photo"


@pytest.mark.asyncio
async def test_import_is_idempotent_and_queues_for_analysis(importer_session):
    session = importer_session
    chat = await make_chat(session)

    assert await importer.import_export(EXPORT, chat.telegram_chat_id) == (4, 0)
    assert await importer.import_export(EXPORT, chat.telegram_chat_id) == (0, 4)

    assert (await session.execute(select(func.count()).select_from(Message))).scalar() == 4
    pending = (await session.execute(
        select(func.count()).select_from(Message).where(Message.pending_analysis.is_(True))
    )).scalar()
    assert pending == 3  # the caption-less photo has nothing to analyze
    sources = set((await session.execute(select(Message.source))).scalars().all())
    assert sources == {"import"}


@pytest.mark.asyncio
async def test_import_can_skip_analysis(importer_session):
    session = importer_session
    chat = await make_chat(session)

    await importer.import_export(EXPORT, chat.telegram_chat_id, analyze=False)

    pending = (await session.execute(
        select(func.count()).select_from(Message).where(Message.pending_analysis.is_(True))
    )).scalar()
    assert pending == 0


# A database as the previous version of the bot left it, duplicates included.
LEGACY_SCHEMA = """
CREATE TABLE chats (id INTEGER PRIMARY KEY, telegram_chat_id BIGINT NOT NULL UNIQUE, title VARCHAR(255),
  timezone VARCHAR(64), digest_time VARCHAR(5), digest_enabled BOOLEAN, is_active BOOLEAN,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE users (id INTEGER PRIMARY KEY, telegram_user_id BIGINT NOT NULL UNIQUE, username VARCHAR(255),
  display_name VARCHAR(255), created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE messages (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, user_id INTEGER,
  telegram_message_id BIGINT NOT NULL, telegram_thread_id BIGINT, reply_to_message_id BIGINT, text TEXT,
  raw_update_json JSON, pending_analysis BOOLEAN, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE tasks (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, title VARCHAR(500) NOT NULL, description TEXT,
  assignee_text VARCHAR(255), reporter_text VARCHAR(255), status VARCHAR(19) NOT NULL, priority VARCHAR(8) NOT NULL,
  deadline VARCHAR(100), confidence FLOAT, source VARCHAR(50), requirements TEXT, open_questions TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE task_messages (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, message_id INTEGER NOT NULL);
CREATE TABLE clarifications (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, question TEXT NOT NULL,
  question_message_id BIGINT, asked_to_user_id INTEGER, status VARCHAR(8) NOT NULL, answer_text TEXT,
  answer_message_id INTEGER, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE digests (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, period_start DATETIME NOT NULL,
  period_end DATETIME NOT NULL, text TEXT NOT NULL, sent_at DATETIME, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
INSERT INTO chats (id, telegram_chat_id) VALUES (1, -100);
INSERT INTO messages (id, chat_id, telegram_message_id, text, pending_analysis, created_at) VALUES
  (1, 1, 50, 'оригинал', 0, '2026-07-01 10:00:00'),
  (2, 1, 50, 'дубль', 0, '2026-07-01 10:00:05'),
  (3, 1, 51, 'другое', 1, '2026-07-01 10:01:00');
INSERT INTO tasks (id, chat_id, title, status, priority) VALUES (1, 1, 'Задача', 'new', 'medium');
INSERT INTO task_messages (id, task_id, message_id) VALUES (1, 1, 1), (2, 1, 2);
INSERT INTO clarifications (id, task_id, question, status, answer_message_id) VALUES (1, 1, 'Кто?', 'answered', 2);
INSERT INTO digests (id, chat_id, period_start, period_end, text, sent_at)
  VALUES (1, 1, '2026-07-01', '2026-07-02', 'старый отчёт', '2026-07-02');
"""


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "bot.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(LEGACY_SCHEMA)
    return path


def _upgrade(path):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as conn:
        result = upgrade_schema(conn)
    return engine, result


def test_upgrade_preserves_data_and_removes_duplicates(legacy_db):
    engine, result = _upgrade(legacy_db)

    assert result["duplicates_removed"] == 1
    with engine.connect() as conn:
        assert schema_is_current(conn)
        rows = conn.exec_driver_sql("SELECT id, text, sent_at FROM messages ORDER BY id").fetchall()
        assert [(r[0], r[1]) for r in rows] == [(1, "оригинал"), (3, "другое")]
        assert rows[0][2] == "2026-07-01 10:00:00"
        links = conn.exec_driver_sql("SELECT task_id, message_id FROM task_messages").fetchall()
        assert links == [(1, 1)]
        answer = conn.exec_driver_sql("SELECT answer_message_id FROM clarifications").scalar()
        assert answer == 1
        kind, chunks = conn.exec_driver_sql("SELECT kind, sent_chunks FROM digests").fetchone()
        assert (kind, chunks) == ("manual", 0)
        weekly = conn.exec_driver_sql("SELECT weekly_digest_enabled, weekly_digest_day FROM chats").fetchone()
        assert weekly == (1, 4)
        pending = conn.exec_driver_sql("SELECT pending_analysis FROM messages WHERE id = 3").scalar()
        assert pending == 1


def test_upgrade_is_idempotent(legacy_db):
    _upgrade(legacy_db)
    _, second = _upgrade(legacy_db)

    assert second == {"columns_added": [], "duplicates_removed": 0}


def test_upgrade_enforces_uniqueness_afterwards(legacy_db):
    engine, _ = _upgrade(legacy_db)

    with engine.connect() as conn:
        names = {i["name"] for i in inspect(conn).get_indexes("messages")}
    assert "uq_messages_chat_telegram_id" in names


def test_outdated_schema_is_detected(legacy_db):
    engine = create_engine(f"sqlite:///{legacy_db.as_posix()}")
    with engine.connect() as conn:
        assert not schema_is_current(conn)


def test_backup_copies_the_database(legacy_db):
    backup = backup_sqlite(f"sqlite+aiosqlite:///{legacy_db.as_posix()}")

    assert backup is not None and backup.exists()
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone() == (3,)


def test_backup_skips_what_it_cannot_copy(tmp_path):
    assert backup_sqlite("postgresql+asyncpg://u:p@host/db") is None
    assert backup_sqlite("sqlite+aiosqlite:///:memory:") is None
    assert backup_sqlite(f"sqlite+aiosqlite:///{(tmp_path / 'absent.db').as_posix()}") is None
