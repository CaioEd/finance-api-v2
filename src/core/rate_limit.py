"""Limite de tentativas, atrás de um contrato que não é o do slowapi.

Três peças, cada uma trocável sem mexer nas outras (ver `docs/rate-limit.md`):

- **`RateLimiter`**, o contrato: consumir uma tentativa de uma chave e dizer se
  ela cabia. É só isso que o serviço conhece. O slowapi — e o `limits`, que conta
  por baixo dele — aparece apenas em `SlowapiRateLimiter`; trocar de biblioteca,
  ou falar direto com um Redis, é escrever outra classe com o mesmo `hit` e
  devolvê-la em `build_rate_limiter`. Nenhuma rota usa os decoradores do slowapi,
  e é isso que mantém a troca dentro deste arquivo.
- **`RateLimit`**, a regra: N tentativas em qualquer janela de tantos segundos,
  sem a notação em texto de biblioteca nenhuma.
- **`ClientIpResolver`**, de quem é a tentativa — atrás de proxy, uma pergunta
  cuja resposta errada é a mais fácil de escrever (ver a classe).

`hit` é assíncrono mesmo com o storage em memória, que não faz I/O: é a
assinatura de que um Redis de verdade precisa, e mudá-la depois obrigaria a
mexer em quem chama.
"""

from __future__ import annotations

import ipaddress
import math
import time
from dataclasses import dataclass
from typing import Protocol

from limits import RateLimitItemPerSecond
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from core.config import Settings

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

KEY_NAMESPACE = "finance-api"
"""Prefixo de toda chave no storage: um Redis dividido com outro serviço não colide."""

IPV6_PREFIX = 64


@dataclass(frozen=True, slots=True)
class RateLimit:
    """No máximo `attempts` tentativas em qualquer janela de `window_seconds`."""

    attempts: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0
    """Negada a tentativa, quantos segundos até caber outra."""


ALLOWED = RateLimitResult(allowed=True)


class RateLimiter(Protocol):
    async def hit(self, limit: RateLimit, key: str) -> RateLimitResult:
        """Consome uma tentativa de `key` sob `limit`, se ainda couber."""
        ...


@dataclass(frozen=True, slots=True)
class LoginRateLimits:
    """Os dois limites do login: um por IP, outro por e-mail."""

    per_ip: RateLimit
    per_email: RateLimit

    @classmethod
    def from_settings(cls, settings: Settings) -> LoginRateLimits:
        return cls(
            per_ip=RateLimit(
                attempts=settings.rate_limit_login_ip_attempts,
                window_seconds=settings.rate_limit_login_ip_window_seconds,
            ),
            per_email=RateLimit(
                attempts=settings.rate_limit_login_email_attempts,
                window_seconds=settings.rate_limit_login_email_window_seconds,
            ),
        )


class SlowapiRateLimiter:
    """O único lugar do projeto que conhece o slowapi."""

    def __init__(self, *, storage_url: str, strategy: str) -> None:
        # O `Limiter` monta storage e estratégia no construtor: URL ou estratégia
        # inválida derruba a subida da aplicação, e não o primeiro login. O
        # `key_func` é obrigatório, mas só serve aos decoradores do slowapi, que
        # ninguém usa aqui — a chave chega pronta em `hit`.
        self._strategy = Limiter(
            key_func=get_remote_address, storage_uri=storage_url, strategy=strategy
        ).limiter

    async def hit(self, limit: RateLimit, key: str) -> RateLimitResult:
        item = RateLimitItemPerSecond(limit.attempts, limit.window_seconds, KEY_NAMESPACE)
        if self._strategy.hit(item, key):
            return ALLOWED
        # O `limits` marca a janela com `time.time()`; a conta usa o mesmo relógio.
        reset_at = self._strategy.get_window_stats(item, key).reset_time
        return RateLimitResult(
            allowed=False, retry_after_seconds=max(1, math.ceil(reset_at - time.time()))
        )


class UnlimitedRateLimiter:
    """`RATE_LIMIT_ENABLED=false`: toda tentativa cabe."""

    async def hit(self, limit: RateLimit, key: str) -> RateLimitResult:
        return ALLOWED


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """O único ponto que escolhe a implementação — é aqui que se troca."""
    if not settings.rate_limit_enabled:
        return UnlimitedRateLimiter()
    return SlowapiRateLimiter(
        storage_url=settings.rate_limit_storage_url, strategy=settings.rate_limit_strategy
    )


@dataclass(frozen=True, slots=True)
class ClientIpResolver:
    """De quem é a requisição, para contar as tentativas por IP.

    Sem `header`, vale o IP do socket — certo só quando nada fica entre o
    cliente e a aplicação. Atrás de proxy o socket é sempre o proxy, e todo
    mundo dividiria um contador só.

    Com `header`, o valor é lido como lista (`a, b, c`) e o IP sai da posição
    `trusted_proxies`, **contada da direita**. É a única leitura que o cliente não
    falsifica: cada proxy acrescenta à direita o endereço de quem falou com ele, e
    o que o cliente escreveu no cabeçalho fica à esquerda. Ler o primeiro da lista
    — o "IP original" de tanto tutorial — deixaria quem ataca escolher o próprio
    IP e zerar o contador a cada requisição. Em cabeçalho de valor único que o
    proxy sobrescreve (`X-Real-IP` na Railway, `CF-Connecting-IP`), `1` lê o valor.

    Valor ausente, curto demais ou que não é IP cai no IP do socket: a requisição
    não passou pelo proxy (health check, rede privada), e quem conectou é o cliente.
    """

    header: str = ""
    trusted_proxies: int = 1

    @classmethod
    def from_settings(cls, settings: Settings) -> ClientIpResolver:
        return cls(header=settings.client_ip_header, trusted_proxies=settings.trusted_proxy_count)

    def resolve(self, request: Request) -> str:
        peer = request.client.host if request.client else "desconhecido"
        forwarded = self._forwarded(request) if self.header else None
        ip = _parse_ip(forwarded) if forwarded is not None else None
        if ip is None:
            ip = _parse_ip(peer)
        return peer if ip is None else _bucket(ip)

    def _forwarded(self, request: Request) -> str | None:
        # `getlist`, e não `get`: o mesmo cabeçalho em duas linhas é uma lista só,
        # e `get` devolveria a primeira — justamente a que o cliente escreveu.
        hops = [
            hop.strip()
            for line in request.headers.getlist(self.header)
            for hop in line.split(",")
            if hop.strip()
        ]
        return hops[-self.trusted_proxies] if len(hops) >= self.trusted_proxies else None


def _parse_ip(value: str) -> IPAddress | None:
    """Aceita também `ip:porta` e `[ipv6]:porta`, que alguns proxies gravam (Azure)."""
    host = value.strip()
    if host.startswith("["):
        host = host[1:].partition("]")[0]
    elif host.count(":") == 1:
        host = host.partition(":")[0]
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _bucket(ip: IPAddress) -> str:
    """A chave de contagem de um IP.

    IPv6 conta por /64: o provedor entrega a /64 inteira a um assinante só, e
    contar por endereço daria a quem ataca 2^64 contadores zerados. IPv4 mapeado
    (`::ffff:203.0.113.9`) volta a ser IPv4, para não virar uma segunda chave do
    mesmo cliente.
    """
    if isinstance(ip, ipaddress.IPv4Address):
        return str(ip)
    if ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return str(ipaddress.IPv6Network((ip, IPV6_PREFIX), strict=False))
