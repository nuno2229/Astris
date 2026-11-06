"""Recurring payments (E06): list per household, dismiss/restore."""
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.deps import DB, User, not_found
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["recurring"])


class RecurringOut(BaseModel):
    id: UUID
    account_id: UUID
    name: str
    category: str | None
    cadence: str
    direction: str
    expected_amount: Decimal
    previous_amount: Decimal | None
    currency: str
    occurrences: int
    last_date: date
    next_date: date
    status: str
    awaiting_statement: bool   # due date passed but no statement imported that far yet
    editable: bool


class RecurringUpdate(BaseModel):
    status: Literal["active", "dismissed"]


SELECT = """
SELECT rs.id, rs.account_id, rs.name, rs.category, rs.cadence, rs.direction, rs.expected_amount, rs.previous_amount,
       rs.currency, rs.occurrences, rs.last_date, rs.next_date, rs.status,
       (rs.status = 'active' AND rs.next_date < CURRENT_DATE) AS awaiting_statement,
       (a.owner_user_id = app_uid()) AS editable
  FROM recurring_series rs
  JOIN household_accounts ha ON ha.account_id = rs.account_id AND ha.household_id = :h
  JOIN accounts a ON a.id = rs.account_id
  JOIN memberships ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
"""


def household_recurring(db: DB, hid: UUID, include_dismissed: bool = False) -> list[RecurringOut]:
    rows = db.execute(text(SELECT + " WHERE (:all OR rs.status <> 'dismissed') ORDER BY rs.status = 'missed' DESC, rs.next_date"),
                      {"h": hid, "all": include_dismissed})
    return [RecurringOut(**r._mapping) for r in rows]


@router.get("/households/{hid}/recurring", response_model=list[RecurringOut])
def list_recurring(hid: UUID, user: User, db: DB, include_dismissed: bool = False):
    my_role(db, hid)
    return household_recurring(db, hid, include_dismissed)


@router.patch("/recurring/{rid}", status_code=204)
def update_recurring(rid: UUID, body: RecurringUpdate, user: User, db: DB):
    """Dismiss ('this isn't a regular payment') or restore. Account owner only (RLS)."""
    n = db.execute(text("UPDATE recurring_series SET status = :s, updated_at = now() WHERE id = :id"),
                   {"s": body.status, "id": rid}).rowcount
    if not n:
        raise not_found("Regular payment not found")
