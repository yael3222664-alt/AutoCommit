"""Schema migrations (Alembic).

    python -m backend.app.db.migrate

upgrades the database in DATABASE_URL to the latest revision. Databases created before
migrations existed (tables made by `create_all`, no `alembic_version` table) are checked
against the baseline schema and stamped with it, so their tables and data are kept.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, text

from .models import Base
from .session import ensure_sqlite_directory, get_engine

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().with_name("migrations")
BASELINE_REVISION = "0001_initial"
# Serializes concurrent migrations (several app instances starting at once) on PostgreSQL.
_POSTGRES_LOCK_ID = 4_210_993_517


class SchemaMismatchError(RuntimeError):
    """An unversioned existing database does not match the baseline schema."""


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = connection
    return config


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    assert head is not None
    return head


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def migrate_db(engine: Engine) -> None:
    ensure_sqlite_directory(engine)
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _POSTGRES_LOCK_ID})
        config = alembic_config(connection)
        if _is_unversioned_existing_schema(connection):
            _verify_baseline_schema(connection)
            command.stamp(config, BASELINE_REVISION)
            logger.info("Stamped existing schema with baseline revision %s", BASELINE_REVISION)
        command.upgrade(config, "head")


def _is_unversioned_existing_schema(connection: Connection) -> bool:
    tables = set(inspect(connection).get_table_names())
    return "alembic_version" not in tables and bool(tables & set(Base.metadata.tables))


def _verify_baseline_schema(connection: Connection) -> None:
    # The baseline revision creates exactly the tables `create_all` made before migrations existed.
    differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    if differences:
        kinds = sorted({_difference_kind(diff) for diff in differences})
        raise SchemaMismatchError(
            "Existing database schema does not match the baseline and was left unchanged "
            f"({len(differences)} differences: {', '.join(kinds)})"
        )


def _difference_kind(diff) -> str:
    first = diff[0] if isinstance(diff, list) else diff
    return str(first[0]) if isinstance(first, tuple) and first else type(first).__name__


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    engine = get_engine()
    try:
        migrate_db(engine)
        with engine.connect() as connection:
            logger.info("Database schema is at revision %s", current_revision(connection))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
