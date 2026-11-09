"""Split a card payment by what its receipt says was bought (P-16). Runs when a receipt is saved against a payment
you own and the receipt has more than one kind of thing on it. Amounts follow the receipt; any difference to the
payment (loyalty savings, bag charges, rounding) is shared out in proportion. Undo it like any split, from the payment."""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.receipts.kinds import item_category


def split_payment(db: Session, receipt_id: UUID, user_id: UUID) -> int:
    rec = db.execute(text("SELECT transaction_id FROM receipts WHERE id = :r"), {"r": receipt_id}).first()
    if not rec or not rec.transaction_id:
        return 0
    t = db.execute(text("""SELECT t.id, t.amount, t.category, t.account_id, t.transfer_group, a.owner_user_id,
                                  EXISTS (SELECT 1 FROM transaction_splits s WHERE s.transaction_id = t.id) AS split
                             FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = :t"""),
                   {"t": rec.transaction_id}).first()
    if t is None or t.owner_user_id != user_id or t.split or t.transfer_group or Decimal(t.amount) >= 0:
        return 0                                    # only your own, unsplit spending
    default = t.category if t.category and t.category not in ("Other", "Shopping") else "Groceries"
    by: dict[str, Decimal] = defaultdict(Decimal)
    for it in db.execute(text("SELECT name, line_price, discount FROM receipt_items WHERE receipt_id = :r"), {"r": receipt_id}):
        paid = Decimal(it.line_price) + Decimal(it.discount)
        if paid > 0:
            by[item_category(it.name, default)] += paid
    if len(by) < 2:
        return 0
    total_paid = -Decimal(t.amount)
    receipt_total = sum(by.values(), Decimal(0))
    if receipt_total <= 0 or abs(receipt_total - total_paid) > max(Decimal("5"), total_paid * Decimal("0.1")):
        return 0                                    # receipt and payment too far apart: don't guess
    parts, running = [], Decimal(0)
    cats = sorted(by.items(), key=lambda kv: -kv[1])
    for i, (cat, v) in enumerate(cats):
        share = total_paid - running if i == len(cats) - 1 else (v * total_paid / receipt_total).quantize(Decimal("0.01"), ROUND_HALF_UP)
        running += share
        if share > 0:
            parts.append((cat, -share))
    if len(parts) < 2:
        return 0
    for i, (cat, amt) in enumerate(parts):
        db.execute(text("""INSERT INTO transaction_splits (transaction_id, position, amount, category, note)
                           VALUES (:t, :i, :a, :c, 'From the receipt')"""), {"t": t.id, "i": i, "a": amt, "c": cat})
    from app import audit
    audit.log(db, user_id, "tx.split", "transaction", f"Split a payment from its receipt into {len(parts)} categories",
              entity_id=t.id, account_id=t.account_id, after=[{"category": c, "amount": str(a)} for c, a in parts])
    return len(parts)
