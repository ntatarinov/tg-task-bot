from __future__ import annotations

import enum

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.db.models import Base
from app.utils.logging import get_logger

logger = get_logger(__name__)


def _add_missing_columns(conn: Connection) -> list[str]:
    inspector = sa.inspect(conn)
    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(dialect=conn.dialect)}"
            default = getattr(column.default, "arg", None)
            if isinstance(default, enum.Enum):
                default = default.name  # how SQLAlchemy stores an Enum column
            if default is not None and not callable(default):
                literal = sa.literal(default).compile(
                    dialect=conn.dialect, compile_kwargs={"literal_binds": True}
                )
                ddl += f" DEFAULT {literal}"
            conn.execute(sa.text(ddl))
            added.append(f"{table.name}.{column.name}")
    return added


def _remove_duplicate_messages(conn: Connection) -> int:
    """Keep the earliest row of each Telegram message and repoint whatever referenced the rest."""
    groups = conn.execute(
        sa.text(
            "SELECT chat_id, telegram_message_id, MIN(id) FROM messages "
            "GROUP BY chat_id, telegram_message_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    removed = 0
    for chat_id, telegram_message_id, keep_id in groups:
        params = {"chat": chat_id, "tg": telegram_message_id, "keep": keep_id}
        duplicates = (
            "SELECT id FROM messages WHERE chat_id = :chat "
            "AND telegram_message_id = :tg AND id <> :keep"
        )
        conn.execute(
            sa.text(f"UPDATE task_messages SET message_id = :keep WHERE message_id IN ({duplicates})"),
            params,
        )
        conn.execute(
            sa.text(
                f"UPDATE clarifications SET answer_message_id = :keep "
                f"WHERE answer_message_id IN ({duplicates})"
            ),
            params,
        )
        result = conn.execute(
            sa.text(
                "DELETE FROM messages WHERE chat_id = :chat "
                "AND telegram_message_id = :tg AND id <> :keep"
            ),
            params,
        )
        removed += result.rowcount
    if removed:
        conn.execute(
            sa.text(
                "DELETE FROM task_messages WHERE id NOT IN "
                "(SELECT MIN(id) FROM task_messages GROUP BY task_id, message_id)"
            )
        )
    return removed


def _create_missing_indexes(conn: Connection) -> None:
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(conn, checkfirst=True)


def schema_is_current(conn: Connection) -> bool:
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            return False
        columns = {c["name"] for c in inspector.get_columns(table.name)}
        if not {c.name for c in table.columns} <= columns:
            return False
    return True


def upgrade_schema(conn: Connection) -> dict[str, object]:
    """
    Bring an existing database up to the current models. Idempotent, and the same
    code path for SQLite (`app.init_db`) and Postgres (Alembic revision 0005).
    """
    Base.metadata.create_all(conn)
    added = _add_missing_columns(conn)
    conn.execute(sa.text("UPDATE messages SET sent_at = created_at WHERE sent_at IS NULL"))
    removed = _remove_duplicate_messages(conn)
    _create_missing_indexes(conn)
    logger.info("schema.upgraded", columns_added=len(added), duplicates_removed=removed)
    return {"columns_added": added, "duplicates_removed": removed}
