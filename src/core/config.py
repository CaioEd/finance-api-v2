"""Configuração da aplicação, exclusivamente a partir do ambiente.

Regra do projeto: nenhum valor de ambiente ou segredo mora no código. O que é
essencial não tem default — a aplicação recusa subir sem ele, em vez de subir
com um valor errado e falhar em produção.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import PositiveInt, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


CsvList = Annotated[list[str], NoDecode]
"""Lista lida do ambiente como `a,b,c` em vez de JSON."""

RateLimitStrategy = Literal["moving-window", "fixed-window", "sliding-window-counter"]
"""Estratégias do `limits`. Só a janela móvel é exata: "5 em quaisquer 5 minutos"."""


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

    # --- Relatórios --------------------------------------------------------
    report_brand_name: str = "FinanceHub"
    """Nome no topo dos PDFs: a marca do produto. `app_name` é o do serviço."""

    report_logo_path: Path | None = None
    """Logo ao lado do nome nos PDFs. Caminho relativo ao diretório do processo."""

    # --- HTTP ------------------------------------------------------------
    allowed_hosts: CsvList = ["*"]
    cors_origins: CsvList = []

    client_ip_header: str = ""
    """Onde o proxy da borda grava o IP de quem conectou; vazio é o IP do socket,
    certo só sem proxy na frente. Railway: `X-Real-IP`. AWS ALB: `X-Forwarded-For`."""

    trusted_proxy_count: PositiveInt = 1
    """Quantos proxies confiáveis escrevem no cabeçalho. O IP sai dessa posição,
    contada da direita — o que o próprio cliente mandou fica à esquerda."""

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

    # --- Limite de tentativas no login (docs/rate-limit.md) ----------------
    rate_limit_enabled: bool = True
    rate_limit_storage_url: str = "memory://"
    """Onde os contadores vivem. `memory://` conta por processo: com mais de uma
    réplica ou worker, cada um conta sozinho e o limite multiplica — aí, `redis://`."""

    rate_limit_strategy: RateLimitStrategy = "moving-window"
    rate_limit_login_ip_attempts: PositiveInt = 5
    rate_limit_login_ip_window_seconds: PositiveInt = 300
    rate_limit_login_email_attempts: PositiveInt = 10
    rate_limit_login_email_window_seconds: PositiveInt = 600

    # --- Recorrências (docs/recorrencias.md) ------------------------------
    recurring_scheduler_enabled: bool = True
    recurring_scheduler_interval_seconds: PositiveInt = 900
    """Intervalo entre rodadas; uma rodada sempre roda na subida."""

    # --- Investimentos (docs/investimentos.md) -----------------------------
    brapi_token: str = ""
    """Token da BRAPI: acoes brasileiras. Vazio desliga o provedor — a busca
    devolve lista vazia e o agendador pula a etapa, em vez de a aplicacao
    recusar subir. E o que permite rodar a suite e um ambiente sem as chaves."""

    twelve_data_api_key: str = ""
    """Chave da Twelve Data: acoes americanas, cripto e o cambio USD/BRL."""

    investment_scheduler_enabled: bool = True
    investment_scheduler_interval_seconds: PositiveInt = 900
    """15 minutos, como as recorrencias. Uma rodada sempre roda na subida."""

    brapi_max_symbols_per_run: PositiveInt = 15
    """Requisicoes a BRAPI por rodada. O plano gratuito aceita **um ativo por
    requisicao** e 20 por minuto; 15 deixa folga para a busca do usuario, que
    divide o mesmo teto."""

    twelve_data_max_symbols_per_run: PositiveInt = 7
    """Simbolos cotados por rodada. Um credito cada, mais um do `USD/BRL` — sao
    8, o teto por minuto do plano gratuito. A 96 rodadas por dia isso da 768
    creditos, dentro dos 800 diarios."""

    investment_rate_max_age_hours: PositiveInt = 12
    """Idade a partir da qual um indice do Banco Central e relido. O SGS publica
    uma vez por dia; reler a cada 15 min seriam 384 requisicoes para o mesmo numero."""

    investment_accrual_batch_size: PositiveInt = 200
    """Posicoes de renda fixa acruadas por rodada. Nao gasta credito de provedor;
    o teto existe para a rodada nao segurar uma transacao longa."""

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

    @field_validator("report_logo_path", mode="before")
    @classmethod
    def _blank_path_is_no_logo(cls, value: object) -> object:
        """`REPORT_LOGO_PATH=` vazio é ausência, não `Path(".")`."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("client_ip_header", mode="before")
    @classmethod
    def _blank_header_is_no_header(cls, value: object) -> object:
        """`CLIENT_IP_HEADER= ` com espaço é ausência, não um cabeçalho chamado " "."""
        return value.strip() if isinstance(value, str) else value

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
