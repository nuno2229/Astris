"""Statement import into the ledger + transaction lists + dashboard summary (M1)."""
from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
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
from app.schemas import ImportResult, ManualTxIn, MoveToWalletIn, TransactionOut, TransactionUpdate, TransactionUpdated
from app.categorise import rules as cat_rules
from app.categorise.service import apply_rules, correct
from app import audit
from app.categorise.worker import worker
from app.recurring import detect as detect_recurring
from app.routers.recurring import RecurringOut, household_recurring
from app.routers.subscriptions import household_subscriptions

router = APIRouter(prefix="/api", tags=["ledger"])

_TX_COLS = "t.id, t.account_id, t.posted_on, t.amount, t.currency, t.description, t.merchant, " \
           "t.counterparty, t.category, t.running_balance, t.category_source, t.category_confidence, " \
           "t.needs_review, (a.owner_user_id = app_uid()) AS editable, t.merchant_key, t.source, " \
           "t.original_amount, t.original_currency, t.transfer_group, t.refund_of"


def parse_checked(filename: str, raw: bytes, options: dict | None = None):
    """Parse + the shared validation gate every importer goes through. Raises ImportError_."""
    parsed = parse_statement(filename, raw, options)
    for t in parsed.transactions:
        t.amount = check_amount(t.amount)
        t.running_balance = check_amount(t.running_balance, "balance")
        t.original_amount = check_amount(t.original_amount, "foreign amount")
    return parsed


def save_parsed(db: DB, user_id: UUID, acc, filename: str, parsed, run_id: UUID | None = None) -> tuple[UUID, int, int]:
    """Insert a parsed statement as one batch; duplicates are skipped by fingerprint. Returns (batch, total, new)."""
    if parsed.currency and parsed.currency != acc.currency:
        parsed.warnings.append(
            f"Statement currency {parsed.currency} differs from account currency {acc.currency}; "
            f"amounts were stored as {acc.currency}.")
    batch_id = db.execute(text("""INSERT INTO import_batches (account_id, imported_by, filename, format, run_id)
                                  VALUES (:a, :u, :f, :fmt, :r) RETURNING id"""),
                          {"a": acc.id, "u": user_id, "f": filename[:255], "fmt": parsed.format, "r": run_id}).scalar_one()
    rows = [{
        "a": acc.id, "b": batch_id, "d": t.posted_on, "amt": t.amount, "c": acc.currency,
        "desc": t.description[:500], "cp": (t.counterparty or None) and t.counterparty[:200],
        "bal": t.running_balance, "fp": fp,
        "oa": t.original_amount if t.original_currency and t.original_currency != acc.currency else None,
        "oc": t.original_currency if t.original_currency and t.original_currency != acc.currency else None,
    } for t, fp in zip(parsed.transactions, assign_fingerprints(parsed.transactions))]
    # keep statement order: insert oldest first (newest-first files are reversed) with a per-row timestamp, so
    # "latest balance" on a day with several transactions is the statement's last line, not a random one
    txs = parsed.transactions
    if txs and txs[0].posted_on > txs[-1].posted_on:
        rows.reverse()
    new = 0
    for r in rows:
        new += db.execute(text("""
            INSERT INTO transactions (account_id, import_batch_id, posted_on, amount, currency, description,
                                      counterparty, running_balance, fingerprint, original_amount, original_currency, created_at)
            VALUES (:a, :b, :d, :amt, :c, :desc, :cp, :bal, :fp, :oa, :oc, clock_timestamp())
            ON CONFLICT (account_id, fingerprint) DO NOTHING"""), r).rowcount
    total = len(rows)
    db.execute(text("UPDATE import_batches SET rows_total = :t, rows_new = :n, rows_duplicate = :d WHERE id = :b"),
               {"t": total, "n": new, "d": total - new, "b": batch_id})
    return batch_id, total, new


def after_import(db: DB, user_id: UUID, account_ids) -> None:
    apply_rules(db, user_id)              # instant offline categories; the LLM refines in the background
    from app.routers.goals import apply_rules as goal_rules
    goal_rules(db, user_id)               # transfers you said count toward a savings goal (E11)
    from app.transfers import auto_link
    auto_link(db, user_id)                # current -> savings, card repayments: not spending (P-1)
    from app.refunds import auto_link as refunds
    refunds(db, user_id)                  # money back from a shop: netted off that purchase's category (P-2)
    for aid in set(account_ids):
        detect_recurring(db, user_id, aid)  # regular payments, next due dates, missed/changed amounts (E06)
    worker.wake()


@router.post("/accounts/{aid}/imports", response_model=ImportResult, status_code=201)
async def import_statement(aid: UUID, user: User, db: DB, file: Annotated[UploadFile, File()],
                           keep_original: Annotated[bool, Form()] = False):
    acc = require_own_account(db, user, aid)  # bank import is the account master (§2): owner imports
    limit = get_settings().max_upload_bytes
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large")
    try:
        # parsing can be slow (PDF/OCR): keep it off the event loop so other requests aren't blocked
        from app.importers import profiles
        parsed = await run_in_threadpool(parse_checked, file.filename or "upload", raw, profiles.find(db, file.filename or "upload", raw))
    except ImportError_ as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    batch_id, total, new = save_parsed(db, user.id, acc, file.filename or "upload", parsed)
    if keep_original:   # opt-in (PO 2026-11-06); statements are visible to their owner only
        from app.documents import keep
        keep(db, user.id, "statement", file.filename or "statement", raw, account_id=aid, import_batch_id=batch_id)
    after_import(db, user.id, [aid])
    audit.log(db, user.id, "import.batch", "import", f"Imported {file.filename or 'a statement'}: {new} new",
              entity_id=batch_id, account_id=aid, after={"batch_id": batch_id, "rows_new": new})
    return ImportResult(batch_id=batch_id, format=parsed.format, rows_total=total, rows_new=new,
                        rows_duplicate=total - new, warnings=parsed.warnings)


@router.delete("/imports/{bid}", status_code=204)
def undo_import(bid: UUID, user: User, db: DB):
    """Undo a whole import (removes only the rows that import added)."""
    b = db.execute(text("SELECT account_id, filename FROM import_batches WHERE id = :b"), {"b": bid}).first()
    n = db.execute(text("DELETE FROM import_batches WHERE id = :b"), {"b": bid}).rowcount
    if not n:
        raise not_found("Import not found")
    audit.log(db, user.id, "import.undo", "import", f"Removed import {b.filename}", entity_id=bid, account_id=b.account_id)


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
          JOIN household_people ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
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


class Converted(BaseModel):
    currency: str                 # the viewer's home currency (E08-07)
    spent: Decimal
    received: Decimal
    spent_today: Decimal
    missing: list[str]            # included currencies with no exchange rate (shown separately, never guessed)
    daily: list[DaySpend]


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
    converted: Converted | None = None
    subscription_alerts: list[dict] = []
    goals: list[dict] = []          # active savings goals, for the Home widget (E11)
    budgets: list[dict] = []        # budgets you can see, most urgent first (E12)
    stock: dict = {}                # pantry: low / expiring soon / expired (E14)  # trial ending/converted, charged after cancel, renewing soon (E07-04)


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
               COALESCE(SUM(-t.amount) FILTER (WHERE (t.amount < 0 OR t.refund_of IS NOT NULL)), 0) AS spent,
               COALESCE(SUM(t.amount)  FILTER (WHERE (t.amount > 0 AND t.refund_of IS NULL)), 0) AS received,
               COALESCE(SUM(-t.amount) FILTER (WHERE (t.amount < 0 OR t.refund_of IS NOT NULL) AND t.posted_on = :today), 0) AS spent_today
          FROM transactions t
          JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h
          JOIN accounts a ON a.id = t.account_id
          JOIN household_people ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
         WHERE t.posted_on >= :s AND t.posted_on < :e AND t.transfer_group IS NULL
         GROUP BY ha.attribution, t.currency ORDER BY t.currency"""),
        {"h": hid, "s": start, "e": end, "today": today}).all()
    counts = dict(db.execute(text("""
        SELECT ha.attribution, count(*) FROM household_accounts ha
          JOIN accounts a ON a.id = ha.account_id
          JOIN household_people ow ON ow.household_id = ha.household_id AND ow.user_id = a.owner_user_id
         WHERE ha.household_id = :h GROUP BY ha.attribution"""), {"h": hid}).all())
    daily = db.execute(text("""
        SELECT t.currency, t.posted_on AS day, SUM(-t.amount) AS spent
          FROM transactions t
          JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h AND ha.attribution = 'include'
          JOIN accounts a ON a.id = t.account_id
          JOIN household_people ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
         WHERE t.posted_on >= :s AND t.posted_on < :e AND (t.amount < 0 OR t.refund_of IS NOT NULL) AND t.transfer_group IS NULL
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
    home = db.execute(text("SELECT home_currency FROM users WHERE id = :u"), {"u": user.id}).scalar_one()
    from app.routers.fx import rate_table, convert
    rates = rate_table(db, hid)
    inc_rows = [r for r in rows if r.attribution == "include"]
    missing = sorted({r.currency for r in inc_rows if convert(Decimal(1), r.currency, home, rates) is None})
    conv = lambda v, c: convert(Decimal(v), c, home, rates) or Decimal(0)
    by_day: dict = {}
    for d in daily:
        if d.currency not in missing:
            by_day[d.day] = by_day.get(d.day, Decimal(0)) + conv(d.spent, d.currency)
    converted = Converted(
        currency=home, missing=missing,
        spent=sum((conv(r.spent, r.currency) for r in inc_rows if r.currency not in missing), Decimal(0)).quantize(Decimal("0.01")),
        received=sum((conv(r.received, r.currency) for r in inc_rows if r.currency not in missing), Decimal(0)).quantize(Decimal("0.01")),
        spent_today=sum((conv(r.spent_today, r.currency) for r in inc_rows if r.currency not in missing), Decimal(0)).quantize(Decimal("0.01")),
        daily=[DaySpend(currency=home, day=k, spent=v.quantize(Decimal("0.01"))) for k, v in sorted(by_day.items())])
    from app.routers.goals import household_goals
    goal_cards = [g.model_dump(include={"id", "name", "target", "currency", "saved", "progress", "achieved", "deadline",
                                        "on_track", "projected_date"}) for g in household_goals(db, hid, user.id)]
    from app.routers.budgets import visible_budgets
    budget_cards = [b.model_dump(include={"id", "label", "scope", "period", "amount", "currency", "spent", "committed",
                                          "used", "level", "message"}) for b in visible_budgets(db, hid, user.id)]
    from app.routers.inventory import household_stock
    pantry = household_stock(db, hid)
    stock = {"products": len(pantry), "low": [p.name for p in pantry if p.low][:8],
             "expiring": [{"name": p.name, "days_left": min(l.days_left for l in p.lots if l.state == "soon")}
                          for p in pantry if any(l.state == "soon" for l in p.lots)][:8],
             "expired": sum(1 for p in pantry for l in p.lots if l.state == "expired")}
    return Summary(household_id=hid, converted=converted, goals=goal_cards, budgets=budget_cards, stock=stock, period_start=start, period_end=end, included=pick("include"),
                   excluded=pick("hide"), accounts_included=counts.get("include", 0),
                   accounts_excluded=counts.get("hide", 0), recent=recent,
                   daily=[DaySpend(**r._mapping) for r in daily], needs_review=review_count,
                   upcoming=[r for r in rec if r.status == "active" and r.next_date <= today + timedelta(days=14)][:6],
                   missed=[r for r in rec if r.status == "missed"],
                   subscription_alerts=[{"subscription_id": str(sub.id), "provider": sub.provider, **a.model_dump()}
                                        for sub in household_subscriptions(db, hid) for a in sub.alerts
                                        if a.kind != "renewing" or (sub.next_renewal and (sub.next_renewal - today).days <= 3)])


@router.get("/categories", response_model=list[str])
def categories(user: User, db: DB, household_id: UUID | None = None):
    """Built-in categories plus the household's own (G-13); without a household, those of all your households."""
    from app.categorise import custom
    if household_id:
        my_role(db, household_id)
        return custom.for_household(db, household_id)
    return custom.for_user(db)


@router.patch("/transactions/{tid}", response_model=TransactionUpdated)
def update_transaction(tid: UUID, body: TransactionUpdate, user: User, db: DB):
    """Set a category by hand (account owner only). remember=True teaches the app this merchant."""
    from app.categorise import custom
    if body.category not in custom.for_user(db):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown category")
    row = db.execute(text("""SELECT a.owner_user_id FROM transactions t JOIN accounts a ON a.id = t.account_id
                              WHERE t.id = :t"""), {"t": tid}).first()
    if row is None:
        raise not_found("Transaction not found")
    if row.owner_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the account owner can change this")
    before = db.execute(text("""SELECT t.category, t.merchant, t.category_source, t.category_confidence, t.needs_review,
                                       t.merchant_key, t.description, t.account_id FROM transactions t WHERE t.id = :t"""),
                        {"t": tid}).one()
    prev_rule = db.execute(text("SELECT category, merchant FROM merchant_rules WHERE user_id = :u AND merchant_key = :k"),
                           {"u": user.id, "k": before.merchant_key}).first() if before.merchant_key else None
    n = correct(db, user.id, tid, body.category, (body.merchant or "").strip() or None, body.remember)
    audit.log(db, user.id, "tx.category", "transaction",
              f"{before.merchant or before.description}: {before.category or 'no category'} → {body.category}"
              + (f" (+{n} more)" if n else ""),
              entity_id=tid, account_id=before.account_id, reason=body.reason,
              before={"category": before.category, "merchant": before.merchant, "category_source": before.category_source,
                      "category_confidence": before.category_confidence, "needs_review": before.needs_review,
                      "rule": dict(prev_rule._mapping) if prev_rule else None},
              after={"category": body.category, "rule_key": before.merchant_key if body.remember else None})
    tx = db.execute(text(f"SELECT {_TX_COLS} FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = :t"),
                    {"t": tid}).one()
    return TransactionUpdated(transaction=TransactionOut(**tx._mapping), also_updated=n)


def _own_tx(db: DB, user, tid: UUID):
    row = db.execute(text("""SELECT t.*, a.owner_user_id, a.type AS account_type, a.currency AS account_currency,
                                    a.name AS account_name
                               FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = :t"""),
                     {"t": tid}).first()
    if row is None:
        raise not_found("Transaction not found")
    if row.owner_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the account owner can change this")
    return row


def _tx_out(db: DB, tid: UUID) -> TransactionOut:
    return TransactionOut(**db.execute(text(f"SELECT {_TX_COLS} FROM transactions t JOIN accounts a ON a.id = t.account_id "
                                            "WHERE t.id = :t"), {"t": tid}).one()._mapping)


@router.post("/accounts/{aid}/transactions", response_model=TransactionOut, status_code=201)
def add_manual(aid: UUID, body: ManualTxIn, user: User, db: DB):
    """Manual entry (cash spend, top-up, anything a statement won't show). Owner only (E08-02)."""
    import uuid as _uuid
    acc = require_own_account(db, user, aid)
    from app.categorise import custom
    if body.category is not None and body.category not in custom.for_user(db):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown category")
    if (body.original_amount is None) != (body.original_currency is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Give both the foreign amount and its currency")
    tid = db.execute(text("""
        INSERT INTO transactions (account_id, posted_on, amount, currency, description, category, category_source,
                                  category_confidence, needs_review, ai_status, fingerprint, source, created_by,
                                  original_amount, original_currency)
        VALUES (:a, :d, :amt, :c, :desc, :cat, CAST(:src AS category_source), :conf, false, :st, :fp, 'manual', :u, :oa, :oc)
        RETURNING id"""),
        {"a": aid, "d": body.posted_on, "amt": body.amount, "c": acc.currency, "desc": body.description.strip(),
         "cat": body.category, "src": "user" if body.category else None, "conf": 1 if body.category else None,
         "st": "done" if body.category else "pending", "fp": f"manual:{_uuid.uuid4()}", "u": user.id,
         "oa": body.original_amount, "oc": body.original_currency}).scalar_one()
    if not body.category:
        apply_rules(db, user.id)
    audit.log(db, user.id, "tx.create", "transaction", f"Added {body.description.strip()} ({body.amount} {acc.currency})",
              entity_id=tid, account_id=aid, after=audit.tx_row(db, tid))
    return _tx_out(db, tid)


@router.delete("/transactions/{tid}", status_code=204)
def delete_manual(tid: UUID, user: User, db: DB, reason: Annotated[str | None, Query(max_length=300)] = None):
    """Only entries you typed in (or cash-count adjustments) can be deleted; statement lines are the record (§2)."""
    row = _own_tx(db, user, tid)
    if row.source not in ("manual", "adjustment"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Imported statement lines can't be deleted. Undo the import instead.")
    snapshot = audit.tx_row(db, tid)
    db.execute(text("DELETE FROM transactions WHERE id = :t"), {"t": tid})
    audit.log(db, user.id, "tx.delete", "transaction", f"Deleted {row.description} ({row.amount} {row.currency})",
              entity_id=tid, account_id=row.account_id, before=snapshot, reason=reason)


@router.post("/transactions/{tid}/move-to-wallet", response_model=TransactionOut)
def move_to_wallet(tid: UUID, body: MoveToWalletIn, user: User, db: DB):
    """Reconcile an ATM withdrawal into your cash wallet (E08-03). Both legs become one transfer, so the cash
    isn't counted as spending twice (once at the ATM, again when spent)."""
    import uuid as _uuid
    tx = _own_tx(db, user, tid)
    if tx.amount >= 0 or tx.account_type == "cash":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick a withdrawal from a bank or card account")
    if tx.transfer_group:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already moved into a wallet")
    wallet = db.execute(text("SELECT id, currency, type, owner_user_id FROM accounts WHERE id = :w"), {"w": body.wallet_id}).first()
    if wallet is None or wallet.owner_user_id != user.id or wallet.type != "cash":
        raise not_found("Cash wallet not found")
    if wallet.currency == tx.account_currency:
        amount = -Decimal(tx.amount)
    elif tx.original_currency == wallet.currency and tx.original_amount is not None:
        amount = abs(Decimal(tx.original_amount))       # e.g. €200 from an ATM abroad, charged in £
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"This withdrawal isn't in {wallet.currency}. Add the cash to the wallet by hand.")
    group = _uuid.uuid4()
    db.execute(text("UPDATE transactions SET transfer_group = :g, category = 'Cash', category_source = 'user', "
                    "category_confidence = 1, needs_review = false WHERE id = :t"), {"g": group, "t": tid})
    db.execute(text("""
        INSERT INTO transactions (account_id, posted_on, amount, currency, description, category, category_source,
                                  category_confidence, needs_review, ai_status, fingerprint, source, created_by, transfer_group)
        VALUES (:w, :d, :amt, :c, :desc, 'Cash', 'user', 1, false, 'done', :fp, 'transfer', :u, :g)"""),
        {"w": wallet.id, "d": tx.posted_on, "amt": amount, "c": wallet.currency,
         "desc": f"Cash from {tx.account_name}", "fp": f"transfer:{group}", "u": user.id, "g": group})
    audit.log(db, user.id, "tx.move_to_wallet", "transaction", f"Moved {abs(tx.amount)} {tx.account_currency} cash into a wallet",
              entity_id=tid, account_id=tx.account_id, after={"transfer_group": group, "wallet_id": wallet.id})
    return _tx_out(db, tid)
