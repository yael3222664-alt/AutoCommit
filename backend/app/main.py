from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import build_configured_page_image_store, router
from .config import get_settings, settings
from .db.session import get_engine, init_db
from .services.storage_factory import verify_page_image_store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if get_settings().DB_AUTO_MIGRATE:
        init_db(get_engine())
    verify_page_image_store(build_configured_page_image_store())
    yield


app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.APP_NAME}


app.include_router(router, prefix="/api")
