"""Daily ECB euro reference rates (D-8 b). Opt-in per household ('uses internet'); cached locally so
conversions keep working offline. Fetches ONE fixed public URL and sends nothing about the user."""
import json
import logging
import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.netcheck import pinned_request

log = logging.getLogger("astris.fx")
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_TIME = re.compile(r"<Cube\s+time=['\"](\d{4}-\d{2}-\d{2})['\"]")
_RATE = re.compile(r"<Cube\s+currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([0-9]{1,8}(?:\.[0-9]{1,8})?)['\"]\s*/>")
RETRY_S = 6 * 3600
_last_attempt = 0.0


def parse(xml: str) -> tuple[date, dict[str, str]]:
    """Regex over the tiny, fixed ECB format (no XML parser, so no entity tricks)."""
    t = _TIME.search(xml)
    if not t:
        raise ValueError("no date")
    rates = {}
    for ccy, rate in _RATE.findall(xml):
        try:
            v = Decimal(rate)
        except InvalidOperation:
            continue
        if Decimal(0) < v < Decimal("1e8"):
            rates[ccy] = str(v)
    if len(rates) < 5:
        raise ValueError("too few rates")
    return date.fromisoformat(t[1]), rates


def fetch() -> tuple[date, dict[str, str]]:
    code, body = pinned_request(ECB_URL, timeout=20, max_bytes=64 * 1024)
    if code != 200:
        raise ValueError(f"HTTP {code}")
    return parse(body.decode("utf-8", "replace"))


def refresh_if_due(db: Session, force: bool = False) -> bool:
    """Called by the background worker. Fetches only if a household opted in and the cache is stale."""
    global _last_attempt
    if not db.execute(text("SELECT fx_auto_wanted()")).scalar():
        return False
    cur = db.execute(text("SELECT max(as_of) FROM fx_ecb")).scalar()
    stale = cur is None or cur < date.today()
    if not force and (not stale or time.monotonic() - _last_attempt < RETRY_S):
        return False
    _last_attempt = time.monotonic()
    try:
        as_of, rates = fetch()
    except Exception as e:  # network down etc.: keep using the cached rates (offline fallback)
        log.warning("ECB rates fetch failed: %s", type(e).__name__)
        return False
    db.execute(text("SELECT fx_ecb_store(:d, CAST(:r AS jsonb))"), {"d": as_of, "r": json.dumps(rates)})
    return True


def ecb_pairs(db: Session) -> tuple[dict[tuple[str, str], Decimal], date | None]:
    rows = db.execute(text("SELECT currency, per_eur, as_of FROM fx_ecb")).all()
    pairs = {("EUR", r.currency): Decimal(r.per_eur) for r in rows}
    return pairs, (rows[0].as_of if rows else None)
