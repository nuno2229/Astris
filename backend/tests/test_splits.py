"""P-3 split payments; P-4 settle-up (household option)."""
from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()
D = (T - timedelta(days=1)).isoformat()
MONTH = T.strftime("%Y-%m")


def setup():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    acc = create_account(a, "A card")
    a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    upload(a, acc, "s.csv", f"Date,Description,Amount\n{D},TESCO STORES 2041,-90.00\n".encode())
    tx = a.get(f"/api/accounts/{acc}/transactions").json()[0]
    return a, b, hid, acc, tx


def cats(c, hid):
    return {x["category"]: Decimal(x["spent"]) for x in c.get(f"/api/households/{hid}/insights?month={MONTH}").json()["categories"]}


def test_split_changes_category_totals():
    a, b, hid, acc, tx = setup()
    bad = a.put(f"/api/transactions/{tx['id']}/splits", json={"parts": [{"amount": "-50", "category": "Groceries"}, {"amount": "-30", "category": "Household"}]})
    assert bad.status_code == 422                                           # must add up (and Household isn't a category)
    r = a.put(f"/api/transactions/{tx['id']}/splits", json={"parts": [
        {"amount": "-60", "category": "Groceries"}, {"amount": "-30", "category": "Personal care", "note": "Shampoo"}]})
    assert r.status_code == 200 and len(r.json()) == 2
    assert cats(a, hid) == {"Groceries": Decimal("60.00"), "Personal care": Decimal("30.00")}
    assert a.get(f"/api/accounts/{acc}/transactions").json()[0]["split"] is True
    assert b.put(f"/api/transactions/{tx['id']}/splits", json={"parts": []}).status_code == 403
    assert b.get(f"/api/transactions/{tx['id']}/splits").json()[1]["note"] == "Shampoo"     # members can see it
    assert a.put(f"/api/transactions/{tx['id']}/splits", json={"parts": []}).json() == []
    assert cats(a, hid) == {"Groceries": Decimal("90.00")}
    # an outsider can't see the parts
    assert make_user("x").get(f"/api/transactions/{tx['id']}/splits").status_code == 404


def test_settle_up():
    a, b, hid, acc, tx = setup()
    assert a.get(f"/api/households/{hid}/balances").json()["enabled"] is False
    assert b.put(f"/api/households/{hid}/splits-enabled", json={"enabled": True}).status_code == 403
    a.put(f"/api/households/{hid}/splits-enabled", json={"enabled": True})
    a.put(f"/api/transactions/{tx['id']}/splits", json={"parts": [
        {"amount": "-45", "category": "Groceries", "for_user": a.me["id"]}, {"amount": "-45", "category": "Groceries", "for_user": b.me["id"]}]})
    owes = b.get(f"/api/households/{hid}/balances").json()["owes"]
    assert owes == [{"from_user": b.me["id"], "from_name": "B", "to_user": a.me["id"], "to_name": "A", "amount": "45.00", "currency": "GBP"}]
    # B says they paid £40: pending until A confirms; B can't clear their own debt
    assert b.post(f"/api/households/{hid}/settlements", json={"from_user": b.me["id"], "to_user": a.me["id"], "amount": "40", "currency": "GBP"}).status_code == 201
    out = a.get(f"/api/households/{hid}/balances").json()
    assert out["owes"][0]["amount"] == "45.00" and out["settlements"][0]["can_confirm"] is True
    sid = out["settlements"][0]["id"]
    assert b.post(f"/api/settlements/{sid}/confirm").status_code == 403
    assert a.post(f"/api/settlements/{sid}/confirm").json()["owes"][0]["amount"] == "5.00"
    # A records £5 received: confirmed straight away; can't record more than is owed
    assert a.post(f"/api/households/{hid}/settlements", json={"from_user": b.me["id"], "to_user": a.me["id"], "amount": "6", "currency": "GBP"}).status_code == 422
    assert a.post(f"/api/households/{hid}/settlements", json={"from_user": b.me["id"], "to_user": a.me["id"], "amount": "5", "currency": "GBP"}).status_code == 201
    assert a.get(f"/api/households/{hid}/balances").json()["owes"] == []
    c = make_user("c")
    invite_and_join(a, c, hid)
    assert c.post(f"/api/households/{hid}/settlements", json={"from_user": b.me["id"], "to_user": a.me["id"], "amount": "5", "currency": "GBP"}).status_code == 422
    # a split for someone outside the households the account is in is refused
    assert a.put(f"/api/transactions/{tx['id']}/splits", json={"parts": [
        {"amount": "-45", "for_user": a.me["id"]}, {"amount": "-45", "for_user": make_user("y").me["id"]}]}).status_code == 422
    # removing: the recorder or the receiver
    assert c.delete(f"/api/settlements/{sid}").status_code == 403
    assert b.delete(f"/api/settlements/{sid}").status_code == 204


def test_split_for_someone_in_another_household_doesnt_count_here():
    a, b, hid, acc, tx = setup()
    other = create_household(a, "Other")
    c = make_user("c")
    invite_and_join(a, c, other)
    a.put(f"/api/households/{other}/accounts/{acc}", json={"attribution": "include"})
    for h in (hid, other):
        a.put(f"/api/households/{h}/splits-enabled", json={"enabled": True})
    a.put(f"/api/transactions/{tx['id']}/splits", json={"parts": [{"amount": "-45", "for_user": a.me["id"]}, {"amount": "-45", "for_user": c.me["id"]}]})
    assert a.get(f"/api/households/{hid}/balances").json()["owes"] == []
    assert a.get(f"/api/households/{other}/balances").json()["owes"][0]["from_name"] == "C"


def test_splits_edited_after_leaving_are_not_shown():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    acc = create_account(b, "B card")
    b.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    upload(b, acc, "s.csv", f"Date,Description,Amount\n{D},TESCO STORES 2041,-90.00\n".encode())
    tx = b.get(f"/api/accounts/{acc}/transactions").json()[0]
    b.put(f"/api/transactions/{tx['id']}/splits", json={"parts": [{"amount": "-60", "category": "Groceries"}, {"amount": "-30", "category": "Health"}]})
    b.delete(f"/api/households/{hid}/members/{b.me['id']}?keep_history=true")
    assert cats(a, hid) == {"Groceries": Decimal("60.00"), "Health": Decimal("30.00")}     # as it was when B left
    b.put(f"/api/transactions/{tx['id']}/splits", json={"parts": [{"amount": "-10", "category": "Groceries"}, {"amount": "-80", "category": "Travel"}]})
    assert a.get(f"/api/transactions/{tx['id']}/splits").json() == []
    assert cats(a, hid) == {"Groceries": Decimal("90.00")}                                  # later edits never show
