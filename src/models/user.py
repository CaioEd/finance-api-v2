"""Model de usuário.

Diferenças deliberadas em relação ao `auth.User` do Django que este substitui:

- **`role` é coluna daqui**, não uma tabela `Profile` 1-para-1 criada por signal.
  Dado que nasce fora do fluxo de criação pode não existir; e obrigava um JOIN
  em toda checagem de permissão.
- **`email` é único e normalizado.** Era o campo usado para login sem ser único,
  o que fazia dois cadastros com o mesmo e-mail derrubarem o login dos dois.
- **Um eixo de permissão só.** `is_superuser` e `is_staff` sumiram: três campos
  descrevendo a mesma coisa é como se perde o controle de quem pode o quê.
  `is_active` fica, porque é estado da conta, não permissão.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, TimestampMixin


class Role(StrEnum):
    USER = "user"
    ADMIN = "admin"


def role_column() -> Enum:
    """VARCHAR, não ENUM nativo do Postgres.

    Alterar um CHECK é um ALTER TABLE simples e reversível no Alembic; mexer
    num tipo ENUM tem restrição transacional e remover valor exige recriar o
    tipo. O ganho de armazenamento seria irrelevante nesta escala.

    `create_constraint=False` e o CHECK declarado à parte em `__table_args__`:
    o CHECK implícito do `Enum` é criado no DDL mas não aparece no metadata, e
    o autogenerate do Alembic passa a propor `DROP CONSTRAINT` a cada migration
    nova — a validação de `role` sumiria em silêncio na primeira delas.
    """
    return Enum(
        Role,
        native_enum=False,
        create_constraint=False,
        length=16,
        name="role",
        values_callable=lambda enum_type: [member.value for member in enum_type],
    )


ROLE_CHECK = CheckConstraint(
    "role IN ({})".format(", ".join(f"'{member.value}'" for member in Role)),
    name="role",  # com a convenção de nomes vira `ck_users_role`
)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (ROLE_CHECK,)

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False, server_default="")
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, server_default="")
    role: Mapped[Role] = mapped_column(
        role_column(), nullable=False, default=Role.USER, server_default=Role.USER.value
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    def __repr__(self) -> str:
        return f"<User {self.username} ({self.role})>"
