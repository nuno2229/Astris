"""Statement import into the ledger + transaction lists + dashboard summary (M1)."""
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from decimal import Decimal
from sqlalchemy import text

from app.config import get_settings
from app.deps import DB, User, not_found
from app.importers.base import ImportError_, assign_fingerprints, check_amount
from app.importers.detect import parse_statement
from app.routers.accounts import require_own_account
from app.routers.households import my_role
from app.schemas import ImportResult, TransactionOut

router = APIRouter(prefix="/api", tags=["ledger"])

_TX_COLS = "t.id, t.account_id, t.posted_on, t.amount, t.currency, t.description, t.merchant, " \
           "t.counterparty, t.category, t.running_balance"


@router.post("/accounts/{aid}/imports", response_model=ImportResult, status_code=201)
async def import_statement(aid: UUID, user: User, db: DB, file: Annotated[UploadFile, File()]):
    acc = require_own_account(db, user, aid)  # bank import is the account master (§2): owner imports
    limit = get_settings().max_upload_bytes
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large")
    try:
        parsed = parse_statement(file.filename or "upload", raw)
        for t in parsed.transactions:  # every parser goes through the same gate
            t.amount = check_amount(t.amount)
            t.running_balance = check_amount(t.running_balance, "balance")
    except ImportError_ as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    if parsed.currency and parsed.currency != acc.currency:
        parsed.warnings.append(
            f"Statement currency {parsed.currency} differs from account currency {acc.currency}; "
            f"amounts were stored as {acc.currency}. Multi-currency support is E08.")

    batch_id = db.execute(text("""INSERT INTO import_batches (account_id, imported_by, filename, format)
                                  VALUES (:a, :u, :f, :fmt) RETURNING id"""),
                          {"a": aid, "u": user.id, "f": (file.filename or "upload")[:255],
                           "fmt": parsed.format}).scalar_one()
    rows = [{
        "a": aid, "b": batch_id, "d": t.posted_on, "amt": t.amount, "c": acc.currency,
        "desc": t.description[:500], "cp": (t.counterparty or None) and t.counterparty[:200],
        "bal": t.running_balance, "fp": fp,
    } for t, fp in zip(parsed.transactions, assign_fingerprints(parsed.transactions))]
    new = 0
    for r in rows:
        new += db.execute(text("""
            INSERT INTO transactions (account_id, import_batch_id, posted_on, amount, currency, description,
                                      counterparty, running_balance, fingerprint)
            VALUES (:a, :b, :d, :amt, :c, :desc, :cp, :bal, :fp)
            ON CONFLICT (account_id, fingerprint) DO NOTHING"""), r).rowcount
    total = len(rows)
    db.execute(text("UPDATE import_batches SET rows_total = :t, rows_new = :n, rows_duplicate = :d WHERE id = :b"),
               {"t": total, "n": new, "d": total - new, "b": batch_id})
    return ImportResult(batch_id=batch_id, format=parsed.format, rows_total=total, rows_new=new,
                        rows_duplicate=total - new, warnings=parsed.warnings)


@router.delete("/imports/{bid}", status_code=204)
def undo_import(bid: UUID, user: User, db: DB):
    """Undo a whole import (removes only the rows that import added)."""
    n = db.execute(text("DELETE FROM import_batches WHERE id = :b"), {"b": bid}).rowcount
    if not n:
        raise not_found("Import not found")


@router.get("/accounts/{aid}/transactions", response_model=list[TransactionOut])
def account_transactions(aid: UUID, user: User, db: DB,
                         limit: Annotated[int, Query(ge=1, le=500)] = 100,
                         offset: Annotated[int, Query(ge=0)] = 0):
    rows = db.execute(text(f"""SELECT {_TX_COLS} FROM transactions t WHERE t.account_id = :a
                               ORDER BY t.posted_on DESC, t.created_at DESC LIMIT :l OFFSET :o"""),
                      {"a": aid, "l": limit, "o": offset}).all()
    if not rows and db.execute(text("SELECT 1 FROM accounts WHERE id = :a"), {"a": aid}).first() is None:
        raise not_found("Account not found")
    return [TransactionOut(**r._mapping) for r in rows]


@router.get("/households/{hid}/transactions", response_model=list[TransactionOut])
def household_transactions(hid: UUID, user: User, db: DB,
                           q: Annotated[str | None, Query(max_length=100)] = None,
                           limit: Annotated[int, Query(ge=1, le=500)] = 100,
                           offset: Annotated[int, Query(ge=0)] = 0):
    """All transactions on accounts shared into this household (include AND hide — attribution never strips access)."""
    my_role(db, hid)
    rows = db.execute(text(f"""
        SELECT {_TX_COLS} FROM transactions t
          JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h
          JOIN accounts a ON a.id = t.account_id
          JOIN memberships ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
         WHERE (CAST(:q AS text) IS NULL OR t.description ILIKE '%' || CAST(:q AS text) || '%')
         ORDER BY t.posted_on DESC, t.created_at DESC LIMIT :l OFFSET :o"""),
        {"h": hid, "q": q, "l": limit, "o": offset})
    return [TransactionOut(**r._mapping) for r in rows]


class CurrencyTotals(BaseModel):
    currency: str
    spent: Decimal
    received: Decimal
    spent_today: Decimal


class Summary(BaseModel):
    household_id: UUID
    period_start: date
    period_end: date
    included: list[CurrencyTotals]   # household roll-up: 'include' accounts only (§3.5)
    excluded: list[CurrencyTotals]   # transparency breakdown of 'hide' accounts (§3.5, §17)
    accounts_included: int
    accounts_excluded: int
    recent: list[TransactionOut]


@router.get("/households/{hid}/summary", response_model=Summary)
def household_summary(hid: UUID, user: User, db: DB,
                      month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None):
    my_role(db, hid)
    today = date.today()
    start = date.fromisoformat(month + "-01") if month else today.replace(day=1)
    end = (start.replace(year=start.year + 1, month=1) if start.month == 12
           else start.replace(month=start.month + 1))
    rows = db.execute(text("""
        SELECT ha.attribution, t.currency,
               COALESCE(SUM(-t.amount) FILTER (WHERE t.amount < 0), 0) AS spent,
               COALESCE(SUM(t.amount)  FILTER (WHERE t.amount > 0), 0) AS received,
               COALESCE(SUM(-t.amount) FILTER (WHERE t.amount < 0 AND t.posted_on = :today), 0) AS spent_today
          FROM transactions t
          JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h
          JOIN accounts a ON a.id = t.account_id
          JOIN memberships ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
         WHERE t.posted_on >= :s AND t.posted_on < :e
         GROUP BY ha.attribution, t.currency ORDER BY t.currency"""),
        {"h": hid, "s": start, "e": end, "today": today}).all()
    counts = dict(db.execute(text("""
        SELECT ha.attribution, count(*) FROM household_accounts ha
          JOIN accounts a ON a.id = ha.account_id
          JOIN memberships ow ON ow.household_id = ha.household_id AND ow.user_id = a.owner_user_id
         WHERE ha.household_id = :h GROUP BY ha.attribution"""), {"h": hid}).all())
    recent = household_transactions(hid, user, db, None, 8, 0)
    pick = lambda attr: [CurrencyTotals(currency=r.currency, spent=r.spent, received=r.received,
                                        spent_today=r.spent_today) for r in rows if r.attribution == attr]
    return Summary(household_id=hid, period_start=start, period_end=end, included=pick("include"),
                   excluded=pick("hide"), accounts_included=counts.get("include", 0),
                   accounts_excluded=counts.get("hide", 0), recent=recent)
