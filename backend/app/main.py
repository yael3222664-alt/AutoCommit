from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.auth import AuthGuardMiddleware, build_auth_config, build_auth_router
from .api.frontend import mount_frontend
from .api.routes import build_configured_page_image_store, router
from .api.security import SecurityMiddleware
from .config import Settings, get_settings, settings
from .db.session import get_engine, init_db
from .services.storage_factory import verify_page_image_store
from .startup_checks import enforce_configuration


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if get_settings().DB_AUTO_MIGRATE:
        init_db(get_engine())
    verify_page_image_store(build_configured_page_image_store())
    yield


def create_app(app_settings: Settings) -> FastAPI:
    enforce_configuration(app_settings)
    production = app_settings.ENVIRONMENT == "production"
    app = FastAPI(
        title=app_settings.APP_NAME,
        debug=app_settings.DEBUG,
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )

    auth = build_auth_config(app_settings)
    app.add_middleware(AuthGuardMiddleware, config=auth)
    # Origin checks guard the session cookie; without login there is no credential to forge.
    app.add_middleware(
        SecurityMiddleware,
        allowed_origins=app_settings.ALLOWED_ORIGINS,
        production=production,
        check_origin=auth.enabled,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": app_settings.APP_NAME}

    app.include_router(build_auth_router(auth), prefix="/api")
    app.include_router(router, prefix="/api")
    if app_settings.FRONTEND_DIST_DIR is not None:
        mount_frontend(app, app_settings.FRONTEND_DIST_DIR)
    return app


app = create_app(settings)
