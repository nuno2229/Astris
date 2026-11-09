"""Round-up messages (P-18): last week / last month at a glance, on the day and time each person picks.

Weekly: the 7 days before the scheduled day, compared with the average of the 8 weeks before.
Monthly: the previous calendar month, compared with the average of the 3 months before.
Covers accounts counted 'in totals' in each of the person's households; transfers left out, refunds netted."""
import calendar
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

LATE = timedelta(days=2)      # if the server was off at the scheduled time, still send within this window


def _money(v: Decimal, cur: str) -> str:
    sym = {"GBP": "£", "EUR": "€", "USD": "$"}.get(cur)
    return f"{sym}{v:,.2f}" if sym else f"{v:,.2f} {cur}"


def due(kind: str, day: int, at: time, now: datetime) -> tuple[str, date] | None:
    """(key, scheduled date) if this period's round-up is due now, else None."""
    if kind == "weekly":
        d = now.date() - timedelta(days=(now.weekday() - day) % 7)          # this week's chosen weekday, today or earlier
        key = f"w:{d.isocalendar().year}-{d.isocalendar().week:02d}"
    else:
        last = calendar.monthrange(now.year, now.month)[1]
        d = date(now.year, now.month, min(day, last))
        if d > now.date():
            pm = (now.replace(day=1) - timedelta(days=1))
            d = date(pm.year, pm.month, min(day, calendar.monthrange(pm.year, pm.month)[1]))
        key = f"m:{d:%Y-%m}"
    at_dt = datetime.combine(d, at)
    return (key, d) if at_dt <= now < at_dt + LATE else None


def _spend(db: Session, hid: UUID, s: date, e: date, home: str, rates) -> tuple[Decimal, dict[str, Decimal]]:
    from app.routers.fx import convert
    by: dict[str, Decimal] = defaultdict(Decimal)
    for r in db.execute(text("""
        SELECT coalesce(t.category, 'Uncategorised') AS cat, t.currency, sum(-t.amount) AS v FROM tx_lines t
          JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h AND ha.attribution = 'include'
               AND (ha.frozen_at IS NULL OR (t.posted_on <= ha.until AND t.created_at <= ha.frozen_at))
          JOIN accounts a ON a.id = t.account_id
          JOIN household_people ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
         WHERE t.transfer_group IS NULL AND (t.amount < 0 OR t.refund_of IS NOT NULL) AND t.posted_on >= :s AND t.posted_on < :e
         GROUP BY 1, 2"""), {"h": hid, "s": s, "e": e}):
        v = convert(Decimal(r.v), r.currency, home, rates)
        if v is not None:
            by[r.cat] += v
    return sum(by.values(), Decimal(0)), by


def household_lines(db: Session, hid: UUID, hname: str, home: str, kind: str, sched: date) -> str:
    from app.routers.fx import rate_table
    rates = rate_table(db, hid)
    if kind == "weekly":
        end = sched
        start = end - timedelta(days=7)
        prev = [(start - timedelta(days=7 * (k + 1)), start - timedelta(days=7 * k)) for k in range(8)]
        label, usual_label = "Last week", "a usual week"
    else:
        start = (sched.replace(day=1) - timedelta(days=1)).replace(day=1)
        end = sched.replace(day=1)
        prev = []
        m = start
        for _ in range(3):
            pm = (m - timedelta(days=1)).replace(day=1)
            prev.append((pm, m))
            m = pm
        label, usual_label = f"{start:%B}", "a usual month"
    total, cats = _spend(db, hid, start, end, home, rates)
    hist = [_spend(db, hid, s, e, home, rates) for s, e in prev]
    hist = [h for h in hist if h[0] > 0]
    out = f"{hname}: {label} {_money(total, home)} spent"
    if hist:
        usual = sum((h[0] for h in hist), Decimal(0)) / len(hist)
        if usual > 0:
            pct = int(round((total / usual - 1) * 100))
            out += f" ({'+' if pct > 0 else ''}{pct}% vs {usual_label}, {_money(usual, home)})"
        avg_cat: dict[str, Decimal] = defaultdict(Decimal)
        for _, by in hist:
            for c, v in by.items():
                avg_cat[c] += v / len(hist)
        changes = sorted(((c, cats.get(c, Decimal(0)) - avg_cat.get(c, Decimal(0))) for c in set(cats) | set(avg_cat)),
                         key=lambda x: -abs(x[1]))
        big = [f"{c} {'+' if d > 0 else '−'}{_money(abs(d), home)}" for c, d in changes[:3] if abs(d) >= 5]
        if big:
            out += ". Biggest changes: " + ", ".join(big)
    if kind == "monthly":
        from app.routers.goals import household_goals
        gs = [g for g in household_goals(db, hid, UUID(int=0)) if not g.archived][:3]
        if gs:
            out += ". Goals: " + ", ".join(f"{g.name} {int(Decimal(g.progress) * 100)}%" for g in gs)
    return out + "."


def events(db: Session, user: UUID, prefs, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    home = db.execute(text("SELECT home_currency FROM users WHERE id = :u"), {"u": user}).scalar() or "GBP"
    hh = db.execute(text("""SELECT h.id, h.name FROM households h JOIN memberships m ON m.household_id = h.id
                             WHERE m.user_id = :u ORDER BY h.created_at"""), {"u": user}).all()
    out = []
    for kind, on, day, at in (("weekly", prefs.weekly_roundup, prefs.weekly_day, prefs.weekly_time),
                              ("monthly", prefs.monthly_roundup, prefs.monthly_day, prefs.monthly_time)):
        if not on or not hh:
            continue
        d = due(kind, day, at, now)
        if not d:
            continue
        key, sched = d
        lines = [household_lines(db, h.id, h.name, home, kind, sched) for h in hh]
        out.append({"key": f"roundup:{key}", "minimal": f"Your {kind} round-up is ready in Astris.",
                    "detailed": f"{'Weekly' if kind == 'weekly' else 'Monthly'} round-up. " + " ".join(lines)})
    return out
