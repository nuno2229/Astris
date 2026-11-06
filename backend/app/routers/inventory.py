"""Household inventory (E14): products, stock lots with expiry, barcode scans, weekly shop suggestions.
Fully offline: barcodes are decoded in the browser; unknown codes are named by the user (no online lookup)."""
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import audit
from app.deps import DB, User, not_found
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["inventory"])
SOON_DAYS = 3
BARCODE = r"^[0-9A-Za-z\-]{4,48}$"
Unit = Literal["g", "kg", "ml", "l", "item"]


class ProductIn(BaseModel):
    barcode: str | None = Field(default=None, pattern=BARCODE)
    name: str = Field(min_length=1, max_length=120)
    brand: str | None = Field(default=None, max_length=80)
    size_qty: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e6"))
    size_unit: Unit | None = None
    restock_below: Decimal | None = Field(default=None, ge=0, lt=Decimal("1e6"))
    location: str | None = Field(default=None, max_length=40)
    quantity: Decimal = Field(default=Decimal(1), ge=0, lt=Decimal("1e6"))
    expires_on: date | None = None


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    brand: str | None = Field(default=None, max_length=80)
    size_qty: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e6"))
    size_unit: Unit | None = None
    restock_below: Decimal | None = Field(default=None, ge=0, lt=Decimal("1e6"))
    location: str | None = Field(default=None, max_length=40)


class LotIn(BaseModel):
    quantity: Decimal = Field(default=Decimal(1), gt=0, lt=Decimal("1e6"))
    expires_on: date | None = None


class LotUpdate(BaseModel):
    quantity: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e6"))
    expires_on: date | None = None


class UseIn(BaseModel):
    quantity: Decimal = Field(default=Decimal(1), gt=0, lt=Decimal("1e6"))


class ScanIn(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=200)   # one entry per scan; repeats = more of the same

    @field_validator("codes")
    @classmethod
    def clean(cls, v):
        import re
        out = [c.strip() for c in v if c and re.fullmatch(BARCODE, c.strip())]
        if not out:
            raise ValueError("No readable barcodes")
        return out


class Lot(BaseModel):
    id: UUID
    quantity: Decimal
    expires_on: date | None
    days_left: int | None
    state: Literal["fresh", "soon", "expired", "no_date"]


class ProductOut(BaseModel):
    id: UUID
    barcode: str | None
    name: str
    brand: str | None
    size_qty: Decimal | None
    size_unit: str | None
    location: str | None
    restock_below: Decimal | None
    on_hand: Decimal                 # items
    amount_label: str | None         # standardised total, e.g. "1.5 kg" (E14-06)
    low: bool
    next_expiry: date | None
    lots: list[Lot]
    flags: list[str]                 # "no barcode", "no expiry date"


class ScanResult(BaseModel):
    added: list[dict]
    unknown: list[str]


class Suggestion(BaseModel):
    id: UUID
    generated_at: datetime
    trigger: str
    items: list[dict]


def human_amount(total: Decimal, unit: str | None) -> str | None:
    """Standardise to the household unit: grams/ml, shown as kg/l from 1000 up (E14-06)."""
    if unit in (None, "item"):
        return None
    base = {"g": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000)}[unit]
    v = total * base[1]
    if v >= 1000:
        big = "kg" if base[0] == "g" else "l"
        return f"{(v / 1000).normalize():f} {big}"
    return f"{v.normalize():f} {base[0]}"


def _state(exp: date | None, today: date) -> tuple[int | None, str]:
    if exp is None:
        return None, "no_date"
    d = (exp - today).days
    return d, "expired" if d < 0 else "soon" if d <= SOON_DAYS else "fresh"


def build(db: Session, p, today: date | None = None) -> ProductOut:
    today = today or date.today()
    lots = []
    for r in db.execute(text("SELECT id, quantity, expires_on FROM stock_lots WHERE product_id = :p ORDER BY expires_on NULLS LAST, added_at"),
                        {"p": p.id}):
        d, st = _state(r.expires_on, today)
        lots.append(Lot(id=r.id, quantity=Decimal(r.quantity).normalize(), expires_on=r.expires_on, days_left=d, state=st))
    on_hand = sum((l.quantity for l in lots), Decimal(0))
    flags = []
    if not p.barcode:
        flags.append("no barcode")
    if lots and any(l.expires_on is None for l in lots):
        flags.append("no expiry date")
    return ProductOut(id=p.id, barcode=p.barcode, name=p.name, brand=p.brand, size_qty=p.size_qty, size_unit=p.size_unit,
                      location=p.location, restock_below=p.restock_below, on_hand=on_hand,
                      amount_label=human_amount(on_hand * Decimal(p.size_qty), p.size_unit) if p.size_qty and lots else None,
                      low=p.restock_below is not None and on_hand <= Decimal(p.restock_below),
                      next_expiry=next((l.expires_on for l in lots if l.expires_on and l.state != "expired"), None),
                      lots=lots, flags=flags)


def household_stock(db: Session, hid: UUID) -> list[ProductOut]:
    return [build(db, p) for p in db.execute(text("SELECT * FROM products WHERE household_id = :h ORDER BY lower(name)"), {"h": hid})]


def _product(db: DB, pid: UUID):
    p = db.execute(text("SELECT * FROM products WHERE id = :p"), {"p": pid}).first()
    if p is None:
        raise not_found("Product not found")
    return p


def _lot(db: DB, lid: UUID):
    r = db.execute(text("SELECT l.*, p.household_id, p.name FROM stock_lots l JOIN products p ON p.id = l.product_id WHERE l.id = :l"),
                   {"l": lid}).first()
    if r is None:
        raise not_found("Stock entry not found")
    return r


def _add_lot(db: DB, pid: UUID, qty: Decimal, exp: date | None, user_id: UUID) -> None:
    if qty <= 0:
        return
    # same product + same expiry -> merge into one lot instead of piling up rows (E14-03 dedupe)
    n = db.execute(text("""UPDATE stock_lots SET quantity = quantity + :q WHERE id = (
                              SELECT id FROM stock_lots WHERE product_id = :p AND expires_on IS NOT DISTINCT FROM :e LIMIT 1)"""),
                   {"q": qty, "p": pid, "e": exp}).rowcount
    if not n:
        db.execute(text("INSERT INTO stock_lots (product_id, quantity, expires_on, added_by) VALUES (:p, :q, :e, :u)"),
                   {"p": pid, "q": qty, "e": exp, "u": user_id})


@router.get("/households/{hid}/stock", response_model=list[ProductOut])
def list_stock(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    return household_stock(db, hid)


@router.post("/households/{hid}/scan", response_model=ScanResult)
def scan(hid: UUID, body: ScanIn, user: User, db: DB):
    """Batch scan (E14-02/03): repeats of a code add more of it; unknown codes come back for naming."""
    my_role(db, hid)
    counts = Counter(body.codes)
    added, unknown = [], []
    for code, n in counts.items():
        p = db.execute(text("SELECT * FROM products WHERE household_id = :h AND barcode = :b"), {"h": hid, "b": code}).first()
        if p is None:
            unknown.append(code)
            continue
        _add_lot(db, p.id, Decimal(n), None, user.id)
        added.append({"product_id": str(p.id), "name": p.name, "quantity": n})
    if added:
        audit.log(db, user.id, "stock.scan", "stock", "Scanned in " + ", ".join(f"{a['quantity']}× {a['name']}" for a in added)[:250],
                  household_id=hid)
    return ScanResult(added=added, unknown=unknown)


@router.post("/households/{hid}/products", response_model=ProductOut, status_code=201)
def create_product(hid: UUID, body: ProductIn, user: User, db: DB):
    my_role(db, hid)
    if body.barcode and db.execute(text("SELECT 1 FROM products WHERE household_id = :h AND barcode = :b"),
                                   {"h": hid, "b": body.barcode}).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "A product with this barcode is already in your stock list")
    pid = db.execute(text("""
        INSERT INTO products (household_id, barcode, name, brand, size_qty, size_unit, restock_below, location, created_by)
        VALUES (:h, :b, :n, :br, :sq, :su, :rb, :loc, :u) RETURNING id"""),
        {"h": hid, "b": body.barcode, "n": body.name.strip(), "br": body.brand, "sq": body.size_qty, "su": body.size_unit,
         "rb": body.restock_below, "loc": body.location, "u": user.id}).scalar_one()
    _add_lot(db, pid, body.quantity, body.expires_on, user.id)
    audit.log(db, user.id, "stock.product", "stock", f"Added {body.name.strip()} to the pantry ({body.quantity:g})", entity_id=pid,
              household_id=hid)
    return build(db, _product(db, pid))


@router.patch("/products/{pid}", response_model=ProductOut)
def update_product(pid: UUID, body: ProductUpdate, user: User, db: DB):
    p = _product(db, pid)
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is None:
        fields.pop("name")
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)   # keys from the schema only
        db.execute(text(f"UPDATE products SET {sets} WHERE id = :id"), {**fields, "id": pid})
    return build(db, _product(db, pid))


@router.delete("/products/{pid}", status_code=204)
def delete_product(pid: UUID, user: User, db: DB):
    p = _product(db, pid)
    db.execute(text("DELETE FROM products WHERE id = :p"), {"p": pid})
    audit.log(db, user.id, "stock.remove", "stock", f"Removed {p.name} from the pantry", entity_id=pid, household_id=p.household_id)


@router.post("/products/{pid}/lots", response_model=ProductOut, status_code=201)
def add_lot(pid: UUID, body: LotIn, user: User, db: DB):
    p = _product(db, pid)
    _add_lot(db, pid, body.quantity, body.expires_on, user.id)
    audit.log(db, user.id, "stock.add", "stock", f"Added {body.quantity:g}× {p.name}", entity_id=pid, household_id=p.household_id)
    return build(db, _product(db, pid))


@router.patch("/lots/{lid}", response_model=ProductOut)
def update_lot(lid: UUID, body: LotUpdate, user: User, db: DB):
    lot = _lot(db, lid)
    fields = body.model_dump(exclude_unset=True)
    if "quantity" in fields and fields["quantity"] is None:
        fields.pop("quantity")
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        db.execute(text(f"UPDATE stock_lots SET {sets} WHERE id = :id"), {**fields, "id": lid})
    return build(db, _product(db, lot.product_id))


@router.delete("/lots/{lid}", status_code=204)
def discard_lot(lid: UUID, user: User, db: DB):
    lot = _lot(db, lid)
    db.execute(text("DELETE FROM stock_lots WHERE id = :l"), {"l": lid})
    audit.log(db, user.id, "stock.discard", "stock", f"Threw away {Decimal(lot.quantity).normalize():f}× {lot.name}",
              entity_id=lot.product_id, household_id=lot.household_id)


@router.post("/products/{pid}/use", response_model=ProductOut)
def use(pid: UUID, body: UseIn, user: User, db: DB):
    """Used some up: take from the soonest-expiring batch first."""
    p = _product(db, pid)
    left = body.quantity
    for r in db.execute(text("SELECT id, quantity FROM stock_lots WHERE product_id = :p ORDER BY expires_on NULLS LAST, added_at"),
                        {"p": pid}).all():
        if left <= 0:
            break
        q = Decimal(r.quantity)
        if q <= left:
            db.execute(text("DELETE FROM stock_lots WHERE id = :l"), {"l": r.id})
            left -= q
        else:
            db.execute(text("UPDATE stock_lots SET quantity = quantity - :q WHERE id = :l"), {"q": left, "l": r.id})
            left = Decimal(0)
    audit.log(db, user.id, "stock.use", "stock", f"Used {body.quantity - left:g}× {p.name}", entity_id=pid, household_id=p.household_id)
    return build(db, _product(db, pid))


@router.post("/households/{hid}/stock/clear-expired", response_model=list[ProductOut])
def clear_expired(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    n = db.execute(text("""DELETE FROM stock_lots WHERE expires_on < current_date
                             AND product_id IN (SELECT id FROM products WHERE household_id = :h)"""), {"h": hid}).rowcount
    if n:
        audit.log(db, user.id, "stock.clear_expired", "stock", f"Cleared {n} expired item(s)", household_id=hid)
    return household_stock(db, hid)


def generate_suggestions(db: Session, hid: UUID, trigger: str) -> dict:
    """Low-stock weekly shop (E14-07): everything at or under its restock level, topping back up to it +1."""
    items = []
    for p in household_stock(db, hid):
        if p.low:
            need = max(Decimal(p.restock_below) + 1 - p.on_hand, Decimal(1))
            items.append({"product_id": str(p.id), "name": p.name, "brand": p.brand, "have": float(p.on_hand),
                          "buy": float(need), "reason": "out of stock" if p.on_hand == 0 else "running low"})
    import json
    row = db.execute(text("""INSERT INTO shop_suggestions (household_id, trigger, items) VALUES (:h, :t, CAST(:i AS jsonb))
                             RETURNING id, generated_at"""), {"h": hid, "t": trigger, "i": json.dumps(items)}).one()
    return {"id": row.id, "generated_at": row.generated_at, "trigger": trigger, "items": items}


@router.get("/households/{hid}/shop-suggestions", response_model=list[Suggestion])
def suggestions(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    rows = db.execute(text("""SELECT id, generated_at, trigger, items FROM shop_suggestions WHERE household_id = :h
                              ORDER BY generated_at DESC LIMIT 12"""), {"h": hid})
    return [Suggestion(**r._mapping) for r in rows]


@router.post("/households/{hid}/shop-suggestions", response_model=Suggestion, status_code=201)
def make_suggestions(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    return Suggestion(**generate_suggestions(db, hid, "manual"))


class ShopDayIn(BaseModel):
    day: int | None = Field(default=None, ge=0, le=6)


@router.get("/households/{hid}/shop-day")
def get_shop_day(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    return {"day": db.execute(text("SELECT shop_day FROM household_settings WHERE household_id = :h"), {"h": hid}).scalar()}


@router.put("/households/{hid}/shop-day")
def set_shop_day(hid: UUID, body: ShopDayIn, user: User, db: DB):
    """Which day the weekly shop list is made. Owner only, like other household settings (RLS)."""
    if my_role(db, hid) != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the household owner can change this")
    db.execute(text("""INSERT INTO household_settings (household_id, shop_day, updated_by) VALUES (:h, :d, :u)
                       ON CONFLICT (household_id) DO UPDATE SET shop_day = EXCLUDED.shop_day, updated_by = EXCLUDED.updated_by,
                           updated_at = now()"""), {"h": hid, "d": body.day, "u": user.id})
    return {"day": body.day}


def run_weekly(db_factory, today: date | None = None) -> int:
    """Worker hook: on each household's shop day, make one list (once per day)."""
    from app.db import bind_user
    today = today or date.today()
    made = 0
    with db_factory() as s:
        targets = s.execute(text("SELECT * FROM shop_day_households(:d)"), {"d": today.weekday()}).all()
    for t in targets:
        if t.member is None:
            continue
        with db_factory() as s, s.begin():
            bind_user(s, t.member)
            done = s.execute(text("""SELECT 1 FROM shop_suggestions WHERE household_id = :h AND trigger = 'weekly'
                                       AND generated_at::date = :d"""), {"h": t.household_id, "d": today}).first()
            if not done:
                generate_suggestions(s, t.household_id, "weekly")
                made += 1
    return made
