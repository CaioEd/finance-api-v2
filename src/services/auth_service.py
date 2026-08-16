"""Regra de autenticação: registro, login, rotação e revogação.

Python puro: nada aqui conhece HTTP nem FastAPI. Falhas saem como
`DomainError`, e a camada de rotas traduz para status e envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.clock import Clock
from core.errors import (
    AccountInactiveError,
    AuthenticationError,
    InvalidRefreshTokenError,
    TokenReuseError,
)
from core.security import PasswordHasher, TokenCodec, fingerprint, generate_opaque_token
from models.refresh_token import RefreshToken
from models.user import Role, User
from repositories.refresh_token_repository import RefreshTokenRepository
from repositories.user_repository import UserRepository, translate_integrity_error
from schemas.auth import RegisterIn


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


class AuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        users: UserRepository,
        tokens: RefreshTokenRepository,
        hasher: PasswordHasher,
        codec: TokenCodec,
        clock: Clock,
        refresh_ttl: timedelta,
    ) -> None:
        self._session = session
        self._users = users
        self._tokens = tokens
        self._hasher = hasher
        self._codec = codec
        self._clock = clock
        self._refresh_ttl = refresh_ttl

    async def register(self, data: RegisterIn) -> tuple[User, TokenPair]:
        user = User(
            email=data.email,
            username=data.username,
            password_hash=self._hasher.hash(data.password),
            first_name=data.first_name,
            last_name=data.last_name,
            role=Role.USER,
        )
        self._users.add(user)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc

        pair, _ = self._issue_pair(user, family_id=uuid4())
        await self._session.commit()
        return user, pair

    async def login(self, email: str, password: str) -> TokenPair:
        user = await self._users.get_by_email(email)

        if user is None:
            # Equaliza o tempo de resposta com o de uma senha errada.
            self._hasher.dummy_verify()
            raise AuthenticationError()

        if not self._hasher.verify(user.password_hash, password):
            raise AuthenticationError()

        if not user.is_active:
            raise AccountInactiveError()

        if self._hasher.needs_rehash(user.password_hash):
            user.password_hash = self._hasher.hash(password)

        user.last_login_at = self._clock.now_utc()
        pair, _ = self._issue_pair(user, family_id=uuid4())
        await self._session.commit()
        return pair

    async def refresh(self, raw_token: str) -> TokenPair:
        now = self._clock.now_utc()
        record = await self._tokens.get_by_hash(fingerprint(raw_token))

        if record is None:
            raise InvalidRefreshTokenError()

        if record.revoked_at is not None:
            # Um token já rotacionado reaparecendo é o sinal clássico de roubo:
            # ou o legítimo ou o atacante está usando uma cópia antiga. Não dá
            # para saber qual, então a linhagem inteira cai e os dois refazem
            # login.
            await self._tokens.revoke_family(record.family_id, at=now)
            await self._session.commit()
            raise TokenReuseError()

        if record.expires_at <= now:
            raise InvalidRefreshTokenError()

        user = await self._users.get(record.user_id)
        if user is None:
            raise InvalidRefreshTokenError()
        if not user.is_active:
            raise AccountInactiveError()

        rotated, successor = self._issue_pair(user, family_id=record.family_id)
        # O sucessor precisa existir no banco antes de ser referenciado: a FK é
        # auto-referente e não há `relationship()` declarado, então o unit of
        # work não tem como saber que o UPDATE depende deste INSERT.
        await self._session.flush()

        record.revoked_at = now
        record.replaced_by_id = successor.id
        await self._session.commit()
        return rotated

    async def logout(self, raw_token: str) -> None:
        """Idempotente: token desconhecido ou já revogado também é sucesso.

        Logout que falha só ensina o cliente a ignorar o erro.
        """
        record = await self._tokens.get_by_hash(fingerprint(raw_token))
        if record is not None and record.revoked_at is None:
            record.revoked_at = self._clock.now_utc()
            await self._session.commit()

    def _issue_pair(self, user: User, *, family_id: UUID) -> tuple[TokenPair, RefreshToken]:
        """Emite o par e devolve também o registro, para quem precisa encadear."""
        now = self._clock.now_utc()
        access = self._codec.issue_access(subject=user.id, role=user.role.value, issued_at=now)

        raw_refresh = generate_opaque_token()
        record = RefreshToken(
            id=uuid4(),
            user_id=user.id,
            token_hash=fingerprint(raw_refresh),
            family_id=family_id,
            expires_at=now + self._refresh_ttl,
        )
        self._tokens.add(record)

        pair = TokenPair(
            access_token=access.token,
            refresh_token=raw_refresh,
            expires_in=access.expires_in,
        )
        return pair, record
