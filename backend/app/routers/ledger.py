"""Statement import into the ledger + transaction lists + dashboard summary (M1)."""
from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from decimal import Decimal
from sqlalchemy import text

from app.config import get_settings
from app.deps import DB, User, not_found
from app.importers.base import ImportError_, assign_fingerprints, check_amount
from app.importers.detect import parse_statement
from app.routers.accounts import require_own_account
from app.routers.households import my_role
from app.schemas import ImportResult, TransactionOut, TransactionUpdate, TransactionUpdated
from app.categorise import rules as cat_rules
from app.categorise.service import apply_rules, correct
from app.categorise.worker import worker
from app.recurring import detect as detect_recurring
from app.routers.recurring import RecurringOut, household_recurring

router = APIRouter(prefix="/api", tags=["ledger"])

_TX_COLS = "t.id, t.account_id, t.posted_on, t.amount, t.currency, t.description, t.merchant, " \
           "t.counterparty, t.category, t.running_balance, t.category_source, t.category_confidence, " \
           "t.needs_review, (a.owner_user_id = app_uid()) AS editable, t.merchant_key"


@router.post("/accounts/{aid}/imports", response_model=ImportResult, status_code=201)
async def import_statement(aid: UUID, user: User, db: DB, file: Annotated[UploadFile, File()]):
    acc = require_own_account(db, user, aid)  # bank import is the account master (§2): owner imports
    limit = get_settings().max_upload_bytes
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large")
    try:
        # parsing can be slow (PDF/OCR): keep it off the event loop so other requests aren't blocked
        parsed = await run_in_threadpool(parse_statement, file.filename or "upload", raw)
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
    apply_rules(db, user.id, batch_id)  # instant offline categories; the LLM refines in the background
    detect_recurring(db, user.id, aid)  # regular payments, next due dates, missed/changed amounts (E06)
    worker.wake()
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
    rows = db.execute(text(f"""SELECT {_TX_COLS} FROM transactions t JOIN accounts a ON a.id = t.account_id
                               WHERE t.account_id = :a
                               ORDER BY t.posted_on DESC, t.created_at DESC LIMIT :l OFFSET :o"""),
                      {"a": aid, "l": limit, "o": offset}).all()
    if not rows and db.execute(text("SELECT 1 FROM accounts WHERE id = :a"), {"a": aid}).first() is None:
        raise not_found("Account not found")
    return [TransactionOut(**r._mapping) for r in rows]


@router.get("/households/{hid}/transactions", response_model=list[TransactionOut])
def household_transactions(hid: UUID, user: User, db: DB,
                           q: Annotated[str | None, Query(max_length=100)] = None,
                           review: bool = False,
                           category: Annotated[str | None, Query(max_length=50)] = None,
                           limit: Annotated[int, Query(ge=1, le=500)] = 100,
                           offset: Annotated[int, Query(ge=0)] = 0):
    """All transactions on accounts shared into this household (include AND hide — attribution never strips access)."""
    my_role(db, hid)
    rows = db.execute(text(f"""
        SELECT {_TX_COLS} FROM transactions t
          JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h
          JOIN accounts a ON a.id = t.account_id
          JOIN memberships ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
         WHERE (CAST(:q AS text) IS NULL OR t.description ILIKE '%' || CAST(:q AS text) || '%'
                OR t.merchant ILIKE '%' || CAST(:q AS text) || '%')
           AND (NOT :review OR t.needs_review)
           AND (CAST(:cat AS text) IS NULL OR t.category = CAST(:cat AS text))
         ORDER BY t.posted_on DESC, t.created_at DESC LIMIT :l OFFSET :o"""),
        {"h": hid, "q": q, "review": review, "cat": category, "l": limit, "o": offset})
    return [TransactionOut(**r._mapping) for r in rows]


class CurrencyTotals(BaseModel):
    currency: str
    spent: Decimal
    received: Decimal
    spent_today: Decimal


class DaySpend(BaseModel):
    currency: str
    day: date
    spent: Decimal


class Summary(BaseModel):
    household_id: UUID
    period_start: date
    period_end: date
    included: list[CurrencyTotals]   # household roll-up: 'include' accounts only (§3.5)
    excluded: list[CurrencyTotals]   # transparency breakdown of 'hide' accounts (§3.5, §17)
    accounts_included: int
    accounts_excluded: int
    recent: list[TransactionOut]
    daily: list[DaySpend] = []       # included accounts only, days with spending (§17 time series, M1 scale)
    needs_review: int = 0            # transactions whose category is uncertain (E05-03)
    upcoming: list[RecurringOut] = []  # regular payments due in the next 14 days (E06-04)
    missed: list[RecurringOut] = []    # expected but never arrived (E06-03)


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
    daily = db.execute(text("""
        SELECT t.currency, t.posted_on AS day, SUM(-t.amount) AS spent
          FROM transactions t
          JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h AND ha.attribution = 'include'
          JOIN accounts a ON a.id = t.account_id
          JOIN memberships ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
         WHERE t.posted_on >= :s AND t.posted_on < :e AND t.amount < 0
         GROUP BY t.currency, t.posted_on ORDER BY t.posted_on"""), {"h": hid, "s": start, "e": end}).all()
    recent = household_transactions(hid, user, db, None, False, None, 12, 0)
    rec = household_recurring(db, hid)
    review_count = db.execute(text("""
        SELECT count(*) FROM transactions t
          JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h
          JOIN accounts a ON a.id = t.account_id
          JOIN memberships ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
         WHERE t.needs_review"""), {"h": hid}).scalar_one()
    pick = lambda attr: [CurrencyTotals(currency=r.currency, spent=r.spent, received=r.received,
                                        spent_today=r.spent_today) for r in rows if r.attribution == attr]
    return Summary(household_id=hid, period_start=start, period_end=end, included=pick("include"),
                   excluded=pick("hide"), accounts_included=counts.get("include", 0),
                   accounts_excluded=counts.get("hide", 0), recent=recent,
                   daily=[DaySpend(**r._mapping) for r in daily], needs_review=review_count,
                   upcoming=[r for r in rec if r.status == "active" and r.next_date <= today + timedelta(days=14)][:6],
                   missed=[r for r in rec if r.status == "missed"])


@router.get("/categories", response_model=list[str])
def categories(user: User):
    return cat_rules.CATEGORIES


@router.patch("/transactions/{tid}", response_model=TransactionUpdated)
def update_transaction(tid: UUID, body: TransactionUpdate, user: User, db: DB):
    """Set a category by hand (account owner only). remember=True teaches the app this merchant."""
    if body.category not in cat_rules.CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown category")
    row = db.execute(text("""SELECT a.owner_user_id FROM transactions t JOIN accounts a ON a.id = t.account_id
                              WHERE t.id = :t"""), {"t": tid}).first()
    if row is None:
        raise not_found("Transaction not found")
    if row.owner_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the account owner can change this")
    n = correct(db, user.id, tid, body.category, (body.merchant or "").strip() or None, body.remember)
    tx = db.execute(text(f"SELECT {_TX_COLS} FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = :t"),
                    {"t": tid}).one()
    return TransactionUpdated(transaction=TransactionOut(**tx._mapping), also_updated=n)
