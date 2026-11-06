"""Recurring & scheduled transaction detection (E06). Pure offline statistics over imported data.

A merchant becomes a series when it hits a steady rhythm: weekly, fortnightly, monthly (≥3 times) or
yearly (≥2 times), with a stable amount (within 25% — so variable bills like energy still count, a
weekly food shop usually doesn't). 'Missed' is only claimed when imported statements already cover the
due date plus a grace period; otherwise we simply haven't seen that statement yet.
"""
import calendar
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

# cadence -> (typical interval in days, tolerance in days, grace before "missed")
CADENCES = {
    "weekly": (7, 1.5, 3),
    "fortnightly": (14, 2, 4),
    "monthly": (30.44, 4, 5),
    "yearly": (365.25, 10, 14),
}
AMOUNT_BAND = Decimal("0.25")
DRIFT = Decimal("0.10")
LOOKBACK_DAYS = 800


def add_period(d: date, cadence: str, anchor_day: int) -> date:
    if cadence == "weekly":
        return d + timedelta(days=7)
    if cadence == "fortnightly":
        return d + timedelta(days=14)
    if cadence == "monthly":
        y, m = (d.year + (d.month // 12), d.month % 12 + 1)
        return date(y, m, min(anchor_day, calendar.monthrange(y, m)[1]))
    y = d.year + 1
    return date(y, d.month, min(d.day, calendar.monthrange(y, d.month)[1]))


@dataclass
class Series:
    cadence: str
    expected: Decimal
    last: date
    next: date
    occurrences: int


def fit(points: list[tuple[date, Decimal]]) -> Series | None:
    """points: one (date, signed amount) per day, sorted. Returns the series if the rhythm is steady."""
    if len(points) < 2:
        return None
    dates = [p[0] for p in points]
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    med = statistics.median(gaps)
    for name, (days, tol, _) in CADENCES.items():
        if abs(med - days) > tol:
            continue
        ok = sum(1 for g in gaps if abs(g - days) <= tol)
        need_n = 2 if name == "yearly" else 3
        if len(points) < need_n or ok < max(1, -(-3 * len(gaps) // 4)):   # ≥75% of gaps on rhythm
            return None
        recent = [abs(p[1]) for p in points[-3:]]
        mid = Decimal(statistics.median(recent))
        if mid == 0 or any(abs(x - mid) > mid * AMOUNT_BAND for x in recent):
            return None
        # the latest charge is the best predictor of the next one (subscriptions change price, then stay)
        anchor = statistics.mode([d.day for d in dates[-6:]]) if name == "monthly" else dates[-1].day
        return Series(name, points[-1][1], dates[-1], add_period(dates[-1], name, anchor), len(points))
    return None


def detect(db: Session, owner: UUID, account_id: UUID | None = None, today: date | None = None) -> int:
    """(Re)build series for the owner's accounts. Runs as the owner (RLS). Returns series upserted."""
    today = today or date.today()
    rows = db.execute(text("""
        SELECT t.account_id, a.currency, t.merchant_key, t.posted_on, t.amount, t.merchant, t.description, t.category
          FROM transactions t JOIN accounts a ON a.id = t.account_id
         WHERE a.owner_user_id = :u AND (CAST(:a AS uuid) IS NULL OR t.account_id = CAST(:a AS uuid))
           AND t.merchant_key IS NOT NULL AND t.amount <> 0 AND t.posted_on >= :since
         ORDER BY t.posted_on"""),
        {"u": owner, "a": str(account_id) if account_id else None,
         "since": today - timedelta(days=LOOKBACK_DAYS)}).all()
    coverage: dict = {}
    groups: dict[tuple, dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    meta: dict[tuple, tuple] = {}
    for r in rows:
        coverage[r.account_id] = max(coverage.get(r.account_id, r.posted_on), r.posted_on)
        k = (r.account_id, r.merchant_key, "out" if r.amount < 0 else "in")
        groups[k][r.posted_on] += Decimal(r.amount)
        meta[k] = (r.merchant or r.merchant_key.title(), r.category, r.currency)
    existing = {(e.account_id, e.merchant_key, e.direction): e for e in db.execute(text("""
        SELECT rs.id, rs.account_id, rs.merchant_key, rs.direction, rs.expected_amount, rs.previous_amount, rs.status
          FROM recurring_series rs JOIN accounts a ON a.id = rs.account_id
         WHERE a.owner_user_id = :u AND (CAST(:a AS uuid) IS NULL OR rs.account_id = CAST(:a AS uuid))"""),
        {"u": owner, "a": str(account_id) if account_id else None})}
    n = 0
    for k, by_day in groups.items():
        s = fit(sorted(by_day.items()))
        if s is None:
            continue
        name, category, currency = meta[k]
        old = existing.get(k)
        if old and old.status == "dismissed":
            continue
        previous = old.previous_amount if old else None
        if old and old.expected_amount and abs(s.expected - old.expected_amount) > abs(old.expected_amount) * DRIFT:
            previous = old.expected_amount
        grace = CADENCES[s.cadence][2]
        status = "missed" if coverage[k[0]] > s.next + timedelta(days=grace) else "active"
        db.execute(text("""
            INSERT INTO recurring_series (account_id, merchant_key, direction, name, category, cadence, expected_amount,
                                          previous_amount, currency, occurrences, last_date, next_date, status)
            VALUES (:a, :k, :d, :name, :cat, :cad, :exp, :prev, :cur, :occ, :last, :next, :st)
            ON CONFLICT (account_id, merchant_key, direction) DO UPDATE SET
                name = EXCLUDED.name, category = EXCLUDED.category, cadence = EXCLUDED.cadence,
                expected_amount = EXCLUDED.expected_amount, previous_amount = EXCLUDED.previous_amount,
                occurrences = EXCLUDED.occurrences, last_date = EXCLUDED.last_date, next_date = EXCLUDED.next_date,
                status = EXCLUDED.status, updated_at = now()
             WHERE recurring_series.status <> 'dismissed'"""),
            {"a": k[0], "k": k[1], "d": k[2], "name": name[:100], "cat": category, "cad": s.cadence,
             "exp": s.expected, "prev": previous, "cur": currency, "occ": s.occurrences,
             "last": s.last, "next": s.next, "st": status})
        n += 1
    return n
