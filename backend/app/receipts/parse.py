"""Receipt reading by rules (E04): store, date, total, line items with quantity, size, discounts and a
normalised unit price. Works on OCR text, fully offline."""
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.categorise.rules import MERCHANTS, categorise

PRICE = r"(-?)[£€$]?\s?(\d{1,5}[.,]\d{2})\s*(-)?\s*[A-Z*]?\s*$"        # trailing price, optional VAT letter
SKIP = re.compile(r"\b(sub\s*-?total|total|balance due|amount due|change|cash|card|visa|mastercard|contactless|debit|"
                  r"vat|tax|items? sold|number of items|auth|merchant|terminal|tel|www\.|thank|receipt|aid:|pan seq|"
                  r"clubcard points|points balance|you saved|saving today)\b", re.I)
DISCOUNT = re.compile(r"\b(saving|discount|promo|offer|clubcard price|nectar price|multibuy|reduced|coupon|voucher|price cut|-\s*\d)", re.I)
TOTAL = re.compile(r"^\s*(?:total|balance due|amount due|to pay|total to pay)\b[^\d-]*(\d{1,5}[.,]\d{2})", re.I)
QTY_AT = re.compile(r"^\s*(\d{1,3})\s*[x@]\s*[£€$]?\s?(\d{1,4}[.,]\d{2})", re.I)                    # 2 x 0.85
WEIGHT = re.compile(r"(\d{1,3}[.,]\d{1,3})\s*kg\s*@\s*[£€$]?\s?(\d{1,4}[.,]\d{2})\s*/\s*kg", re.I)   # 0.512 kg @ £1.20/kg
SIZE = re.compile(r"(?:(\d{1,2})\s*[xX]\s*)?(\d{1,4}(?:[.,]\d{1,3})?)\s*(kg|g|ml|cl|ltr|l|litre|pk|pack)\b", re.I)
DATE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")


@dataclass
class Item:
    name: str
    line_price: Decimal
    quantity: Decimal = Decimal(1)
    size_qty: Decimal | None = None
    size_unit: str | None = None
    discount: Decimal = Decimal(0)
    category: str | None = None

    def unit_price(self) -> tuple[Decimal | None, str | None]:
        """Price per 100 g / 100 ml (or per item), after discounts — for like-for-like comparison (E04-03)."""
        paid = self.line_price + self.discount
        if paid <= 0 or self.quantity <= 0:
            return None, None
        if self.size_unit in ("g", "kg", "ml", "l") and self.size_qty:
            base = self.size_qty * (1000 if self.size_unit in ("kg", "l") else 1)       # grams or ml per item
            total = base * self.quantity
            return (paid / total * 100).quantize(Decimal("0.0001"), ROUND_HALF_UP), "100g" if self.size_unit in ("g", "kg") else "100ml"
        return (paid / self.quantity).quantize(Decimal("0.0001"), ROUND_HALF_UP), "item"


@dataclass
class Receipt:
    store: str | None = None
    purchased_on: date | None = None
    total: Decimal | None = None
    items: list[Item] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def adds_up(self) -> bool | None:
        if self.total is None or not self.items:
            return None
        s = sum((i.line_price + i.discount for i in self.items), Decimal(0))
        return abs(s - self.total) <= Decimal("0.02")


def _d(s: str) -> Decimal:
    return Decimal(s.replace(",", "."))


def size_from_name(name: str) -> tuple[Decimal | None, str | None, Decimal | None, str]:
    """'MILK 2L' -> (2, 'l'); '4X330ML' -> (330, 'ml', pack=4); returns (size, unit, pack_multiplier, cleaned name)."""
    m = None
    for m in SIZE.finditer(name):
        pass
    if not m:
        return None, None, None, name.strip()
    pack, qty, unit = m[1], _d(m[2]), m[3].lower()
    unit = {"ltr": "l", "litre": "l", "cl": "ml", "pk": "item", "pack": "item"}.get(unit, unit)
    if m[3].lower() == "cl":
        qty *= 10
    if unit == "item":
        return qty, "item", None, name.strip()
    return qty, unit, Decimal(pack) if pack else None, name.strip()


def parse(lines: list[str], today: date | None = None) -> list[Receipt]:
    """Split on TOTAL lines (several receipts in one photo) and parse each."""
    lines = [re.sub(r"\s+", " ", l).strip() for l in lines if l and l.strip()]
    chunks: list[list[str]] = []
    cur: list[str] = []
    after_total = False
    for l in lines:
        is_item = bool(re.search(PRICE, l)) and not SKIP.search(l) and not TOTAL.match(l)
        starts_new = after_total and (is_item or _store([l]) is not None)
        if starts_new:
            chunks.append(cur)
            cur, after_total = [], False
        cur.append(l)
        if TOTAL.match(l):
            after_total = True        # footer lines (card, change, date) still belong to this receipt
    if cur:
        chunks.append(cur)
    return [r for r in (_parse_one(c, today) for c in chunks) if r.items or r.total is not None]


def _store(lines: list[str]) -> str | None:
    for l in lines[:6]:
        low = l.lower()
        for alias, (name, cat) in MERCHANTS.items():
            if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", low) and cat in ("Groceries", "Health", "Shopping", "Personal care", "Eating out"):
                return name
    return None


def _parse_one(lines: list[str], today: date | None) -> Receipt:
    r = Receipt(store=_store(lines))
    for l in lines:
        m = DATE.search(l)
        if m:
            d, mo, y = int(m[1]), int(m[2]), int(m[3])
            y = y + 2000 if y < 100 else y
            try:
                r.purchased_on = date(y, mo, d)
                break
            except ValueError:
                continue
    pending_qty: tuple[Decimal, Decimal] | None = None
    last_text: str | None = None      # a name line with no price (e.g. loose produce, weight + price on the next line)
    for l in lines:
        t = TOTAL.match(l)
        if t:
            r.total = _d(t[1])
            continue
        if SKIP.search(l) and not DISCOUNT.search(l):
            continue
        q = QTY_AT.match(l)
        if q and not re.search(r"[A-Za-z]{3}", l[q.end():]):   # quantity line belongs to the next/previous item
            pending_qty = (Decimal(q[1]), _d(q[2]))
            if r.items and abs(r.items[-1].line_price - pending_qty[0] * pending_qty[1]) <= Decimal("0.01"):
                r.items[-1].quantity = pending_qty[0]
                pending_qty = None
            continue
        w = WEIGHT.search(l)
        m = re.search(PRICE, l)
        if not m:
            if w and r.items:                                  # weight line under a loose-produce item
                r.items[-1].quantity, r.items[-1].size_qty, r.items[-1].size_unit = Decimal(1), _d(w[1]), "kg"
            elif not w and not DATE.search(l) and re.search(r"[A-Za-z]{3}", l):
                last_text = l.strip(" .:-*")
            continue
        price = _d(m[2])
        neg = bool(m[1]) or bool(m[3])
        name = l[:m.start()].strip(" .:-*")
        if w:
            name = l[:w.start()].strip(" .:-*") or last_text or name
        if not name or len(name) < 2:
            continue
        if neg or (DISCOUNT.search(name) and r.items):
            if r.items:
                r.items[-1].discount -= price
            continue
        size, unit, pack, clean = size_from_name(name)
        it = Item(name=clean.title()[:120], line_price=price, size_qty=size if unit != "item" else None,
                  size_unit=unit if unit != "item" else None)
        if unit == "item" and size:
            it.size_qty, it.size_unit = size, "item"
        if pack:
            it.size_qty = (size or Decimal(0)) * pack
        if w:
            it.size_qty, it.size_unit = _d(w[1]), "kg"
        if pending_qty:
            it.quantity = pending_qty[0]
            pending_qty = None
        it.category = categorise(name, Decimal("-1")).category or (
            "Groceries" if r.store and r.store in {v[0] for v in MERCHANTS.values() if v[1] == "Groceries"} else None)
        r.items.append(it)
        last_text = None
    if r.adds_up() is False:
        r.warnings.append("The items don't add up to the total. Some lines may have been misread; please check them.")
    if any("clubcard" in l.lower() or "nectar" in l.lower() for l in lines) and not any(i.discount for i in r.items):
        r.warnings.append("Loyalty card savings aren't itemised on this receipt, so prices may be before discounts.")
    return r
