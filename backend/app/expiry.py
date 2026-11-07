"""Use-by / best-before dates read from a photo of the packaging (P-7 extension, PO 2026-11-07).
OCR runs in the same sandboxed extractor as receipts; this module only finds dates in the text."""
import calendar
import re
from datetime import date

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})
KEYWORDS = re.compile(r"\b(use\s*by|best\s*before|bb|bbe|exp(iry|ires)?|bb\s*end)\b", re.I)
SHOP_ONLY = re.compile(r"\b(display\s*until|sell\s*by)\b", re.I)        # for the shop, not you: lowest priority
MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"


def _year(y: str | None, today: date) -> int:
    if not y:
        return today.year
    y = int(y)
    return 2000 + y if y < 100 else y


def _ok(d: date, today: date) -> bool:
    return date(today.year - 1, 1, 1) <= d <= date(today.year + 6, 12, 31)


def find_dates(lines: list[str], today: date | None = None) -> list[date]:
    """Candidate dates, likeliest first: ones next to 'use by'/'best before', then the soonest plausible."""
    today = today or date.today()
    found: list[tuple[int, date]] = []
    for line in lines:
        s = re.sub(r"(?<=[\d./-])[Oo]|[Oo](?=[\d./-])", "0", line)   # OCR often reads 0 as O inside dates
        s_low = s.lower()
        near = 3 if SHOP_ONLY.search(line) else 0 if KEYWORDS.search(line) else 1
        for m in re.finditer(r"\b(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2,4})\b", s):       # 12/11/26, 12.11.2026
            try:
                d = date(_year(m[3], today), int(m[2]), int(m[1]))
                if _ok(d, today):
                    found.append((near, d))
            except ValueError:
                pass
        for m in re.finditer(rf"\b(\d{{1,2}})\s*{MON}\s*(\d{{2,4}})?\b", s_low):                      # 12 NOV 26, 12NOV
            try:
                d = date(_year(m[3], today), MONTHS[m[2][:3]], int(m[1]))
                if not m[3] and d < today:
                    d = d.replace(year=d.year + 1)
                if _ok(d, today):
                    found.append((near, d))
            except (ValueError, KeyError):
                pass
        for m in re.finditer(rf"(?<!\d\s)(?<!\d)\b{MON}\s*(\d{{4}}|\d{{2}})\b", s_low):               # MAR 2027 = end of March
            try:
                y, mo = _year(m[2], today), MONTHS[m[1][:3]]
                d = date(y, mo, calendar.monthrange(y, mo)[1])
                if _ok(d, today):
                    found.append((near + 1, d))
            except (ValueError, KeyError):
                pass
        for m in re.finditer(r"\b(\d{1,2})\s*/\s*(20\d{2})\b", s):                                     # 03/2027
            try:
                y, mo = int(m[2]), int(m[1])
                d = date(y, mo, calendar.monthrange(y, mo)[1])
                if _ok(d, today):
                    found.append((near + 1, d))
            except ValueError:
                pass
    seen, out = set(), []
    for _, d in sorted(found, key=lambda x: (x[0], x[1] < today, x[1])):
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out[:5]
