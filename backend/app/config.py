from functools import lru_cache
from pathlib import Path
from typing import Optional

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
    GEMINI_API_KEY: Optional[SecretStr] = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_API_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"

    DATABASE_URL: str = f"sqlite:///{BACKEND_DIR / 'data' / 'autocommit.db'}"
    PAGE_IMAGE_DIR: Path = BACKEND_DIR / "data" / "pages"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
