"""Audit trail helper (E10). Append-only; hashes are filled in by a database trigger."""
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def _j(v: Any) -> str | None:
    return None if v is None else json.dumps(v, default=str, sort_keys=True)


def log(db: Session, actor: UUID, action: str, entity: str, summary: str, *, entity_id: UUID | None = None,
        account_id: UUID | None = None, household_id: UUID | None = None, before: Any = None, after: Any = None,
        reason: str | None = None, reverses: int | None = None) -> int:
    return db.execute(text("""
        INSERT INTO audit_log (actor_user_id, household_id, account_id, action, entity, entity_id, summary, reason,
                               before, after, reverses)
        VALUES (:actor, :h, :a, :action, :entity, :eid, :summary, :reason, CAST(:before AS jsonb), CAST(:after AS jsonb), :rev)
        RETURNING id"""),
        {"actor": actor, "h": household_id, "a": account_id, "action": action, "entity": entity, "eid": entity_id,
         "summary": summary[:300], "reason": (reason or "").strip()[:300] or None, "before": _j(before),
         "after": _j(after), "rev": reverses}).scalar_one()


def tx_row(db: Session, tid: UUID) -> dict | None:
    r = db.execute(text("SELECT to_jsonb(t) FROM transactions t WHERE id = :t"), {"t": tid}).scalar()
    return r
