"""Minimal iCalendar reader for known bills/income (G-5). Event text is data: only a name, a date rule and an
amount are taken from it. Supports DTSTART (date or date-time), RRULE FREQ/INTERVAL/UNTIL/COUNT."""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

MAX_EVENTS = 500
INCOME = re.compile(r"\b(salary|wages?|pay ?day|payroll|income|pension|dividends?|benefit|child benefit|refund|rent in)\b", re.I)
AMOUNT = re.compile(r"(?:[£€$]\s?|\b(?:gbp|eur|usd)\s?)(\d{1,7}(?:,\d{3})*(?:\.\d{1,2})?)|(\d{1,7}(?:,\d{3})*\.\d{2})\b", re.I)
CUR = {"£": "GBP", "€": "EUR", "$": "USD", "gbp": "GBP", "eur": "EUR", "usd": "USD"}


@dataclass
class Item:
    uid: str
    name: str
    amount: Decimal | None
    currency: str | None
    direction: str
    start: date
    cadence: str
    every: int
    until: date | None


def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(v: str) -> str:
    return v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\;", ";").replace("\\\\", "\\").strip()


def _date(v: str) -> date | None:
    m = re.match(r"(\d{4})(\d{2})(\d{2})", v.strip())
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def _amount(text: str) -> tuple[Decimal | None, str | None]:
    m = AMOUNT.search(text)
    if not m:
        return None, None
    raw = (m[1] or m[2]).replace(",", "")
    try:
        v = Decimal(raw)
    except InvalidOperation:
        return None, None
    sym = re.match(r"[£€$]|gbp|eur|usd", m[0].strip(), re.I)
    return v, CUR.get(sym[0].lower()) if sym else None


def parse(text: str) -> list[Item]:
    items: list[Item] = []
    ev: dict | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            ev = {}
            continue
        if line == "END:VEVENT" and ev is not None:
            start = _date(ev.get("DTSTART", ""))
            name = _unescape(ev.get("SUMMARY", ""))[:120]
            if start and name:
                cadence, every, until = "once", 1, None
                rule = dict(p.split("=", 1) for p in ev.get("RRULE", "").split(";") if "=" in p)
                freq = rule.get("FREQ", "").upper()
                if freq in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
                    cadence = freq.lower()
                    try:
                        every = max(1, min(52, int(rule.get("INTERVAL", "1"))))
                    except ValueError:
                        every = 1
                    until = _date(rule.get("UNTIL", ""))
                    if not until and rule.get("COUNT", "").isdigit():
                        n = int(rule["COUNT"]) - 1
                        until = _advance(start, cadence, every * n)
                amount, cur = _amount(name + " " + _unescape(ev.get("DESCRIPTION", "")))
                text_all = name + " " + _unescape(ev.get("DESCRIPTION", ""))
                direction = "in" if INCOME.search(text_all) or re.search(r"\+\s?[£€$]?\d", text_all) else "out"
                uid = (ev.get("UID") or f"{name}|{start}")[:300]
                shown = re.sub(r"\s{2,}", " ", AMOUNT.sub("", name).replace("+", " ")).strip(" -–:") or name   # "Rent £950" -> "Rent"
                items.append(Item(uid, shown, amount, cur, direction, start, cadence, every, until))
                if len(items) >= MAX_EVENTS:
                    break
            ev = None
            continue
        if ev is not None and ":" in line:
            key, val = line.split(":", 1)
            ev.setdefault(key.split(";", 1)[0].upper(), val)
    return items


def _advance(d: date, cadence: str, n: int) -> date:
    if cadence == "daily":
        return d + timedelta(days=n)
    if cadence == "weekly":
        return d + timedelta(weeks=n)
    if cadence == "monthly":
        y, m = divmod(d.month - 1 + n, 12)
        y += d.year
        import calendar
        return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))
    if cadence == "yearly":
        try:
            return d.replace(year=d.year + n)
        except ValueError:
            return d.replace(year=d.year + n, day=28)
    return d


def next_on_or_after(start: date, cadence: str, every: int, until: date | None, today: date) -> date | None:
    if cadence == "once":
        return start if start >= today else None
    d, k = start, 0
    step = {"daily": 1, "weekly": 7, "monthly": 30, "yearly": 365}[cadence] * every
    if start < today:          # jump close, then walk
        k = max(0, (today - start).days // step - 1) * every
        d = _advance(start, cadence, k)
    while d < today:
        k += every
        d = _advance(start, cadence, k)
    return None if until and d > until else d
