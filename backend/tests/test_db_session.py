"""Engine configuration for SQLite and PostgreSQL. Live PostgreSQL tests need TEST_POSTGRES_URL."""

import os
from datetime import datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.pool import QueuePool, StaticPool

from backend.app.config import Settings
from backend.app.db.models import RecordEditRow, RecordRow
from backend.app.db.session import (
    POSTGRES_DRIVER,
    EngineOptions,
    UnsupportedDatabaseError,
    create_db_engine,
    engine_arguments,
    init_db,
    normalize_database_url,
)

PG_URL = "postgresql://user:db-password@db.example.supabase.co:6543/postgres"
TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")
requires_postgres = pytest.mark.skipif(not TEST_POSTGRES_URL, reason="TEST_POSTGRES_URL not set")


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_postgres_urls_use_psycopg_driver(scheme):
    url = normalize_database_url(PG_URL.replace("postgresql", scheme, 1))
    assert url.drivername == POSTGRES_DRIVER
    assert url.host == "db.example.supabase.co" and url.port == 6543 and url.database == "postgres"


@pytest.mark.parametrize("url", ["mysql://u:p@h/db", "postgresql+asyncpg://u:p@h/db", "oracle://h"])
def test_unsupported_drivers_are_rejected_without_echoing_credentials(url):
    with pytest.raises(UnsupportedDatabaseError) as excinfo:
        normalize_database_url(url)
    assert ":p@" not in str(excinfo.value)


def test_postgres_engine_arguments_configure_pool_timeout_tls_and_pooler_compatibility():
    options = EngineOptions(
        pool_size=7, max_overflow=2, pool_timeout_seconds=12.5, pool_recycle_seconds=600, connect_timeout_seconds=4
    )
    url, kwargs = engine_arguments(PG_URL, options)
    assert url.drivername == POSTGRES_DRIVER
    assert kwargs["pool_size"] == 7 and kwargs["max_overflow"] == 2
    assert kwargs["pool_timeout"] == 12.5 and kwargs["pool_recycle"] == 600
    assert kwargs["pool_pre_ping"] is True and kwargs["hide_parameters"] is True
    assert kwargs["connect_args"] == {
        "connect_timeout": 4,
        "prepare_threshold": None,
        "application_name": "autocommit",
        "sslmode": "require",
    }


def test_sslmode_in_url_takes_precedence():
    _, kwargs = engine_arguments(PG_URL + "?sslmode=verify-full", EngineOptions(ssl_mode="disable"))
    assert "sslmode" not in kwargs["connect_args"]


def test_engine_options_from_settings():
    settings = Settings(DB_POOL_SIZE=3, DB_MAX_OVERFLOW=0, DB_CONNECT_TIMEOUT_SECONDS=2, DB_SSL_MODE="verify-full")
    options = EngineOptions.from_settings(settings)
    assert (options.pool_size, options.max_overflow, options.connect_timeout_seconds, options.ssl_mode) == (
        3,
        0,
        2,
        "verify-full",
    )


def test_postgres_engine_is_created_lazily_with_queue_pool():
    engine = create_db_engine(PG_URL, EngineOptions(pool_size=4, max_overflow=1))
    try:
        assert isinstance(engine.pool, QueuePool) and engine.pool.size() == 4
        assert engine.dialect.name == "postgresql" and engine.dialect.driver == "psycopg"
        assert "db-password" not in repr(engine)
    finally:
        engine.dispose()


def test_in_memory_sqlite_uses_one_shared_connection():
    url, kwargs = engine_arguments("sqlite://")
    assert url.get_backend_name() == "sqlite" and kwargs["poolclass"] is StaticPool
    assert "pool_size" not in kwargs


def test_sqlite_file_database_is_created_and_migrated(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'nested' / 'app.db'}")
    try:
        init_db(engine)
        assert {"records", "record_edits", "alembic_version"} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_sqlite_enforces_foreign_keys():
    engine = create_db_engine("sqlite://")
    init_db(engine)
    with engine.begin() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            RecordEditRow.__table__.insert().values(
                record_id="missing", field="first_name", old_value=None, new_value="x", edited_at=datetime(2026, 1, 1)
            )
        )


def test_sql_errors_do_not_include_bound_parameters():
    engine = create_db_engine("sqlite://")
    init_db(engine)
    with pytest.raises(StatementError) as excinfo, engine.begin() as connection:
        connection.execute(RecordRow.__table__.insert().values(id="r1", document_id="SECRET-PATIENT-NAME"))
    assert "SECRET-PATIENT-NAME" not in str(excinfo.value)


@requires_postgres
def test_live_postgres_session_round_trip_over_tls():
    from sqlalchemy.orm import sessionmaker

    from backend.app.services.record_service import RecordService
    from backend.tests.test_record_service import complete_record

    engine = create_db_engine(TEST_POSTGRES_URL, EngineOptions(ssl_mode="require"))
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS record_edits, records, alembic_version"))
        init_db(engine)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")).scalar()
            column = inspect(connection).get_columns("records")
            assert next(c for c in column if c["name"] == "data")["type"].__class__.__name__ == "JSONB"
        with sessionmaker(bind=engine)() as session:
            [record_id] = RecordService(session).save_records([complete_record()], "d" * 64)
            stored = RecordService(session).get_record(record_id)
            assert stored.first_name.value == "Dana" and stored.document_id == "d" * 64
    finally:
        engine.dispose()
