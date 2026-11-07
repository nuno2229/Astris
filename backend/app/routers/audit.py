"""Change history (E10): view, verify the hash chain, and undo changes (owner only)."""
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import audit
from app.deps import DB, User, not_found

router = APIRouter(prefix="/api/audit", tags=["audit"])

REVERSIBLE = {"tx.category", "tx.create", "tx.delete", "tx.move_to_wallet", "tx.transfer", "tx.refund", "cash.count", "account.opening",
              "import.batch", "import.run"}


class Entry(BaseModel):
    id: int
    at: datetime
    actor: str | None
    is_me: bool
    action: str
    summary: str
    reason: str | None
    account_name: str | None
    household_name: str | None
    reversed: bool
    is_reversal: bool
    can_undo: bool


class ChainStatus(BaseModel):
    label: str
    entries: int
    intact: bool
    broken_id: int | None
    head: str | None


class UndoIn(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


@router.get("", response_model=list[Entry])
def history(user: User, db: DB, household_id: UUID | None = None, account_id: UUID | None = None,
            limit: int = Query(50, ge=1, le=200), before_id: int | None = None):
    rows = db.execute(text("""
        SELECT l.id, l.at, u.display_name AS actor, (l.actor_user_id = app_uid()) AS is_me, l.action, l.summary, l.reason,
               a.name AS account_name, h.name AS household_name, (a.owner_user_id = app_uid()) AS own_account,
               EXISTS (SELECT 1 FROM audit_log r WHERE r.reverses = l.id) AS reversed, (l.reverses IS NOT NULL) AS is_reversal
          FROM audit_log l
          LEFT JOIN users u ON u.id = l.actor_user_id
          LEFT JOIN accounts a ON a.id = l.account_id
          LEFT JOIN households h ON h.id = l.household_id
         WHERE (CAST(:h AS uuid) IS NULL OR l.household_id = CAST(:h AS uuid))
           AND (CAST(:a AS uuid) IS NULL OR l.account_id = CAST(:a AS uuid))
           AND (CAST(:b AS bigint) IS NULL OR l.id < CAST(:b AS bigint))
         ORDER BY l.id DESC LIMIT :l"""),
        {"h": str(household_id) if household_id else None, "a": str(account_id) if account_id else None,
         "b": before_id, "l": limit}).all()
    return [Entry(id=r.id, at=r.at, actor=r.actor, is_me=bool(r.is_me), action=r.action, summary=r.summary, reason=r.reason,
                  account_name=r.account_name, household_name=r.household_name, reversed=r.reversed,
                  is_reversal=r.is_reversal,
                  can_undo=bool(r.own_account) and r.action in REVERSIBLE and not r.reversed and not r.is_reversal)
            for r in rows]


@router.get("/verify", response_model=list[ChainStatus])
def verify(user: User, db: DB):
    """Recompute every chain you can see. A broken chain means a past entry was altered or removed."""
    chains = db.execute(text("""
        SELECT 'account:' || a.id AS chain, a.name AS label FROM accounts a WHERE a.owner_user_id = app_uid()
        UNION ALL
        SELECT 'household:' || h.id, h.name || ' (household)' FROM households h
         WHERE app_my_role(h.id) IN ('owner', 'admin')
        UNION ALL SELECT 'user:' || app_uid(), 'Your profile'""")).all()
    out = []
    for c in chains:
        r = db.execute(text("SELECT * FROM audit_verify(:c)"), {"c": c.chain}).one()
        if r.entries:
            out.append(ChainStatus(label=c.label, entries=r.entries, intact=r.broken_id is None,
                                   broken_id=r.broken_id, head=r.head))
    return out


def _changed():
    return HTTPException(status.HTTP_409_CONFLICT, "This has changed since, so it can't be undone automatically.")


@router.post("/{eid}/undo", response_model=Entry)
def undo(eid: int, body: UndoIn, user: User, db: DB):
    e = db.execute(text("""SELECT l.*, a.owner_user_id FROM audit_log l LEFT JOIN accounts a ON a.id = l.account_id
                            WHERE l.id = :id"""), {"id": eid}).first()
    if e is None:
        raise not_found("History entry not found")
    if e.owner_user_id != user.id or e.action not in REVERSIBLE or e.reverses is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This change can't be undone")
    if db.execute(text("SELECT 1 FROM audit_log WHERE reverses = :id"), {"id": eid}).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Already undone")
    before, after = e.before or {}, e.after or {}
    summary = "Undid: " + e.summary

    if e.action == "tx.category":
        cur = db.execute(text("SELECT category, merchant FROM transactions WHERE id = :t"), {"t": e.entity_id}).first()
        if cur is None or cur.category != after.get("category"):
            raise _changed()
        db.execute(text("""UPDATE transactions SET category = :c, merchant = :m, category_source = CAST(:s AS category_source),
                                  category_confidence = CAST(:conf AS numeric), needs_review = :r WHERE id = :t"""),
                   {"c": before.get("category"), "m": before.get("merchant"), "s": before.get("category_source"),
                    "conf": before.get("category_confidence"), "r": before.get("needs_review", False), "t": e.entity_id})
        rule = after.get("rule_key")
        if rule:
            prev = before.get("rule")
            if prev:
                db.execute(text("UPDATE merchant_rules SET category = :c, merchant = :m WHERE user_id = :u AND merchant_key = :k"),
                           {"c": prev["category"], "m": prev.get("merchant"), "u": user.id, "k": rule})
            else:
                db.execute(text("DELETE FROM merchant_rules WHERE user_id = :u AND merchant_key = :k"), {"u": user.id, "k": rule})
    elif e.action == "tx.create":
        if not db.execute(text("DELETE FROM transactions WHERE id = :t AND source = 'manual'"), {"t": e.entity_id}).rowcount:
            raise _changed()
    elif e.action == "tx.delete":
        if db.execute(text("SELECT 1 FROM transactions WHERE id = :t"), {"t": e.entity_id}).first():
            raise _changed()
        row = dict(before)
        row["import_batch_id"] = None
        db.execute(text("INSERT INTO transactions SELECT * FROM jsonb_populate_record(NULL::transactions, CAST(:j AS jsonb))"),
                   {"j": json.dumps(row)})
    elif e.action == "tx.move_to_wallet":
        g = after.get("transfer_group")
        if not db.execute(text("SELECT 1 FROM transactions WHERE transfer_group = :g"), {"g": g}).first():
            raise _changed()
        db.execute(text("DELETE FROM transactions WHERE transfer_group = :g AND source = 'transfer'"), {"g": g})
        db.execute(text("UPDATE transactions SET transfer_group = NULL WHERE transfer_group = :g"), {"g": g})
    elif e.action == "tx.transfer":
        g = after.get("transfer_group")
        if not db.execute(text("SELECT 1 FROM transactions WHERE transfer_group = :g"), {"g": g}).first():
            raise _changed()
        db.execute(text("SELECT unlink_transfer(:g)"), {"g": g})
        from app.categorise.service import apply_rules
        apply_rules(db, user.id)
    elif e.action == "tx.refund":
        from app import refunds
        if str(db.execute(text("SELECT refund_of FROM transactions WHERE id = :t"), {"t": e.entity_id}).scalar()) != str(after.get("refund_of")):
            raise _changed()
        refunds.unlink(db, e.entity_id, dismiss=True)
        from app.categorise.service import apply_rules
        apply_rules(db, user.id)
    elif e.action == "cash.count":
        if not db.execute(text("DELETE FROM transactions WHERE id = :t AND source = 'adjustment'"),
                          {"t": after.get("adjustment_id")}).rowcount:
            raise _changed()
    elif e.action == "account.opening":
        cur = db.execute(text("SELECT opening_balance, opening_date FROM accounts WHERE id = :a"), {"a": e.account_id}).one()
        if str(cur.opening_balance.normalize()) != str(__import__("decimal").Decimal(after["balance"]).normalize()) \
                or str(cur.opening_date) != str(after["date"]):
            raise _changed()
        db.execute(text("UPDATE accounts SET opening_balance = :b, opening_date = :d WHERE id = :a"),
                   {"b": before.get("balance", 0), "d": before.get("date"), "a": e.account_id})
    elif e.action == "import.batch":
        if not db.execute(text("DELETE FROM import_batches WHERE id = :b"), {"b": after.get("batch_id")}).rowcount:
            raise _changed()
    elif e.action == "import.run":
        if not db.execute(text("DELETE FROM import_runs WHERE id = :r"), {"r": after.get("run_id")}).rowcount:
            raise _changed()
    if e.action in ("import.batch", "import.run") and e.account_id:
        from app.recurring import detect
        detect(db, user.id, e.account_id)
    nid = audit.log(db, user.id, "undo", e.entity, summary, entity_id=e.entity_id, account_id=e.account_id,
                    household_id=e.household_id, reason=body.reason, reverses=eid)
    return history(user, db, None, None, 1, nid + 1)[0]
