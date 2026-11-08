"""Savings pots (PO 2026-11-08): "To Bills pot" is savings, not spending."""
from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import create_account, create_household, make_user, upload

T = date.today()
D = lambda n: (T - timedelta(days=n)).isoformat()   # noqa: E731
MONTH = T.strftime("%Y-%m")


def test_pot_moves_become_a_savings_account():
    a = make_user("a")
    hid = create_household(a)
    cur = create_account(a, "Chase")
    a.put(f"/api/households/{hid}/accounts/{cur}", json={"attribution": "include"})
    upload(a, cur, "s.csv", (f"Date,Description,Amount\n{D(3)},To Bills pot,-1000.00\n{D(2)},To Holiday pot,-50.00\n"
                             f"{D(1)},From Bills pot,200.00\n{D(1)},TESCO STORES 2041,-20.00\n{D(1)},To Pottery Class,-15.00\n").encode())
    inc = a.get(f"/api/households/{hid}/summary?month={MONTH}").json()["included"][0]
    assert Decimal(inc["spent"]) == Decimal("35.00") and Decimal(inc["received"]) == 0     # pots aren't spending or income
    accs = {x["name"]: x for x in a.get(f"/api/households/{hid}/accounts").json()}
    assert accs["Bills pot"]["type"] == "savings" and Decimal(accs["Bills pot"]["balance"]) == Decimal("800.00")
    assert Decimal(accs["Holiday pot"]["balance"]) == Decimal("50.00")
    # a goal can follow a pot
    g = a.post(f"/api/households/{hid}/goals", json={"name": "Trip", "target": "500", "currency": "GBP",
                                                     "account_id": accs["Holiday pot"]["id"]}).json()
    assert g["saved"] == "50.00"
    # "not a transfer" sticks across later imports
    t = next(x for x in a.get(f"/api/accounts/{cur}/transactions").json() if x["description"] == "To Holiday pot")
    assert a.delete(f"/api/transfers/{t['transfer_group']}").status_code == 204
    upload(a, cur, "s2.csv", f"Date,Description,Amount\n{D(0)},To Bills pot,-10.00\n".encode())
    t = next(x for x in a.get(f"/api/accounts/{cur}/transactions").json() if x["description"] == "To Holiday pot")
    assert t["transfer_group"] is None
    assert Decimal({x["name"]: x for x in a.get(f"/api/households/{hid}/accounts").json()}["Bills pot"]["balance"]) == Decimal("810.00")


def test_named_saver_account_and_people_are_not_pots():
    from app.pots import pot_name
    assert pot_name("To Chase Saver", Decimal("-1000")) == "Chase Saver\x00"
    assert pot_name("From Easy Access Savings", Decimal("100")) == "Easy Access Savings\x00"
    assert pot_name("To Alex Example - Chase", Decimal("-50")) is None
    assert pot_name("To Chase Saver", Decimal("1000")) is None          # "to" with money in: not a move to savings
    a = make_user("a")
    cur = create_account(a, "Chase")
    upload(a, cur, "s.csv", f"Date,Description,Amount\n{D(3)},To Chase Saver,-1000.00\n".encode())
    names = [x["name"] for x in a.get("/api/accounts").json()]
    assert "Chase Saver" in names
