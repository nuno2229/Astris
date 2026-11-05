from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

Currency = Field(pattern=r"^[A-Z]{3}$")
Role = Literal["owner", "admin", "member"]


class RegisterIn(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=10, max_length=256)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class MeOut(BaseModel):
    id: UUID
    email: str
    display_name: str
    home_currency: str


class MeUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    home_currency: str | None = Currency


class HouseholdIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class HouseholdOut(BaseModel):
    id: UUID
    name: str
    role: Role


class MemberOut(BaseModel):
    user_id: UUID
    display_name: str
    email: str
    role: Role
    joined_at: datetime


class HouseholdDetail(HouseholdOut):
    members: list[MemberOut]


class InviteIn(BaseModel):
    email: EmailStr | None = None
    role: Literal["admin", "member"] = "member"


class InviteOut(BaseModel):
    id: UUID
    email: str | None
    role: Role
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


class InviteCreated(InviteOut):
    code: str  # shown exactly once


class AcceptInviteIn(BaseModel):
    code: str = Field(min_length=10, max_length=100)


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: Literal["bank", "card", "cash"]
    currency: str = Currency
    institution: str | None = Field(default=None, max_length=100)

    @field_validator("currency", mode="before")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper() if isinstance(v, str) else v


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    institution: str | None = Field(default=None, max_length=100)


class AccountOut(BaseModel):
    id: UUID
    name: str
    type: str
    currency: str
    institution: str | None
    owner_user_id: UUID
    owner_name: str
    is_mine: bool
    balance: Decimal | None = None


class ShareIn(BaseModel):
    attribution: Literal["include", "hide"]


class HouseholdAccountOut(AccountOut):
    attribution: Literal["include", "hide"]


class TransactionOut(BaseModel):
    id: UUID
    account_id: UUID
    posted_on: date
    amount: Decimal
    currency: str
    description: str
    merchant: str | None
    counterparty: str | None
    category: str | None
    running_balance: Decimal | None


class ImportResult(BaseModel):
    batch_id: UUID
    format: str
    rows_total: int
    rows_new: int
    rows_duplicate: int
    warnings: list[str]
