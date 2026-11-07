"""Transfers between your own / your household's accounts (P-1): suggestions, link, not-a-transfer, unlink."""
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import audit, transfers
from app.deps import DB, User, not_found
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["transfers"])


class Leg(BaseModel):
    id: UUID
    account_id: UUID
    account: str
    description: str
    posted_on: date


class Suggestion(BaseModel):
    out: Leg
    into: Leg
    amount: Decimal
    currency: str


class PairIn(BaseModel):
    out_id: UUID
    in_id: UUID


def _out(p: transfers.Pair) -> Suggestion:
    return Suggestion(out=Leg(id=p.out_id, account_id=p.out_account_id, account=p.out_account, description=p.out_desc, posted_on=p.out_date),
                      into=Leg(id=p.in_id, account_id=p.in_account_id, account=p.in_account, description=p.in_desc, posted_on=p.in_date),
                      amount=abs(p.amount).quantize(Decimal("0.01")), currency=p.currency)


@router.get("/transfers/suggestions", response_model=list[Suggestion])
def suggestions(user: User, db: DB, household_id: UUID | None = None, tx: UUID | None = None,
                limit: int = Query(50, ge=1, le=200)):
    if household_id:
        my_role(db, household_id)
    pairs = transfers.candidates(db, user.id, household_id)
    if tx:
        pairs = [p for p in pairs if tx in (p.out_id, p.in_id)]
    return [_out(p) for p in pairs[:limit]]


@router.post("/transfers", status_code=201)
def link(body: PairIn, user: User, db: DB):
    try:
        with db.begin_nested():
            g = transfers.link(db, body.out_id, body.in_id)
    except DBAPIError as e:
        code = getattr(e.orig, "sqlstate", "")
        if code == "P0002":
            raise not_found("Transaction not found")
        if code == "42501":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only link transfers involving your own accounts")
        if code == "23505":
            raise HTTPException(status.HTTP_409_CONFLICT, "Already linked as a transfer")
        if code == "22023":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "These two don't match: same amount, opposite direction, within a week")
        raise
    r = db.execute(text("""SELECT t.amount, t.currency, t.account_id, a.owner_user_id, a.name FROM transactions t
                            JOIN accounts a ON a.id = t.account_id WHERE t.id IN (:o, :i)
                            ORDER BY (a.owner_user_id = app_uid()) DESC, t.amount"""), {"o": body.out_id, "i": body.in_id}).all()
    mine = r[0]
    audit.log(db, user.id, "tx.transfer", "transaction", f"Marked {abs(mine.amount)} {mine.currency} as a transfer ({mine.name})",
              entity_id=body.out_id if mine.amount < 0 else body.in_id, account_id=mine.account_id, after={"transfer_group": g})
    return {"transfer_group": g}


@router.post("/transfers/dismiss", status_code=204)
def dismiss(body: PairIn, user: User, db: DB):
    if not db.execute(text("SELECT count(*) FROM transactions WHERE id IN (:o, :i)"), {"o": body.out_id, "i": body.in_id}).scalar() == 2:
        raise not_found("Transaction not found")
    db.execute(text("INSERT INTO transfer_dismissals (out_tx, in_tx) VALUES (:o, :i) ON CONFLICT DO NOTHING"),
               {"o": body.out_id, "i": body.in_id})


@router.delete("/transfers/{group}", status_code=204)
def unlink(group: UUID, user: User, db: DB):
    mine = db.execute(text("""SELECT t.id, t.account_id FROM transactions t JOIN accounts a ON a.id = t.account_id
                               WHERE t.transfer_group = :g AND a.owner_user_id = app_uid() LIMIT 1"""), {"g": group}).first()
    if mine is None:
        raise not_found("Transfer not found")
    try:
        with db.begin_nested():
            db.execute(text("SELECT unlink_transfer(:g)"), {"g": group})
    except DBAPIError as e:
        if getattr(e.orig, "sqlstate", "") == "42501":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the account owner can change this")
        raise
    from app.categorise.service import apply_rules
    apply_rules(db, user.id)
    audit.log(db, user.id, "tx.unlink", "transaction", "No longer counted as a transfer", entity_id=mine.id, account_id=mine.account_id)
