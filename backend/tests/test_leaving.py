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
