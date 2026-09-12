"""Configuração é código de produção: o que não pode ter default é testado."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config import Environment, Settings

VALID = {
    "environment": "local",
    "app_timezone": "America/Sao_Paulo",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "jwt_secret_key": "chave-de-teste-com-comprimento-mais-que-suficiente",
}


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**VALID, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "missing", ["environment", "app_timezone", "database_url", "jwt_secret_key"]
)
def test_required_settings_have_no_default(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    for name in VALID:
        monkeypatch.delenv(name.upper(), raising=False)
    values = {key: value for key, value in VALID.items() if key != missing}

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_database_url_must_use_async_driver() -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        _settings(database_url="postgresql://u:p@localhost:5432/db")


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError, match="APP_TIMEZONE"):
        _settings(app_timezone="Mars/Olympus_Mons")


def test_production_cannot_run_in_debug() -> None:
    with pytest.raises(ValidationError, match="DEBUG"):
        _settings(environment=Environment.PRODUCTION, debug=True)


def test_lists_are_read_as_csv_not_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_HOSTS", "api.example.com, localhost ")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")

    settings = _settings()

    assert settings.allowed_hosts == ["api.example.com", "localhost"]
    assert settings.cors_origins == ["https://app.example.com"]


def test_tzinfo_is_derived_from_app_timezone() -> None:
    assert str(_settings().tzinfo) == "America/Sao_Paulo"


def test_the_report_logo_is_optional() -> None:
    assert _settings().report_logo_path is None


def test_a_blank_report_logo_is_the_same_as_none() -> None:
    assert _settings(report_logo_path="   ").report_logo_path is None


def test_a_report_logo_becomes_a_path() -> None:
    assert _settings(report_logo_path="assets/logo.png") == _settings(
        report_logo_path=Path("assets/logo.png")
    )
