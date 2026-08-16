"""Contrato de autenticação.

Um único formato de resposta para registro, login e renovação. O fluxo antigo
tinha dois endpoints de login com payloads diferentes; aqui existe um só,
alinhado ao vocabulário OAuth2 que qualquer cliente HTTP já entende.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from schemas.user import Email, Password, PersonName, Username, UserOut


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    username: Username
    password: Password
    first_name: PersonName = ""
    last_name: PersonName = ""


class LoginIn(BaseModel):
    """Login é por e-mail. `username` não é credencial."""

    model_config = ConfigDict(extra="forbid")

    email: Email
    password: str


class RefreshIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class LogoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    """Segundos de validade do access token."""


class RegisterOut(TokenPairOut):
    user: UserOut
