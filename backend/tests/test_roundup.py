"""P-18: weekly and monthly round-ups on a chosen day and time."""
from datetime import date, datetime, time, timedelta

from app.notify import roundup
from tests.conftest import create_account, create_household, make_user, upload


def test_due_windows():
    mon8 = time(8, 0)
    # weekly on Monday 08:00
    assert roundup.due("weekly", 0, mon8, datetime(2026, 11, 9, 7, 59)) is None           # Monday, too early
    assert roundup.due("weekly", 0, mon8, datetime(2026, 11, 9, 8, 0))[0] == "w:2026-46"
    assert roundup.due("weekly", 0, mon8, datetime(2026, 11, 10, 20, 0))[0] == "w:2026-46"  # server was off: next day still
    assert roundup.due("weekly", 0, mon8, datetime(2026, 11, 12, 9, 0)) is None            # too late, wait for next week
    # monthly on the 31st = last day; February has 28
    assert roundup.due("monthly", 31, time(19, 30), datetime(2027, 2, 28, 19, 31))[0] == "m:2027-02"
    assert roundup.due("monthly", 15, mon8, datetime(2026, 11, 14, 9, 0)) is None


def test_roundup_message_and_once_only(monkeypatch):
    from tests.test_notifications import add_channel
    a = make_user("a")
    hid = create_household(a)
    acc = create_account(a)
    a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    sched = date.today() - timedelta(days=date.today().weekday())       # this Monday
    rows = ["Date,Description,Amount"]
    for w in range(1, 9):                                               # 8 earlier weeks: £100 groceries a week
        rows.append(f"{sched - timedelta(days=7 * w + 3)},TESCO STORES 2041,-100.00")
    rows += [f"{sched - timedelta(days=2)},TESCO STORES 2041,-90.00", f"{sched - timedelta(days=3)},PRET A MANGER,-60.00"]
    upload(a, acc, "s.csv", ("\n".join(rows) + "\n").encode())
    p = a.put("/api/me/notifications/prefs", json={"weekly_roundup": True, "weekly_day": 0, "weekly_time": "08:00",
                                                   "monthly_roundup": False})
    assert p.status_code == 200 and p.json()["weekly_time"] == "08:00:00"
    add_channel(a, include_details=True)
    sent = []
    import app.notify.scheduler as S
    monkeypatch.setattr(S, "send", lambda kind, cfg, title, body: sent.append(body))
    real = roundup.events
    monkeypatch.setattr(roundup, "events", lambda db, u, prefs, now=None: real(db, u, prefs, datetime.combine(sched, time(9, 0))))
    from app.notify.scheduler import notify_user
    from app.db import bind_user, get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as db, db.begin():
        bind_user(db, a.me["id"])
        notify_user(db, a.me["id"])
        notify_user(db, a.me["id"])                                     # second run: nothing new
    msgs = [m for m in sent if "Weekly round-up" in m]
    assert len(msgs) == 1
    m = msgs[0]
    assert "Last week £150.00 spent" in m and "+50% vs a usual week, £100.00" in m and "Eating out +£60.00" in m
