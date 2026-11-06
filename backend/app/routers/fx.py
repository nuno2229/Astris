"""Household exchange rates (E08-04/05). Manual for now; the automatic offline source is decision D-8.
Conversions never guess: a currency without a rate is reported as missing."""
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from app.deps import DB, User
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["fx"])


class RateIn(BaseModel):
    from_currency: str = Field(pattern=r"^[A-Z]{3}$")
    to_currency: str = Field(pattern=r"^[A-Z]{3}$")
    rate: Decimal = Field(gt=0, lt=Decimal("1e8"), max_digits=18, decimal_places=8)
    as_of: date | None = None

    @field_validator("from_currency", "to_currency", mode="before")
    @classmethod
    def up(cls, v):
        return v.upper() if isinstance(v, str) else v


class RateOut(BaseModel):
    from_currency: str
    to_currency: str
    rate: Decimal
    as_of: date
    source: str


def rate_table(db: DB, hid: UUID) -> dict[tuple[str, str], Decimal]:
    return {(r.from_currency, r.to_currency): Decimal(r.rate) for r in db.execute(
        text("SELECT from_currency, to_currency, rate FROM fx_rates WHERE household_id = :h"), {"h": hid})}


def convert(v: Decimal, frm: str, to: str, rates: dict) -> Decimal | None:
    if frm == to:
        return v
    if (frm, to) in rates:
        return v * rates[(frm, to)]
    if (to, frm) in rates:
        return v / rates[(to, frm)]
    return None


@router.get("/households/{hid}/fx-rates", response_model=list[RateOut])
def list_rates(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    rows = db.execute(text("SELECT from_currency, to_currency, rate, as_of, source FROM fx_rates WHERE household_id = :h "
                           "ORDER BY from_currency, to_currency"), {"h": hid})
    return [RateOut(**r._mapping) for r in rows]


@router.put("/households/{hid}/fx-rates", response_model=RateOut)
def set_rate(hid: UUID, body: RateIn, user: User, db: DB):
    if my_role(db, hid) not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an admin can set exchange rates")
    if body.from_currency == body.to_currency:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick two different currencies")
    # keep one direction per pair
    db.execute(text("DELETE FROM fx_rates WHERE household_id = :h AND from_currency = :t AND to_currency = :f"),
               {"h": hid, "f": body.from_currency, "t": body.to_currency})
    row = db.execute(text("""
        INSERT INTO fx_rates (household_id, from_currency, to_currency, rate, as_of, updated_by)
        VALUES (:h, :f, :t, :r, :d, :u)
        ON CONFLICT (household_id, from_currency, to_currency) DO UPDATE SET rate = EXCLUDED.rate, as_of = EXCLUDED.as_of,
            updated_by = EXCLUDED.updated_by, updated_at = now()
        RETURNING from_currency, to_currency, rate, as_of, source"""),
        {"h": hid, "f": body.from_currency, "t": body.to_currency, "r": body.rate, "d": body.as_of or date.today(),
         "u": user.id}).one()
    return RateOut(**row._mapping)


@router.delete("/households/{hid}/fx-rates/{frm}/{to}", status_code=204)
def delete_rate(hid: UUID, frm: str, to: str, user: User, db: DB):
    if my_role(db, hid) not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an admin can set exchange rates")
    db.execute(text("DELETE FROM fx_rates WHERE household_id = :h AND from_currency = :f AND to_currency = :t"),
               {"h": hid, "f": frm.upper()[:3], "t": to.upper()[:3]})
