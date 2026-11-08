"""Savings pots (PO 2026-11-08). Statement lines like "To Bills pot" / "From Holiday pot" (Chase, and banks that
word it the same way) are moves into a savings pot: each pot becomes a savings account of yours, the move gets a
matching line there, and both are linked as a transfer, so it's never counted as spending or income. The pot's
balance is its starting balance (set it once in Accounts) plus every move seen on statements."""
import re
import uuid
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

POT = re.compile(r"^\s*(to|from)\s+(?:the\s+)?(.{1,50}?)\s+pot\b", re.I)                          # "To Bills pot"
SAVER = re.compile(r"^\s*(to|from)\s+((?:[\w&'-]+\s+){0,3}(?:saver|savings|isa))\s*$", re.I)        # "To Chase Saver"


def pot_name(description: str, amount) -> str | None:
    m = POT.match(description or "") or SAVER.match(description or "")
    if not m:
        return None
    if (m[1].lower() == "to") != (amount < 0):       # "to" must be money out, "from" money in
        return None
    name = " ".join(m[2].split()).title()
    return name if m.re is POT else name + "\x00"     # marker: a named saver account keeps its own name (no " pot")


def link_pots(db: Session, user_id: UUID) -> int:
    from app import audit
    rows = db.execute(text("""
        SELECT t.id, t.account_id, t.amount, t.currency, t.posted_on, t.description, a.name AS account, a.currency AS acc_cur
          FROM transactions t JOIN accounts a ON a.id = t.account_id
         WHERE a.owner_user_id = :u AND a.pot_of IS NULL AND t.transfer_group IS NULL AND t.source = 'import'
           AND t.description ~* '^\\s*(to|from)\\s+.+(\\spot\\M|(saver|savings|isa)\\s*$)'
           AND NOT EXISTS (SELECT 1 FROM transfer_dismissals d WHERE d.out_tx = t.id AND d.in_tx = t.id)   -- you said no"""),
                      {"u": user_id}).all()
    n = 0
    for r in rows:
        name = pot_name(r.description, r.amount)
        if not name:
            continue
        saver = name.endswith("\x00")
        name = name.rstrip("\x00")
        label = name if saver else f"{name} pot"
        key = name.lower()
        pot = db.execute(text("SELECT id FROM accounts WHERE pot_of = :p AND pot_key = :k"), {"p": r.account_id, "k": key}).scalar()
        if pot is None:
            pot = db.execute(text("""INSERT INTO accounts (owner_user_id, name, type, currency, institution, pot_of, pot_key)
                                     VALUES (:u, :n, 'savings', :c, :i, :p, :k) RETURNING id"""),
                             {"u": user_id, "n": label[:100], "c": r.acc_cur, "i": f"Pot in {r.account}"[:100],
                              "p": r.account_id, "k": key}).scalar_one()
            # shared wherever the main account is, the same way
            db.execute(text("""INSERT INTO household_accounts (household_id, account_id, attribution)
                               SELECT household_id, :pot, attribution FROM household_accounts
                                WHERE account_id = :a AND frozen_at IS NULL ON CONFLICT DO NOTHING"""), {"pot": pot, "a": r.account_id})
        g = uuid.uuid4()
        db.execute(text("""UPDATE transactions SET transfer_group = :g, needs_review = false, ai_status = 'done',
                                  category = CASE WHEN category_source = 'user' THEN category ELSE 'Transfers' END,
                                  category_source = CASE WHEN category_source = 'user' THEN category_source ELSE 'rule' END
                            WHERE id = :t"""), {"g": g, "t": r.id})
        db.execute(text("""INSERT INTO transactions (account_id, posted_on, amount, currency, description, category, category_source,
                                                     category_confidence, needs_review, ai_status, fingerprint, source, created_by, transfer_group)
                           VALUES (:pot, :d, :amt, :c, :desc, 'Transfers', 'rule', 1, false, 'done', :fp, 'transfer', :u, :g)"""),
                   {"pot": pot, "d": r.posted_on, "amt": -r.amount, "c": r.currency,
                    "desc": f"{'From' if r.amount < 0 else 'To'} {r.account}", "fp": f"pot:{r.id}", "u": user_id, "g": g})
        audit.log(db, user_id, "tx.transfer", "transaction", f"Moved {abs(r.amount):.2f} {r.currency} {'into' if r.amount < 0 else 'out of'} {label}",
                  entity_id=r.id, account_id=r.account_id, after={"transfer_group": g, "pot": True})
        n += 1
    return n


def remember_no(db: Session, group) -> None:
    """'Not a transfer' on a pot move: don't redo it on the next import."""
    db.execute(text("""INSERT INTO transfer_dismissals (out_tx, in_tx)
                       SELECT t.id, t.id FROM transactions t WHERE t.transfer_group = :g AND t.source = 'import'
                          AND EXISTS (SELECT 1 FROM transactions p WHERE p.transfer_group = :g AND p.fingerprint LIKE 'pot:%')
                       ON CONFLICT DO NOTHING"""), {"g": group})
