import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


class ImportError_(ValueError):
    """Unparseable file; message is safe to show to the user."""


@dataclass
class ParsedTx:
    posted_on: date
    amount: Decimal               # negative = money out
    description: str
    counterparty: str | None = None
    running_balance: Decimal | None = None
    external_id: str | None = None  # e.g. OFX FITID
    original_amount: Decimal | None = None   # card used abroad: amount in the foreign currency (E08-07)
    original_currency: str | None = None


@dataclass
class ParseResult:
    format: str
    transactions: list[ParsedTx]
    currency: str | None = None
    warnings: list[str] = field(default_factory=list)


MAX_ABS = Decimal("1e14")  # numeric(18,4)


def check_amount(v: Decimal | None, what: str = "amount") -> Decimal | None:
    """Reject NaN/Infinity and values that don't fit numeric(18,4); round to 4 dp."""
    if v is None:
        return None
    if not v.is_finite() or abs(v) >= MAX_ABS:
        raise ImportError_(f"File contains an invalid {what}")
    return v.quantize(Decimal("0.0001"))


def normalise_desc(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def assign_fingerprints(txs: list[ParsedTx]) -> list[str]:
    """Stable dedupe keys. Bank-provided IDs win; otherwise (date, amount, description, n-th occurrence)
    so two identical coffees on the same day stay two rows, and re-importing the same file is a no-op."""
    seen: Counter[tuple] = Counter()
    out = []
    for t in txs:
        if t.external_id:
            out.append("id:" + t.external_id)
            continue
        key = (t.posted_on.isoformat(), str(t.amount.normalize()), normalise_desc(t.description))
        n = seen[key]
        seen[key] += 1
        out.append("h:" + hashlib.sha256("|".join((*key, str(n))).encode()).hexdigest())
    return out


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")
