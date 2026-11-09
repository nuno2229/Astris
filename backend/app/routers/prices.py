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
from pydantic import BaseModel, Field
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
    for r in db.execute(text("""SELECT store, name, price, size_qty, size_unit, seen_on, source, url FROM web_prices
                                 WHERE household_id = :h"""), {"h": hid}):
        from app.receipts.parse import size_from_name
        qty, unit = r.size_qty, r.size_unit
        if not unit:
            q2, u2, pack, _ = size_from_name(r.name)
            qty, unit = (q2 * (pack or 1), u2) if q2 else (None, None)
        up, basis = _unit(Decimal(r.price), Decimal(1), qty, unit)
        if up is None:
            continue
        obs.append((product_key(r.name), r.name, Seen(store=r.store, unit_price=up, basis=basis, price=Decimal(r.price), seen_on=r.seen_on,
                                                     source="shop website" if r.source == "auto" else "shop website (you)",
                                                     stale=(date.today() - r.seen_on).days > STALE_DAYS)))
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


# ---------- shop websites (G-17, PO 2026-11-08) ----------

class WebPriceIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    name: str = Field(min_length=1, max_length=160)
    price: Decimal = Field(gt=0, lt=10000, decimal_places=2)


@router.post("/households/{hid}/web-prices", status_code=201)
def add_web_price(hid: UUID, body: WebPriceIn, user: User, db: DB):
    """A price you sent from a shop's product page with the "Send price to Astris" bookmark."""
    from app.webprices import clean_url, shop_for
    my_role(db, hid)
    store = shop_for(body.url)
    if not store:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That page isn't from a UK supermarket Astris knows")
    from app.receipts.parse import size_from_name
    q, u, pack, _ = size_from_name(body.name)
    db.execute(text("""INSERT INTO web_prices (household_id, added_by, source, store, name, price, size_qty, size_unit, url)
                       VALUES (:h, :u, 'you', :s, :n, :p, :q, :unit, :url)"""),
               {"h": hid, "u": user.id, "s": store, "n": " ".join(body.name.split())[:160], "p": body.price,
                "q": (q * (pack or 1)) if q else None, "unit": u if q else None, "url": clean_url(body.url)})
    return {"store": store}


class LookupsIn(BaseModel):
    enabled: bool


_running: set = set()


def _auto_queries(db: Session, hid: UUID) -> list[str]:
    """What to look up: the household's shared list and household favourites (never anyone's personal list)."""
    names = [r[0] for r in db.execute(text("""
        SELECT i.name FROM shopping_items i JOIN shopping_lists l ON l.id = i.list_id
         WHERE l.household_id = :h AND l.scope = 'household' AND NOT i.checked
        UNION SELECT name FROM favourites WHERE household_id = :h AND user_id IS NULL"""), {"h": hid})]
    out, seen = [], set()
    for n in names:
        k = product_key(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n.strip()[:80])
    return out[:12]


def run_lookups(user_id: UUID, hid: UUID) -> int:
    from sqlalchemy.orm import Session as S
    from app.db import bind_user, get_engine
    from app import webprices
    n = 0
    try:
        with S(get_engine()) as db, db.begin():
            bind_user(db, user_id)
            cfg = db.execute(text("""SELECT llm_base_url, llm_model, llm_allow_internet FROM household_settings
                                      WHERE household_id = :h AND llm_enabled AND llm_base_url IS NOT NULL"""), {"h": hid}).first()
            llm = (cfg.llm_base_url, cfg.llm_model, cfg.llm_allow_internet) if cfg else None
            for q in _auto_queries(db, hid):
                for store in webprices.AUTO:
                    if db.execute(text("""SELECT 1 FROM web_prices WHERE household_id = :h AND source = 'auto' AND store = :s
                                           AND lower(query) = lower(:q) AND seen_on > current_date - 7"""), {"h": hid, "s": store, "q": q}).first():
                        continue
                    try:
                        hit = webprices.lookup(store, q, llm)
                    except Exception:
                        hit = None
                    price = webprices.to_decimal(hit["price"]) if hit else None
                    name = " ".join(str(hit.get("name") or "").split())[:160] if hit else ""
                    if price and name:
                        try:
                            with db.begin_nested():       # one bad row can't undo the others
                                db.execute(text("""INSERT INTO web_prices (household_id, added_by, source, store, name, query, price)
                                                   VALUES (:h, :u, 'auto', :s, :n, :q, :p)"""),
                                           {"h": hid, "u": user_id, "s": store, "n": name, "q": q, "p": price})
                            n += 1
                        except Exception:
                            pass
    finally:
        _running.discard(hid)
    return n


@router.get("/households/{hid}/web-lookups")
def get_lookups(hid: UUID, user: User, db: DB):
    role = my_role(db, hid)
    r = db.execute(text("SELECT web_lookups, web_lookups_at FROM household_settings WHERE household_id = :h"), {"h": hid}).first()
    from app import webprices
    return {"enabled": bool(r and r.web_lookups), "last_run": r.web_lookups_at.isoformat() if r and r.web_lookups_at else None,
            "running": hid in _running, "can_edit": role == "owner", "shops": list(webprices.AUTO),
            "searches": _auto_queries(db, hid)}


@router.put("/households/{hid}/web-lookups")
def set_lookups(hid: UUID, body: LookupsIn, user: User, db: DB):
    if my_role(db, hid) != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the household owner can change this")
    db.execute(text("""INSERT INTO household_settings (household_id, web_lookups, updated_by) VALUES (:h, :e, :u)
                       ON CONFLICT (household_id) DO UPDATE SET web_lookups = EXCLUDED.web_lookups, updated_by = EXCLUDED.updated_by,
                           updated_at = now()"""), {"h": hid, "e": body.enabled, "u": user.id})
    from app import audit
    audit.log(db, user.id, "settings.web_lookups", "household", f"Shop website lookups {'on' if body.enabled else 'off'}", household_id=hid)
    return get_lookups(hid, user, db)


@router.post("/households/{hid}/web-lookups/run", status_code=202)
def start_lookups(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    r = db.execute(text("SELECT web_lookups, web_lookups_at FROM household_settings WHERE household_id = :h"), {"h": hid}).first()
    if not (r and r.web_lookups):
        raise HTTPException(status.HTTP_409_CONFLICT, "Shop website lookups are off for this household")
    if hid in _running:
        return {"started": False, "running": True}
    if r.web_lookups_at and db.execute(text("SELECT :t > now() - interval '1 hour'"), {"t": r.web_lookups_at}).scalar():
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Looked up within the last hour. Try again later.")
    # record the run before it starts, so the hourly limit holds even if the run fails
    db.execute(text("UPDATE household_settings SET web_lookups_at = now() WHERE household_id = :h"), {"h": hid})
    db.commit()
    import threading
    _running.add(hid)
    threading.Thread(target=run_lookups, args=(user.id, hid), daemon=True).start()
    return {"started": True, "running": True}


# ---------- Home: shopping at a glance (E16-01) ----------

@router.get("/households/{hid}/home-shopping")
def home_shopping(hid: UUID, user: User, db: DB):
    """Items on the shared list, the shop that's cheapest for most of them, and the top 'what you go through'."""
    my_role(db, hid)
    items = [r[0] for r in db.execute(text("""
        SELECT i.name FROM shopping_items i JOIN shopping_lists l ON l.id = i.list_id
         WHERE l.household_id = :h AND l.scope = 'household' AND l.is_default AND NOT i.checked"""), {"h": hid})]
    book = price_book(db, hid) if items else []
    wins: dict[str, int] = {}
    for n in items:
        c = cheapest_for(book, n)
        if c:
            wins[c.store] = wins.get(c.store, 0) + 1
    best = max(wins.items(), key=lambda kv: kv[1]) if wins else None
    from app.routers.insights import quantities
    q = quantities(db, hid, date.today() + timedelta(days=1))
    top = q[0] if q else None
    return {"list_items": len(items),
            "cheapest": {"store": best[0], "items": best[1]} if best else None,
            "trend": {"product": top.product, "weekly": str(top.weekly_avg), "unit": top.unit, "weeks": [str(w) for w in top.weeks]} if top else None}
