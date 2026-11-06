from datetime import date
from decimal import Decimal

from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()


def ms(back: int, day: int = 5) -> date:
    y, m = divmod(T.year * 12 + T.month - 1 - back, 12)
    return date(y, m + 1, min(day, 28))


def csv(rows):
    return ("Date,Description,Amount\n" + "".join(f"{d.isoformat()},{x},{a}\n" for d, x, a in rows)).encode()


def setup():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    acc_a, acc_b, hidden = create_account(a, "Joint"), create_account(b, "Bob's"), create_account(b, "Bob secret")
    a.put(f"/api/households/{hid}/accounts/{acc_a}", json={"attribution": "include"})
    b.put(f"/api/households/{hid}/accounts/{acc_b}", json={"attribution": "include"})
    b.put(f"/api/households/{hid}/accounts/{hidden}", json={"attribution": "hide"})
    return a, b, hid, acc_a, acc_b, hidden


def test_categories_vs_typical_trend_shops_members_and_excluded():
    a, b, hid, acc_a, acc_b, hidden = setup()
    rows = [(ms(k), "TESCO", "-100.00") for k in (1, 2, 3)] + [(ms(0, 1), "TESCO", "-150.00"), (ms(0, 2), "NETFLIX.COM", "-10.00"),
                                                              (ms(0, 2), "ACME SALARY", "2000.00")]
    upload(a, acc_a, "a.csv", csv(rows))
    upload(b, acc_b, "b.csv", csv([(ms(0, 1), "ALDI", "-50.00")]))
    upload(b, hidden, "h.csv", csv([(ms(0, 1), "GADGET SHOP", "-999.00")]))
    a.post(f"/api/households/{hid}/budgets", json={"kind": "category", "category": "Groceries", "amount": "180", "currency": "GBP"})
    r = a.get(f"/api/households/{hid}/insights").json()
    assert Decimal(r["total"]) == Decimal("210.00")                       # hidden account not in totals
    g = [c for c in r["categories"] if c["category"] == "Groceries"][0]
    assert (Decimal(g["spent"]), Decimal(g["typical"]), Decimal(g["change"]), Decimal(g["budget"])) == \
        (Decimal("200.00"), Decimal("100.00"), Decimal("1.000"), Decimal("180"))
    assert len(r["trend"]) == 12 and Decimal(r["trend"][-1]["spent"]) == Decimal("210.00") and Decimal(r["trend"][-1]["received"]) == 2000
    assert [s["name"] for s in r["top_shops"]][:2] == ["Tesco", "Aldi"]
    assert {m["name"]: Decimal(m["spent"]) for m in r["by_member"]} == {"A": Decimal("160.00"), "B": Decimal("50.00")}
    assert [(e["name"], Decimal(e["spent"])) for e in r["excluded"]] == [("Bob secret (B)", Decimal("999.00"))]


def test_seasonality_from_last_year():
    a, b, hid, acc_a, *_ = setup()
    rows = []
    for k in range(13, 25):                       # a full year, starting 2 years ago
        rows.append((ms(k), "GIFT SHOP", "-30.00"))
    rows.append((ms(12), "GIFT SHOP", "-200.00"))   # same month last year: big spike
    for k in range(1, 12):
        rows.append((ms(k), "GIFT SHOP", "-30.00"))
    upload(a, acc_a, "a.csv", csv(rows))
    r = a.get(f"/api/households/{hid}/insights").json()
    s = [x for x in r["seasonal"] if x["month"] == f"{T.year:04d}-{T.month:02d}"]
    assert s and Decimal(s[0]["ratio"]) > Decimal("2")


def test_month_param_and_isolation():
    a, b, hid, acc_a, *_ = setup()
    upload(a, acc_a, "a.csv", csv([(ms(2), "TESCO", "-40.00")]))
    m = ms(2)
    r = a.get(f"/api/households/{hid}/insights?month={m.year:04d}-{m.month:02d}").json()
    assert Decimal(r["total"]) == Decimal("40.00")
    x = make_user("x")
    assert x.get(f"/api/households/{hid}/insights").status_code == 404
