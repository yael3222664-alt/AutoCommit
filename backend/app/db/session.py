"""Engine/session factories for SQLite (local runs, tests) and PostgreSQL (cloud, e.g. Supabase)."""

from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Union

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ..config import Settings, get_settings

POSTGRES_DRIVER = "postgresql+psycopg"
_POSTGRES_ALIASES = frozenset({"postgres", "postgresql", POSTGRES_DRIVER})


class UnsupportedDatabaseError(ValueError):
    pass


@dataclass(frozen=True)
class EngineOptions:
    """Connection pool and connection settings. Only PostgreSQL uses them."""

    pool_size: int = 5
    max_overflow: int = 5
    pool_timeout_seconds: float = 30.0
    pool_recycle_seconds: int = 1800
    connect_timeout_seconds: int = 10
    ssl_mode: str = "require"

    @classmethod
    def from_settings(cls, settings: Settings) -> "EngineOptions":
        return cls(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_timeout_seconds=settings.DB_POOL_TIMEOUT_SECONDS,
            pool_recycle_seconds=settings.DB_POOL_RECYCLE_SECONDS,
            connect_timeout_seconds=settings.DB_CONNECT_TIMEOUT_SECONDS,
            ssl_mode=settings.DB_SSL_MODE,
        )


def normalize_database_url(url: Union[str, URL]) -> URL:
    """Parse a database URL; plain postgres:// and postgresql:// URLs use the psycopg (v3) driver."""
    parsed = make_url(url)
    if parsed.drivername in _POSTGRES_ALIASES:
        return parsed.set(drivername=POSTGRES_DRIVER)
    if parsed.get_backend_name() == "sqlite":
        return parsed
    raise UnsupportedDatabaseError(
        f"Unsupported database driver {parsed.drivername!r}: use sqlite:// or postgresql://"
    )


def engine_arguments(url: Union[str, URL], options: Optional[EngineOptions] = None) -> tuple[URL, dict[str, Any]]:
    """The URL and `create_engine` keyword arguments for a database URL.

    Bound parameters (patient data) are never included in SQL error messages or logs.
    PostgreSQL connections are pre-pinged and recycled so connections dropped by a cloud
    proxy are replaced, use TLS by default, and disable server-side prepared statements
    so they also work through transaction-mode poolers (Supabase Supavisor, PgBouncer).
    """
    options = options or EngineOptions()
    parsed = normalize_database_url(url)
    kwargs: dict[str, Any] = {"hide_parameters": True}
    if parsed.get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False}
        if parsed.database in (None, "", ":memory:"):
            kwargs["poolclass"] = StaticPool
        return parsed, kwargs

    connect_args: dict[str, Any] = {
        "connect_timeout": options.connect_timeout_seconds,
        "prepare_threshold": None,
        "application_name": "autocommit",
    }
    if "sslmode" not in parsed.query:
        connect_args["sslmode"] = options.ssl_mode
    kwargs.update(
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_size=options.pool_size,
        max_overflow=options.max_overflow,
        pool_timeout=options.pool_timeout_seconds,
        pool_recycle=options.pool_recycle_seconds,
    )
    return parsed, kwargs


def create_db_engine(url: Union[str, URL], options: Optional[EngineOptions] = None) -> Engine:
    parsed, kwargs = engine_arguments(url, options)
    engine = create_engine(parsed, **kwargs)
    if parsed.get_backend_name() == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def ensure_sqlite_directory(engine: Engine) -> None:
    database = engine.url.database
    if engine.url.get_backend_name() == "sqlite" and database not in (None, "", ":memory:"):
        Path(database).parent.mkdir(parents=True, exist_ok=True)


def init_db(engine: Engine) -> None:
    """Bring the schema to the latest migration. Existing tables and data are preserved."""
    from .migrate import migrate_db

    migrate_db(engine)


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_db_engine(settings.DATABASE_URL.get_secret_value(), EngineOptions.from_settings(settings))


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db_session() -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session
