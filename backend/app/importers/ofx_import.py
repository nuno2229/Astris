"""OFX / QFX import (E03-02). Uses the bank's FITID for dedupe."""
import io
from decimal import Decimal

from ofxparse import OfxParser

from app.importers.base import ImportError_, ParsedTx, ParseResult


def parse_ofx(raw: bytes) -> ParseResult:
    try:
        ofx = OfxParser.parse(io.BytesIO(raw))
    except Exception as e:  # ofxparse raises a variety of errors on malformed input
        raise ImportError_(f"Couldn't read this OFX/QFX file ({type(e).__name__})")
    accounts = ofx.accounts or ([ofx.account] if getattr(ofx, "account", None) else [])
    if not accounts:
        raise ImportError_("No account statement found in this OFX file")
    result = ParseResult(format="ofx", transactions=[])
    if len(accounts) > 1:
        result.warnings.append(f"File contains {len(accounts)} accounts; only the first was imported.")
    stmt = accounts[0].statement
    result.currency = (getattr(stmt, "currency", None) or "").upper() or None
    for i, t in enumerate(stmt.transactions):
        desc = " — ".join(x for x in (t.payee, t.memo) if x and x.strip()) or "(no description)"
        fitid = (t.id or "").strip() or None
        result.transactions.append(ParsedTx(
            posted_on=t.date.date(), amount=Decimal(str(t.amount)), description=desc.strip(),
            counterparty=(t.payee or "").strip() or None, external_id=fitid,
        ))
    if not result.transactions:
        raise ImportError_("No transactions found in this file")
    bal = getattr(stmt, "balance", None)
    if bal is not None:
        # The ledger balance applies to the end of the statement: attach it to the latest transaction.
        last = max(range(len(result.transactions)), key=lambda k: (result.transactions[k].posted_on, k))
        result.transactions[last].running_balance = Decimal(str(bal))
    return result
