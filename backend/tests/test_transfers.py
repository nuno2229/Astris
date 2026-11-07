"""P-1: transfers between your own / your household's accounts are linked and left out of spending."""
from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()
D = lambda n: (T - timedelta(days=n)).isoformat()   # noqa: E731
MONTH = T.strftime("%Y-%m")


def csv(*rows):
    return ("Date,Description,Amount\n" + "".join(f"{d},{desc},{amt}\n" for d, desc, amt in rows)).encode()


def spent(c, hid):
    inc = c.get(f"/api/households/{hid}/summary?month={MONTH}").json()["included"]
    return sum((Decimal(x["spent"]) for x in inc), Decimal(0)), sum((Decimal(x["received"]) for x in inc), Decimal(0))


def txs(c, acc):
    return {t["description"]: t for t in c.get(f"/api/accounts/{acc}/transactions").json()}


def setup_own():
    a = make_user("a")
    hid = create_household(a)
    cur, sav = create_account(a, "Current"), create_account(a, "Savings")
    for acc in (cur, sav):
        a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    return a, hid, cur, sav


def test_own_transfer_linked_on_import_and_undoable():
    a, hid, cur, sav = setup_own()
    upload(a, sav, "s.csv", csv((D(1), "FROM CURRENT", "250.00")))
    upload(a, cur, "c.csv", csv((D(2), "TO SAVINGS", "-250.00"), (D(2), "TESCO STORES", "-30.00")))
    t = txs(a, cur)["TO SAVINGS"]
    assert t["transfer_group"] and t["category"] == "Transfers"
    assert txs(a, sav)["FROM CURRENT"]["transfer_group"] == t["transfer_group"]
    assert spent(a, hid) == (Decimal("30.00"), Decimal(0))       # neither leg counts
    assert a.get("/api/transfers/suggestions").json() == []
    # undo from History puts both back as ordinary lines
    e = next(e for e in a.get("/api/audit").json() if e["action"] == "tx.transfer")
    assert e["can_undo"]
    assert a.post(f"/api/audit/{e['id']}/undo", json={}).status_code == 200
    assert txs(a, cur)["TO SAVINGS"]["transfer_group"] is None
    assert spent(a, hid) == (Decimal("280.00"), Decimal("250.00"))
    # now it's a suggestion again; "not a transfer" hides it for good
    s = a.get("/api/transfers/suggestions").json()
    assert len(s) == 1 and s[0]["amount"] == "250.00"
    assert a.post("/api/transfers/dismiss", json={"out_id": s[0]["out"]["id"], "in_id": s[0]["into"]["id"]}).status_code == 204
    assert a.get("/api/transfers/suggestions").json() == []


def test_ambiguous_pairs_are_suggested_not_linked():
    a, hid, cur, sav = setup_own()
    card = create_account(a, "Card")
    upload(a, sav, "s.csv", csv((D(3), "IN ONE", "50.00")))
    upload(a, card, "k.csv", csv((D(2), "IN TWO", "50.00")))
    upload(a, cur, "c.csv", csv((D(3), "PAYMENT OUT", "-50.00")))
    assert txs(a, cur)["PAYMENT OUT"]["transfer_group"] is None
    s = a.get("/api/transfers/suggestions").json()
    assert {x["into"]["account"] for x in s} == {"Savings", "Card"}
    pick = next(x for x in s if x["into"]["account"] == "Card")
    r = a.post("/api/transfers", json={"out_id": pick["out"]["id"], "in_id": pick["into"]["id"]})
    assert r.status_code == 201
    assert a.post("/api/transfers", json={"out_id": pick["out"]["id"], "in_id": pick["into"]["id"]}).status_code == 409
    assert a.get("/api/transfers/suggestions").json() == []
    # amounts must match
    other = txs(a, sav)["IN ONE"]
    upload(a, cur, "c2.csv", csv((D(1), "SOMETHING", "-49.00")))
    bad = a.post("/api/transfers", json={"out_id": txs(a, cur)["SOMETHING"]["id"], "in_id": other["id"]})
    assert bad.status_code == 422
    # unlink brings the line back to normal rules
    g = txs(a, cur)["PAYMENT OUT"]["transfer_group"]
    assert a.delete(f"/api/transfers/{g}").status_code == 204
    assert txs(a, card)["IN TWO"]["transfer_group"] is None


def test_payment_into_partners_account_in_same_household():
    a, hid, cur, _ = setup_own()
    b = make_user("b")
    invite_and_join(a, b, hid)
    joint = create_account(b, "Joint")
    b.put(f"/api/households/{hid}/accounts/{joint}", json={"attribution": "include"})
    upload(a, cur, "c.csv", csv((D(2), "TO JOINT", "-400.00")))
    upload(b, joint, "j.csv", csv((D(1), "FROM A", "400.00")))
    assert txs(a, cur)["TO JOINT"]["transfer_group"] is None     # different people: asked, never automatic
    sa, sb = a.get(f"/api/transfers/suggestions?household_id={hid}").json(), b.get("/api/transfers/suggestions").json()
    assert len(sa) == 1 and len(sb) == 1
    assert b.post("/api/transfers", json={"out_id": sb[0]["out"]["id"], "in_id": sb[0]["into"]["id"]}).status_code == 201
    assert txs(a, cur)["TO JOINT"]["transfer_group"] == txs(b, joint)["FROM A"]["transfer_group"]
    assert spent(a, hid) == (Decimal(0), Decimal(0))
    # either owner may undo it
    assert a.delete(f"/api/transfers/{txs(a, cur)['TO JOINT']['transfer_group']}").status_code == 204


def test_no_transfers_across_unrelated_people():
    a, hid, cur, _ = setup_own()
    c = make_user("c")
    other = create_account(c, "Theirs")
    c.put(f"/api/households/{create_household(c)}/accounts/{other}", json={"attribution": "include"})
    upload(a, cur, "c.csv", csv((D(2), "OUT", "-75.00")))
    upload(c, other, "o.csv", csv((D(2), "IN", "75.00")))
    assert a.get("/api/transfers/suggestions").json() == [] and c.get("/api/transfers/suggestions").json() == []
    r = c.post("/api/transfers", json={"out_id": txs(a, cur)["OUT"]["id"], "in_id": txs(c, other)["IN"]["id"]})
    assert r.status_code == 404                                  # c can't see a's line at all
    # and a stranger can't unlink someone else's transfer
    sav = create_account(a, "Savings2")
    upload(a, sav, "s.csv", csv((D(5), "BACK", "10.00")))
    upload(a, cur, "c2.csv", csv((D(5), "AWAY", "-10.00")))
    g = txs(a, cur)["AWAY"]["transfer_group"]
    assert g and c.delete(f"/api/transfers/{g}").status_code == 404
