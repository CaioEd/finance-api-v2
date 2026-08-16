"""Hash de senha, access token (JWT) e tokens opacos de refresh.

Duas naturezas de token, de propósito (ver documento de arquitetura, §1.4):

- **access**: JWT curto (15 min), sem estado no servidor. Não é revogável;
  a janela de exposição é o próprio TTL. Carrega só `role` além do sujeito —
  nome e e-mail dentro do token ficam velhos assim que o perfil muda.
- **refresh**: string aleatória opaca, guardada no banco apenas como SHA-256.
  Não é JWT porque a revogação precisa ser autoritativa, e isso já obriga a
  consultar o banco — o JWT só somaria superfície de erro.

O hash do refresh usa SHA-256 puro, e não argon2, porque o segredo tem 256
bits de entropia aleatória: não há o que um ataque de dicionário adivinhe, e a
verificação acontece a cada renovação.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerifyMismatchError

from core.config import Settings
from core.errors import InvalidTokenError, TokenExpiredError

ACCESS_TOKEN_TYPE = "access"
_DUMMY_PASSWORD = "argon2-timing-equalizer"


@dataclass(frozen=True, slots=True)
class PasswordHasher:
    """argon2id, com os parâmetros de custo vindos das Settings."""

    _hasher: Argon2PasswordHasher

    @classmethod
    def from_settings(cls, settings: Settings) -> PasswordHasher:
        return cls(
            Argon2PasswordHasher(
                time_cost=settings.argon2_time_cost,
                memory_cost=settings.argon2_memory_cost_kib,
                parallelism=settings.argon2_parallelism,
            )
        )

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, InvalidHashError, Argon2Error):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        """Verdadeiro quando o hash foi gerado com custo menor que o atual."""
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True

    def dummy_verify(self) -> None:
        """Queima o mesmo tempo de um verify.

        Chamado quando o e-mail não existe: sem isto, a resposta a um e-mail
        desconhecido volta perceptivelmente mais rápido que a de uma senha
        errada, e o tempo de resposta vira um oráculo de contas cadastradas.
        """
        self._hasher.hash(_DUMMY_PASSWORD)


@dataclass(frozen=True, slots=True)
class AccessClaims:
    subject: UUID
    role: str
    token_id: UUID


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    token: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class TokenCodec:
    """Emite e valida o access token.

    Atenção a um acoplamento que não dá para remover: `issued_at` vem do `Clock`
    da aplicação, mas quem valida `iat`/`exp` na decodificação é o PyJWT, contra
    o relógio do **sistema**. Um `Clock` adiantado emite tokens com `iat` no
    futuro, e o próprio PyJWT os recusa como "not yet valid". Ou seja: o relógio
    da aplicação não pode ser artificialmente deslocado.
    """

    secret: str
    algorithm: str
    access_ttl: timedelta

    @classmethod
    def from_settings(cls, settings: Settings) -> TokenCodec:
        return cls(
            secret=settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
            access_ttl=timedelta(seconds=settings.access_token_ttl_seconds),
        )

    def issue_access(self, *, subject: UUID, role: str, issued_at: datetime) -> IssuedAccessToken:
        expires_at = issued_at + self.access_ttl
        payload: dict[str, Any] = {
            "sub": str(subject),
            "role": role,
            "jti": str(uuid4()),
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "typ": ACCESS_TOKEN_TYPE,
        }
        return IssuedAccessToken(
            token=jwt.encode(payload, self.secret, algorithm=self.algorithm),
            expires_in=int(self.access_ttl.total_seconds()),
        )

    def decode_access(self, token: str) -> AccessClaims:
        try:
            payload = jwt.decode(
                token,
                self.secret,
                algorithms=[self.algorithm],
                options={"require": ["sub", "exp", "typ"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError() from exc
        except jwt.PyJWTError as exc:
            raise InvalidTokenError() from exc

        # Sem esta checagem, um refresh token assinado com a mesma chave passaria
        # por access. Aqui o refresh é opaco, mas a garantia é barata.
        if payload.get("typ") != ACCESS_TOKEN_TYPE:
            raise InvalidTokenError("Token não é um access token.")

        try:
            return AccessClaims(
                subject=UUID(payload["sub"]),
                role=str(payload.get("role", "")),
                token_id=UUID(payload["jti"]),
            )
        except (KeyError, ValueError) as exc:
            raise InvalidTokenError("Token malformado.") from exc


def generate_opaque_token() -> str:
    """256 bits de entropia, seguros para URL."""
    return secrets.token_urlsafe(32)


def fingerprint(token: str) -> str:
    """SHA-256 hexadecimal — o que vai para o banco no lugar do token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
