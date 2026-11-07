"""Split payments (P-3) and settling up between household members (P-4). Splitting is a household option.

A payment can be divided into parts, each with a category and optionally who it was for. Category totals use the
parts. In a household with splitting on, a part that was for someone other than the payer means they owe the payer;
recording a settlement pays it back."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import audit
from app.deps import DB, User, not_found
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["splits"])
Q = Decimal("0.01")


class Part(BaseModel):
    amount: Decimal = Field(gt=Decimal("-1e12"), lt=Decimal("1e12"), decimal_places=2)
    category: str | None = Field(default=None, max_length=40)
    for_user: UUID | None = None
    note: str | None = Field(default=None, max_length=120)


class SplitIn(BaseModel):
    parts: list[Part] = Field(max_length=20)       # empty = undo the split


class PartOut(Part):
    for_name: str | None = None


def _enabled(db, hid: UUID) -> bool:
    return bool(db.execute(text("SELECT splits_enabled FROM household_settings WHERE household_id = :h"), {"h": hid}).scalar())


@router.get("/transactions/{tid}/splits", response_model=list[PartOut])
def get_splits(tid: UUID, user: User, db: DB):
    if not db.execute(text("SELECT 1 FROM transactions WHERE id = :t"), {"t": tid}).first():
        raise not_found("Transaction not found")
    return [PartOut(**r._mapping) for r in db.execute(text("""
        SELECT s.amount, s.category, s.for_user, s.note, u.display_name AS for_name FROM transaction_splits s
          LEFT JOIN users u ON u.id = s.for_user WHERE s.transaction_id = :t ORDER BY s.position"""), {"t": tid})]


@router.put("/transactions/{tid}/splits", response_model=list[PartOut])
def set_splits(tid: UUID, body: SplitIn, user: User, db: DB):
    t = db.execute(text("""SELECT t.id, t.amount, t.currency, t.account_id, t.description, a.owner_user_id FROM transactions t
                            JOIN accounts a ON a.id = t.account_id WHERE t.id = :t"""), {"t": tid}).first()
    if t is None:
        raise not_found("Transaction not found")
    if t.owner_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the account owner can split this")
    if body.parts:
        if len(body.parts) < 2:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Split into at least two parts")
        amt = Decimal(t.amount)
        if any(p.amount == 0 or (p.amount > 0) != (amt > 0) for p in body.parts):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Each part must be part of the payment (same direction, not zero)")
        if sum((p.amount for p in body.parts), Decimal(0)) != amt:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"The parts must add up to {abs(amt):.2f}")
        from app.categorise import custom
        allowed = set(custom.for_user(db))
        if any(p.category and p.category not in allowed for p in body.parts):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown category")
        # "for" someone: you, or a member of a household this account is shared into
        people = {r[0] for r in db.execute(text("""
            SELECT m.user_id FROM household_accounts ha JOIN memberships m ON m.household_id = ha.household_id
             WHERE ha.account_id = :a AND ha.frozen_at IS NULL"""), {"a": t.account_id})} | {user.id}
        if any(p.for_user and p.for_user not in people for p in body.parts):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick someone from a household this account is shared with")
    before = get_splits(tid, user, db)
    db.execute(text("DELETE FROM transaction_splits WHERE transaction_id = :t"), {"t": tid})
    for i, p in enumerate(body.parts):
        db.execute(text("""INSERT INTO transaction_splits (transaction_id, position, amount, category, for_user, note)
                           VALUES (:t, :i, :a, :c, :f, :n)"""),
                   {"t": tid, "i": i, "a": p.amount, "c": p.category, "f": p.for_user, "n": (p.note or "").strip() or None})
    audit.log(db, user.id, "tx.split", "transaction",
              f"Split {t.description} into {len(body.parts)} parts" if body.parts else f"Undid the split of {t.description}",
              entity_id=tid, account_id=t.account_id, before=[x.model_dump(mode="json") for x in before],
              after=[p.model_dump(mode="json") for p in body.parts])
    return get_splits(tid, user, db)


# ---------- settle up (P-4) ----------

class Owes(BaseModel):
    from_user: UUID
    from_name: str
    to_user: UUID
    to_name: str
    amount: Decimal
    currency: str


class SettlementOut(BaseModel):
    id: UUID
    from_name: str | None
    to_name: str | None
    amount: Decimal
    currency: str
    settled_on: date
    note: str | None
    mine: bool
    confirmed: bool = True
    can_confirm: bool = False           # you're the receiver of a pending payment


class Balances(BaseModel):
    enabled: bool
    can_change: bool
    owes: list[Owes] = []
    settlements: list[SettlementOut] = []


class SettlementIn(BaseModel):
    from_user: UUID
    to_user: UUID
    amount: Decimal = Field(gt=0, lt=Decimal("1e9"), decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    settled_on: date | None = None
    note: str | None = Field(default=None, max_length=200)


class EnabledIn(BaseModel):
    enabled: bool


def balances(db, hid: UUID) -> list[Owes]:
    owed: dict = defaultdict(Decimal)          # (debtor, creditor, currency) -> amount
    for r in db.execute(text("""
        SELECT s.for_user AS debtor, a.owner_user_id AS creditor, t.currency, sum(-s.amount) AS v
          FROM transaction_splits s JOIN transactions t ON t.id = s.transaction_id
          JOIN accounts a ON a.id = t.account_id
          JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h
               AND (ha.frozen_at IS NULL OR (t.posted_on <= ha.until AND t.created_at <= ha.frozen_at))
          JOIN memberships md ON md.household_id = :h AND md.user_id = s.for_user          -- both still in this household
          JOIN memberships mc ON mc.household_id = :h AND mc.user_id = a.owner_user_id
         WHERE s.for_user IS NOT NULL AND s.for_user <> a.owner_user_id AND t.transfer_group IS NULL
         GROUP BY 1, 2, 3"""), {"h": hid}):
        owed[(r.debtor, r.creditor, r.currency)] += Decimal(r.v)
    for r in db.execute(text("""SELECT from_user, to_user, currency, sum(amount) AS v FROM settlements
                                 WHERE household_id = :h AND confirmed AND from_user IS NOT NULL AND to_user IS NOT NULL
                                 GROUP BY 1, 2, 3"""), {"h": hid}):
        owed[(r.from_user, r.to_user, r.currency)] -= Decimal(r.v)
    names = {r.id: r.display_name for r in db.execute(text("SELECT id, display_name FROM users"))}
    out, done = [], set()
    for (x, y, c) in list(owed):
        if (y, x, c) in done or (x, y, c) in done:
            continue
        done.add((x, y, c))
        net = owed.get((x, y, c), Decimal(0)) - owed.get((y, x, c), Decimal(0))
        net = net.quantize(Q)
        if net > 0:
            out.append(Owes(from_user=x, from_name=names.get(x, "Someone"), to_user=y, to_name=names.get(y, "Someone"), amount=net, currency=c))
        elif net < 0:
            out.append(Owes(from_user=y, from_name=names.get(y, "Someone"), to_user=x, to_name=names.get(x, "Someone"), amount=-net, currency=c))
    return sorted(out, key=lambda o: -o.amount)


@router.get("/households/{hid}/balances", response_model=Balances)
def get_balances(hid: UUID, user: User, db: DB):
    role = my_role(db, hid)
    on = _enabled(db, hid)
    if not on:
        return Balances(enabled=False, can_change=role == "owner")
    st = [SettlementOut(id=r.id, from_name=r.from_nm, to_name=r.to_nm, amount=Decimal(r.amount).quantize(Q), currency=r.currency,
                        settled_on=r.settled_on, note=r.note, mine=r.created_by == user.id or r.to_user == user.id,
                        confirmed=r.confirmed, can_confirm=not r.confirmed and r.to_user == user.id)
          for r in db.execute(text("""SELECT s.*, fu.display_name AS from_nm, tu.display_name AS to_nm FROM settlements s
                                       LEFT JOIN users fu ON fu.id = s.from_user LEFT JOIN users tu ON tu.id = s.to_user
                                      WHERE s.household_id = :h ORDER BY s.settled_on DESC, s.created_at DESC LIMIT 20"""), {"h": hid})]
    return Balances(enabled=True, can_change=role == "owner", owes=balances(db, hid), settlements=st)


@router.put("/households/{hid}/splits-enabled", response_model=Balances)
def set_enabled(hid: UUID, body: EnabledIn, user: User, db: DB):
    if my_role(db, hid) != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the household owner can change this")
    db.execute(text("""INSERT INTO household_settings (household_id, splits_enabled, updated_by) VALUES (:h, :e, :u)
                       ON CONFLICT (household_id) DO UPDATE SET splits_enabled = EXCLUDED.splits_enabled,
                           updated_by = EXCLUDED.updated_by, updated_at = now()"""), {"h": hid, "e": body.enabled, "u": user.id})
    audit.log(db, user.id, "settings.splits", "household", f"Splitting and settling up {'on' if body.enabled else 'off'}", household_id=hid)
    return get_balances(hid, user, db)


@router.post("/households/{hid}/settlements", response_model=Balances, status_code=201)
def settle(hid: UUID, body: SettlementIn, user: User, db: DB):
    my_role(db, hid)
    if not _enabled(db, hid):
        raise HTTPException(status.HTTP_409_CONFLICT, "Settling up is off for this household")
    if user.id not in (body.from_user, body.to_user) or body.from_user == body.to_user:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Record a payment you made or received")
    other = body.to_user if body.from_user == user.id else body.from_user
    if not db.execute(text("SELECT 1 FROM memberships WHERE household_id = :h AND user_id = :u"), {"h": hid, "u": other}).first():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "They're not in this household")
    owed = next((o.amount for o in balances(db, hid) if o.from_user == body.from_user and o.to_user == body.to_user
                 and o.currency == body.currency), Decimal(0))
    if body.amount > owed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"That's more than is owed ({owed:.2f} {body.currency})" if owed else "Nothing is owed that way")
    # the receiver's word confirms it; a payer's record waits for the receiver to confirm (no clearing your own debt)
    db.execute(text("""INSERT INTO settlements (household_id, from_user, to_user, amount, currency, settled_on, note, confirmed)
                       VALUES (:h, :f, :t, :a, :c, :d, :n, :ok)"""),
               {"h": hid, "f": body.from_user, "t": body.to_user, "a": body.amount, "c": body.currency,
                "d": body.settled_on or date.today(), "n": body.note, "ok": body.to_user == user.id})
    names = dict(db.execute(text("SELECT id, display_name FROM users WHERE id IN (:f, :t)"), {"f": body.from_user, "t": body.to_user}).all())
    audit.log(db, user.id, "settle.add", "household",
              f"{names.get(body.from_user)} paid {names.get(body.to_user)} {body.amount} {body.currency}", household_id=hid)
    return get_balances(hid, user, db)


@router.post("/settlements/{sid}/confirm", response_model=Balances)
def confirm_settlement(sid: UUID, user: User, db: DB):
    s = db.execute(text("SELECT household_id, amount, currency FROM settlements WHERE id = :s"), {"s": sid}).first()
    if s is None:
        raise not_found("Settlement not found")
    if not db.execute(text("UPDATE settlements SET confirmed = true WHERE id = :s AND to_user = :u"), {"s": sid, "u": user.id}).rowcount:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who received it can confirm it")
    audit.log(db, user.id, "settle.confirm", "household", f"Confirmed receiving {s.amount:.2f} {s.currency}", household_id=s.household_id)
    return get_balances(s.household_id, user, db)


@router.delete("/settlements/{sid}", status_code=204)
def remove_settlement(sid: UUID, user: User, db: DB):
    s = db.execute(text("SELECT household_id, amount, currency FROM settlements WHERE id = :s"), {"s": sid}).first()
    if s is None:
        raise not_found("Settlement not found")
    if not db.execute(text("DELETE FROM settlements WHERE id = :s"), {"s": sid}).rowcount:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the payer can remove a confirmed payment; the receiver can reject a pending one")
    audit.log(db, user.id, "settle.remove", "household", f"Removed a settlement of {s.amount:.2f} {s.currency}", household_id=s.household_id)
