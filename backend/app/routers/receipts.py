"""Receipts (E04): photo -> OCR (sandboxed) -> line items with unit prices and discounts -> review -> save,
reconciled onto the bank transaction (§2). Originals are kept only if the user asks (PO 2026-11-06)."""
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.deps import DB, User, not_found
from app.documents import keep as keep_document
from app.importers.base import ImportError_
from app.receipts.parse import Item, Receipt, parse
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["receipts"])
MAX_FILES = 10


class ItemOut(BaseModel):
    id: UUID
    name: str
    quantity: Decimal
    size_qty: Decimal | None
    size_unit: str | None
    line_price: Decimal
    discount: Decimal
    paid: Decimal
    unit_price: Decimal | None
    unit_basis: str | None
    category: str | None


class Candidate(BaseModel):
    id: UUID
    posted_on: date
    description: str
    amount: Decimal
    account: str


class ReceiptOut(BaseModel):
    id: UUID
    store: str | None
    purchased_on: date | None
    total: Decimal | None
    currency: str
    status: str
    adds_up: bool | None
    read_by: str
    warnings: list[str]
    transaction_id: UUID | None
    items: list[ItemOut] = []
    candidates: list[Candidate] = []
    documents: list[dict] = []


class ReceiptUpdate(BaseModel):
    store: str | None = Field(default=None, max_length=80)
    purchased_on: date | None = None
    total: Decimal | None = Field(default=None, ge=0, lt=Decimal("1e9"), decimal_places=2)


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    quantity: Decimal = Field(default=Decimal(1), gt=0, lt=Decimal("100000"))
    size_qty: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e6"))
    size_unit: str | None = Field(default=None, pattern=r"^(g|kg|ml|l|item)$")
    line_price: Decimal = Field(ge=0, lt=Decimal("1e8"), decimal_places=2)
    discount: Decimal = Field(default=Decimal(0), le=0, gt=Decimal("-1e8"), decimal_places=2)
    category: str | None = Field(default=None, max_length=50)


class ItemPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    quantity: Decimal | None = Field(default=None, gt=0, lt=Decimal("100000"))
    size_qty: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e6"))
    size_unit: str | None = Field(default=None, pattern=r"^(g|kg|ml|l|item)$")
    line_price: Decimal | None = Field(default=None, ge=0, lt=Decimal("1e8"), decimal_places=2)
    discount: Decimal | None = Field(default=None, le=0, gt=Decimal("-1e8"), decimal_places=2)
    category: str | None = Field(default=None, max_length=50)


class SaveIn(BaseModel):
    transaction_id: UUID | None = None
    to_pantry: bool = False
    to_list: list[UUID] = []          # receipt item ids to put back on the shopping list (buy again)


def _lines(filename: str, raw: bytes) -> list[str]:
    """Text files are read as-is; photos and PDFs go through the sandboxed OCR/text extractor."""
    from app.importers.pdf_import import _extract
    head = raw[:8]
    if head.startswith(b"%PDF"):
        return _extract("pdf", raw)["lines"]
    if head.startswith((b"\xff\xd8\xff", b"\x89PNG", b"II*\x00", b"MM\x00*")) or (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"):
        return _extract("image", raw)["lines"]
    try:
        return raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise ImportError_("Couldn't read this file. Use a photo, PDF or text file of the receipt.")


def _read(db: Session, hid: UUID, filename: str, raw: bytes) -> tuple[list[Receipt], str]:
    lines = _lines(filename, raw)
    by_rules = parse(lines)
    cfg = db.execute(text("""SELECT llm_base_url, llm_model, llm_allow_internet FROM household_settings
                              WHERE household_id = :h AND llm_enabled AND llm_base_url IS NOT NULL"""), {"h": hid}).first()
    if cfg and (not by_rules or not all(r.adds_up() for r in by_rules) or len(by_rules) == 1):
        from app.receipts.ai import structure
        ai = structure(lines, cfg.llm_base_url, cfg.llm_model, cfg.llm_allow_internet)
        rules_ok = len(by_rules) == 1 and by_rules[0].adds_up()
        if ai is not None and ai.adds_up() and not rules_ok:
            ai.warnings.append("Read with the help of your AI assistant. Check the items before saving.")
            return [ai], "ai"
    return by_rules, "rules"


def _insert(db: Session, hid: UUID, user_id: UUID, rec: Receipt, read_by: str, currency: str) -> UUID:
    import json
    rid = db.execute(text("""INSERT INTO receipts (household_id, uploaded_by, store, purchased_on, total, currency, adds_up,
                                                   read_by, warnings)
                             VALUES (:h, :u, :s, :d, :t, :c, :a, :rb, CAST(:w AS jsonb)) RETURNING id"""),
                     {"h": hid, "u": user_id, "s": rec.store, "d": rec.purchased_on, "t": rec.total, "c": currency,
                      "a": rec.adds_up(), "rb": read_by, "w": json.dumps(rec.warnings)}).scalar_one()
    for k, it in enumerate(rec.items):
        _insert_item(db, rid, k, it)
    return rid


def _insert_item(db: Session, rid: UUID, pos: int, it: Item) -> None:
    up, basis = it.unit_price()
    db.execute(text("""INSERT INTO receipt_items (receipt_id, position, name, quantity, size_qty, size_unit, line_price, discount,
                                                  unit_price, unit_basis, category)
                       VALUES (:r, :p, :n, :q, :sq, :su, :lp, :d, :up, :ub, :c)"""),
               {"r": rid, "p": pos, "n": it.name, "q": it.quantity, "sq": it.size_qty, "su": it.size_unit, "lp": it.line_price,
                "d": it.discount, "up": up, "ub": basis, "c": it.category})


def _refresh(db: Session, rid: UUID) -> None:
    """Recompute unit prices and the 'adds up' check after edits."""
    total = db.execute(text("SELECT total FROM receipts WHERE id = :r"), {"r": rid}).scalar()
    s = Decimal(0)
    for r in db.execute(text("SELECT * FROM receipt_items WHERE receipt_id = :r"), {"r": rid}).all():
        it = Item(name=r.name, line_price=Decimal(r.line_price), quantity=Decimal(r.quantity), size_qty=r.size_qty,
                  size_unit=r.size_unit, discount=Decimal(r.discount))
        up, basis = it.unit_price()
        db.execute(text("UPDATE receipt_items SET unit_price = :u, unit_basis = :b WHERE id = :i"), {"u": up, "b": basis, "i": r.id})
        s += it.line_price + it.discount
    db.execute(text("UPDATE receipts SET adds_up = :a WHERE id = :r"),
               {"a": None if total is None else abs(s - Decimal(total)) <= Decimal("0.02"), "r": rid})


def _candidates(db: Session, rec) -> list[Candidate]:
    if rec.total is None:
        return []
    near = rec.purchased_on or date.today()
    rows = db.execute(text("""
        SELECT t.id, t.posted_on, coalesce(t.merchant, t.description) AS description, t.amount, a.name AS account,
               (lower(coalesce(t.merchant, t.description)) LIKE '%' || lower(coalesce(:store, '~')) || '%') AS same_store
          FROM transactions t JOIN accounts a ON a.id = t.account_id
         WHERE t.amount BETWEEN :lo AND :hi AND t.posted_on BETWEEN :d1 AND :d2
           AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.transaction_id = t.id AND r.id <> :rid)
           AND (a.owner_user_id = app_uid() OR t.account_id IN (SELECT account_id FROM household_accounts WHERE household_id = :h))
         ORDER BY same_store DESC, abs(t.posted_on - CAST(:d AS date)) LIMIT 5"""),
        {"lo": -Decimal(rec.total) - Decimal("0.05"), "hi": -Decimal(rec.total) + Decimal("0.05"),
         "d1": near - timedelta(days=3), "d2": near + timedelta(days=3), "d": near, "store": rec.store, "rid": rec.id,
         "h": rec.household_id}).all()
    return [Candidate(id=r.id, posted_on=r.posted_on, description=r.description, amount=r.amount, account=r.account) for r in rows]


def _out(db: Session, rid: UUID, detail: bool = True) -> ReceiptOut:
    rec = db.execute(text("SELECT * FROM receipts WHERE id = :r"), {"r": rid}).first()
    if rec is None:
        raise not_found("Receipt not found")
    out = ReceiptOut(id=rec.id, store=rec.store, purchased_on=rec.purchased_on, total=rec.total, currency=rec.currency,
                     status=rec.status, adds_up=rec.adds_up, read_by=rec.read_by, warnings=rec.warnings or [],
                     transaction_id=rec.transaction_id)
    if detail:
        out.items = [ItemOut(id=r.id, name=r.name, quantity=Decimal(r.quantity).normalize(), size_qty=r.size_qty, size_unit=r.size_unit,
                             line_price=r.line_price, discount=r.discount, paid=Decimal(r.line_price) + Decimal(r.discount),
                             unit_price=r.unit_price, unit_basis=r.unit_basis, category=r.category)
                     for r in db.execute(text("SELECT * FROM receipt_items WHERE receipt_id = :r ORDER BY position"), {"r": rid})]
        out.candidates = _candidates(db, rec) if rec.status == "review" else []
        out.documents = [dict(r._mapping) for r in db.execute(
            text("SELECT id, filename, size FROM documents WHERE receipt_id = :r"), {"r": rid})]
    return out


@router.post("/households/{hid}/receipts", response_model=list[ReceiptOut], status_code=201)
async def upload(hid: UUID, user: User, db: DB, files: Annotated[list[UploadFile], File()],
                 keep_original: Annotated[bool, Form()] = False):
    my_role(db, hid)
    if len(files) > MAX_FILES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Up to {MAX_FILES} receipts at a time")
    home = db.execute(text("SELECT home_currency FROM users WHERE id = :u"), {"u": user.id}).scalar_one()
    limit = get_settings().max_upload_bytes
    out = []
    for f in files:
        raw = await f.read(limit + 1)
        if len(raw) > limit:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"{f.filename} is too large")
        try:
            recs, read_by = await run_in_threadpool(_read, db, hid, f.filename or "receipt", raw)
        except ImportError_ as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{f.filename}: {e}")
        if not recs:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"{f.filename}: couldn't find any items or a total. Try a flatter, sharper photo.")
        ids = [_insert(db, hid, user.id, r, read_by, home) for r in recs]
        if keep_original:
            keep_document(db, user.id, "receipt", f.filename or "receipt", raw, household_id=hid, receipt_id=ids[0])
        out += [_out(db, i) for i in ids]
    return out


@router.get("/households/{hid}/receipts", response_model=list[ReceiptOut])
def list_receipts(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    ids = [r[0] for r in db.execute(text("SELECT id FROM receipts WHERE household_id = :h ORDER BY status = 'review' DESC, purchased_on DESC NULLS FIRST, created_at DESC LIMIT 100"), {"h": hid})]
    return [_out(db, i, detail=False) for i in ids]


@router.get("/receipts/{rid}", response_model=ReceiptOut)
def get_receipt(rid: UUID, user: User, db: DB):
    return _out(db, rid)


@router.patch("/receipts/{rid}", response_model=ReceiptOut)
def update_receipt(rid: UUID, body: ReceiptUpdate, user: User, db: DB):
    _out(db, rid, detail=False)
    fields = body.model_dump(exclude_unset=True)
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)    # keys from the schema only
        db.execute(text(f"UPDATE receipts SET {sets} WHERE id = :id"), {**fields, "id": rid})
        _refresh(db, rid)
    return _out(db, rid)


@router.post("/receipts/{rid}/items", response_model=ReceiptOut, status_code=201)
def add_item(rid: UUID, body: ItemIn, user: User, db: DB):
    _out(db, rid, detail=False)
    pos = db.execute(text("SELECT coalesce(max(position), -1) + 1 FROM receipt_items WHERE receipt_id = :r"), {"r": rid}).scalar()
    _insert_item(db, rid, pos, Item(**body.model_dump()))
    _refresh(db, rid)
    return _out(db, rid)


@router.patch("/receipt-items/{iid}", response_model=ReceiptOut)
def update_item(iid: UUID, body: ItemPatch, user: User, db: DB):
    rid = db.execute(text("SELECT receipt_id FROM receipt_items WHERE id = :i"), {"i": iid}).scalar()
    if rid is None:
        raise not_found("Item not found")
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if not (k in ("name", "quantity", "line_price", "discount") and v is None)}
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        db.execute(text(f"UPDATE receipt_items SET {sets} WHERE id = :id"), {**fields, "id": iid})
        _refresh(db, rid)
    return _out(db, rid)


@router.delete("/receipt-items/{iid}", response_model=ReceiptOut)
def delete_item(iid: UUID, user: User, db: DB):
    rid = db.execute(text("SELECT receipt_id FROM receipt_items WHERE id = :i"), {"i": iid}).scalar()
    if rid is None:
        raise not_found("Item not found")
    db.execute(text("DELETE FROM receipt_items WHERE id = :i"), {"i": iid})
    _refresh(db, rid)
    return _out(db, rid)


@router.post("/receipts/{rid}/save", response_model=ReceiptOut)
def save(rid: UUID, body: SaveIn, user: User, db: DB):
    rec = db.execute(text("SELECT * FROM receipts WHERE id = :r"), {"r": rid}).first()
    if rec is None:
        raise not_found("Receipt not found")
    if body.transaction_id:
        ok = any(c.id == body.transaction_id for c in _candidates(db, rec)) or db.execute(text("""
            SELECT 1 FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = :t AND (a.owner_user_id = app_uid()
              OR t.account_id IN (SELECT account_id FROM household_accounts WHERE household_id = :h))"""),
            {"t": body.transaction_id, "h": rec.household_id}).first()
        if not ok:
            raise not_found("Transaction not found")
        if db.execute(text("SELECT 1 FROM receipts WHERE transaction_id = :t AND id <> :r"), {"t": body.transaction_id, "r": rid}).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "That payment already has a receipt")
    db.execute(text("UPDATE receipts SET status = 'saved', transaction_id = :t WHERE id = :r"), {"t": body.transaction_id, "r": rid})
    items = db.execute(text("SELECT * FROM receipt_items WHERE receipt_id = :r ORDER BY position"), {"r": rid}).all()
    stocked = 0
    if body.to_pantry:
        from app.routers.inventory import _add_lot
        for it in items:
            pid = db.execute(text("SELECT id FROM products WHERE household_id = :h AND lower(name) = lower(:n)"),
                             {"h": rec.household_id, "n": it.name}).scalar()
            if pid is None:
                pid = db.execute(text("""INSERT INTO products (household_id, name, size_qty, size_unit, created_by)
                                         VALUES (:h, :n, :sq, :su, :u) RETURNING id"""),
                                 {"h": rec.household_id, "n": it.name, "sq": it.size_qty if it.size_unit != "kg" or it.quantity != 1 else it.size_qty,
                                  "su": it.size_unit, "u": user.id}).scalar_one()
            db.execute(text("UPDATE receipt_items SET product_id = :p WHERE id = :i"), {"p": pid, "i": it.id})
            _add_lot(db, pid, Decimal(it.quantity), None, user.id)
            stocked += 1
    if body.to_list:
        from app.routers.shopping import ItemIn as ListItemIn, _list, add_item as list_add, default_list
        lst = _list(db, default_list(db, rec.household_id, user.id))
        for it in items:
            if it.id in body.to_list:
                list_add(db, lst, user.id, ListItemIn(name=it.name, quantity=Decimal(it.quantity)))
    audit.log(db, user.id, "receipt.save", "receipt",
              f"Saved a {rec.store or ''} receipt ({len(items)} items{', ' + str(stocked) + ' into the pantry' if stocked else ''})".replace("  ", " "),
              entity_id=rid, household_id=rec.household_id)
    return _out(db, rid)


@router.delete("/receipts/{rid}", status_code=204)
def delete_receipt(rid: UUID, user: User, db: DB):
    """Uploader or household owner/admin only: deleting a receipt also removes its kept photo."""
    rec = db.execute(text("SELECT uploaded_by, household_id FROM receipts WHERE id = :r"), {"r": rid}).first()
    if rec is None:
        raise not_found("Receipt not found")
    if rec.uploaded_by != user.id and my_role(db, rec.household_id) not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only whoever scanned this receipt, or the household owner or an admin, can delete it")
    db.execute(text("DELETE FROM receipts WHERE id = :r"), {"r": rid})
