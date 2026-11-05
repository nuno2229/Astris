"""MT940 (SWIFT bank statement) import — E03-03."""
from decimal import Decimal

import mt940

from app.importers.base import ImportError_, ParsedTx, ParseResult, decode


def parse_mt940(raw: bytes) -> ParseResult:
    try:
        # Transactions().parse() takes text only; mt940.parse() would treat a path-like string as a file to open
        stmts = mt940.models.Transactions()
        stmts.parse(decode(raw))
    except Exception as e:  # library raises varied errors on malformed input
        raise ImportError_(f"Couldn't read this MT940 file ({type(e).__name__})")
    result = ParseResult(format="mt940", transactions=[])
    for t in stmts:
        d = t.data
        amt = d.get("amount")
        if amt is None or d.get("date") is None:
            continue
        details = (d.get("transaction_details") or "").replace("\n", " ").strip()
        ref = (d.get("customer_reference") or "").strip()
        desc = details or (ref if ref and ref != "NONREF" else "") or "(no description)"
        result.transactions.append(ParsedTx(
            posted_on=d["date"], amount=Decimal(str(amt.amount)), description=desc[:500]))
        result.currency = result.currency or getattr(amt, "currency", None)
    if not result.transactions:
        raise ImportError_("No transactions found in this file")
    closing = stmts.data.get("final_closing_balance")
    if closing is not None:
        last = max(range(len(result.transactions)), key=lambda k: (result.transactions[k].posted_on, k))
        result.transactions[last].running_balance = Decimal(str(closing.amount.amount))
    return result
