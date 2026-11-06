"""Categorisation pipeline (E05). Order: the user's own learned rules -> built-in dictionary -> local LLM
(background, only for what rules couldn't place confidently). Everything runs as the account owner, so
Postgres RLS scopes every read/write."""
import logging
from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.categorise import rules
from app.categorise.llm import LlmError, label_batch

log = logging.getLogger("astris.categorise")
BATCH = 20


def apply_rules(db: Session, owner: UUID, batch_id: UUID | None = None) -> int:
    """Categorise the owner's not-yet-categorised rows with learned rules + dictionary. Fast, offline."""
    learned = {r.merchant_key: r for r in db.execute(
        text("SELECT merchant_key, merchant, category FROM merchant_rules WHERE user_id = :u"), {"u": owner})}
    rows = db.execute(text("""
        SELECT t.id, t.description, t.amount FROM transactions t JOIN accounts a ON a.id = t.account_id
         WHERE a.owner_user_id = :u AND t.category_source IS NULL
           AND (CAST(:b AS uuid) IS NULL OR t.import_batch_id = CAST(:b AS uuid))"""),
        {"u": owner, "b": str(batch_id) if batch_id else None}).all()
    for r in rows:
        res = rules.categorise(r.description, Decimal(r.amount))
        rule = learned.get(res.key)
        if rule:
            vals = dict(cat=rule.category, m=rule.merchant or res.merchant, src="user_rule", conf=Decimal(1), st="done")
        elif res.category:
            vals = dict(cat=res.category, m=res.merchant, src="rule", conf=res.confidence,
                        st="done" if res.confidence >= rules.CONFIDENT else "pending")
        else:
            vals = dict(cat=None, m=None, src=None, conf=None, st="pending")
        db.execute(text("""
            UPDATE transactions SET category = :cat, merchant = :m, merchant_key = :k,
                   category_source = CAST(:src AS category_source), category_confidence = CAST(:conf AS numeric),
                   needs_review = (CAST(:conf AS numeric) IS NULL OR CAST(:conf AS numeric) < :rev), ai_status = :st
             WHERE id = :id"""), {**vals, "k": res.key, "rev": rules.REVIEW_BELOW, "id": r.id})
    return len(rows)


def run_ai_for_owner(db: Session, owner: UUID, limit: int = 60, budget_s: float = 120) -> int:
    """Background pass for one owner (caller has bound app.user_id = owner). Bounded per round (rows and
    wall-clock budget) so one slow LLM endpoint can't starve other households; leftovers stay pending."""
    import time
    started = time.monotonic()
    apply_rules(db, owner)
    rows = db.execute(text("""
        SELECT t.id, t.account_id, t.description, t.amount, t.category_confidence
          FROM transactions t JOIN accounts a ON a.id = t.account_id
         WHERE a.owner_user_id = :u AND t.ai_status = 'pending'
         ORDER BY t.posted_on DESC LIMIT :l
         FOR UPDATE OF t SKIP LOCKED"""), {"u": owner, "l": limit}).all()
    if not rows:
        return 0
    # LLM config: first household (by share date) the account is shared into with AI turned on
    cfg_rows = db.execute(text("""
        SELECT DISTINCT ON (ha.account_id) ha.account_id, hs.llm_base_url, hs.llm_model, hs.llm_allow_internet
          FROM household_accounts ha
          JOIN household_settings hs ON hs.household_id = ha.household_id
               AND hs.llm_enabled AND hs.llm_base_url IS NOT NULL
         WHERE ha.account_id = ANY(:ids)
         ORDER BY ha.account_id, ha.shared_at"""), {"ids": list({r.account_id for r in rows})}).all()
    cfg = {c.account_id: (c.llm_base_url, c.llm_model, c.llm_allow_internet) for c in cfg_rows}

    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        groups[cfg.get(r.account_id)].append(r)
    for conf, items in groups.items():
        ids = [r.id for r in items]
        if conf is None:  # no LLM available: keep the rules result
            db.execute(text("UPDATE transactions SET ai_status = 'skipped' WHERE id = ANY(:ids)"), {"ids": ids})
            continue
        base, model, allow = conf
        for k in range(0, len(items), BATCH):
            chunk = items[k:k + BATCH]
            if time.monotonic() - started > budget_s:
                break  # rest stays 'pending' for the next round
            try:
                labels = label_batch(base, model, allow, [(r.description, Decimal(r.amount)) for r in chunk])
            except LlmError as e:
                log.warning("LLM batch failed for owner %s: %s", owner, e)
                db.execute(text("UPDATE transactions SET ai_status = 'failed' WHERE id = ANY(:ids)"),
                           {"ids": [r.id for r in chunk]})
                continue
            for i, r in enumerate(chunk):
                lab = labels.get(i)
                if lab and (r.category_confidence is None or lab.confidence > Decimal(r.category_confidence)):
                    db.execute(text("""
                        UPDATE transactions SET category = :c, merchant = COALESCE(:m, merchant),
                               category_source = 'llm', category_confidence = :conf,
                               needs_review = (:conf < :rev), ai_status = 'done'
                         WHERE id = :id AND category_source IS DISTINCT FROM 'user'"""),
                        {"c": lab.category, "m": lab.merchant, "conf": lab.confidence,
                         "rev": rules.REVIEW_BELOW, "id": r.id})
                else:
                    db.execute(text("UPDATE transactions SET ai_status = 'done' WHERE id = :id"), {"id": r.id})
    return len(rows)


def correct(db: Session, owner: UUID, tx_id: UUID, category: str, merchant: str | None, remember: bool) -> int:
    """User correction (always wins). With remember=True it becomes a personal rule and is applied to the
    owner's other transactions from the same merchant that the user hasn't set by hand. Returns that count."""
    row = db.execute(text("""UPDATE transactions SET category = :c, merchant = COALESCE(:m, merchant),
                                    category_source = 'user', category_confidence = 1, needs_review = false,
                                    ai_status = 'done'
                              WHERE id = :id RETURNING merchant_key, merchant"""),
                     {"c": category, "m": merchant, "id": tx_id}).first()
    if not row or not remember or not row.merchant_key:
        return 0
    db.execute(text("""
        INSERT INTO merchant_rules (user_id, merchant_key, merchant, category) VALUES (:u, :k, :m, :c)
        ON CONFLICT (user_id, merchant_key) DO UPDATE SET merchant = EXCLUDED.merchant, category = EXCLUDED.category,
            updated_at = now()"""), {"u": owner, "k": row.merchant_key, "m": row.merchant, "c": category})
    return db.execute(text("""
        UPDATE transactions t SET category = :c, merchant = COALESCE(:m, t.merchant), category_source = 'user_rule',
               category_confidence = 1, needs_review = false, ai_status = 'done'
          FROM accounts a
         WHERE a.id = t.account_id AND a.owner_user_id = :u AND t.merchant_key = :k AND t.id <> :id
           AND t.category_source IS DISTINCT FROM 'user'"""),
        {"c": category, "m": row.merchant, "u": owner, "k": row.merchant_key, "id": tx_id}).rowcount
