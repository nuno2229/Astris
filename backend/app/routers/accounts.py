"""Accounts are owned by a user (§3.5) and shared into households with an attribution.
Attribution only changes household rollups — never access."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.deps import DB, User, not_found
from app.routers.households import my_role
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas import AccountIn, AccountOut, AccountUpdate, HouseholdAccountOut, ShareIn

router = APIRouter(prefix="/api", tags=["accounts"])

# Bank/card: latest running balance from statements (NULL if none). Cash wallet (E08): starting balance plus
# every entry since the starting date.
_ACCOUNT_SELECT = """
SELECT a.id, a.name, a.type, a.currency, a.institution, a.owner_user_id, u.display_name AS owner_name,
       (a.owner_user_id = :u) AS is_mine, a.opening_balance, a.opening_date,
       CASE WHEN a.type = 'cash' THEN
            a.opening_balance + COALESCE((SELECT sum(t.amount) FROM transactions t WHERE t.account_id = a.id
                                           AND (a.opening_date IS NULL OR t.posted_on >= a.opening_date)), 0)
       ELSE (SELECT t.running_balance FROM transactions t
              WHERE t.account_id = a.id AND t.running_balance IS NOT NULL
              ORDER BY t.posted_on DESC, t.created_at DESC LIMIT 1) END AS balance
  FROM accounts a JOIN users u ON u.id = a.owner_user_id
"""


def get_account(db: DB, user: User, aid: UUID) -> AccountOut:
    row = db.execute(text(_ACCOUNT_SELECT + " WHERE a.id = :a"), {"u": user.id, "a": aid}).first()
    if row is None:
        raise not_found("Account not found")
    return AccountOut(**row._mapping)


def require_own_account(db: DB, user: User, aid: UUID) -> AccountOut:
    acc = get_account(db, user, aid)
    if not acc.is_mine:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the account owner can do this")
    return acc


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(body: AccountIn, user: User, db: DB):
    aid = db.execute(text("""INSERT INTO accounts (owner_user_id, name, type, currency, institution)
                             VALUES (:u, :n, :t, :c, :i) RETURNING id"""),
                     {"u": user.id, "n": body.name.strip(), "t": body.type, "c": body.currency,
                      "i": body.institution}).scalar_one()
    return get_account(db, user, aid)


@router.get("/accounts", response_model=list[AccountOut])
def list_my_accounts(user: User, db: DB):
    """Per-account view: my own accounts, regardless of household attribution (§3.5)."""
    rows = db.execute(text(_ACCOUNT_SELECT + " WHERE a.owner_user_id = :u ORDER BY a.created_at"), {"u": user.id})
    return [AccountOut(**r._mapping) for r in rows]


@router.get("/accounts/{aid}", response_model=AccountOut)
def read_account(aid: UUID, user: User, db: DB):
    return get_account(db, user, aid)


@router.patch("/accounts/{aid}", response_model=AccountOut)
def update_account(aid: UUID, body: AccountUpdate, user: User, db: DB):
    require_own_account(db, user, aid)
    fields = body.model_dump(exclude_unset=True)
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)  # keys from schema only
        db.execute(text(f"UPDATE accounts SET {sets} WHERE id = :a"), {**fields, "a": aid})
    return get_account(db, user, aid)


@router.delete("/accounts/{aid}", status_code=204)
def delete_account(aid: UUID, user: User, db: DB):
    require_own_account(db, user, aid)
    db.execute(text("DELETE FROM accounts WHERE id = :a"), {"a": aid})


@router.get("/households/{hid}/accounts", response_model=list[HouseholdAccountOut])
def household_accounts(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    rows = db.execute(text(f"""
        SELECT x.*, ha.attribution FROM ({_ACCOUNT_SELECT}) x
          JOIN household_accounts ha ON ha.account_id = x.id AND ha.household_id = :h
          JOIN memberships ow ON ow.household_id = :h AND ow.user_id = x.owner_user_id
         ORDER BY x.owner_name, x.name"""), {"u": user.id, "h": hid})
    return [HouseholdAccountOut(**r._mapping) for r in rows]


@router.put("/households/{hid}/accounts/{aid}", response_model=HouseholdAccountOut)
def share_account(hid: UUID, aid: UUID, body: ShareIn, user: User, db: DB):
    """Share my account into a household (or change its attribution). Attribution must be chosen (G-11)."""
    my_role(db, hid)
    acc = require_own_account(db, user, aid)
    db.execute(text("""INSERT INTO household_accounts (household_id, account_id, attribution)
                       VALUES (:h, :a, :at)
                       ON CONFLICT (household_id, account_id) DO UPDATE SET attribution = EXCLUDED.attribution"""),
               {"h": hid, "a": aid, "at": body.attribution})
    return HouseholdAccountOut(**acc.model_dump(), attribution=body.attribution)


@router.delete("/households/{hid}/accounts/{aid}", status_code=204)
def unshare_account(hid: UUID, aid: UUID, user: User, db: DB):
    my_role(db, hid)
    require_own_account(db, user, aid)
    n = db.execute(text("DELETE FROM household_accounts WHERE household_id = :h AND account_id = :a"),
                   {"h": hid, "a": aid}).rowcount
    if not n:
        raise not_found("Account is not shared into this household")


class OpeningIn(BaseModel):
    balance: Decimal = Field(gt=Decimal("-1e14"), lt=Decimal("1e14"), decimal_places=2)
    date: date


class CashCountIn(BaseModel):
    counted: Decimal = Field(ge=0, lt=Decimal("1e14"), decimal_places=2)
    date: date


class CashCountOut(BaseModel):
    expected: Decimal
    counted: Decimal
    adjustment: Decimal      # counted - expected; recorded as an 'adjustment' entry when not zero
    account: AccountOut


@router.put("/accounts/{aid}/opening", response_model=AccountOut)
def set_opening(aid: UUID, body: OpeningIn, user: User, db: DB):
    """Starting balance (E08-02). For a cash wallet, the balance is this plus every entry since the date."""
    require_own_account(db, user, aid)
    db.execute(text("UPDATE accounts SET opening_balance = :b, opening_date = :d WHERE id = :a"),
               {"b": body.balance, "d": body.date, "a": aid})
    return get_account(db, user, aid)


@router.post("/accounts/{aid}/cash-count", response_model=CashCountOut)
def cash_count(aid: UUID, body: CashCountIn, user: User, db: DB):
    """Count the cash in your wallet (closing balance, E08-02). Any gap becomes an adjustment entry so the
    wallet matches reality; spending you forgot to log shows up here."""
    acc = require_own_account(db, user, aid)
    if acc.type != "cash":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Cash counts are for cash wallets")
    expected = db.execute(text("""
        SELECT a.opening_balance + COALESCE((SELECT sum(t.amount) FROM transactions t WHERE t.account_id = a.id
               AND (a.opening_date IS NULL OR t.posted_on >= a.opening_date) AND t.posted_on <= :d), 0)
          FROM accounts a WHERE a.id = :a"""), {"a": aid, "d": body.date}).scalar_one()
    adj = (body.counted - Decimal(expected)).quantize(Decimal("0.01"))
    if adj != 0:
        import uuid
        db.execute(text("""
            INSERT INTO transactions (account_id, posted_on, amount, currency, description, category, category_source,
                                      category_confidence, needs_review, ai_status, fingerprint, source, created_by)
            VALUES (:a, :d, :amt, :c, :desc, 'Cash', 'user', 1, false, 'done', :fp, 'adjustment', :u)"""),
            {"a": aid, "d": body.date, "amt": adj, "c": acc.currency,
             "desc": "Cash count: unrecorded spending" if adj < 0 else "Cash count: extra cash found",
             "fp": f"adjust:{uuid.uuid4()}", "u": user.id})
    return CashCountOut(expected=expected, counted=body.counted, adjustment=adj, account=get_account(db, user, aid))
