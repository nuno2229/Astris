"""Receipts scanned before the statement arrived (PO 2026-11-09: never count a purchase twice, link them instead).

A receipt is never spending on its own: totals only ever come from bank lines. When a statement is imported later,
each saved receipt that isn't linked yet is attached to its card payment if there is exactly one clear match (same
amount within 5p, within 3 days, preferably the same shop, on the importer's own accounts), then split by what was
bought (P-16)."""
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def link_waiting_receipts(db: Session, user_id: UUID) -> int:
    from app.receipts.split import split_payment
    rows = db.execute(text("""
        SELECT r.id, r.household_id, r.total, r.store, coalesce(r.purchased_on, r.created_at::date) AS d FROM receipts r
         WHERE r.status = 'saved' AND r.transaction_id IS NULL AND r.total IS NOT NULL AND r.total > 0
           AND coalesce(r.purchased_on, r.created_at::date) >= current_date - 120
           AND r.household_id IN (SELECT household_id FROM memberships WHERE user_id = :u)"""), {"u": user_id}).all()
    n = 0
    for r in rows:
        cands = db.execute(text("""
            SELECT t.id, (lower(coalesce(t.merchant, t.description)) LIKE '%' || lower(coalesce(:store, '~')) || '%') AS same_store
              FROM transactions t JOIN accounts a ON a.id = t.account_id
             WHERE a.owner_user_id = :u AND t.amount BETWEEN :lo AND :hi AND t.posted_on BETWEEN :d1 AND :d2
               -- only accounts you've shared into the receipt's household: a private account is never revealed
               AND t.account_id IN (SELECT account_id FROM household_accounts WHERE household_id = :h AND frozen_at IS NULL)
               AND t.transfer_group IS NULL
               AND NOT EXISTS (SELECT 1 FROM receipts x WHERE x.transaction_id = t.id)"""),
            {"u": user_id, "h": r.household_id, "store": r.store, "lo": -r.total - Decimal("0.05"), "hi": -r.total + Decimal("0.05"),
             "d1": r.d - timedelta(days=3), "d2": r.d + timedelta(days=3)}).all()
        same = [c for c in cands if c.same_store]
        pick = same if same else cands
        if len(pick) != 1:
            continue                                   # none, or ambiguous: leave it for you to pick on the receipt
        db.execute(text("UPDATE receipts SET transaction_id = :t WHERE id = :r AND transaction_id IS NULL"), {"t": pick[0].id, "r": r.id})
        split_payment(db, r.id, user_id)
        n += 1
    return n
