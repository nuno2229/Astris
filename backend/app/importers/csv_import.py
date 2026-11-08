"""Generic bank CSV import with header auto-detection (E03-01).
Unknown layouts fail with a clear message listing the headers; the mapping UI is E09."""
import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal

from app.importers.base import ImportError_, ParsedTx, ParseResult, decode

DATE = ("date", "transaction date", "posted date", "posting date", "booking date", "value date", "completed date")
DESC = ("description", "transaction description", "details", "narrative", "memo", "reference",
        "payee", "merchant", "name", "transaction details")
AMOUNT = ("amount", "value", "transaction amount", "amount (gbp)")
OUT = ("paid out", "money out", "debit", "debit amount", "withdrawals", "out")
IN = ("paid in", "money in", "credit", "credit amount", "deposits", "in")
BALANCE = ("balance", "running balance", "balance (gbp)")
COUNTERPARTY = ("counterparty", "payee", "merchant", "name")
ORIG_AMOUNT = ("original amount", "foreign amount", "amount (foreign)", "local amount", "transaction amount (original)")
ORIG_CCY = ("original currency", "foreign currency", "local currency", "currency (original)")


def _find(headers: list[str], names: tuple[str, ...]) -> int | None:
    norm = [h.strip().lower() for h in headers]
    for n in names:
        if n in norm:
            return norm.index(n)
    return None


def parse_amount(s: str) -> Decimal | None:
    s = (s or "").strip()
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    up = s.upper()
    if up.endswith("DR"):
        neg, s = True, s[:-2]
    elif up.endswith("CR"):
        s = s[:-2]
    s = re.sub(r"[£$€\s,]", "", s)
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    # strict: plain digits only, so NaN / Infinity / exponents can never reach the ledger
    if not re.fullmatch(r"\d{1,14}(\.\d{1,4})?", s):
        raise ImportError_(f"Couldn't read amount '{s[:20]}'")
    v = Decimal(s)
    return -v if neg else v


def _date_parser(samples: list[str], force_day_first: bool | None = None):
    """ISO first; for slashed dates decide day-first vs month-first from the data.
    Ambiguous files default to day-first (UK, D-6) and say so."""
    # ignore junk rows (footers, notes); they are skipped and reported later
    samples = [s.strip() for s in samples if re.search(r"\d", s or "")]
    iso = [s for s in samples if re.match(r"^\d{4}-\d{2}-\d{2}", s)]
    if samples and len(iso) >= 0.8 * len(samples):
        return (lambda s: date.fromisoformat(s.strip()[:10])), None
    slashed = [s for s in samples if re.match(r"^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}$", s)]
    parts = [re.split(r"[/.\-]", s) for s in slashed]
    if samples and len(slashed) >= 0.8 * len(samples):
        first_gt12 = any(int(p[0]) > 12 for p in parts)
        second_gt12 = any(int(p[1]) > 12 for p in parts)
        if force_day_first is not None:
            day_first, warn = force_day_first, None
        elif second_gt12 and not first_gt12:
            day_first, warn = False, None
        else:
            day_first = True
            warn = None if first_gt12 else "Dates were ambiguous (e.g. 03/04); read them as day/month (UK)."

        def p(s: str) -> date:
            a, b, c = (int(x) for x in re.split(r"[/.\-]", s.strip()))
            y = c + 2000 if c < 100 else c
            return date(y, b, a) if day_first else date(y, a, b)
        p.day_first = day_first  # type: ignore[attr-defined]
        return p, warn
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%b %d, %Y"):
        try:
            for s in samples[:5]:
                datetime.strptime(s, fmt)
            return (lambda s, f=fmt: datetime.strptime(s.strip(), f).date()), None
        except ValueError:
            continue
    raise ImportError_("Couldn't recognise the date format in this file")


ROLES = ("date", "description", "amount", "out", "in", "balance")


def _amex(headers: list[str]) -> bool:
    """American Express UK export: purchases positive, payments/credits negative."""
    h = {x.strip().lower() for x in headers}
    return {"appears on your statement as", "extended details", "date", "amount"} <= h


def parse_csv(raw: bytes, mapping: dict[str, str | None] | None = None, day_first: bool | None = None,
              negate: bool | None = None) -> ParseResult:
    """mapping: role -> header name chosen by the user (overrides detection). day_first forces date order.
    negate: flip every amount (card statements that list purchases as positive); None = detect (AMEX)."""
    text_ = decode(raw)
    try:
        dialect = csv.Sniffer().sniff(text_[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    # banks quote with ": the sniffer can be fooled by values like 'AT2625…' (AMEX references) and then split
    # quoted multi-line addresses into bogus rows
    dialect.quotechar, dialect.doublequote = '"', True
    rows = [r for r in csv.reader(io.StringIO(text_), dialect) if any(c.strip() for c in r)]
    # Some banks put preamble lines before the header: take the first row that has a date column
    want = (mapping or {}).get("date")
    if want:  # the user told us which column is the date: find the row that has it
        hdr_idx = next((i for i, r in enumerate(rows[:15]) if want.strip().lower() in [c.strip().lower() for c in r]), None)
    else:
        hdr_idx = next((i for i, r in enumerate(rows[:15]) if _find(r, DATE) is not None), None)
    if hdr_idx is None:
        raise ImportError_("No header row with a date column found. Headers seen: "
                           + ", ".join(rows[0] if rows else []))
    headers, body = rows[hdr_idx], rows[hdr_idx + 1:]
    di, de = _find(headers, DATE), _find(headers, DESC)
    ai, oi, ii = _find(headers, AMOUNT), _find(headers, OUT), _find(headers, IN)
    bi = _find(headers, BALANCE)
    if mapping:
        norm = [h.strip().lower() for h in headers]

        def pick(role, cur):
            if role not in mapping:
                return cur
            v = mapping[role]
            if v is None or v == "":
                return None
            if v.strip().lower() not in norm:
                raise ImportError_(f"Column '{v}' isn't in this file")
            return norm.index(v.strip().lower())
        di, de, ai = pick("date", di), pick("description", de), pick("amount", ai)
        oi, ii, bi = pick("out", oi), pick("in", ii), pick("balance", bi)
        if di is None:
            raise ImportError_("Choose which column holds the date")
    ci = _find(headers, COUNTERPARTY)
    oai, oci = _find(headers, ORIG_AMOUNT), _find(headers, ORIG_CCY)
    if ci == de:
        ci = None
    if de is None or (ai is None and oi is None and ii is None):
        raise ImportError_("Couldn't find description/amount columns. Headers seen: " + ", ".join(headers))

    parse_date, warn = _date_parser([r[di] for r in body if len(r) > di], day_first)
    hdr = lambda i: headers[i].strip() if i is not None else None  # noqa: E731
    amex = _amex(headers)
    flip = amex if negate is None else bool(negate)
    cat_i = _find(headers, ("category",)) if amex else None
    result = ParseResult(format="csv-amex" if amex else "csv", transactions=[], headers=[h.strip() for h in headers], negated=flip,
                         columns={"date": hdr(di), "description": hdr(de), "amount": hdr(ai), "out": hdr(oi),
                                  "in": hdr(ii), "balance": hdr(bi)},
                         day_first=getattr(parse_date, "day_first", None))
    if warn:
        result.warnings.append(warn)
    skipped = 0
    for r in body:
        r = r + [""] * (len(headers) - len(r))
        if not r[di].strip():
            skipped += 1
            continue
        try:
            d = parse_date(r[di])
        except (ValueError, TypeError):
            skipped += 1
            continue
        if ai is not None:
            amt = parse_amount(r[ai])
        else:
            out_ = parse_amount(r[oi]) if oi is not None else None
            in_ = parse_amount(r[ii]) if ii is not None else None
            amt = (in_ or Decimal(0)) - abs(out_ or Decimal(0))
            if out_ is None and in_ is None:
                amt = None
        if amt is None:
            skipped += 1
            continue
        oa = oc = None
        if oai is not None and oci is not None and re.fullmatch(r"[A-Za-z]{3}", r[oci].strip() or ""):
            try:
                oa, oc = parse_amount(r[oai]), r[oci].strip().upper()
            except ImportError_:
                oa = oc = None
            if oa is not None and amt < 0 and oa > 0:
                oa = -oa
            if oa is None:
                oc = None
        if flip:
            amt = -amt
            if oa is not None:
                oa = -oa
        result.transactions.append(ParsedTx(
            original_amount=oa, original_currency=oc,
            bank_category=(r[cat_i].strip()[:80] or None) if cat_i is not None and len(r) > cat_i else None,
            posted_on=d, amount=amt, description=r[de].strip() or "(no description)",
            counterparty=(r[ci].strip() or None) if ci is not None else None,
            running_balance=parse_amount(r[bi]) if bi is not None else None,
        ))
    if skipped:
        result.warnings.append(f"Skipped {skipped} row(s) without a readable date or amount.")
    if not result.transactions:
        raise ImportError_("No transactions found in this file")
    return result
