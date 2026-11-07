"""G-9: leaving a household, the member chooses whether their past transactions stay visible there."""
from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()
D = lambda n: (T - timedelta(days=n)).isoformat()   # noqa: E731
MONTH = T.strftime("%Y-%m")


def setup():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    acc = create_account(b, "B current")
    b.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    upload(b, acc, "s.csv", f"Date,Description,Amount\n{D(3)},TESCO STORES 2041,-40.00\n".encode())
    return a, b, hid, acc


def descs(c, hid):
    return [t["description"] for t in c.get(f"/api/households/{hid}/transactions").json()]


def spent(c, hid):
    inc = c.get(f"/api/households/{hid}/summary?month={MONTH}").json()["included"]
    return sum((Decimal(x["spent"]) for x in inc), Decimal(0))


def test_leave_and_take_history():
    a, b, hid, acc = setup()
    assert b.delete(f"/api/households/{hid}/members/{b.me['id']}").status_code == 204
    assert descs(a, hid) == [] and spent(a, hid) == 0
    assert b.get(f"/api/households/{hid}").status_code == 404


def test_leave_and_keep_history_frozen_at_that_day():
    a, b, hid, acc = setup()
    assert b.delete(f"/api/households/{hid}/members/{b.me['id']}?keep_history=true").status_code == 204
    assert b.get(f"/api/households/{hid}").status_code == 404                      # no access back in
    assert descs(a, hid) == ["TESCO STORES 2041"] and spent(a, hid) == Decimal("40.00")
    shown = a.get(f"/api/households/{hid}/accounts").json()
    assert shown[0]["name"] == "B current" and shown[0]["until"] == T.isoformat()
    # later imports, even backdated, never show
    upload(b, acc, "s2.csv", f"Date,Description,Amount\n{D(10)},PHARMACY,-9.00\n{D(0)},PUB,-12.00\n".encode())
    assert descs(a, hid) == ["TESCO STORES 2041"]
    assert a.get(f"/api/transactions/{b.get(f'/api/accounts/{acc}/transactions').json()[0]['id']}/refund-candidates").status_code in (403, 404)
    # the leaver sees where their history is kept and can take it back
    kept = b.get(f"/api/accounts/{acc}/kept-history").json()
    assert kept == [{"household_id": hid, "household": "Home", "until": T.isoformat()}]
    assert a.delete(f"/api/accounts/{acc}/kept-history/{hid}").status_code in (403, 404)
    assert b.delete(f"/api/accounts/{acc}/kept-history/{hid}").status_code == 204
    assert descs(a, hid) == [] and spent(a, hid) == 0


def test_rejoining_clears_the_frozen_share():
    a, b, hid, acc = setup()
    b.delete(f"/api/households/{hid}/members/{b.me['id']}?keep_history=true")
    invite_and_join(a, b, hid)
    assert descs(a, hid) == []                         # share again to show it
    assert b.get(f"/api/accounts/{acc}/kept-history").json() == []
    b.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    assert len(descs(a, hid)) == 1


def test_owner_cannot_leave():
    a, b, hid, acc = setup()
    assert a.delete(f"/api/households/{hid}/members/{a.me['id']}?keep_history=true").status_code == 403


def test_each_household_keeps_its_own_cut_off(owner_engine):
    from sqlalchemy import text
    a, b, h1, acc = setup()                       # B's TESCO line is dated 3 days ago
    h2 = create_household(a, "Second")
    invite_and_join(a, b, h2)
    b.put(f"/api/households/{h2}/accounts/{acc}", json={"attribution": "include"})
    b.delete(f"/api/households/{h1}/members/{b.me['id']}?keep_history=true")
    b.delete(f"/api/households/{h2}/members/{b.me['id']}?keep_history=true")
    with owner_engine.begin() as c:              # pretend B left the first household a week ago
        c.execute(text("UPDATE household_accounts SET until = current_date - 7 WHERE household_id = :h"), {"h": h1})
    assert descs(a, h2) == ["TESCO STORES 2041"]
    assert descs(a, h1) == [] and spent(a, h1) == 0
    # taking everything back also stops B being listed there
    b.delete(f"/api/accounts/{acc}/kept-history/{h1}")
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM former_members WHERE household_id = :h"), {"h": h1}).scalar() == 0


def test_kept_account_shows_only_its_label():
    a, b, hid, acc = setup()
    b.put(f"/api/accounts/{acc}/opening", json={"balance": "123.00", "date": D(30)})
    b.delete(f"/api/households/{hid}/members/{b.me['id']}?keep_history=true")
    shown = a.get(f"/api/households/{hid}/accounts").json()[0]
    assert shown["name"] == "B current" and shown["institution"] is None and shown["opening_balance"] == "0"
    assert shown["ai_household_id"] is None
