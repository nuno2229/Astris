"""P-2: refunds are linked to the purchase and netted off its category, not counted as income."""
from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import create_account, create_household, make_user, upload

T = date.today()
D = lambda n: (T - timedelta(days=n)).isoformat()   # noqa: E731
MONTH = T.strftime("%Y-%m")


def csv(*rows):
    return ("Date,Description,Amount\n" + "".join(f"{d},{desc},{amt}\n" for d, desc, amt in rows)).encode()


def setup():
    a = make_user("a")
    hid = create_household(a)
    card = create_account(a, "Card", type_="card")
    a.put(f"/api/households/{hid}/accounts/{card}", json={"attribution": "include"})
    return a, hid, card


def txs(c, acc):
    return {t["description"]: t for t in c.get(f"/api/accounts/{acc}/transactions").json()}


def totals(c, hid):
    inc = c.get(f"/api/households/{hid}/summary?month={MONTH}").json()["included"][0]
    return Decimal(inc["spent"]), Decimal(inc["received"])


def test_refund_linked_and_netted_off_spending():
    a, hid, card = setup()
    upload(a, card, "c.csv", csv((D(1), "AMAZON.CO.UK*AB12CD", "-60.00"), (D(1), "TESCO STORES 2041", "-20.00"),
                                 (D(0), "AMAZON.CO.UK REFUND", "25.00"), (D(0), "INTEREST PAID", "1.00")))
    t = txs(a, card)
    r, p = t["AMAZON.CO.UK REFUND"], t["AMAZON.CO.UK*AB12CD"]
    assert r["refund_of"] == p["id"] and r["category"] == p["category"]
    assert totals(a, hid) == (Decimal("55.00"), Decimal("1.00"))      # 60 + 20 - 25 spent; refund isn't income
    ins = a.get(f"/api/households/{hid}/insights?month={MONTH}").json()
    shopping = next(x for x in ins["categories"] if x["category"] == p["category"])
    assert Decimal(shopping["spent"]) == Decimal("35.00")
    # "not a refund" puts it back as money in and it's never suggested again
    assert a.delete(f"/api/transactions/{r['id']}/refund").status_code == 204
    assert txs(a, card)["AMAZON.CO.UK REFUND"]["refund_of"] is None
    assert totals(a, hid) == (Decimal("80.00"), Decimal("26.00"))
    upload(a, card, "c2.csv", csv((D(0), "ANOTHER LINE", "-1.00")))   # re-import runs detection again
    assert txs(a, card)["AMAZON.CO.UK REFUND"]["refund_of"] is None
    # but you can still link it by hand
    cands = a.get(f"/api/transactions/{r['id']}/refund-candidates").json()
    assert [c["id"] for c in cands] == [p["id"]]
    assert a.post(f"/api/transactions/{r['id']}/refund", json={"original_id": p["id"]}).status_code == 204
    assert totals(a, hid)[0] == Decimal("56.00")


def test_refund_rules():
    a, hid, card = setup()
    b = make_user("b")
    upload(a, card, "c.csv", csv((D(200), "ASOS.COM", "-30.00"), (D(5), "NEXT RETAIL LTD", "-10.00"),
                                 (D(1), "ASOS.COM REFUND", "30.00"), (D(1), "NEXT RETAIL REFUND", "15.00")))
    t = txs(a, card)
    assert t["ASOS.COM REFUND"]["refund_of"] is None        # purchase too long ago (120 days)
    assert t["NEXT RETAIL REFUND"]["refund_of"] is None     # more back than was paid
    bad = a.post(f"/api/transactions/{t['NEXT RETAIL REFUND']['id']}/refund", json={"original_id": t["NEXT RETAIL LTD"]["id"]})
    assert bad.status_code == 422
    # someone else can't link or see candidates on your lines
    assert b.get(f"/api/transactions/{t['ASOS.COM REFUND']['id']}/refund-candidates").status_code == 404
    assert b.post(f"/api/transactions/{t['ASOS.COM REFUND']['id']}/refund", json={"original_id": t["ASOS.COM"]["id"]}).status_code == 404
