"""Transfers between a household's own accounts (P-1): money out of one account and the same amount into another
within a few days, e.g. current -> savings or a credit card repayment. Linked pairs are left out of spending,
income, budgets and insights (everything already ignores rows with a transfer_group)."""
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

WINDOW_DAYS = 4          # suggested when the two legs are this close
AUTO_DAYS = 3            # linked without asking when both legs are yours and nothing else matches


@dataclass
class Pair:
    out_id: UUID
    in_id: UUID
    out_account_id: UUID
    in_account_id: UUID
    out_account: str
    in_account: str
    out_desc: str
    in_desc: str
    out_date: date
    in_date: date
    amount: Decimal
    currency: str
    mine_both: bool
    out_mine: bool
    unique: bool


def candidates(db: Session, user_id: UUID, household_id: UUID | None = None) -> list[Pair]:
    """Unlinked opposite-amount pairs the user can see, at least one leg theirs, both legs either the same person's
    or shared into a common household (optionally: this household). Pairs the user dismissed are skipped."""
    rows = db.execute(text("""
        SELECT o.id AS out_id, i.id AS in_id, ao.id AS out_account_id, ai.id AS in_account_id, ao.name AS out_account, ai.name AS in_account,
               o.description AS out_desc, i.description AS in_desc, o.posted_on AS out_date, i.posted_on AS in_date,
               i.amount, i.currency, (ao.owner_user_id = :u AND ai.owner_user_id = :u) AS mine_both,
               (ao.owner_user_id = :u) AS out_mine
          FROM transactions o
          JOIN accounts ao ON ao.id = o.account_id
          JOIN transactions i ON i.amount = -o.amount AND i.currency = o.currency AND i.account_id <> o.account_id
               AND i.posted_on BETWEEN o.posted_on - :w AND o.posted_on + :w
          JOIN accounts ai ON ai.id = i.account_id
         WHERE o.amount < 0 AND o.transfer_group IS NULL AND i.transfer_group IS NULL
           AND o.posted_on >= current_date - 730
           AND (ao.owner_user_id = :u OR ai.owner_user_id = :u)
           AND (ao.owner_user_id = ai.owner_user_id OR EXISTS (
                SELECT 1 FROM household_accounts x JOIN household_accounts y ON y.household_id = x.household_id
                 WHERE x.account_id = ao.id AND y.account_id = ai.id))
           AND (CAST(:h AS uuid) IS NULL OR (
                EXISTS (SELECT 1 FROM household_accounts x WHERE x.account_id = ao.id AND x.household_id = CAST(:h AS uuid))
            AND EXISTS (SELECT 1 FROM household_accounts y WHERE y.account_id = ai.id AND y.household_id = CAST(:h AS uuid))))
           AND NOT EXISTS (SELECT 1 FROM transfer_dismissals d WHERE d.out_tx = o.id AND d.in_tx = i.id)
         ORDER BY o.posted_on DESC, abs(i.posted_on - o.posted_on)"""),
        {"u": user_id, "w": WINDOW_DAYS, "h": str(household_id) if household_id else None}).all()
    outs = Counter(r.out_id for r in rows)
    ins = Counter(r.in_id for r in rows)
    return [Pair(**r._mapping, unique=outs[r.out_id] == 1 and ins[r.in_id] == 1) for r in rows]


def link(db: Session, out_id: UUID, in_id: UUID) -> UUID:
    return db.execute(text("SELECT link_transfer(:o, :i)"), {"o": out_id, "i": in_id}).scalar_one()


def auto_link(db: Session, user_id: UUID) -> int:
    """After an import: link pairs between the user's own accounts that can only mean one thing."""
    from app import audit
    n = 0
    for p in candidates(db, user_id):
        if p.mine_both and p.unique and abs((p.in_date - p.out_date).days) <= AUTO_DAYS:
            g = link(db, p.out_id, p.in_id)
            audit.log(db, user_id, "tx.transfer", "transaction",
                      f"Spotted a transfer of {abs(p.amount)} {p.currency} from {p.out_account} to {p.in_account}",
                      entity_id=p.out_id, account_id=p.out_account_id, after={"transfer_group": g, "auto": True})
            n += 1
    return n
