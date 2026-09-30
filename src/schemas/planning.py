"""Contratos de limites e objetivos."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from schemas.balance import Amount
from schemas.base import PatchIn


class LimitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    starts_on: date
    ends_on: date
    category_id: UUID | None = None


class LimitPatch(PatchIn):
    name: str | None = Field(None, min_length=2, max_length=120)
    amount: Decimal | None = Field(None, gt=0, max_digits=14, decimal_places=2)
    starts_on: date | None = None
    ends_on: date | None = None
    category_id: UUID | None = None
    clear_category: bool | None = None


class LimitOut(BaseModel):
    id: UUID
    name: str
    amount: Amount
    starts_on: date
    ends_on: date
    category_id: UUID | None
    category_name: str | None
    spent: Amount
    exceeded: bool
    created_at: datetime


class GoalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    target_amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    target_on: date | None = None
    investment_id: UUID | None = None


class GoalPatch(PatchIn):
    name: str | None = Field(None, min_length=2, max_length=120)
    target_amount: Decimal | None = Field(None, gt=0, max_digits=14, decimal_places=2)
    target_on: date | None = None
    investment_id: UUID | None = None
    clear_investment: bool | None = None
    clear_target_on: bool | None = None


class GoalOut(BaseModel):
    id: UUID
    name: str
    target_amount: Amount
    target_on: date | None
    investment_id: UUID | None
    investment_name: str | None
    current_amount: Amount
    progress_percent: float
    achieved: bool
    created_at: datetime
