import pytest

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.startup_checks import StartupConfigurationError, configuration_problems, enforce_configuration


def production_settings(tmp_path, **overrides) -> Settings:
    (tmp_path / "index.html").write_text("<!doctype html>")
    values = dict(
        _env_file=None, ENVIRONMENT="production", DEBUG=False, AUTH_ENABLED=True,
        APP_ACCESS_PASSWORD="a-long-enough-password", SESSION_SECRET="s" * 40,
        DATABASE_URL="postgresql://user:pw@pooler.example:6543/postgres",
        PAGE_IMAGE_STORAGE="supabase", SUPABASE_URL="https://x.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="service-key", GEMINI_API_KEY="gemini-key",
        GEMINI_API_KEYS=None, FRONTEND_DIST_DIR=tmp_path,
    )
    values.update(overrides)
    return Settings(**values)


def test_valid_production_configuration_passes(tmp_path):
    assert configuration_problems(production_settings(tmp_path)) == []


def test_production_without_auth_passes_only_with_explicit_public_access(tmp_path):
    settings = production_settings(
        tmp_path, AUTH_ENABLED=False, ALLOW_PUBLIC_ACCESS=True, APP_ACCESS_PASSWORD=None, SESSION_SECRET=None
    )
    assert configuration_problems(settings) == []


def test_development_defaults_pass():
    assert configuration_problems(Settings(_env_file=None)) == []


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"AUTH_ENABLED": False}, "AUTH_ENABLED"),
        ({"DEBUG": True}, "DEBUG"),
        ({"DATABASE_URL": "sqlite:///x.db"}, "DATABASE_URL must point to PostgreSQL"),
        ({"DB_SSL_MODE": "prefer"}, "DB_SSL_MODE"),
        ({"PAGE_IMAGE_STORAGE": "local"}, "PAGE_IMAGE_STORAGE"),
        ({"GEMINI_API_KEY": None}, "GEMINI_API_KEY"),
        ({"APP_ACCESS_PASSWORD": "short"}, "APP_ACCESS_PASSWORD"),
        ({"APP_ACCESS_PASSWORD": None}, "APP_ACCESS_PASSWORD"),
        ({"SESSION_SECRET": "short"}, "SESSION_SECRET"),
        ({"FRONTEND_DIST_DIR": None}, "FRONTEND_DIST_DIR"),
    ],
)
def test_unsafe_production_settings_are_reported(tmp_path, overrides, expected):
    problems = configuration_problems(production_settings(tmp_path, **overrides))
    assert any(expected in problem for problem in problems)


@pytest.mark.parametrize("name", ["DATABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_URL", "GEMINI_API_KEY"])
def test_whitespace_in_credentials_is_reported_without_the_value(tmp_path, name):
    secret_value = "postgresql://user:pw@aws-0-eu- west-1.example:6543/db" if name == "DATABASE_URL" else "abc def"
    settings = production_settings(tmp_path, **{name: secret_value})
    with pytest.raises(StartupConfigurationError) as excinfo:
        enforce_configuration(settings)
    message = str(excinfo.value)
    assert f"{name} contains whitespace" in message
    assert secret_value not in message and "pw@" not in message


def test_unused_supabase_credentials_are_not_checked():
    assert configuration_problems(Settings(_env_file=None, SUPABASE_SERVICE_ROLE_KEY="has space")) == []


def test_create_app_refuses_unsafe_production_configuration(tmp_path):
    with pytest.raises(StartupConfigurationError):
        create_app(production_settings(tmp_path, AUTH_ENABLED=False))
