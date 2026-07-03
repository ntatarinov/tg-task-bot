from __future__ import annotations

import asyncio

from app.db.models import Base
from app.db.session import engine
from app.utils.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


async def main() -> None:
    """
    Create all tables directly from the models — a quick start for local dev
    (e.g. on SQLite). For Postgres in production use Alembic migrations instead:
        alembic upgrade head
    Run with:  python -m app.init_db
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    logger.info("init_db.done")


if __name__ == "__main__":
    asyncio.run(main())
