"""G-5: known bills and income from a calendar file."""
from datetime import date, timedelta

from app.calendar_ics import next_on_or_after, parse
from tests.conftest import create_household, make_user

T = date.today()


def ics(*events):
    body = "".join("BEGIN:VEVENT\r\n" + e + "\r\nEND:VEVENT\r\n" for e in events)
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + body + "END:VCALENDAR\r\n").encode()


def test_parse_rules_amounts_and_direction():
    raw = ics("UID:1\r\nSUMMARY:Rent £950\r\nDTSTART;VALUE=DATE:20260101\r\nRRULE:FREQ=MONTHLY",
              "UID:2\r\nSUMMARY:Payday\r\nDESCRIPTION:Salary 2,100.00\r\nDTSTART:20260125T090000Z\r\nRRULE:FREQ=MONTHLY;INTERVAL=1",
              "UID:3\r\nSUMMARY:Car insurance\r\n  renewal\r\nDTSTART;VALUE=DATE:20270310",
              "UID:4\r\nSUMMARY:Bin day\r\nDTSTART;VALUE=DATE:20260105\r\nRRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=3").decode()
    items = {i.uid: i for i in parse(raw)}
    assert (items["1"].amount, items["1"].currency, items["1"].direction, items["1"].cadence) == (950, "GBP", "out", "monthly")
    assert (items["2"].amount, items["2"].direction) == (2100, "in")
    assert items["3"].name == "Car insurance renewal" and items["3"].cadence == "once"
    assert items["4"].until == date(2026, 2, 2)
    assert next_on_or_after(date(2026, 1, 31), "monthly", 1, None, date(2026, 2, 10)) == date(2026, 2, 28)
    assert next_on_or_after(date(2026, 1, 5), "weekly", 2, date(2026, 2, 2), date(2026, 2, 3)) is None


def test_upload_shows_in_coming_up_and_only_owner_removes():
    a, b, x = make_user("a"), make_user("b"), make_user("x")
    hid = create_household(a)
    code = a.post(f"/api/households/{hid}/invites", json={"role": "member"}).json()["code"]
    b.post("/api/invites/accept", json={"code": code})
    soon = (T + timedelta(days=3)).strftime("%Y%m%d")
    raw = ics(f"UID:r\r\nSUMMARY:Council tax £142.50\r\nDTSTART;VALUE=DATE:{soon}\r\nRRULE:FREQ=MONTHLY",
              "UID:old\r\nSUMMARY:Old thing\r\nDTSTART;VALUE=DATE:20200101")
    r = a.post(f"/api/households/{hid}/calendar/upload", files={"file": ("bills.ics", raw)})
    assert r.status_code == 201
    cal = b.get(f"/api/households/{hid}/calendar").json()
    assert [i["name"] for i in cal["items"]] == ["Council tax"] and cal["feeds"][0]["mine"] is False
    s = b.get(f"/api/households/{hid}/summary").json()
    assert s["scheduled"][0]["amount"] == "142.50" and s["scheduled"][0]["next_date"] == (T + timedelta(days=3)).isoformat()
    assert x.get(f"/api/households/{hid}/calendar").status_code == 404
    assert a.post(f"/api/households/{hid}/calendar/upload", files={"file": ("x.ics", b"hello")}).status_code == 422
    fid = cal["feeds"][0]["id"]
    assert b.delete(f"/api/calendar-feeds/{fid}").status_code == 403
    assert a.delete(f"/api/calendar-feeds/{fid}").status_code == 204


def test_calendar_link_refuses_the_server_itself():
    a = make_user("a")
    hid = create_household(a)
    for url in ("http://127.0.0.1:8000/api/health", "https://127.0.0.1/x.ics", "https://192.168.1.1/x.ics",
                "https://10.0.0.5/x.ics", "https://[fd00::1]/x.ics", "https://example.com:8443/x.ics", "https://user:pw@example.com/x.ics"):
        r = a.post(f"/api/households/{hid}/calendar/link", json={"name": "Mine", "url": url})
        assert r.status_code in (422, 502), url
        assert "192.168" not in r.text and "Errno" not in r.text
    assert a.post(f"/api/households/{hid}/calendar/link", json={"name": "Mine", "url": "file:///etc/passwd"}).status_code == 422
