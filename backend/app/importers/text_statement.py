"""Line-based statement reader for PDF text and OCR output (E03-04, E03-05).

Bank PDFs differ a lot, so this reads lines that start with a date and end with money values:
  * one money value        -> amount
  * two or more            -> amount, then running balance (last value)
Sign comes from the running balance when there is one (balance went down = money out), which works
for UK "Paid out / Paid in / Balance" layouts. Otherwise an explicit '-', 'DR' or brackets; anything
still unknown is treated as money out and counted in a warning so the user can check it.
"""
import re
from collections import Counter
from datetime import date
from decimal import Decimal

from app.importers.base import ImportError_, ParsedTx, ParseResult

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
DATE_RE = re.compile(
    rf"^\s*(?P<d>(?:\d{{4}}-\d{{2}}-\d{{2}})|(?:\d{{1,2}}[/.\-]\d{{1,2}}[/.\-]\d{{2,4}})|(?:\d{{1,2}}\s+{_MON}(?:\s+\d{{2,4}})?))\b",
    re.I)
# money needs exactly 2 decimals, so dates, sort codes and reference numbers don't match
MONEY_RE = re.compile(r"(?<![\w.])(?P<neg>-)?[£$€]?\s?(?P<paren>\()?(?P<num>\d{1,3}(?:,\d{3})*|\d+)\.(?P<dec>\d{2})\)?(?:\s?(?P<crdr>CR|DR)\b)?",
                      re.I)
SKIP_RE = re.compile(r"\b(total|summary|page \d|statement (date|period)|sort code|account (number|no)|iban|bic)\b", re.I)
CLOSING_RE = re.compile(r"(closing balance|carried forward|balance c/?f)", re.I)   # a summary line, never a transaction
OPENING_RE = re.compile(r"(brought forward|opening balance|previous balance|balance b/?f)", re.I)
YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _money(m: re.Match) -> tuple[Decimal, bool | None]:
    v = Decimal(m.group("num").replace(",", "") + "." + m.group("dec"))
    neg = bool(m.group("neg")) or bool(m.group("paren")) or (m.group("crdr") or "").upper() == "DR"
    explicit = True if neg else (False if (m.group("crdr") or "").upper() == "CR" else None)
    return v, explicit  # explicit: True = out, False = in, None = unknown


def _parse_date(s: str, day_first: bool, default_year: int | None) -> date | None:
    s = s.strip()
    try:
        if re.match(r"^\d{4}-", s):
            return date.fromisoformat(s[:10])
        m = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$", s)
        if m:
            a, b, y = int(m[1]), int(m[2]), int(m[3])
            y = y + 2000 if y < 100 else y
            return date(y, b, a) if day_first else date(y, a, b)
        m = re.match(rf"^(\d{{1,2}})\s+({_MON})(?:\s+(\d{{2,4}}))?$", s, re.I)
        if m:
            y = int(m[3]) if m[3] else default_year
            if y is None:
                return None
            y = y + 2000 if y < 100 else y
            return date(y, _MONTHS[m[2][:3].lower()], int(m[1]))
    except ValueError:
        return None
    return None


def parse_statement_lines(lines: list[str], fmt: str) -> ParseResult:
    years = Counter(int(y) for ln in lines for y in YEAR_RE.findall(ln))
    default_year = years.most_common(1)[0][0] if years else None
    # day/month order: any first part > 12 means day-first; UK default otherwise (D-6)
    slashed = [re.match(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-]", ln) for ln in lines]
    firsts = [int(m[1]) for m in slashed if m]
    seconds = [int(m[2]) for m in slashed if m]
    day_first = not (any(s > 12 for s in seconds) and not any(f > 12 for f in firsts))

    result = ParseResult(format=fmt, transactions=[])
    prev_bal: Decimal | None = None
    cur_date: date | None = None
    guessed = 0
    for ln in lines:
        if not ln.strip():
            continue
        monies = list(MONEY_RE.finditer(ln))
        if OPENING_RE.search(ln) and monies:
            prev_bal = _money(monies[-1])[0] * (-1 if _money(monies[-1])[1] else 1)
            continue
        if SKIP_RE.search(ln) or CLOSING_RE.search(ln) or not monies:
            continue
        dm = DATE_RE.match(ln)
        if dm:
            d = _parse_date(dm["d"], day_first, default_year)
            if d is None:
                continue
            cur_date = d
            desc_start = dm.end()
        elif cur_date is not None:
            desc_start = 0  # continuation row under the previous date (common in UK PDFs)
        else:
            continue
        desc = re.sub(r"\s+", " ", ln[desc_start:monies[0].start()]).strip(" -|")
        if not desc or desc.lower().startswith("balance"):
            continue
        amt, explicit = _money(monies[-2] if len(monies) >= 2 else monies[0])
        bal = None
        if len(monies) >= 2:
            b, b_out = _money(monies[-1])
            bal = -b if b_out else b
        sign: int | None = None
        if bal is not None and prev_bal is not None:
            if abs(prev_bal - amt - bal) < Decimal("0.01"):
                sign = -1
            elif abs(prev_bal + amt - bal) < Decimal("0.01"):
                sign = 1
        if sign is None and explicit is not None:
            sign = -1 if explicit else 1
        if sign is None:
            sign = -1
            guessed += 1
        desc = re.sub(r"\s+[+\-−]$", "", desc).strip() or desc     # a sign that belonged to the amount column
        result.transactions.append(ParsedTx(posted_on=cur_date, amount=sign * amt, description=desc[:500],
                                            running_balance=bal))
        if bal is not None:
            prev_bal = bal
    if not result.transactions:
        raise ImportError_("Couldn't find any transactions in this statement. "
                           "If it's a scan, try a clearer photo; otherwise export CSV or OFX from your bank.")
    if guessed:
        result.warnings.append(f"{guessed} transaction(s) had no clear in/out direction and were read as money out. "
                               "Check them in Activity.")
    if not default_year and any(DATE_RE.match(ln) and re.search(_MON, ln, re.I) for ln in lines):
        result.warnings.append("The statement didn't show a year; dates may be off.")
    return result
