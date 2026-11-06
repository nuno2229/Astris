"""Notification settings (E22). Per user. Secrets (webhook URLs, bot tokens) are write-only: the API
returns a masked hint, never the stored value."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.deps import DB, User, not_found
from app.notify.channels import ChannelError, send, validate

router = APIRouter(prefix="/api/me/notifications", tags=["notifications"])

Kind = Literal["discord", "telegram", "ntfy"]


class Prefs(BaseModel):
    renew_days: Literal[1, 3, 7] = 3
    renewals: bool = True
    trials: bool = True
    cancelled_charges: bool = True
    missed_payments: bool = True
    budgets: bool = True
    daily_summary: bool = False


class ChannelIn(BaseModel):
    kind: Kind
    label: str | None = Field(default=None, max_length=60)
    config: dict[str, str | None]
    include_details: bool = False
    allow_internet: bool = False


class ChannelPatch(BaseModel):
    enabled: bool | None = None
    include_details: bool | None = None
    label: str | None = Field(default=None, min_length=1, max_length=60)


class ChannelOut(BaseModel):
    id: UUID
    kind: Kind
    label: str
    hint: str
    uses_internet: bool
    include_details: bool
    enabled: bool
    last_sent_at: datetime | None
    last_error: str | None


class NotificationsOut(BaseModel):
    prefs: Prefs
    channels: list[ChannelOut]


class TestOut(BaseModel):
    ok: bool
    message: str


def _out(r) -> ChannelOut:
    clean = validate(r.kind, r.config, True)
    return ChannelOut(id=r.id, kind=r.kind, label=r.label, hint=clean.hint, uses_internet=clean.uses_internet,
                      include_details=r.include_details, enabled=r.enabled, last_sent_at=r.last_sent_at,
                      last_error=r.last_error)


def _channel(db: DB, cid: UUID):
    r = db.execute(text("SELECT * FROM notification_channels WHERE id = :id"), {"id": cid}).first()
    if r is None:
        raise not_found("Channel not found")
    return r


@router.get("", response_model=NotificationsOut)
def get_notifications(user: User, db: DB):
    p = db.execute(text("SELECT * FROM notification_prefs WHERE user_id = :u"), {"u": user.id}).first()
    prefs = Prefs(**{k: getattr(p, k) for k in Prefs.model_fields}) if p else Prefs()
    rows = db.execute(text("SELECT * FROM notification_channels WHERE user_id = :u ORDER BY created_at"), {"u": user.id})
    return NotificationsOut(prefs=prefs, channels=[_out(r) for r in rows])


@router.put("/prefs", response_model=Prefs)
def put_prefs(body: Prefs, user: User, db: DB):
    db.execute(text("""
        INSERT INTO notification_prefs (user_id, renew_days, renewals, trials, cancelled_charges, missed_payments,
                                        budgets, daily_summary)
        VALUES (:u, :rd, :r, :t, :c, :m, :b, :ds)
        ON CONFLICT (user_id) DO UPDATE SET renew_days = EXCLUDED.renew_days, renewals = EXCLUDED.renewals,
            trials = EXCLUDED.trials, cancelled_charges = EXCLUDED.cancelled_charges, missed_payments = EXCLUDED.missed_payments,
            budgets = EXCLUDED.budgets, daily_summary = EXCLUDED.daily_summary"""),
        {"u": user.id, "rd": body.renew_days, "r": body.renewals, "t": body.trials, "c": body.cancelled_charges,
         "m": body.missed_payments, "b": body.budgets, "ds": body.daily_summary})
    return body


@router.post("/channels", response_model=ChannelOut, status_code=201)
def add_channel(body: ChannelIn, user: User, db: DB):
    try:
        clean = validate(body.kind, body.config, body.allow_internet or body.kind in ("discord", "telegram"))
    except ChannelError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    if clean.uses_internet and not body.allow_internet:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "This channel sends alerts over the internet. Confirm that to add it.")
    import json
    cid = db.execute(text("""
        INSERT INTO notification_channels (user_id, kind, label, config, include_details, allow_internet)
        VALUES (:u, :k, :l, CAST(:c AS jsonb), :d, :ai) RETURNING id"""),
        {"u": user.id, "k": body.kind, "l": (body.label or body.kind.title()).strip()[:60],
         "c": json.dumps(clean.config), "d": body.include_details, "ai": clean.uses_internet}).scalar_one()
    return _out(_channel(db, cid))


@router.patch("/channels/{cid}", response_model=ChannelOut)
def patch_channel(cid: UUID, body: ChannelPatch, user: User, db: DB):
    _channel(db, cid)
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)  # keys from the schema only
        db.execute(text(f"UPDATE notification_channels SET {sets} WHERE id = :id"), {**fields, "id": cid})
    return _out(_channel(db, cid))


@router.delete("/channels/{cid}", status_code=204)
def delete_channel(cid: UUID, user: User, db: DB):
    _channel(db, cid)
    db.execute(text("DELETE FROM notification_channels WHERE id = :id"), {"id": cid})


_test_hits: dict = {}
TEST_LIMIT, TEST_WINDOW_S = 5, 60


def _rate_limit(user_id) -> None:
    import time
    now = time.monotonic()
    hits = [t for t in _test_hits.get(user_id, []) if now - t < TEST_WINDOW_S]
    if len(hits) >= TEST_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many test messages. Try again in a minute.")
    hits.append(now)
    _test_hits[user_id] = hits


@router.post("/channels/{cid}/test", response_model=TestOut)
def test_channel(cid: UUID, user: User, db: DB):
    r = _channel(db, cid)
    _rate_limit(user.id)
    try:
        send(r.kind, r.config, "Astris", "Test message. Notifications are working.")
    except ChannelError as e:
        db.execute(text("UPDATE notification_channels SET last_error = :e WHERE id = :id"), {"e": str(e)[:200], "id": cid})
        return TestOut(ok=False, message=str(e))
    db.execute(text("UPDATE notification_channels SET last_sent_at = now(), last_error = NULL WHERE id = :id"), {"id": cid})
    return TestOut(ok=True, message="Sent. Check your app for the test message.")
