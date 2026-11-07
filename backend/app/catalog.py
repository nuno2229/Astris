"""Server-wide product names by barcode (G-8, P-7). Never prices, never which household.

Sources:
  - households that opted in to sharing product names (Settings): their pantry names are offered to others
  - an offline Open Food Facts import, run by the server admin:
        python -m app.catalog import-off /data/en.openfoodfacts.org.products.csv.gz
    (download the CSV export from https://world.openfoodfacts.org/data once; only UK products are kept)
"""
import csv
import gzip
import io
import sys
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def lookup(db: Session, codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    return {r.barcode: {"name": r.name, "brand": r.brand, "size_qty": r.size_qty, "size_unit": r.size_unit, "source": r.source}
            for r in db.execute(text("SELECT * FROM product_catalog WHERE barcode = ANY(:c)"), {"c": list(codes)})}


def share(db: Session, hid: UUID, barcode: str | None, name: str, brand: str | None, size_qty, size_unit: str | None) -> None:
    """Offer this product name to other households, if this household opted in (checked in the database)."""
    if barcode:
        db.execute(text("SELECT catalog_share(:h, :b, :n, :br, :q, :u)"),
                   {"h": hid, "b": barcode, "n": name, "br": brand, "q": size_qty, "u": size_unit})


def _rows(path: str, country: str):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace", newline="") as f:
        csv.field_size_limit(10_000_000)
        for r in csv.DictReader(f, delimiter="\t"):
            if country not in (r.get("countries_tags") or ""):
                continue
            code, name = (r.get("code") or "").strip(), (r.get("product_name") or "").strip()
            if not code.isdigit() or not (6 <= len(code) <= 14) or not name:
                continue
            yield code, name[:120], ((r.get("brands") or "").split(",")[0].strip() or None), (r.get("quantity") or "")


def import_off(path: str, country: str = "en:united-kingdom") -> int:
    from app.config import get_settings
    from app.receipts.parse import size_from_name
    from sqlalchemy import create_engine
    eng = create_engine(get_settings().owner_db_url)
    n, batch = 0, []

    def flush(c):
        c.execute(text("""INSERT INTO product_catalog (barcode, name, brand, size_qty, size_unit, source)
                          VALUES (:b, :n, :br, :q, :u, 'open_food_facts')
                          ON CONFLICT (barcode) DO UPDATE SET name = EXCLUDED.name, brand = EXCLUDED.brand,
                                 size_qty = EXCLUDED.size_qty, size_unit = EXCLUDED.size_unit, updated_at = now()
                          WHERE product_catalog.source = 'open_food_facts'"""), batch)
    with eng.begin() as c:
        for code, name, brand, qty in _rows(path, country):
            q, u, _, _ = size_from_name(qty) if qty else (None, None, None, "")
            batch.append({"b": code, "n": name, "br": brand and brand[:80],
                          "q": q if q is not None and Decimal(q) < Decimal("1e9") else None, "u": u})
            if len(batch) >= 2000:
                flush(c)
                n += len(batch)
                batch = []
        if batch:
            flush(c)
            n += len(batch)
    return n


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "import-off":
        print(f"Imported {import_off(sys.argv[2])} UK products.")
    else:
        print(__doc__)
        sys.exit(2)
