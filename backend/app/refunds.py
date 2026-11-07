"""Refunds (P-2): money back from a shop you paid earlier is linked to that purchase. A linked refund takes the
purchase's category and counts as negative spending in it, not as income. Only within one person's own accounts
(refunds go back to the card you paid with, or another of your accounts)."""
import re
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

WINDOW_DAYS = 120
_RETURN_WORDS = re.compile(r"\b(refunds?|rfnd|return(s|ed)?|reversal|reversed|credit|cr|rev)\b")

# Spending/income SQL fragments shared by every total (ledger, insights, budgets, assistant, notifications):
# a linked refund is negative spending, never income.
SPEND = "(t.amount < 0 OR t.refund_of IS NOT NULL)"
INCOME = "(t.amount > 0 AND t.refund_of IS NULL)"


def base_key(key: str | None) -> str:
    return " ".join(_RETURN_WORDS.sub(" ", key or "").split())


def candidates(db: Session, user_id: UUID, tx_id: UUID) -> list:
    """Purchases a money-in line could be refunding: same shop, your accounts, up to 120 days earlier, at least
    as much. Most recent first."""
    t = db.execute(text("""SELECT t.id, t.amount, t.currency, t.posted_on, t.merchant_key FROM transactions t
                            JOIN accounts a ON a.id = t.account_id WHERE t.id = :t AND a.owner_user_id = :u"""),
                   {"t": tx_id, "u": user_id}).first()
    if t is None or t.amount <= 0 or not base_key(t.merchant_key):
        return []
    rows = db.execute(text("""
        SELECT p.id, p.posted_on, p.amount, p.description, p.merchant, p.category, a.name AS account, p.merchant_key
          FROM transactions p JOIN accounts a ON a.id = p.account_id
         WHERE a.owner_user_id = :u AND p.amount < 0 AND p.amount <= -:amt AND p.currency = :c
           AND p.transfer_group IS NULL AND p.posted_on BETWEEN :d - :w AND :d AND p.merchant_key IS NOT NULL
         ORDER BY p.posted_on DESC, p.amount DESC LIMIT 200"""),
        {"u": user_id, "amt": t.amount, "c": t.currency, "d": t.posted_on, "w": WINDOW_DAYS}).all()
    key = base_key(t.merchant_key)
    hits = [r for r in rows if base_key(r.merchant_key) == key]
    # an exact-amount purchase is the likeliest; then the most recent one big enough
    return sorted(hits, key=lambda r: (-r.amount != t.amount, -r.posted_on.toordinal()))


def link(db: Session, refund_id: UUID, original_id: UUID) -> None:
    db.execute(text("""
        UPDATE transactions r SET refund_of = o.id, not_refund = false, needs_review = false, ai_status = 'done',
               category = CASE WHEN r.category_source = 'user' THEN r.category ELSE o.category END,
               category_source = CASE WHEN r.category_source = 'user' THEN r.category_source ELSE 'rule' END
          FROM transactions o WHERE r.id = :r AND o.id = :o"""), {"r": refund_id, "o": original_id})


def unlink(db: Session, refund_id: UUID, dismiss: bool) -> None:
    db.execute(text("""
        UPDATE transactions SET refund_of = NULL, not_refund = :d,
               category = CASE WHEN category_source = 'rule' THEN NULL ELSE category END,
               category_source = CASE WHEN category_source = 'rule' THEN NULL ELSE category_source END
         WHERE id = :r"""), {"r": refund_id, "d": dismiss})


def auto_link(db: Session, user_id: UUID) -> int:
    """After an import: money in from a shop you paid in the last 120 days becomes a refund of that purchase.
    Lines you categorised yourself, transfers, and lines you said aren't refunds are left alone."""
    from app import audit
    rows = db.execute(text("""
        SELECT t.id, t.account_id, t.amount, t.currency, t.description FROM transactions t JOIN accounts a ON a.id = t.account_id
         WHERE a.owner_user_id = :u AND t.amount > 0 AND t.refund_of IS NULL AND NOT t.not_refund AND t.transfer_group IS NULL
           AND t.merchant_key IS NOT NULL AND t.category_source IS DISTINCT FROM 'user'
           AND t.category_source IS DISTINCT FROM 'user_rule' AND coalesce(t.category, '') NOT IN ('Income', 'Transfers')
           AND t.posted_on >= current_date - 400"""), {"u": user_id}).all()
    n = 0
    for r in rows:
        c = candidates(db, user_id, r.id)
        if not c:
            continue
        link(db, r.id, c[0].id)
        audit.log(db, user_id, "tx.refund", "transaction",
                  f"Refund of {r.amount:.2f} {r.currency} linked to {c[0].merchant or c[0].description} on {c[0].posted_on}",
                  entity_id=r.id, account_id=r.account_id, after={"refund_of": c[0].id, "auto": True})
        n += 1
    return n
