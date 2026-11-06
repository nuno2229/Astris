"""Consumption analytics (E13). All figures are household roll-ups of accounts shared 'in totals' (attribution-aware,
E13-05), transfers excluded, converted to the viewer's home currency with the household's rates (never guessed)."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import DB, User
from app.routers.fx import convert, rate_table
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["insights"])
Q = Decimal("0.01")
SEASON_RATIO = Decimal("1.25")   # a month is "seasonal" if ≥25% above that category's typical month
SEASON_MIN = Decimal("20")       # …and at least this much more in absolute terms


class CategoryRow(BaseModel):
    category: str
    spent: Decimal
    typical: Decimal | None        # average of the previous 3 months (None if no history)
    change: Decimal | None         # spent / typical - 1
    budget: Decimal | None         # monthly household budget for this category, if any
    weekly_avg: Decimal            # last 12 weeks


class MonthPoint(BaseModel):
    month: str                     # YYYY-MM
    spent: Decimal
    received: Decimal


class Named(BaseModel):
    name: str
    spent: Decimal
    count: int


class Seasonal(BaseModel):
    category: str
    month: str                     # the month it applies to (this or next), YYYY-MM
    last_year: Decimal
    typical: Decimal
    ratio: Decimal


class Consumption(BaseModel):
    product: str
    unit: str                      # "l" | "kg" | "items"
    weekly_avg: Decimal
    total: Decimal
    purchases: int
    weeks: list[Decimal]           # oldest -> newest, 12 weeks ending this week / end of the month


class Insights(BaseModel):
    currency: str
    month: str
    total: Decimal
    categories: list[CategoryRow]
    trend: list[MonthPoint]
    top_shops: list[Named]
    by_member: list[Named]
    seasonal: list[Seasonal]
    excluded: list[Named]          # 'separate' accounts: spending this month, not in the totals above
    unconverted: list[str]
    consumption: list[Consumption] = []   # quantities from scanned receipts (E13-01): e.g. litres of milk a week


def _month_start(d: date, back: int = 0) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - back, 12)
    return date(y, m + 1, 1)


def _key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


_BASE = """
  FROM transactions t
  JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h
  JOIN accounts a ON a.id = t.account_id
  JOIN memberships ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
  JOIN users u ON u.id = a.owner_user_id
 WHERE t.transfer_group IS NULL AND ha.attribution = :attr
"""


def compute(db: Session, hid: UUID, home: str, month: date) -> Insights:
    rates = rate_table(db, hid)
    missing: set[str] = set()

    def conv(v, cur) -> Decimal:
        c = convert(Decimal(v), cur, home, rates)
        if c is None:
            missing.add(cur)
            return Decimal(0)
        return c

    start, end = month, _month_start(month, -1)
    year_ago = _month_start(month, 12)
    # one pass over 13 months (+ next month last year for seasonality)
    rows = db.execute(text(f"""
        SELECT date_trunc('month', t.posted_on)::date AS m, coalesce(t.category, 'Uncategorised') AS cat, t.currency,
               sum(-t.amount) FILTER (WHERE t.amount < 0) AS out_, sum(t.amount) FILTER (WHERE t.amount > 0) AS in_
        {_BASE} AND t.posted_on >= :from AND t.posted_on < :to
         GROUP BY 1, 2, 3"""), {"h": hid, "attr": "include", "from": _month_start(month, 13), "to": end}).all()
    by_month_cat: dict = defaultdict(lambda: defaultdict(Decimal))
    trend_out: dict = defaultdict(Decimal)
    trend_in: dict = defaultdict(Decimal)
    for r in rows:
        if r.out_:
            v = conv(r.out_, r.currency)
            by_month_cat[r.m][r.cat] += v
            trend_out[r.m] += v
        if r.in_:
            trend_in[r.m] += conv(r.in_, r.currency)
    this = by_month_cat.get(start, {})
    prev3 = [_month_start(month, k) for k in (1, 2, 3)]
    have_prev = [m for m in prev3 if m in by_month_cat]
    budgets = {b.category: Decimal(b.amount) for b in db.execute(text("""
        SELECT category, amount FROM budgets WHERE household_id = :h AND scope = 'household' AND kind = 'category'
           AND period = 'monthly' AND currency = :c"""), {"h": hid, "c": home})}
    # weekly averages over the last 12 full weeks before the month ends (or today)
    wk_end = min(end, date.today() + timedelta(days=1))
    wk_start = wk_end - timedelta(weeks=12)
    weekly = defaultdict(Decimal)
    for r in db.execute(text(f"""
            SELECT coalesce(t.category, 'Uncategorised') AS cat, t.currency, sum(-t.amount) AS out_
            {_BASE} AND t.amount < 0 AND t.posted_on >= :s AND t.posted_on < :e GROUP BY 1, 2"""),
            {"h": hid, "attr": "include", "s": wk_start, "e": wk_end}):
        weekly[r.cat] += conv(r.out_, r.currency) / 12
    cats = []
    for cat in sorted(set(this) | {c for m in have_prev for c in by_month_cat[m]}, key=lambda c: -this.get(c, 0)):
        spent = this.get(cat, Decimal(0))
        typical = (sum(by_month_cat[m].get(cat, Decimal(0)) for m in have_prev) / len(have_prev)) if have_prev else None
        cats.append(CategoryRow(category=cat, spent=spent.quantize(Q), typical=typical.quantize(Q) if typical is not None else None,
                                change=((spent / typical) - 1).quantize(Decimal("0.001")) if typical else None,
                                budget=budgets.get(cat), weekly_avg=weekly.get(cat, Decimal(0)).quantize(Q)))
    trend = [MonthPoint(month=_key(_month_start(month, k)), spent=trend_out.get(_month_start(month, k), Decimal(0)).quantize(Q),
                        received=trend_in.get(_month_start(month, k), Decimal(0)).quantize(Q)) for k in range(11, -1, -1)]

    def ranked(group_sql: str, attr: str = "include", limit: int = 8) -> list[Named]:
        acc: dict = defaultdict(lambda: [Decimal(0), 0])
        for r in db.execute(text(f"""SELECT {group_sql} AS name, t.currency, sum(-t.amount) AS out_, count(*) AS n
                                     {_BASE} AND t.amount < 0 AND t.posted_on >= :s AND t.posted_on < :e GROUP BY 1, 2"""),
                            {"h": hid, "attr": attr, "s": start, "e": end}):
            acc[r.name][0] += conv(r.out_, r.currency)
            acc[r.name][1] += r.n
        return [Named(name=k, spent=v[0].quantize(Q), count=v[1])
                for k, v in sorted(acc.items(), key=lambda kv: -kv[1][0])[:limit]]

    # seasonality: this month and next, compared with the same month last year vs that category's typical month
    seasonal = []
    hist = db.execute(text(f"""
        SELECT date_trunc('month', t.posted_on)::date AS m, coalesce(t.category, 'Uncategorised') AS cat, t.currency,
               sum(-t.amount) AS out_
        {_BASE} AND t.amount < 0 AND t.posted_on >= :from AND t.posted_on < :to GROUP BY 1, 2, 3"""),
        {"h": hid, "attr": "include", "from": _month_start(month, 24), "to": _month_start(year_ago, -2)}).all()
    hm: dict = defaultdict(lambda: defaultdict(Decimal))
    for r in hist:
        hm[r.cat][r.m] += conv(r.out_, r.currency)
    for target in (year_ago, _month_start(year_ago, -1)):
        for cat, months in hm.items():
            if target not in months:
                continue
            others = [v for m, v in months.items() if m != target and _month_start(target, 12) <= m < _month_start(target, -12)]
            if len(others) < 6:
                continue
            typical = sum(others) / len(others)
            ly = months[target]
            if typical > 0 and ly >= typical * SEASON_RATIO and ly - typical >= SEASON_MIN:
                seasonal.append(Seasonal(category=cat, month=_key(_month_start(target, -12)), last_year=ly.quantize(Q),
                                         typical=typical.quantize(Q), ratio=(ly / typical).quantize(Decimal("0.01"))))
    seasonal.sort(key=lambda s: (s.month, -s.ratio))
    consumption = quantities(db, hid, min(end, date.today() + timedelta(days=1)))
    return Insights(currency=home, month=_key(start), total=sum(this.values(), Decimal(0)).quantize(Q), categories=cats,
                    trend=trend, top_shops=ranked("coalesce(t.merchant, initcap(t.merchant_key), t.description)"),
                    by_member=ranked("u.display_name"), seasonal=seasonal[:6],
                    excluded=ranked("a.name || ' (' || u.display_name || ')'", attr="hide"), unconverted=sorted(missing),
                    consumption=consumption)


WEEKS = 12


def quantities(db: Session, hid: UUID, end: date) -> list[Consumption]:
    """How much of each product the household goes through per week, from saved receipts. Volumes in litres,
    weights in kg, otherwise items. Averages use the weeks since the first receipt in the window (at least 4)."""
    from app.routers.prices import product_key
    start = end - timedelta(weeks=WEEKS)
    rows = db.execute(text("""
        SELECT i.name, i.quantity, i.size_qty, i.size_unit, coalesce(r.purchased_on, r.created_at::date) AS d
          FROM receipt_items i JOIN receipts r ON r.id = i.receipt_id
         WHERE r.household_id = :h AND r.status = 'saved'
           AND coalesce(r.purchased_on, r.created_at::date) >= :s AND coalesce(r.purchased_on, r.created_at::date) < :e"""),
        {"h": hid, "s": start, "e": end}).all()
    if not rows:
        return []
    first = min(r.d for r in rows)
    span = max(4, -(-(end - first).days // 7))
    span = min(span, WEEKS)
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = product_key(r.name)
        if not key:
            continue
        q = Decimal(r.quantity)
        if r.size_unit in ("ml", "l") and r.size_qty:
            unit, amount = "l", q * Decimal(r.size_qty) / (1000 if r.size_unit == "ml" else 1)
        elif r.size_unit in ("g", "kg") and r.size_qty:
            unit, amount = "kg", q * Decimal(r.size_qty) / (1000 if r.size_unit == "g" else 1)
        elif r.size_unit == "item" and r.size_qty:
            unit, amount = "items", q * Decimal(r.size_qty)
        else:
            unit, amount = "items", q
        g = groups.setdefault((key, unit), {"weeks": [Decimal(0)] * WEEKS, "total": Decimal(0), "n": 0})
        w = min(WEEKS - 1, max(0, (r.d - start).days // 7))
        g["weeks"][w] += amount
        g["total"] += amount
        g["n"] += 1
    out = [Consumption(product=k[:1].upper() + k[1:], unit=u, total=g["total"].quantize(Decimal("0.01")), purchases=g["n"],
                       weekly_avg=(g["total"] / span).quantize(Decimal("0.01")), weeks=[x.quantize(Decimal("0.01")) for x in g["weeks"]])
           for (k, u), g in groups.items() if g["n"] >= 2]          # bought at least twice: a habit, not a one-off
    return sorted(out, key=lambda c: (-c.purchases, -c.total))[:12]


@router.get("/households/{hid}/insights", response_model=Insights)
def insights(hid: UUID, user: User, db: DB, month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")):
    my_role(db, hid)
    home = db.execute(text("SELECT home_currency FROM users WHERE id = :u"), {"u": user.id}).scalar_one()
    m = date.fromisoformat(month + "-01") if month else date.today().replace(day=1)
    return compute(db, hid, home, m)
