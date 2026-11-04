from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import settings
from app.db.schema import schema_is_current, upgrade_schema
from app.db.session import engine
from app.utils.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


def _sqlite_file_exists(database_url: str) -> bool:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return False
    return Path(url.database).exists()


def backup_sqlite(database_url: str) -> Path | None:
    """Snapshot an existing SQLite file before changing it. Returns the backup path."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    source = Path(url.database)
    if not source.exists():
        return None
    target = source.with_name(f"{source.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return target


async def main() -> None:
    """
    Create or upgrade the schema in place — safe to run repeatedly. A SQLite file is
    backed up only when the schema really changes: this runs on every container start,
    and a copy per restart would fill the disk with chat history that outlives retention.
    For Postgres in production use Alembic instead:
        alembic upgrade head
    Run with:  python -m app.init_db
    """
    if _sqlite_file_exists(settings.database_url):
        async with engine.connect() as conn:
            current = await conn.run_sync(schema_is_current)
        backup = None if current else backup_sqlite(settings.database_url)
        if backup is not None:
            logger.info("init_db.backup_created", path=str(backup))
    async with engine.begin() as conn:
        result = await conn.run_sync(upgrade_schema)
    await engine.dispose()
    logger.info("init_db.done", **result)


if __name__ == "__main__":
    asyncio.run(main())
