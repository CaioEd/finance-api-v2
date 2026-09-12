"""Ciclo de vida da sessão: quanto dura cada token e o que encerra cada um.

Irmão de `tests/integration/test_auth.py`, que cobre registro, login, rotação e
o logout de um dispositivo contra Postgres. Aqui ficam a duração dos dois tokens
e os caminhos de revogação — nenhum deles usa SQL que o SQLite não tenha. O
desenho inteiro está em `docs/autenticacao-jwt.md`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from core.security import TokenCodec, fingerprint
from models.refresh_token import RefreshToken
from models.user import User
from tests.api.client import ApiClient, Response
from tests.api.factories import register_admin, register_user
from tests.factories import RegisteredUser

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT_ALL = "/api/v1/users/me/logout-all"
ME = "/api/v1/users/me"
ADMIN_USERS = "/api/v1/admin/users"


# --------------------------------------------------------------------- apoio


def another_device(client: ApiClient, user: RegisteredUser) -> RegisteredUser:
    """O mesmo usuário logado noutro aparelho: uma família de refresh tokens nova."""
    response = client.post(LOGIN, json={"email": user.email, "password": user.password})
    response.raise_for_status()
    body = response.json()
    return replace(user, access_token=body["access_token"], refresh_token=body["refresh_token"])


def refresh(client: ApiClient, raw_token: str) -> Response:
    return client.post(REFRESH, json={"refresh_token": raw_token})


def log_out_everywhere(client: ApiClient, user: RegisteredUser) -> None:
    """Afirma o 204: sem isso, um teste do *depois* passaria com a rota respondendo 404."""
    response = client.post(LOGOUT_ALL, headers=user.auth)
    assert response.status_code == 204, response.text


def stored(client: ApiClient, raw_token: str) -> RefreshToken:
    """A linha do refresh token, achada pelo hash — o banco nunca vê o token."""

    async def load(session: AsyncSession) -> RefreshToken:
        record = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == fingerprint(raw_token))
        )
        assert record is not None, "refresh token não encontrado no banco"
        return record

    return client.in_the_database(load)


def expire_at(client: ApiClient, raw_token: str, moment: datetime) -> None:
    """Move a validade do refresh token: é como o teste faz o tempo passar."""

    async def move(session: AsyncSession) -> None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == fingerprint(raw_token))
            .values(expires_at=moment)
        )

    client.in_the_database(move)


def set_active(
    client: ApiClient, admin: RegisteredUser, target: RegisteredUser, *, active: bool
) -> None:
    response = client.patch(
        f"{ADMIN_USERS}/{target.id}", headers=admin.auth, json={"is_active": active}
    )
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------ duração


def test_the_access_token_lasts_fifteen_minutes(client: ApiClient) -> None:
    """E carrega só o necessário: nome e e-mail no token envelheceriam com o perfil."""
    user = register_user(client)

    claims = jwt.decode(user.access_token, options={"verify_signature": False})

    assert claims["exp"] - claims["iat"] == 900
    assert set(claims) == {"sub", "role", "jti", "iat", "exp", "typ"}
    assert claims["sub"] == user.id
    assert claims["typ"] == "access"


def test_an_access_token_past_its_expiry_is_refused(client: ApiClient, settings: Settings) -> None:
    """Assinado com a chave certa, para um usuário que existe — só que velho."""
    user = register_user(client)
    codec = TokenCodec.from_settings(settings)
    stale = codec.issue_access(
        subject=UUID(user.id), role="user", issued_at=datetime.now(UTC) - timedelta(minutes=16)
    )

    response = client.get(ME, headers={"Authorization": f"Bearer {stale.token}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


def test_the_refresh_token_lasts_thirty_days(client: ApiClient) -> None:
    before = datetime.now(UTC)
    user = register_user(client)
    after = datetime.now(UTC)

    record = stored(client, user.refresh_token)

    assert before + timedelta(days=30) <= record.expires_at <= after + timedelta(days=30)


def test_each_rotation_restarts_the_thirty_days(client: ApiClient) -> None:
    """Janela deslizante: sessão usada ao menos uma vez a cada 30 dias não expira.

    Não há teto absoluto para uma sessão — a família dura enquanto for renovada.
    """
    user = register_user(client)
    expire_at(client, user.refresh_token, datetime.now(UTC) + timedelta(days=1))

    before = datetime.now(UTC)
    rotated = refresh(client, user.refresh_token)

    assert rotated.status_code == 200
    successor = stored(client, rotated.json()["refresh_token"])
    assert successor.expires_at >= before + timedelta(days=30)
    assert successor.family_id == stored(client, user.refresh_token).family_id


def test_an_expired_refresh_token_is_refused(client: ApiClient) -> None:
    user = register_user(client)
    expire_at(client, user.refresh_token, datetime.now(UTC) - timedelta(seconds=1))

    response = refresh(client, user.refresh_token)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_refresh_token"


# ---------------------------------------------------- sair de todos os dispositivos


def test_logout_all_ends_the_session_on_every_device(client: ApiClient) -> None:
    """Inclusive a de quem pediu: "todos" não tem exceção."""
    phone = register_user(client)
    laptop = another_device(client, phone)
    tablet = another_device(client, phone)

    response = client.post(LOGOUT_ALL, headers=laptop.auth)

    assert response.status_code == 204
    assert response.content == b""
    for device in (phone, laptop, tablet):
        assert refresh(client, device.refresh_token).status_code == 401


def test_a_token_revoked_by_logout_all_comes_back_as_reuse(client: ApiClient) -> None:
    """O servidor guarda que o token foi revogado, não por quê.

    Para `/auth/refresh`, token revogado reaparecendo é o sinal de roubo, e o
    código é o da detecção de reuso. Quem consome trata todo 401 do refresh do
    mesmo jeito: descarta os tokens e volta para o login.
    """
    user = register_user(client)
    log_out_everywhere(client, user)

    response = refresh(client, user.refresh_token)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_reuse_detected"


def test_logout_all_leaves_other_people_signed_in(client: ApiClient) -> None:
    ana = register_user(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")

    log_out_everywhere(client, ana)

    assert refresh(client, bruno.refresh_token).status_code == 200


def test_logout_all_is_idempotent(client: ApiClient) -> None:
    """Sem sessão viva não há o que revogar — e isso também é sucesso."""
    user = register_user(client)

    first = client.post(LOGOUT_ALL, headers=user.auth)
    second = client.post(LOGOUT_ALL, headers=user.auth)

    assert first.status_code == second.status_code == 204


def test_access_tokens_outlive_logout_all_until_they_expire(client: ApiClient) -> None:
    """O access token não é revogável: a janela é o TTL, 15 min por padrão.

    Nenhum dispositivo consegue mais renovar, mas o access token que cada um tem
    em mãos vale até o `exp`. É a decisão registrada em `core/security.py`; se o
    access passar a ser revogável, é este o teste que muda.
    """
    phone = register_user(client)
    laptop = another_device(client, phone)

    log_out_everywhere(client, phone)

    assert client.get(ME, headers=phone.auth).status_code == 200
    assert client.get(ME, headers=laptop.auth).status_code == 200


def test_logging_in_after_logout_all_starts_a_new_session(client: ApiClient) -> None:
    user = register_user(client)
    log_out_everywhere(client, user)

    fresh = another_device(client, user)

    assert refresh(client, fresh.refresh_token).status_code == 200


# ------------------------------------------------------------------ conta desativada


def test_reactivating_an_account_does_not_bring_its_sessions_back(client: ApiClient) -> None:
    """Desativar encerra as sessões, e não só as suspende.

    Enquanto a conta está desativada nada passa — `refresh` e `get_current_user`
    checam `is_active`. Se os refresh tokens ficassem vivos, reativar devolveria
    a sessão a quem estivesse com eles, inclusive a quem motivou a desativação.
    """
    admin = register_admin(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")

    set_active(client, admin, bruno, active=False)
    set_active(client, admin, bruno, active=True)

    assert refresh(client, bruno.refresh_token).status_code == 401


def test_an_inactive_account_cannot_log_in(client: ApiClient) -> None:
    """403 só com a senha certa: quem não a sabe leva o 401 de sempre."""
    admin = register_admin(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")
    set_active(client, admin, bruno, active=False)

    right = client.post(LOGIN, json={"email": bruno.email, "password": bruno.password})
    wrong = client.post(LOGIN, json={"email": bruno.email, "password": "senha-errada-mas-longa"})

    assert right.status_code == 403
    assert right.json()["error"]["code"] == "account_inactive"
    assert wrong.status_code == 401


def test_an_inactive_account_cannot_refresh(client: ApiClient) -> None:
    """A checagem continua no refresh, mesmo com a desativação revogando tudo.

    Conta desativada por fora da rota de administração — direto no banco, ou por
    um caminho futuro que esqueça de revogar — não pode depender de todo caminho
    lembrar.
    """
    user = register_user(client)

    async def deactivate(session: AsyncSession) -> None:
        record = await session.get(User, UUID(user.id))
        assert record is not None
        record.is_active = False

    client.in_the_database(deactivate)

    response = refresh(client, user.refresh_token)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_inactive"
