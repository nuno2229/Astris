"""Bulk import wizard (E09): stage several files, dry-run each (format, duplicates, balance check, column
mapping, sample), let the user adjust, then commit all as one run that can be rolled back as a whole."""
import json
from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import audit
from app.config import get_settings
from app.deps import DB, User, not_found
from app.importers.base import ImportError_, assign_fingerprints
from app.recurring import detect as detect_recurring
from app.routers.accounts import require_own_account
from app.routers.ledger import after_import, parse_checked, save_parsed

router = APIRouter(prefix="/api", tags=["import-wizard"])
MAX_FILES = 20
MAX_REQUEST_BYTES = 50 * 1024 * 1024     # all files in one upload
MAX_USER_PREVIEWS = 40                   # pending (not yet imported) previews per user
MAX_USER_PREVIEW_BYTES = 100 * 1024 * 1024
TOLERANCE = Decimal("0.01")


class SampleRow(BaseModel):
    posted_on: date
    description: str
    amount: Decimal
    running_balance: Decimal | None


class BalanceCheck(BaseModel):
    checked_rows: int                 # rows with a running balance we could verify
    mismatched_rows: int              # balance didn't move by the transaction amount
    gap_expected: Decimal | None = None   # account balance before this statement (from earlier imports)
    gap_found: Decimal | None = None      # opening balance implied by this statement
    gap_before: date | None = None


class Mapping(BaseModel):
    headers: list[str]
    columns: dict[str, str | None]
    day_first: bool | None


class PreviewOut(BaseModel):
    id: UUID
    filename: str
    account_id: UUID
    ok: bool
    error: str | None = None
    format: str | None = None
    rows_total: int = 0
    rows_new: int = 0
    rows_duplicate: int = 0            # already in the account
    rows_duplicate_in_batch: int = 0   # also in an earlier file of this upload (e.g. overlapping statements)
    date_from: date | None = None
    date_to: date | None = None
    balance: BalanceCheck | None = None
    warnings: list[str] = []
    mapping: Mapping | None = None
    sample: list[SampleRow] = []


class AdjustIn(BaseModel):
    account_id: UUID | None = None
    day_first: bool | None = None
    mapping: dict[str, str | None] | None = None


class CommitIn(BaseModel):
    preview_ids: list[UUID] = Field(min_length=1, max_length=MAX_FILES)
    keep_originals: bool = False     # keep a copy of each statement file (owner-only)


class RunFile(BaseModel):
    filename: str
    rows_new: int
    rows_duplicate: int


class RunOut(BaseModel):
    id: UUID
    files: int
    rows_new: int
    created_at: str | None = None
    rows_present: int | None = None
    details: list[RunFile] = []


def _balance_check(db: DB, account_id: UUID, txs) -> BalanceCheck | None:
    with_bal = [t for t in txs if t.running_balance is not None]
    if len(with_bal) < 1:
        return None
    seq = list(txs)
    if seq and seq[0].posted_on > seq[-1].posted_on:   # newest-first statements
        seq.reverse()
    checked = mismatched = 0
    prev = None
    for t in seq:
        if t.running_balance is None:
            prev = None
            continue
        if prev is not None:
            checked += 1
            if abs(prev + t.amount - t.running_balance) > TOLERANCE:
                mismatched += 1
        prev = t.running_balance
    first = next(t for t in seq if t.running_balance is not None)
    opening = first.running_balance - first.amount
    before = db.execute(text("""SELECT running_balance, posted_on FROM transactions
                                 WHERE account_id = :a AND running_balance IS NOT NULL AND posted_on < :d
                                 ORDER BY posted_on DESC, created_at DESC LIMIT 1"""),
                        {"a": account_id, "d": first.posted_on}).first()
    chk = BalanceCheck(checked_rows=checked, mismatched_rows=mismatched)
    if before is not None and abs(Decimal(before.running_balance) - opening) > TOLERANCE:
        chk.gap_expected, chk.gap_found, chk.gap_before = Decimal(before.running_balance), opening, first.posted_on
    return chk


def _analyse(db: DB, user, pid: UUID, filename: str, account_id: UUID, raw: bytes, options: dict,
             seen: dict | None = None) -> PreviewOut:
    try:
        parsed = parse_checked(filename, raw, options)
    except ImportError_ as e:
        return PreviewOut(id=pid, filename=filename, account_id=account_id, ok=False, error=str(e))
    txs = parsed.transactions
    fps = assign_fingerprints(txs)
    existing = {r[0] for r in db.execute(text("SELECT fingerprint FROM transactions WHERE account_id = :a AND fingerprint = ANY(:f)"),
                                         {"a": account_id, "f": fps})}
    if seen is None:   # NB: not `seen or {}` — an empty dict from the caller must be shared across files
        seen = {}
    acct_seen = seen.setdefault(str(account_id), set())
    dup_db = dup_batch = 0
    new_rows = []
    for t, fp in zip(txs, fps):
        if fp in existing:
            dup_db += 1
        elif fp in acct_seen:
            dup_batch += 1
        else:
            new_rows.append(t)
        acct_seen.add(fp)
    return PreviewOut(
        id=pid, filename=filename, account_id=account_id, ok=True, format=parsed.format, rows_total=len(txs),
        rows_new=len(new_rows), rows_duplicate=dup_db, rows_duplicate_in_batch=dup_batch,
        date_from=min(t.posted_on for t in txs), date_to=max(t.posted_on for t in txs),
        balance=_balance_check(db, account_id, txs), warnings=parsed.warnings,
        mapping=Mapping(headers=parsed.headers, columns=parsed.columns, day_first=parsed.day_first) if parsed.headers else None,
        sample=[SampleRow(posted_on=t.posted_on, description=t.description, amount=t.amount,
                          running_balance=t.running_balance) for t in new_rows[:5]])


def _preview_row(db: DB, pid: UUID):
    r = db.execute(text("SELECT * FROM import_previews WHERE id = :id AND expires_at > now()"), {"id": pid}).first()
    if r is None:
        raise not_found("Preview not found or expired")
    return r


@router.post("/accounts/{aid}/imports/preview", response_model=list[PreviewOut], status_code=201)
async def preview(aid: UUID, user: User, db: DB, files: Annotated[list[UploadFile], File()]):
    """Dry run (E09-02): nothing is saved to the ledger until you commit."""
    require_own_account(db, user, aid)
    if len(files) > MAX_FILES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Up to {MAX_FILES} files at a time")
    db.execute(text("DELETE FROM import_previews WHERE user_id = :u AND expires_at <= now()"), {"u": user.id})
    limit = get_settings().max_upload_bytes
    # serialise this user's uploads so concurrent requests can't each pass the quota check (released at commit)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('import_previews:' || :u, 0))"), {"u": str(user.id)})
    held = db.execute(text("SELECT count(*), COALESCE(sum(octet_length(raw)), 0) FROM import_previews WHERE user_id = :u"),
                      {"u": user.id}).one()
    if held[0] + len(files) > MAX_USER_PREVIEWS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Too many files waiting to be imported. Import or remove some first.")
    seen: dict = {}
    out = []
    used = 0
    for f in files:
        raw = await f.read(limit + 1)
        name = (f.filename or "upload")[:255]
        if len(raw) > limit:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"{name} is too large")
        used += len(raw)
        if used > MAX_REQUEST_BYTES or held[1] + used > MAX_USER_PREVIEW_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                "These files are too large together. Import them in smaller groups.")
        pid = db.execute(text("INSERT INTO import_previews (user_id, account_id, filename, raw) VALUES (:u, :a, :f, :r) RETURNING id"),
                         {"u": user.id, "a": aid, "f": name, "r": raw}).scalar_one()
        out.append(await run_in_threadpool(_analyse, db, user, pid, name, aid, raw, {}, seen))
    return out


@router.patch("/import-previews/{pid}", response_model=PreviewOut)
async def adjust(pid: UUID, body: AdjustIn, user: User, db: DB):
    """Change the target account, date order or CSV columns, and see the new preview (E09-03)."""
    r = _preview_row(db, pid)
    account_id = body.account_id or r.account_id
    if body.account_id:
        require_own_account(db, user, body.account_id)
    opts = dict(r.options or {})
    if body.day_first is not None:
        opts["day_first"] = body.day_first
    if body.mapping is not None:
        allowed = {"date", "description", "amount", "out", "in", "balance"}
        opts["mapping"] = {k: v for k, v in body.mapping.items() if k in allowed}
    db.execute(text("UPDATE import_previews SET account_id = :a, options = CAST(:o AS jsonb) WHERE id = :id"),
               {"a": account_id, "o": json.dumps(opts), "id": pid})
    return await run_in_threadpool(_analyse, db, user, pid, r.filename, account_id, bytes(r.raw), opts, None)


@router.delete("/import-previews/{pid}", status_code=204)
def discard(pid: UUID, user: User, db: DB):
    _preview_row(db, pid)
    db.execute(text("DELETE FROM import_previews WHERE id = :id"), {"id": pid})


@router.post("/import-runs", response_model=RunOut, status_code=201)
async def commit(body: CommitIn, user: User, db: DB):
    """Save the previewed files as one run. Duplicates are skipped; nothing existing is changed (E09-04)."""
    previews = [_preview_row(db, p) for p in dict.fromkeys(body.preview_ids)]
    parsed = []
    for p in previews:
        try:
            parsed.append(await run_in_threadpool(parse_checked, p.filename, bytes(p.raw), dict(p.options or {})))
        except ImportError_ as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{p.filename}: {e}")
    run_id = db.execute(text("INSERT INTO import_runs (user_id, files, rows_new) VALUES (:u, :n, 0) RETURNING id"),
                        {"u": user.id, "n": len(previews)}).scalar_one()
    details, total_new = [], 0
    for p, res in zip(previews, parsed):
        acc = require_own_account(db, user, p.account_id)
        batch_id, total, new = save_parsed(db, user.id, acc, p.filename, res, run_id)
        if body.keep_originals:
            from app.documents import keep
            keep(db, user.id, "statement", p.filename, bytes(p.raw), account_id=p.account_id, import_batch_id=batch_id)
        details.append(RunFile(filename=p.filename, rows_new=new, rows_duplicate=total - new))
        total_new += new
    db.execute(text("UPDATE import_runs SET rows_new = :n WHERE id = :id"), {"n": total_new, "id": run_id})
    db.execute(text("DELETE FROM import_previews WHERE id = ANY(:ids)"), {"ids": [p.id for p in previews]})
    after_import(db, user.id, [p.account_id for p in previews])
    for aid in dict.fromkeys(p.account_id for p in previews):
        names = [p.filename for p in previews if p.account_id == aid]
        audit.log(db, user.id, "import.run", "import", f"Imported {len(names)} file(s): {', '.join(names)[:200]}",
                  entity_id=run_id, account_id=aid, after={"run_id": run_id, "rows_new": total_new})
    return RunOut(id=run_id, files=len(previews), rows_new=total_new, details=details)


@router.get("/import-runs", response_model=list[RunOut])
def runs(user: User, db: DB):
    rows = db.execute(text("""
        SELECT r.id, r.files, r.rows_new, to_char(r.created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at,
               (SELECT count(*) FROM transactions t JOIN import_batches b ON b.id = t.import_batch_id WHERE b.run_id = r.id) AS rows_present
          FROM import_runs r WHERE r.user_id = :u ORDER BY r.created_at DESC LIMIT 20"""), {"u": user.id})
    return [RunOut(**r._mapping) for r in rows]


@router.delete("/import-runs/{rid}", status_code=204)
def rollback(rid: UUID, user: User, db: DB):
    """Undo a whole run: removes only the rows that run added (E09-04)."""
    if db.execute(text("SELECT 1 FROM import_runs WHERE id = :id"), {"id": rid}).first() is None:
        raise not_found("Import not found")
    accounts = [r[0] for r in db.execute(text("SELECT DISTINCT account_id FROM import_batches WHERE run_id = :id"), {"id": rid})]
    db.execute(text("DELETE FROM import_runs WHERE id = :id"), {"id": rid})   # cascades batches -> transactions
    for a in accounts:
        detect_recurring(db, user.id, a)
        audit.log(db, user.id, "import.rollback", "import", "Undid a multi-file import", entity_id=rid, account_id=a)
