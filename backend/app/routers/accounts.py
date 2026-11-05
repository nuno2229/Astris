"""Accounts are owned by a user (§3.5) and shared into households with an attribution.
Attribution only changes household rollups — never access."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.deps import DB, User, not_found
from app.routers.households import my_role
from app.schemas import AccountIn, AccountOut, AccountUpdate, HouseholdAccountOut, ShareIn

router = APIRouter(prefix="/api", tags=["accounts"])

# latest known running balance (from statement imports); NULL if the statement had none
_ACCOUNT_SELECT = """
SELECT a.id, a.name, a.type, a.currency, a.institution, a.owner_user_id, u.display_name AS owner_name,
       (a.owner_user_id = :u) AS is_mine,
       (SELECT t.running_balance FROM transactions t
         WHERE t.account_id = a.id AND t.running_balance IS NOT NULL
         ORDER BY t.posted_on DESC, t.created_at DESC LIMIT 1) AS balance
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
