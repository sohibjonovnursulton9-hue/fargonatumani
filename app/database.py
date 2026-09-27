"""
Database engine, session factory, and base model.
Uses async SQLAlchemy 2.0 with aiosqlite for development.
"""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


# These are initialized in init_db()
engine = None
async_session_factory = None
_database_url: str | None = None


def get_session_factory():
    """Return the live factory instead of a stale imported global reference."""
    if async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return async_session_factory


async def init_db(database_url: str) -> None:
    """Initialize database engine and session factory."""
    global engine, async_session_factory, _database_url

    if engine is not None and _database_url == database_url:
        return
    if engine is not None:
        await close_db()

    connect_args = {}
    if "sqlite" in database_url:
        connect_args = {"check_same_thread": False, "timeout": 30}

    engine = create_async_engine(
        database_url,
        echo=False,
        hide_parameters=True,
        connect_args=connect_args,
    )

    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    _database_url = database_url


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields an async database session."""
    if async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_all_tables() -> None:
    """Create all tables for isolated tests. Application startup uses Alembic migrations."""
    if engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    # Ensure every model has registered its tables before create_all is called.
    from app import models  # noqa: F401
    from app import change_models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database engine."""
    global engine, async_session_factory, _database_url
    if engine:
        await engine.dispose()
    engine = None
    async_session_factory = None
    _database_url = None
