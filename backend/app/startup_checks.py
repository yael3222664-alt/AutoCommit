"""Fail-closed configuration checks run before the app accepts traffic.

Messages name settings only; they never include configured values.
"""
from typing import Optional, Union

from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from .config import Settings

MIN_PASSWORD_LENGTH = 12
MIN_SESSION_SECRET_LENGTH = 32
_SECURE_SSL_MODES = frozenset({"require", "verify-ca", "verify-full"})
_SUPABASE_SETTINGS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")


class StartupConfigurationError(RuntimeError):
    """The configured settings are unsafe or incomplete for this environment."""


def _raw(value: Union[str, SecretStr, None]) -> Optional[str]:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


def _is_postgres(database_url: str) -> bool:
    try:
        backend = make_url(database_url).get_backend_name()
    except ArgumentError:
        return False
    return backend in ("postgresql", "postgres")


def _settings_in_use(settings: Settings) -> list[str]:
    """Credential settings the configured backends will actually read."""
    names = ["DATABASE_URL"]
    if settings.PAGE_IMAGE_STORAGE == "supabase":
        names.extend(_SUPABASE_SETTINGS)
    if settings.VISION_PROVIDER == "gemini":
        names.append("GEMINI_API_KEY")
    if settings.AUTH_ENABLED:
        names.append("SESSION_SECRET")
    return names


def configuration_problems(settings: Settings) -> list[str]:
    problems: list[str] = []
    for name in _settings_in_use(settings):
        value = _raw(getattr(settings, name))
        if value is not None and any(char.isspace() for char in value):
            problems.append(f"{name} contains whitespace; re-copy it without spaces or line breaks")

    if settings.AUTH_ENABLED:
        password = _raw(settings.APP_ACCESS_PASSWORD) or ""
        if len(password) < MIN_PASSWORD_LENGTH:
            problems.append(f"APP_ACCESS_PASSWORD must have at least {MIN_PASSWORD_LENGTH} characters")
        session_secret = _raw(settings.SESSION_SECRET) or ""
        if len(session_secret) < MIN_SESSION_SECRET_LENGTH:
            problems.append(f"SESSION_SECRET must have at least {MIN_SESSION_SECRET_LENGTH} characters")

    if settings.ENVIRONMENT == "production":
        if not settings.AUTH_ENABLED:
            problems.append("AUTH_ENABLED must be true in production")
        if settings.DEBUG:
            problems.append("DEBUG must be false in production")
        if not _is_postgres(settings.DATABASE_URL.get_secret_value()):
            problems.append("DATABASE_URL must point to PostgreSQL in production")
        if settings.DB_SSL_MODE not in _SECURE_SSL_MODES:
            problems.append("DB_SSL_MODE must be require, verify-ca or verify-full in production")
        if settings.PAGE_IMAGE_STORAGE != "supabase":
            problems.append("PAGE_IMAGE_STORAGE must be supabase in production")
        if settings.VISION_PROVIDER == "gemini" and not _raw(settings.GEMINI_API_KEY):
            problems.append("GEMINI_API_KEY is required in production")
        dist = settings.FRONTEND_DIST_DIR
        if dist is None or not (dist / "index.html").is_file():
            problems.append("FRONTEND_DIST_DIR must point to a built frontend in production")
    return problems


def enforce_configuration(settings: Settings) -> None:
    problems = configuration_problems(settings)
    if problems:
        raise StartupConfigurationError("Invalid configuration:\n- " + "\n- ".join(problems))
