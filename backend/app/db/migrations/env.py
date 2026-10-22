"""Alembic environment. Uses the connection passed by `backend.app.db.migrate`, or DATABASE_URL."""

from alembic import context

from backend.app.config import get_settings
from backend.app.db.models import Base
from backend.app.db.session import EngineOptions, create_db_engine, normalize_database_url

target_metadata = Base.metadata


def _configure_and_run(**kwargs) -> None:
    context.configure(target_metadata=target_metadata, compare_type=True, **kwargs)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    url = normalize_database_url(get_settings().DATABASE_URL.get_secret_value())
    _configure_and_run(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _configure_and_run(connection=connection, render_as_batch=connection.dialect.name == "sqlite")
        return
    settings = get_settings()
    engine = create_db_engine(settings.DATABASE_URL.get_secret_value(), EngineOptions.from_settings(settings))
    try:
        with engine.connect() as connection:
            _configure_and_run(connection=connection, render_as_batch=connection.dialect.name == "sqlite")
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
