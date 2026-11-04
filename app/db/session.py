from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# hide_parameters keeps message text out of SQLAlchemy exceptions, which end up in logs.
_engine_kwargs: dict = {"echo": settings.sql_echo, "pool_pre_ping": True, "hide_parameters": True}
_is_sqlite = settings.database_url.startswith("sqlite")
# SQLite (used for quick local runs / tests) ignores the connection-pool sizing
# options that Postgres needs, so only pass them for non-SQLite backends.
if _is_sqlite:
    _engine_kwargs["connect_args"] = {"timeout": 30}
else:
    _engine_kwargs.update(pool_size=10, max_overflow=20)

engine = create_async_engine(settings.database_url, **_engine_kwargs)

if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_wal(dbapi_connection, _record) -> None:
        # WAL lets the analysis worker read while a handler writes an incoming message.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
