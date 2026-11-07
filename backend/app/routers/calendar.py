"""Known bills and income from a calendar (G-5): upload an .ics file, or add a calendar link (opt-in, uses the
internet when it's an outside address). Items show in "Coming up" and in Bills."""
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import audit
from app.calendar_ics import next_on_or_after, parse
from app.deps import DB, User, not_found
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["calendar"])
MAX_BYTES = 2 * 1024 * 1024
STALE = timedelta(hours=24)


class ItemOut(BaseModel):
    id: UUID
    name: str
    amount: Decimal | None
    currency: str
    direction: str
    cadence: str
    every: int
    next_date: date | None
    calendar: str


class FeedOut(BaseModel):
    id: UUID
    name: str
    has_link: bool
    mine: bool
    fetched_at: str | None
    last_error: str | None
    items: int


class CalendarOut(BaseModel):
    feeds: list[FeedOut]
    items: list[ItemOut]


class LinkIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=1000)


def _store(db: Session, hid: UUID, feed_id: UUID, raw: bytes, currency: str) -> int:
    try:
        text_ = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text_ = raw.decode("latin-1")
    if "BEGIN:VCALENDAR" not in text_[:2000]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That isn't a calendar (.ics) file")
    items = parse(text_)
    db.execute(text("DELETE FROM calendar_items WHERE feed_id = :f"), {"f": feed_id})
    today = date.today()
    n = 0
    for it in items:
        if it.cadence == "once" and not (today - timedelta(days=31) <= it.start <= today + timedelta(days=400)):
            continue
        if it.until and it.until < today:
            continue
        db.execute(text("""INSERT INTO calendar_items (household_id, feed_id, uid, name, amount, currency, direction,
                                                       start_date, cadence, every, until)
                           VALUES (:h, :f, :uid, :n, :a, :c, :d, :s, :cad, :e, :u) ON CONFLICT (feed_id, uid) DO NOTHING"""),
                   {"h": hid, "f": feed_id, "uid": it.uid, "n": it.name, "a": it.amount if it.amount is None or it.amount < Decimal("1e9") else None,
                    "c": it.currency or currency, "d": it.direction, "s": it.start, "cad": it.cadence, "e": it.every, "u": it.until})
        n += 1
    return n


def _fetch(url: str) -> bytes:
    """Calendar links are internet calendars (Google, iCloud, Outlook…): https on the standard port, public addresses
    only, so a link can't be used to probe the home network. A calendar on your own network: upload its .ics file."""
    from urllib.parse import urlsplit
    from app.netcheck import pinned_request
    u = url.strip()
    if u.lower().startswith("webcal://"):
        u = "https://" + u[9:]
    parts = urlsplit(u)
    if parts.scheme != "https" or not parts.hostname or parts.port not in (None, 443) or parts.username or parts.password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Use the calendar's https:// or webcal:// link")
    try:
        code, raw = pinned_request(u, timeout=20, max_bytes=MAX_BYTES, headers={"Accept": "text/calendar"},
                                   block_self=True, public_only=True)
    except Exception:
        code, raw = 0, b""
    if code != 200 or b"BEGIN:VCALENDAR" not in raw[:4000]:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't read a calendar from that link")   # one message, whatever went wrong
    return raw


def _home(db: Session, user) -> str:
    return db.execute(text("SELECT home_currency FROM users WHERE id = :u"), {"u": user.id}).scalar_one()


def upcoming(db: Session, hid: UUID, days: int = 14) -> list[ItemOut]:
    today = date.today()
    return [i for i in calendar_items(db, hid) if i.next_date and i.next_date <= today + timedelta(days=days)]


def calendar_items(db: Session, hid: UUID) -> list[ItemOut]:
    today = date.today()
    out = []
    for r in db.execute(text("""SELECT i.*, f.name AS calendar FROM calendar_items i JOIN calendar_feeds f ON f.id = i.feed_id
                                 WHERE i.household_id = :h"""), {"h": hid}):
        nd = next_on_or_after(r.start_date, r.cadence, r.every, r.until, today)
        out.append(ItemOut(id=r.id, name=r.name, amount=r.amount, currency=r.currency, direction=r.direction, cadence=r.cadence,
                           every=r.every, next_date=nd, calendar=r.calendar))
    return sorted(out, key=lambda i: (i.next_date is None, i.next_date or date.max, i.name))


@router.get("/households/{hid}/calendar", response_model=CalendarOut)
async def get_calendar(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    # your own calendar links are refreshed when you look, at most once a day
    for f in db.execute(text("""SELECT id, url FROM calendar_feeds WHERE household_id = :h AND created_by = :u AND url IS NOT NULL
                                  AND (fetched_at IS NULL OR fetched_at < now() - interval '24 hours')"""), {"h": hid, "u": user.id}).all():
        try:
            raw = await run_in_threadpool(_fetch, f.url)
            _store(db, hid, f.id, raw, _home(db, user))
            db.execute(text("UPDATE calendar_feeds SET fetched_at = now(), last_error = NULL WHERE id = :f"), {"f": f.id})
        except HTTPException as e:
            db.execute(text("UPDATE calendar_feeds SET fetched_at = now(), last_error = :e WHERE id = :f"), {"f": f.id, "e": str(e.detail)[:200]})
    feeds = [FeedOut(id=r.id, name=r.name, has_link=r.url is not None, mine=r.created_by == user.id,
                     fetched_at=r.fetched_at.isoformat() if r.fetched_at else None, last_error=r.last_error, items=r.n)
             for r in db.execute(text("""SELECT f.*, (SELECT count(*) FROM calendar_items i WHERE i.feed_id = f.id) AS n
                                          FROM calendar_feeds f WHERE f.household_id = :h ORDER BY f.created_at"""), {"h": hid})]
    return CalendarOut(feeds=feeds, items=calendar_items(db, hid))


@router.post("/households/{hid}/calendar/upload", response_model=CalendarOut, status_code=201)
async def upload_calendar(hid: UUID, user: User, db: DB, file: Annotated[UploadFile, File()],
                          name: Annotated[str | None, Form(max_length=80)] = None):
    my_role(db, hid)
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Calendar file too large")
    label = (name or (file.filename or "Calendar").rsplit(".", 1)[0]).strip()[:80] or "Calendar"
    fid = db.execute(text("INSERT INTO calendar_feeds (household_id, name, fetched_at) VALUES (:h, :n, now()) RETURNING id"),
                     {"h": hid, "n": label}).scalar_one()
    n = _store(db, hid, fid, raw, _home(db, user))
    audit.log(db, user.id, "calendar.add", "household", f"Added calendar {label} ({n} items)", household_id=hid)
    return await get_calendar(hid, user, db)


@router.post("/households/{hid}/calendar/link", response_model=CalendarOut, status_code=201)
async def link_calendar(hid: UUID, body: LinkIn, user: User, db: DB):
    my_role(db, hid)
    raw = await run_in_threadpool(_fetch, body.url)
    fid = db.execute(text("INSERT INTO calendar_feeds (household_id, name, url, fetched_at) VALUES (:h, :n, :u, now()) RETURNING id"),
                     {"h": hid, "n": body.name.strip(), "u": body.url.strip()}).scalar_one()
    n = _store(db, hid, fid, raw, _home(db, user))
    audit.log(db, user.id, "calendar.add", "household", f"Added calendar link {body.name.strip()} ({n} items)", household_id=hid)
    return await get_calendar(hid, user, db)


@router.delete("/calendar-feeds/{fid}", status_code=204)
def remove_calendar(fid: UUID, user: User, db: DB):
    f = db.execute(text("SELECT household_id, name FROM calendar_feeds WHERE id = :f"), {"f": fid}).first()
    if f is None:
        raise not_found("Calendar not found")
    if not db.execute(text("DELETE FROM calendar_feeds WHERE id = :f"), {"f": fid}).rowcount:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only whoever added this calendar can remove it")
    audit.log(db, user.id, "calendar.remove", "household", f"Removed calendar {f.name}", household_id=f.household_id)
