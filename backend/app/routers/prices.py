"""Price comparison (E18, UK). Sources, in order of trust:
  1. your own receipts (offline): what you actually paid, where, with unit prices
  2. Open Prices by Open Food Facts (opt-in, uses internet): open, crowd-sourced prices looked up by barcode, UK only
No supermarket websites are scraped (their terms forbid it); online prices are labelled with their date and source."""
import json
import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import DB, User
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["prices"])
STALE_DAYS = 60
OPEN_PRICES = "https://prices.openfoodfacts.org/api/v1/prices"
STORE_WORDS = r"tesco|sainsbury'?s?|asda|aldi|lidl|morrisons|waitrose|co-?op|m&s|marks|iceland|ocado|own|finest|essential|everyday|value|basics"
SIZE_WORDS = r"\d+(?:[.,]\d+)?\s*(?:x\s*\d+(?:[.,]\d+)?\s*)?(?:kg|g|ml|cl|ltr|l|litre|litres|pints?|pk|pack)?\b"


def product_key(name: str) -> str:
    """'Tesco Semi Skimmed Milk 2L' and 'Aldi Semi Skimmed Milk 4 Pints' -> 'semi skimmed milk'."""
    s = name.lower()
    s = re.sub(rf"\b({STORE_WORDS})\b", " ", s)
    s = re.sub(SIZE_WORDS, " ", s)
    s = re.sub(r"\bpints?\b|\bloose\b|\beach\b", " ", s)
    s = re.sub(r"[^a-z& ]+", " ", s)
    return " ".join(s.split())[:80]


def _unit(price: Decimal, qty: Decimal, size_qty, size_unit) -> tuple[Decimal | None, str | None]:
    if price <= 0 or qty <= 0:
        return None, None
    if size_unit in ("g", "kg", "ml", "l") and size_qty:
        base = Decimal(size_qty) * (1000 if size_unit in ("kg", "l") else 1) * qty
        return (price / base * 100).quantize(Decimal("0.0001"), ROUND_HALF_UP), "100g" if size_unit in ("g", "kg") else "100ml"
    return (price / qty).quantize(Decimal("0.0001"), ROUND_HALF_UP), "item"


class Seen(BaseModel):
    store: str
    unit_price: Decimal
    basis: str
    price: Decimal
    seen_on: date | None
    source: str          # "your receipt" | "Open Prices"
    stale: bool


class ProductPrices(BaseModel):
    key: str
    name: str
    basis: str
    cheapest: Seen
    stores: list[Seen]           # latest per store, cheapest first
    saving: Decimal | None       # cheapest vs dearest latest unit price, 0..1


class PricesOut(BaseModel):
    online_enabled: bool
    can_edit: bool
    products: list[ProductPrices]


def price_book(db: Session, hid: UUID) -> list[ProductPrices]:
    obs: list[tuple[str, str, Seen]] = []
    for r in db.execute(text("""
        SELECT i.name, i.quantity, i.size_qty, i.size_unit, i.line_price, i.discount, r.store, r.purchased_on, r.created_at
          FROM receipt_items i JOIN receipts r ON r.id = i.receipt_id
         WHERE r.household_id = :h AND r.status = 'saved' AND r.store IS NOT NULL"""), {"h": hid}):
        paid = Decimal(r.line_price) + Decimal(r.discount)
        up, basis = _unit(paid, Decimal(r.quantity), r.size_qty, r.size_unit)
        if up is None:
            continue
        seen = r.purchased_on or r.created_at.date()
        obs.append((product_key(r.name), r.name, Seen(store=r.store, unit_price=up, basis=basis, price=paid.quantize(Decimal("0.01")),
                                                     seen_on=seen, source="your receipt",
                                                     stale=(date.today() - seen).days > STALE_DAYS)))
    for r in db.execute(text("""
        SELECT p.name, o.store, o.price, o.size_qty, o.size_unit, o.observed_on FROM online_prices o
          JOIN products p ON p.household_id = o.household_id AND p.barcode = o.barcode
         WHERE o.household_id = :h"""), {"h": hid}):
        up, basis = _unit(Decimal(r.price), Decimal(1), r.size_qty, r.size_unit)
        if up is None:
            continue
        obs.append((product_key(r.name), r.name, Seen(store=r.store, unit_price=up, basis=basis, price=Decimal(r.price), seen_on=r.observed_on,
                                                     source="Open Prices",
                                                     stale=r.observed_on is None or (date.today() - r.observed_on).days > STALE_DAYS)))
    groups: dict[tuple[str, str], dict] = {}
    for key, name, s in obs:
        if not key:
            continue
        g = groups.setdefault((key, s.basis), {"name": name, "stores": {}})
        cur = g["stores"].get(s.store)
        if cur is None or (s.seen_on or date.min) > (cur.seen_on or date.min):     # latest price per store
            g["stores"][s.store] = s
    out = []
    for (key, basis), g in groups.items():
        stores = sorted(g["stores"].values(), key=lambda s: s.unit_price)
        saving = None
        if len(stores) > 1 and stores[-1].unit_price > 0:
            saving = (1 - stores[0].unit_price / stores[-1].unit_price).quantize(Decimal("0.001"))
        out.append(ProductPrices(key=key, name=key[:1].upper() + key[1:], basis=basis, cheapest=stores[0], stores=stores, saving=saving))
    return sorted(out, key=lambda p: (-(p.saving or 0), p.name))


def cheapest_for(book: list[ProductPrices], name: str) -> Seen | None:
    """Best known store for a shopping-list item (only when at least two stores have been seen)."""
    k = product_key(name)
    hits = [p for p in book if p.key == k and len(p.stores) > 1]
    return hits[0].cheapest if hits else None


@router.get("/households/{hid}/prices", response_model=PricesOut)
def prices(hid: UUID, user: User, db: DB, q: str | None = None):
    role = my_role(db, hid)
    on = bool(db.execute(text("SELECT prices_online FROM household_settings WHERE household_id = :h"), {"h": hid}).scalar())
    book = price_book(db, hid)
    if q:
        book = [p for p in book if q.lower() in p.name.lower() or q.lower() in p.key]
    return PricesOut(online_enabled=on, can_edit=role == "owner", products=book)


class OnlineIn(BaseModel):
    enabled: bool


@router.put("/households/{hid}/prices-online")
def set_online(hid: UUID, body: OnlineIn, user: User, db: DB):
    if my_role(db, hid) != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the household owner can change this")
    db.execute(text("""INSERT INTO household_settings (household_id, prices_online, updated_by) VALUES (:h, :e, :u)
                       ON CONFLICT (household_id) DO UPDATE SET prices_online = EXCLUDED.prices_online,
                           updated_by = EXCLUDED.updated_by, updated_at = now()"""), {"h": hid, "e": body.enabled, "u": user.id})
    from app import audit
    audit.log(db, user.id, "settings.prices_online", "household", f"Online price lookups {'on' if body.enabled else 'off'}", household_id=hid)
    return {"enabled": body.enabled}


def _fetch(barcode: str) -> list[dict]:
    from app.netcheck import pinned_request
    code, raw = pinned_request(f"{OPEN_PRICES}?product_code={barcode}&size=50&order_by=-date", timeout=15, max_bytes=1024 * 1024,
                               headers={"User-Agent": "Astris (self-hosted household app)"})
    if code != 200:
        return []
    return (json.loads(raw) or {}).get("items") or []


def refresh_online(db: Session, hid: UUID, limit: int = 25) -> int:
    """Look up pantry barcodes not refreshed in a week. UK prices only; latest per store kept."""
    if not db.execute(text("SELECT prices_online FROM household_settings WHERE household_id = :h"), {"h": hid}).scalar():
        return 0
    codes = [r[0] for r in db.execute(text("""
        SELECT p.barcode FROM products p WHERE p.household_id = :h AND p.barcode ~ '^[0-9]{6,14}$'
           AND NOT EXISTS (SELECT 1 FROM online_prices o WHERE o.household_id = :h AND o.barcode = p.barcode
                            AND o.fetched_at > now() - interval '7 days')
         LIMIT :l"""), {"h": hid, "l": limit})]
    n = 0
    for code in codes:
        try:
            items = _fetch(code)
        except Exception:
            continue
        latest: dict[str, dict] = {}
        for it in items:
            loc = it.get("location") or {}
            if (loc.get("osm_address_country_code") or "").upper() != "GB":
                continue
            if (it.get("currency") or "GBP") != "GBP":
                continue
            store = (loc.get("osm_brand") or loc.get("osm_name") or "").strip()[:80]
            try:
                price = Decimal(str(it.get("price")))
            except Exception:
                continue
            if not store or not (Decimal("0") < price < Decimal("10000")):
                continue
            d = it.get("date")
            if store not in latest or (d or "") > (latest[store].get("date") or ""):
                latest[store] = {**it, "_store": store, "_price": price}
        for store, it in latest.items():
            prod = it.get("product") or {}
            unit = (prod.get("product_quantity_unit") or "").lower()
            qty = prod.get("product_quantity")
            size_unit = unit if unit in ("g", "kg", "ml", "l") else None
            try:
                observed = date.fromisoformat(it["date"][:10]) if it.get("date") else None
            except ValueError:
                observed = None
            db.execute(text("""
                INSERT INTO online_prices (household_id, barcode, store, price, currency, size_qty, size_unit, observed_on)
                VALUES (:h, :b, :s, :p, 'GBP', :q, :u, :d)
                ON CONFLICT (household_id, barcode, store) DO UPDATE SET price = EXCLUDED.price, size_qty = EXCLUDED.size_qty,
                    size_unit = EXCLUDED.size_unit, observed_on = EXCLUDED.observed_on, fetched_at = now()"""),
                {"h": hid, "b": code, "s": store, "p": it["_price"], "q": qty if size_unit and isinstance(qty, (int, float)) and qty > 0 else None,
                 "u": size_unit, "d": observed})
            n += 1
    return n


@router.post("/households/{hid}/prices/refresh")
async def refresh(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    if not db.execute(text("SELECT prices_online FROM household_settings WHERE household_id = :h"), {"h": hid}).scalar():
        raise HTTPException(status.HTTP_409_CONFLICT, "Online price lookups are off for this household")
    n = await run_in_threadpool(refresh_online, db, hid)
    return {"updated": n}
