import os

# Never touch the development database: any engine built from settings is in-memory.
os.environ["DATABASE_URL"] = "sqlite://"
# Deployment settings from the developer's shell must not leak into tests.
for _name in (
    "ENVIRONMENT", "AUTH_ENABLED", "APP_ACCESS_PASSWORD", "SESSION_SECRET", "FRONTEND_DIST_DIR",
    "PAGE_IMAGE_STORAGE", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_STORAGE_BUCKET",
):
    os.environ.pop(_name, None)

import pytest  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from backend.app.api import routes  # noqa: E402
from backend.app.db.session import create_db_engine, get_db_session, init_db  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.services.page_image_store import LocalPageImageStore  # noqa: E402


@pytest.fixture
def db_sessionmaker():
    engine = create_db_engine("sqlite://")
    init_db(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db_session(db_sessionmaker) -> Session:
    with db_sessionmaker() as session:
        yield session


@pytest.fixture(autouse=True)
def isolated_database(db_sessionmaker):
    def override():
        with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    yield
    app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
def page_image_store(tmp_path) -> LocalPageImageStore:
    return LocalPageImageStore(tmp_path / "pages")


@pytest.fixture(autouse=True)
def isolated_page_images(page_image_store):
    app.dependency_overrides[routes.get_page_image_store] = lambda: page_image_store
    yield
    app.dependency_overrides.pop(routes.get_page_image_store, None)
