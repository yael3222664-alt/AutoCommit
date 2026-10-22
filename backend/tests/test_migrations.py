import os
import threading

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, inspect, text

from backend.app import main
from backend.app.db import migrate
from backend.app.db.migrate import BASELINE_REVISION, SchemaMismatchError, current_revision, head_revision
from backend.app.db.models import Base
from backend.app.db.session import create_db_engine, init_db

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.fixture
def engine(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'app.db'}")
    yield engine
    engine.dispose()


def _revision(engine):
    with engine.connect() as connection:
        return current_revision(connection)


def test_empty_database_is_migrated_to_head(engine):
    init_db(engine)
    assert _revision(engine) == head_revision()
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())


def test_migration_is_idempotent(engine):
    init_db(engine)
    init_db(engine)
    assert _revision(engine) == head_revision()


def test_pre_migration_database_is_adopted_and_keeps_its_data(engine):
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO records (id, document_id, page_number, status, requires_manual_review, data, "
                "created_at, updated_at) VALUES ('r1', :doc, 1, 'pending_review', 1, '{}', "
                "'2026-01-01', '2026-01-01')"
            ),
            {"doc": "a" * 64},
        )
    init_db(engine)
    assert _revision(engine) == head_revision()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id FROM records")).scalars().all() == ["r1"]


def test_unexpected_existing_schema_is_refused_and_left_unchanged(engine):
    metadata = MetaData()
    Table("records", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    with pytest.raises(SchemaMismatchError):
        init_db(engine)
    tables = set(inspect(engine).get_table_names())
    assert tables == {"records"}
    assert [c["name"] for c in inspect(engine).get_columns("records")] == ["id"]


def test_unrelated_tables_do_not_trigger_adoption(engine):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE other (id INTEGER)"))
    init_db(engine)
    assert _revision(engine) == head_revision()
    assert "other" in inspect(engine).get_table_names()


def test_baseline_downgrade_and_upgrade(engine):
    from alembic import command

    init_db(engine)
    with engine.begin() as connection:
        command.downgrade(migrate.alembic_config(connection), "base")
    assert not {"records", "record_edits"} & set(inspect(engine).get_table_names())
    init_db(engine)
    assert _revision(engine) == BASELINE_REVISION


def test_migrate_command_uses_configured_database(tmp_path, monkeypatch):
    database = tmp_path / "cli.db"
    engine = create_db_engine(f"sqlite:///{database}")
    monkeypatch.setattr(migrate, "get_engine", lambda: engine)
    migrate.main()
    check = create_db_engine(f"sqlite:///{database}")
    try:
        assert _revision(check) == head_revision()
    finally:
        check.dispose()


@pytest.mark.parametrize("enabled", [True, False])
def test_startup_migration_can_be_disabled(monkeypatch, enabled):
    calls = []
    monkeypatch.setattr(main, "init_db", lambda engine: calls.append(engine))
    monkeypatch.setenv("DB_AUTO_MIGRATE", str(enabled).lower())
    main.get_settings.cache_clear()
    try:
        from fastapi.testclient import TestClient

        with TestClient(main.app):
            pass
    finally:
        main.get_settings.cache_clear()
    assert len(calls) == (1 if enabled else 0)


@pytest.mark.skipif(not TEST_POSTGRES_URL, reason="TEST_POSTGRES_URL not set")
def test_concurrent_startups_on_postgres_migrate_once():
    setup = create_db_engine(TEST_POSTGRES_URL)
    with setup.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS record_edits, records, alembic_version"))
    setup.dispose()
    errors = []

    def start():
        engine = create_db_engine(TEST_POSTGRES_URL)
        try:
            init_db(engine)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            engine.dispose()

    threads = [threading.Thread(target=start) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    check = create_db_engine(TEST_POSTGRES_URL)
    try:
        assert _revision(check) == head_revision()
    finally:
        check.dispose()
