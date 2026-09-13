"""Database engine factory.

Two engines coexist:

  * **sync** — used by FastAPI routes via `Depends(get_db)`. Existing routers
    (auth, lending, …) keep working unchanged.
  * **async** — used by the lending worker loops (event_indexer, keeper,
    alert_engine) and any new async route that needs `with_for_update` plus
    awaitable session operations.

Both share the same `DATABASE_URL`; the async one is derived by swapping the
driver: `postgresql://` → `postgresql+asyncpg://`, `sqlite://` → `sqlite+aiosqlite://`.
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# ---------- sync (FastAPI routes) ----------

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- async (worker loops) ----------


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+asyncpg://"):
        return sync_url
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("sqlite+aiosqlite://"):
        return sync_url
    if sync_url.startswith("sqlite://"):
        return sync_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return sync_url


async_engine = create_async_engine(_async_url(settings.database_url))
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    async_engine, expire_on_commit=False
)


async def get_async_db():
    async with AsyncSessionLocal() as session:
        yield session
