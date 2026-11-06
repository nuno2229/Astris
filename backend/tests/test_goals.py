from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()


def setup():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    return a, b, hid


def new_goal(c, hid, **kw):
    r = c.post(f"/api/households/{hid}/goals", json={"name": "Holiday", "target": "1000", "currency": "GBP", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_members_contribute_and_progress():
    a, b, hid = setup()
    g = new_goal(a, hid, deadline=(T + timedelta(days=150)).isoformat())
    a.post(f"/api/goals/{g['id']}/contributions", json={"amount": "300", "made_on": (T - timedelta(days=30)).isoformat()})
    r = b.post(f"/api/goals/{g['id']}/contributions", json={"amount": "100", "note": "birthday money"}).json()
    assert Decimal(r["saved"]) == 400 and Decimal(r["progress"]) == Decimal("0.4") and Decimal(r["remaining"]) == 600
    shares = {m["name"]: (Decimal(m["saved"]), Decimal(m["share"])) for m in r["members"]}
    assert shares == {"A": (Decimal(300), Decimal("0.75")), "B": (Decimal(100), Decimal("0.25"))}
    assert r["days_left"] == 150 and Decimal(r["per_month_needed"]) > 0 and r["projected_date"] is not None
    # taking money back out
    out = a.post(f"/api/goals/{g['id']}/contributions", json={"amount": "-50"}).json()
    assert Decimal(out["saved"]) == 350


def test_achieved_and_on_track():
    a, b, hid = setup()
    g = new_goal(a, hid, target="100", deadline=(T + timedelta(days=60)).isoformat())
    r = a.post(f"/api/goals/{g['id']}/contributions", json={"amount": "120"}).json()
    assert r["achieved"] and Decimal(r["progress"]) == 1 and r["on_track"] is True and r["per_month_needed"] is None
    slow = new_goal(a, hid, name="Car", target="10000", deadline=(T + timedelta(days=60)).isoformat())
    s = a.post(f"/api/goals/{slow['id']}/contributions", json={"amount": "10", "made_on": (T - timedelta(days=80)).isoformat()}).json()
    assert s["on_track"] is False


def test_from_statement_and_auto_rule():
    a, b, hid = setup()
    acc = create_account(a)
    g = new_goal(a, hid)
    upload(a, acc, "s.csv", f"Date,Description,Amount\n{T.isoformat()},TRANSFER TO HOLIDAY POT,-200.00\n".encode())
    tx = a.get(f"/api/accounts/{acc}/transactions").json()[0]
    r = a.post(f"/api/goals/{g['id']}/from-transaction", json={"transaction_id": tx["id"], "always": True})
    assert r.status_code == 201 and Decimal(r.json()["saved"]) == 200 and len(r.json()["my_rules"]) == 1
    assert a.post(f"/api/goals/{g['id']}/from-transaction", json={"transaction_id": tx["id"]}).status_code == 409
    # next month's statement: the transfer counts automatically
    upload(a, acc, "s2.csv", f"Date,Description,Amount\n{(T + timedelta(days=1)).isoformat()},TRANSFER TO HOLIDAY POT,-150.00\n".encode())
    assert Decimal(a.get(f"/api/goals/{g['id']}").json()["saved"]) == 350
    # b can see who saved what, but can't claim a's statement line or remove a's contribution
    gb = b.get(f"/api/goals/{g['id']}").json()
    assert {m["name"] for m in gb["members"]} == {"A"} and gb["my_rules"] == []
    assert b.post(f"/api/goals/{g['id']}/from-transaction", json={"transaction_id": tx["id"]}).status_code in (403, 404)
    cid = gb["recent"][0]["id"]
    assert b.delete(f"/api/goal-contributions/{cid}").status_code == 403
    assert a.delete(f"/api/goal-contributions/{cid}").status_code == 204


def test_summary_widget_and_isolation():
    a, b, hid = setup()
    x = make_user("x")
    g = new_goal(a, hid)
    a.post(f"/api/goals/{g['id']}/contributions", json={"amount": "250"})
    s = a.get(f"/api/households/{hid}/summary").json()
    assert [(w["name"], Decimal(w["progress"])) for w in s["goals"]] == [("Holiday", Decimal("0.25"))]
    assert x.get(f"/api/goals/{g['id']}").status_code == 404
    assert x.post(f"/api/goals/{g['id']}/contributions", json={"amount": "1"}).status_code == 404
    assert x.get(f"/api/households/{hid}/goals").status_code == 404
    a.patch(f"/api/goals/{g['id']}", json={"archived": True})
    assert a.get(f"/api/households/{hid}/goals").json() == []
    assert len(a.get(f"/api/households/{hid}/goals?include_archived=true").json()) == 1
    assert any(e["action"] == "goal.contribute" for e in a.get(f"/api/audit?household_id={hid}").json())


def test_validation():
    a, b, hid = setup()
    assert a.post(f"/api/households/{hid}/goals", json={"name": "x", "target": "0", "currency": "GBP"}).status_code == 422
    assert a.post(f"/api/households/{hid}/goals", json={"name": "x", "target": "5", "currency": "GBP",
                                                        "deadline": T.isoformat()}).status_code == 422
    g = new_goal(a, hid)
    assert a.post(f"/api/goals/{g['id']}/contributions", json={"amount": "0"}).status_code == 422
