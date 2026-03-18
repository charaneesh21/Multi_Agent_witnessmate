"""backend/database/connection.py
SQLite database connection and async session management.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.database.models import Base
from config.settings import get_settings

settings = get_settings()

# Async engine – aiosqlite driver required (pip: aiosqlite)
engine = create_async_engine(
    settings.database_url,
    echo=(settings.app_env == "development"),   # log SQL in dev only
    connect_args={"check_same_thread": False},  # SQLite-specific; safe for async
)

# Session factory – expire_on_commit=False avoids lazy-load errors after commit
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create all tables if they don't exist. Called on app startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency – yields an AsyncSession per request, then closes it.

    Usage:
        from fastapi import Depends
        from backend.database.connection import get_db

        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
