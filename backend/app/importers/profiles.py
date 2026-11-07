"""Import profiles (G-16): remember the CSV column layout / date order you picked for a bank, keyed by its header
row, and reuse it next time. Per person; only CSV options are stored (column names and day-first)."""
import csv
import hashlib
import io
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.importers.base import decode

ROLES = {"date", "description", "amount", "out", "in", "balance"}


def signature(headers: list[str]) -> str:
    return hashlib.sha256("|".join(h.strip().lower() for h in headers).encode()).hexdigest()


def _candidate_rows(raw: bytes) -> list[list[str]]:
    try:
        t = decode(raw)
    except Exception:
        return []
    head = t[:8192]
    try:
        dialect = csv.Sniffer().sniff(head[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    try:
        rows = [r for r in csv.reader(io.StringIO(head), dialect) if any(c.strip() for c in r)]
    except csv.Error:
        return []
    return [r for r in rows[:15] if len(r) >= 2]


def clean(options: dict) -> dict:
    out: dict = {}
    m = options.get("mapping")
    if isinstance(m, dict) and any(m.values()):
        out["mapping"] = {k: (str(v)[:80] if v else None) for k, v in m.items() if k in ROLES}
    if isinstance(options.get("day_first"), bool):
        out["day_first"] = options["day_first"]
    return out


def find(db: Session, filename: str, raw: bytes) -> dict | None:
    """Saved options for this file's header row, if you saved some. CSV files only."""
    if not filename.lower().endswith((".csv", ".txt")) and b"," not in raw[:2048] and b";" not in raw[:2048]:
        return None
    sigs = [signature(r) for r in _candidate_rows(raw)]
    if not sigs:
        return None
    row = db.execute(text("SELECT options FROM import_profiles WHERE signature = ANY(:s) ORDER BY updated_at DESC LIMIT 1"),
                     {"s": sigs}).first()
    return clean(row.options) if row else None


def remember(db: Session, headers: list[str] | None, options: dict) -> None:
    opts = clean(options or {})
    if not headers or not opts:
        return
    db.execute(text("""INSERT INTO import_profiles (signature, headers, options) VALUES (:s, :h, CAST(:o AS jsonb))
                       ON CONFLICT (user_id, signature) DO UPDATE SET options = EXCLUDED.options, updated_at = now()"""),
               {"s": signature(headers), "h": ", ".join(h.strip() for h in headers)[:600], "o": json.dumps(opts)})


def forget(db: Session, user_id: UUID, sig: str) -> bool:
    return bool(db.execute(text("DELETE FROM import_profiles WHERE signature = :s"), {"s": sig}).rowcount)
