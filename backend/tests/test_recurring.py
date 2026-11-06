from datetime import date, timedelta
from decimal import Decimal

from app.recurring import add_period, fit
from tests.conftest import create_account, create_household, invite_and_join, make_user, upload


def month_add(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def csv(rows) -> bytes:
    return ("Date,Description,Amount\n" + "".join(f"{d.isoformat()},{desc},{amt}\n" for d, desc, amt in rows)).encode()


def setup():
    a = make_user("a")
    hid = create_household(a)
    acc = create_account(a)
    a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    return a, hid, acc


def series(c, hid, **kw):
    q = "?include_dismissed=true" if kw.get("all") else ""
    return {s["name"]: s for s in c.get(f"/api/households/{hid}/recurring{q}").json()}


def test_fit_cadences():
    d0 = date(2026, 1, 2)
    weekly = [(d0 + timedelta(days=7 * i), Decimal("-5")) for i in range(5)]
    assert fit(weekly).cadence == "weekly"
    fortnight = [(d0 + timedelta(days=14 * i), Decimal("850")) for i in range(4)]
    assert fit(fortnight).cadence == "fortnightly"
    monthly = [(date(2026, m, 28 if m != 2 else 27), Decimal("-9.99")) for m in range(1, 6)]  # weekend shift
    assert fit(monthly).cadence == "monthly"
    yearly = [(date(2025, 3, 3), Decimal("-89")), (date(2026, 3, 1), Decimal("-95"))]
    assert fit(yearly).cadence == "yearly"
    shop = [(d0 + timedelta(days=7 * i), Decimal(v)) for i, v in enumerate(["-30", "-82", "-51", "-120"])]
    assert fit(shop) is None                                   # weekly rhythm but amounts all over the place
    assert fit(weekly[:2]) is None                             # too few
    assert add_period(date(2026, 1, 31), "monthly", 31) == date(2026, 2, 28)


def test_detects_bills_income_and_next_dates():
    a, hid, acc = setup()
    today = date.today()
    start = month_add(today.replace(day=1), -3)
    rows = []
    for i in range(4):
        m = month_add(start, i)
        rows += [(m.replace(day=1), "STANDING ORDER TO LANDLORD RENT", "-1200.00"),
                 (m.replace(day=15), "NETFLIX.COM", "-10.99"),
                 (m.replace(day=25), "ACME LTD SALARY", "2500.00"),
                 (m.replace(day=9), f"TESCO STORES {i}", ["-30.10", "-88.40", "-12.00", "-140.00"][i])]
    rows = [r for r in rows if r[0] <= today]
    upload(a, acc, "s.csv", csv(rows))
    s = series(a, hid)
    assert set(s) >= {"Rent", "Netflix", "Acme Salary"} and "Tesco" not in s
    assert s["Rent"]["cadence"] == "monthly" and Decimal(s["Rent"]["expected_amount"]) == Decimal("-1200")
    assert s["Acme Salary"]["direction"] == "in"
    last_rent = max(r[0] for r in rows if "RENT" in r[1])
    assert s["Rent"]["next_date"] == add_period(last_rent, "monthly", 1).isoformat()


def test_missed_only_when_statements_cover_the_due_date():
    a, hid, acc = setup()
    d0 = date.today() - timedelta(days=150)
    rows = [(d0 + timedelta(days=30 * i), "DIRECT DEBIT TO GYMBOX", "-40.00") for i in range(3)]
    upload(a, acc, "a.csv", csv(rows))
    assert series(a, hid)["Gymbox"]["status"] == "active"      # nothing imported past the due date yet
    later = rows[-1][0] + timedelta(days=45)
    upload(a, acc, "b.csv", csv([(later, "CORNER SHOP", "-3.00")]))   # statement now covers the due date
    s = series(a, hid)["Gymbox"]
    assert s["status"] == "missed"
    summ = a.get(f"/api/households/{hid}/summary").json()
    assert [m["name"] for m in summ["missed"]] == ["Gymbox"]


def test_price_change_is_flagged():
    a, hid, acc = setup()
    d0 = date.today() - timedelta(days=120)
    first = [(d0 + timedelta(days=30 * i), "SPOTIFY P1234", "-10.99") for i in range(3)]
    upload(a, acc, "a.csv", csv(first))
    upload(a, acc, "b.csv", csv([(d0 + timedelta(days=90), "SPOTIFY P5678", "-12.99")]))
    s = series(a, hid)["Spotify"]
    assert Decimal(s["expected_amount"]) == Decimal("-12.99") and Decimal(s["previous_amount"]) == Decimal("-10.99")


def test_upcoming_in_summary_and_dismiss():
    a, hid, acc = setup()
    today = date.today()
    rows = [(today - timedelta(days=7 * i), "POCKET MONEY KIDS", "-5.00") for i in range(1, 5)]
    upload(a, acc, "a.csv", csv(rows))
    up = a.get(f"/api/households/{hid}/summary").json()["upcoming"]
    assert [u["name"] for u in up] == ["Pocket Money Kids"]
    rid = up[0]["id"]
    assert a.patch(f"/api/recurring/{rid}", json={"status": "dismissed"}).status_code == 204
    assert series(a, hid) == {}
    # re-importing doesn't bring a dismissed series back
    upload(a, acc, "b.csv", csv([(today, "POCKET MONEY KIDS", "-5.00")]))
    assert series(a, hid) == {} and "Pocket Money Kids" in series(a, hid, all=True)


def test_recurring_isolation_and_permissions():
    a, hid, acc = setup()
    b, x = make_user("b"), make_user("x")
    invite_and_join(a, b, hid)
    d0 = date.today() - timedelta(days=100)
    upload(a, acc, "a.csv", csv([(d0 + timedelta(days=30 * i), "COUNCIL TAX DD", "-150.00") for i in range(3)]))
    rid = series(a, hid)["Council tax"]["id"]
    assert series(b, hid)["Council tax"]["editable"] is False
    assert b.patch(f"/api/recurring/{rid}", json={"status": "dismissed"}).status_code == 404
    assert x.get(f"/api/households/{hid}/recurring").status_code == 404
    assert x.patch(f"/api/recurring/{rid}", json={"status": "dismissed"}).status_code == 404
