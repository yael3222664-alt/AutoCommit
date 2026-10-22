from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "AutoCommit Engine"
    DEBUG: bool = True
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:4200",
    ]

    MAX_UPLOAD_SIZE_MB: int = Field(default=20, ge=1)

    VISION_PROVIDER: str = "gemini"
    VISION_MAX_CONCURRENCY: int = Field(default=3, ge=1)
    VISION_TIMEOUT_SECONDS: float = Field(default=120.0, gt=0)
    # Retries for rate-limit/overload responses (429, 5xx), with exponential backoff.
    VISION_MAX_RETRIES: int = Field(default=3, ge=0)
    GEMINI_API_KEY: Optional[SecretStr] = None
    GEMINI_MODEL: str = "gemini-3.5-flash"
    GEMINI_API_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"

    # SQLite (local default) or PostgreSQL, e.g. postgresql://user:pass@host:5432/db (Supabase).
    DATABASE_URL: SecretStr = SecretStr(f"sqlite:///{BACKEND_DIR / 'data' / 'autocommit.db'}")
    # Apply pending schema migrations at startup. Disable to run `python -m backend.app.db.migrate` separately.
    DB_AUTO_MIGRATE: bool = True
    # PostgreSQL only; ignored for SQLite. An sslmode in DATABASE_URL takes precedence over DB_SSL_MODE.
    DB_POOL_SIZE: int = Field(default=5, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=5, ge=0)
    DB_POOL_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)
    DB_POOL_RECYCLE_SECONDS: int = Field(default=1800, ge=1)
    DB_CONNECT_TIMEOUT_SECONDS: int = Field(default=10, ge=1)
    DB_SSL_MODE: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = "require"

    # Where rendered page images are kept: "local" (PAGE_IMAGE_DIR) or "supabase" (private bucket).
    PAGE_IMAGE_STORAGE: Literal["local", "supabase"] = "local"
    PAGE_IMAGE_DIR: Path = BACKEND_DIR / "data" / "pages"
    SUPABASE_URL: Optional[str] = None
    SUPABASE_SERVICE_ROLE_KEY: Optional[SecretStr] = None
    SUPABASE_STORAGE_BUCKET: str = "page-images"
    STORAGE_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0)
    STORAGE_MAX_RETRIES: int = Field(default=3, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
