"""P-15: net worth over time."""
from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()
M0 = T.replace(day=1)
PREV = (M0 - timedelta(days=1)).replace(day=15)


def test_net_worth_month_by_month():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    cur = create_account(a, "Current")
    card = create_account(a, "Amex", type_="card")
    wallet = create_account(b, "Wallet", type_="cash")
    hidden = create_account(b, "Private")
    for c, acc, attr in ((a, cur, "include"), (a, card, "include"), (b, wallet, "include"), (b, hidden, "hide")):
        c.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": attr})
    upload(a, cur, "c.csv", (f"Date,Description,Amount,Balance\n{PREV},SALARY ACME,2000.00,2500.00\n"
                             f"{T},To Chase Saver,-1000.00,1500.00\n").encode())
    upload(a, card, "k.csv", f"Date,Description,Amount\n{T},TESCO STORES 2041,-120.00\n".encode())     # no balances: owed £120
    b.put(f"/api/accounts/{wallet}/opening", json={"balance": "50.00", "date": PREV.isoformat()})
    upload(b, hidden, "h.csv", f"Date,Description,Amount,Balance\n{T},X,-1.00,99999.00\n".encode())
    r = b.get(f"/api/households/{hid}/net-worth?months=3").json()
    vals = {p["month"]: p["value"] for p in r["points"]}
    # this month: current 1,500 + saver pot 1,000 + wallet 50 - card 120 (the separate account isn't counted)
    assert Decimal(vals[T.strftime("%Y-%m")]) == Decimal("2430.00")
    assert Decimal(vals[PREV.strftime("%Y-%m")]) == Decimal("2550.00")       # 2,500 + wallet 50
    assert Decimal(r["change_month"]) == Decimal("-120.00")
    names = {x["name"]: x for x in r["accounts"]}
    assert set(names) == {"Current", "Amex", "Wallet", "Chase Saver"} and Decimal(names["Amex"]["in_home"]) == Decimal("-120.00")
    assert make_user("x").get(f"/api/households/{hid}/net-worth").status_code == 404
