"""Configuração da aplicação, exclusivamente a partir do ambiente.

Regra do projeto: nenhum valor de ambiente ou segredo mora no código. O que é
essencial não tem default — a aplicação recusa subir sem ele, em vez de subir
com um valor errado e falhar em produção.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


CsvList = Annotated[list[str], NoDecode]
"""Lista lida do ambiente como `a,b,c` em vez de JSON."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- Aplicação (obrigatórios) ---------------------------------------
    environment: Environment
    app_timezone: str
    """Fuso em que "hoje" e "mês corrente" são resolvidos. Ver core.clock."""

    database_url: str
    """DSN async obrigatório: postgresql+asyncpg://user:pass@host:port/db"""

    jwt_secret_key: str
    """Assina o access token. Sem default e sem valor de exemplo, por motivos óbvios."""

    # --- Aplicação (com default) ----------------------------------------
    app_name: str = "finance-api"
    debug: bool = False
    docs_enabled: bool = True

    # --- HTTP ------------------------------------------------------------
    allowed_hosts: CsvList = ["*"]
    cors_origins: CsvList = []

    # --- Autenticação -----------------------------------------------------
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    """15 minutos. Access token não é revogável: a janela de exposição é o TTL."""
    refresh_token_ttl_days: int = 30

    # Custo do argon2id. Configurável para que a suíte de testes possa baixá-lo
    # sem que isso vire uma decisão de produção escondida no código.
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 4

    # --- Banco -----------------------------------------------------------
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30

    @field_validator("allowed_hosts", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("app_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"APP_TIMEZONE desconhecido: {value!r}") from exc
        return value

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL precisa usar o driver async: postgresql+asyncpg://...")
        return value

    @field_validator("jwt_secret_key")
    @classmethod
    def _secret_is_long_enough(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET_KEY precisa ter ao menos 32 caracteres")
        return value

    @model_validator(mode="after")
    def _production_is_not_debug(self) -> Settings:
        if self.environment is Environment.PRODUCTION and self.debug:
            raise ValueError("DEBUG não pode ser verdadeiro em produção")
        return self

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instância única, resolvida na primeira chamada (nunca no import)."""
    return Settings()
