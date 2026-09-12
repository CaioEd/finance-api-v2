"""Regra do CRUD administrativo de usuários — sem banco e sem HTTP.

O serviço conversa com um `Protocol` (`AdminUserStore`) e com uma transação
mínima, não com o repositório concreto nem com a `AsyncSession`. É o que
permite exercitar aqui o que de fato é regra — o que vira 404, o que vira
conflito, e o que um administrador não pode fazer contra a própria conta — sem
subir Postgres.

A autorização em si (só admin chega nestas rotas) é do router, e está coberta
pela matriz em `tests/integration/test_authorization_matrix.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from argon2 import PasswordHasher as Argon2PasswordHasher
from sqlalchemy.exc import IntegrityError

from core.clock import Clock
from core.errors import (
    EmailTakenError,
    SelfTargetError,
    UsernameTakenError,
    UserNotFoundError,
)
from core.security import PasswordHasher
from models.user import Role, User
from repositories.admin_user_repository import UserFilters
from schemas.user import AdminUserCreateIn, AdminUserUpdateIn
from services.admin_user_service import AdminUserService, AdminUserStore, TokenRevoker

PASSWORD = "senha-bem-comprida"
NOW = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)


def make_user(
    *,
    user_id: UUID | None = None,
    email: str = "ana@exemplo.com",
    username: str = "ana",
    role: Role = Role.USER,
    is_active: bool = True,
) -> User:
    return User(
        id=user_id or uuid4(),
        email=email,
        username=username,
        password_hash="$argon2id$hash-de-mentira",
        first_name="Ana",
        last_name="Ribeiro",
        role=role,
        is_active=is_active,
    )


def integrity_error(constraint: str) -> IntegrityError:
    """Reproduz o erro que o Postgres devolve ao violar um UNIQUE."""
    orig = Exception(f'duplicate key value violates unique constraint "{constraint}"')
    return IntegrityError("INSERT INTO users ...", {}, orig)


class FakeAdminUserStore:
    """Implementa `AdminUserStore` em memória, guardando o que recebeu."""

    def __init__(self, users: Sequence[User] = ()) -> None:
        self.users = list(users)
        self.added: list[User] = []
        self.deleted: list[User] = []
        self.last_query: dict[str, Any] | None = None

    async def get(self, user_id: UUID) -> User | None:
        return next((user for user in self.users if user.id == user_id), None)

    async def list_users(self, *, filters: UserFilters, limit: int, offset: int) -> Sequence[User]:
        self.last_query = {"filters": filters, "limit": limit, "offset": offset}
        return self.users[offset : offset + limit]

    async def count_users(self, *, filters: UserFilters) -> int:
        return len(self.users)

    def add(self, user: User) -> None:
        self.added.append(user)
        self.users.append(user)

    async def delete(self, user: User) -> None:
        self.deleted.append(user)
        self.users.remove(user)


class FakeTransaction:
    """Transação de mentira: conta os commits e sabe falhar sob comando."""

    def __init__(self, *, fails_with: Exception | None = None) -> None:
        self._fails_with = fails_with
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1
        if self._fails_with is not None:
            raise self._fails_with

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeTokenRevoker:
    """Implementa `TokenRevoker`, guardando de quem encerrou as sessões e quando."""

    def __init__(self) -> None:
        self.revoked: list[tuple[UUID, datetime]] = []

    async def revoke_all_for_user(self, user_id: UUID, *, at: datetime) -> None:
        self.revoked.append((user_id, at))


@pytest.fixture
def hasher() -> PasswordHasher:
    # Custo mínimo: aqui interessa que a senha vire hash, não o quanto custa.
    return PasswordHasher(Argon2PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))


@pytest.fixture
def admin() -> User:
    return make_user(email="admin@exemplo.com", username="admin", role=Role.ADMIN)


def build_service(
    store: FakeAdminUserStore,
    transaction: FakeTransaction,
    hasher: PasswordHasher,
    revoker: FakeTokenRevoker | None = None,
) -> AdminUserService:
    users: AdminUserStore = store  # o fake precisa satisfazer o Protocol
    tokens: TokenRevoker = revoker or FakeTokenRevoker()
    return AdminUserService(
        transaction=transaction,
        users=users,
        tokens=tokens,
        hasher=hasher,
        clock=Clock(tz=ZoneInfo("America/Sao_Paulo"), instant=lambda: NOW),
    )


# ------------------------------------------------------------------- LIST


async def test_list_returns_the_page_and_the_total(hasher: PasswordHasher) -> None:
    store = FakeAdminUserStore([make_user(username=f"u{n}", email=f"u{n}@x.com") for n in range(5)])
    service = build_service(store, FakeTransaction(), hasher)

    page = await service.list_users(filters=UserFilters(), limit=2, offset=0)

    assert len(page.items) == 2
    assert page.total == 5, "o total é do filtro inteiro, não da página"
    assert page.limit == 2
    assert page.offset == 0


async def test_list_forwards_the_filters_to_the_repository(hasher: PasswordHasher) -> None:
    """Filtrar é construir query: a decisão é do repositório, não do serviço."""
    store = FakeAdminUserStore([make_user()])
    service = build_service(store, FakeTransaction(), hasher)
    filters = UserFilters(role=Role.ADMIN, is_active=False, search="ana")

    await service.list_users(filters=filters, limit=10, offset=20)

    assert store.last_query == {"filters": filters, "limit": 10, "offset": 20}


# ------------------------------------------------------------------ CREATE


async def test_create_never_stores_the_raw_password(hasher: PasswordHasher) -> None:
    store, transaction = FakeAdminUserStore(), FakeTransaction()
    service = build_service(store, transaction, hasher)

    created = await service.create_user(
        AdminUserCreateIn(email="novo@exemplo.com", username="novo", password=PASSWORD)
    )

    assert created.password_hash != PASSWORD
    assert created.password_hash.startswith("$argon2id$")
    assert store.added == [created]
    assert transaction.commits == 1


async def test_create_defaults_to_the_common_role(hasher: PasswordHasher) -> None:
    service = build_service(FakeAdminUserStore(), FakeTransaction(), hasher)

    created = await service.create_user(
        AdminUserCreateIn(email="novo@exemplo.com", username="novo", password=PASSWORD)
    )

    assert created.role is Role.USER
    assert created.is_active is True


async def test_create_can_promote_on_the_spot(hasher: PasswordHasher) -> None:
    """Criar admin é privilégio de admin — e é o caminho depois do `create-admin` do CLI."""
    service = build_service(FakeAdminUserStore(), FakeTransaction(), hasher)

    created = await service.create_user(
        AdminUserCreateIn(
            email="chefe@exemplo.com",
            username="chefe",
            password=PASSWORD,
            role=Role.ADMIN,
            is_active=False,
        )
    )

    assert created.role is Role.ADMIN
    assert created.is_active is False


@pytest.mark.parametrize(
    ("constraint", "expected"),
    [("uq_users_email", EmailTakenError), ("uq_users_username", UsernameTakenError)],
)
async def test_create_translates_a_unique_violation_into_a_conflict(
    hasher: PasswordHasher, constraint: str, expected: type[Exception]
) -> None:
    """Quem decide se houve colisão é o banco; o serviço só traduz o nome da constraint."""
    transaction = FakeTransaction(fails_with=integrity_error(constraint))
    service = build_service(FakeAdminUserStore(), transaction, hasher)

    with pytest.raises(expected):
        await service.create_user(
            AdminUserCreateIn(email="novo@exemplo.com", username="novo", password=PASSWORD)
        )

    assert transaction.rollbacks == 1, "sessão suja não pode voltar para o pool"


# ------------------------------------------------------------------ UPDATE


async def test_update_changes_only_what_was_sent(hasher: PasswordHasher, admin: User) -> None:
    target = make_user()
    store, transaction = FakeAdminUserStore([target, admin]), FakeTransaction()
    service = build_service(store, transaction, hasher)

    updated = await service.update_user(target.id, AdminUserUpdateIn(role=Role.ADMIN), actor=admin)

    assert updated.role is Role.ADMIN
    assert updated.email == "ana@exemplo.com", "campo não enviado não muda"
    assert updated.is_active is True
    assert transaction.commits == 1


async def test_update_can_deactivate_someone(hasher: PasswordHasher, admin: User) -> None:
    """Desativar é o botão de emergência: `get_current_user` recusa a conta na hora."""
    target = make_user()
    service = build_service(FakeAdminUserStore([target, admin]), FakeTransaction(), hasher)

    updated = await service.update_user(target.id, AdminUserUpdateIn(is_active=False), actor=admin)

    assert updated.is_active is False


async def test_deactivating_someone_ends_every_session(hasher: PasswordHasher, admin: User) -> None:
    """Sem isto, reativar a conta devolveria a sessão a quem estivesse com o refresh token."""
    target = make_user()
    revoker, transaction = FakeTokenRevoker(), FakeTransaction()
    service = build_service(FakeAdminUserStore([target, admin]), transaction, hasher, revoker)

    await service.update_user(target.id, AdminUserUpdateIn(is_active=False), actor=admin)

    assert revoker.revoked == [(target.id, NOW)]
    assert transaction.commits == 1, "a revogação fecha no mesmo commit da desativação"


@pytest.mark.parametrize(
    "data",
    [
        AdminUserUpdateIn(role=Role.ADMIN),
        AdminUserUpdateIn(is_active=True),
        AdminUserUpdateIn(first_name="Ana Paula", is_active=None),
    ],
    ids=["papel", "reativacao", "is_active-nulo"],
)
async def test_an_update_that_does_not_deactivate_keeps_the_sessions(
    hasher: PasswordHasher, admin: User, data: AdminUserUpdateIn
) -> None:
    target = make_user()
    revoker = FakeTokenRevoker()
    service = build_service(FakeAdminUserStore([target, admin]), FakeTransaction(), hasher, revoker)

    await service.update_user(target.id, data, actor=admin)

    assert revoker.revoked == []


async def test_update_of_someone_who_does_not_exist_is_a_404(
    hasher: PasswordHasher, admin: User
) -> None:
    service = build_service(FakeAdminUserStore([admin]), FakeTransaction(), hasher)

    with pytest.raises(UserNotFoundError):
        await service.update_user(uuid4(), AdminUserUpdateIn(is_active=False), actor=admin)


async def test_update_refuses_the_acting_admin(hasher: PasswordHasher, admin: User) -> None:
    """Sem esta trava o último admin se rebaixa e ninguém mais administra nada.

    Como quem chega aqui já é admin e não consegue mexer na própria conta,
    sempre resta pelo menos um administrador ativo no sistema.
    """
    transaction = FakeTransaction()
    service = build_service(FakeAdminUserStore([admin]), transaction, hasher)

    with pytest.raises(SelfTargetError):
        await service.update_user(admin.id, AdminUserUpdateIn(role=Role.USER), actor=admin)

    assert admin.role is Role.ADMIN
    assert transaction.commits == 0


async def test_update_conflict_rolls_back(hasher: PasswordHasher, admin: User) -> None:
    target = make_user()
    transaction = FakeTransaction(fails_with=integrity_error("uq_users_email"))
    service = build_service(FakeAdminUserStore([target, admin]), transaction, hasher)

    with pytest.raises(EmailTakenError):
        await service.update_user(
            target.id, AdminUserUpdateIn(email="admin@exemplo.com"), actor=admin
        )

    assert transaction.rollbacks == 1


# ------------------------------------------------------------------ DELETE


async def test_delete_removes_the_user(hasher: PasswordHasher, admin: User) -> None:
    target = make_user()
    store, transaction = FakeAdminUserStore([target, admin]), FakeTransaction()
    service = build_service(store, transaction, hasher)

    await service.delete_user(target.id, actor=admin)

    assert store.deleted == [target]
    assert transaction.commits == 1


async def test_delete_of_someone_who_does_not_exist_is_a_404(
    hasher: PasswordHasher, admin: User
) -> None:
    service = build_service(FakeAdminUserStore([admin]), FakeTransaction(), hasher)

    with pytest.raises(UserNotFoundError):
        await service.delete_user(uuid4(), actor=admin)


async def test_delete_refuses_the_acting_admin(hasher: PasswordHasher, admin: User) -> None:
    """Existe `DELETE /users/me` para isso, e ele exige a senha."""
    store = FakeAdminUserStore([admin])
    service = build_service(store, FakeTransaction(), hasher)

    with pytest.raises(SelfTargetError):
        await service.delete_user(admin.id, actor=admin)

    assert store.deleted == []
