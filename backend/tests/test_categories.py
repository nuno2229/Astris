"""G-13 household categories; G-14 the account owner picks which household's AI labels the account."""
import json

from app.categorise import worker
from tests.conftest import create_account, create_household, invite_and_join, make_user, upload
from tests.test_categorise import CSV, fake_llm, setup_household, txs_by_desc  # noqa: F401


def test_household_categories_add_use_remove():
    a, hid, acc = setup_household()
    b, outsider = make_user("b"), make_user("c")
    invite_and_join(a, b, hid)
    r = b.post(f"/api/households/{hid}/categories", json={"name": "  Allotment  "})
    assert r.status_code == 201 and r.json()["name"] == "Allotment"
    assert b.post(f"/api/households/{hid}/categories", json={"name": "allotment"}).status_code == 409
    assert b.post(f"/api/households/{hid}/categories", json={"name": "groceries"}).status_code == 409
    assert b.post(f"/api/households/{hid}/categories", json={"name": "<script>"}).status_code == 422
    assert outsider.post(f"/api/households/{hid}/categories", json={"name": "Nope"}).status_code == 404
    assert "Allotment" in a.get(f"/api/categories?household_id={hid}").json()
    assert "Allotment" not in outsider.get("/api/categories").json()
    # usable on a transaction and in a budget
    upload(a, acc, "s.csv", CSV)
    t = txs_by_desc(a, acc)["ZORBLAX GADGETS 77"]
    assert a.patch(f"/api/transactions/{t['id']}", json={"category": "Allotment", "remember": False}).json()["transaction"]["category"] == "Allotment"
    assert outsider.post(f"/api/households/{create_household(outsider)}/budgets", json={
        "kind": "category", "category": "Allotment", "period": "monthly", "amount": "50", "currency": "GBP"}).status_code == 422
    assert a.post(f"/api/households/{hid}/budgets", json={
        "kind": "category", "category": "Allotment", "period": "monthly", "amount": "50", "currency": "GBP"}).status_code == 201
    # the creator or an owner can remove; another member can't; the label stays on the transaction
    cats = {c["name"]: c for c in a.get(f"/api/households/{hid}/categories").json()}
    c2 = a.post(f"/api/households/{hid}/categories", json={"name": "Boat"}).json()
    assert b.delete(f"/api/households/{hid}/categories/{c2['id']}").status_code == 403
    assert a.delete(f"/api/households/{hid}/categories/{cats['Allotment']['id']}").status_code == 204
    assert txs_by_desc(a, acc)["ZORBLAX GADGETS 77"]["category"] == "Allotment"
    assert "Allotment" not in a.get(f"/api/categories?household_id={hid}").json()


def test_ai_uses_the_household_the_owner_picked(fake_llm):
    fake_llm["reply"] = lambda txs: {"results": [{"i": x["i"], "category": "Allotment", "confidence": 0.9} for x in txs]}
    a, home, acc = setup_household()                      # first household: no AI
    other = create_household(a, "Parents")
    a.put(f"/api/households/{other}/accounts/{acc}", json={"attribution": "hide"})
    assert a.put(f"/api/households/{other}/settings", json={"llm_enabled": True, "llm_base_url": fake_llm["url"]}).status_code == 200
    a.post(f"/api/households/{other}/categories", json={"name": "Allotment"})
    # off: nothing is sent anywhere
    assert a.put(f"/api/accounts/{acc}/ai", json={"mode": "off"}).json()["mode"] == "off"
    upload(a, acc, "s.csv", CSV)
    worker.run_once()
    assert fake_llm["requests"] == []
    # the picked household's AI is used, with that household's categories offered
    r = a.put(f"/api/accounts/{acc}/ai", json={"mode": "household", "household_id": other}).json()
    assert r["mode"] == "household" and {o["name"]: o["ai_on"] for o in r["options"]} == {"Home": False, "Parents": True}
    a.put(f"/api/households/{home}/settings", json={"llm_enabled": False})
    upload(a, acc, "s2.csv", CSV.replace(b"ZORBLAX GADGETS 77", b"QUIBBLE CO 12"))
    worker.run_once()
    body = fake_llm["requests"][-1]
    assert "Allotment" in body["messages"][0]["content"]
    assert "Allotment" in body["response_format"]["schema"]["properties"]["results"]["items"]["properties"]["category"]["enum"]
    assert txs_by_desc(a, acc)["QUIBBLE CO 12"]["category"] == "Allotment"
    # can't pick a household the account isn't in; others can't see or change the choice
    b = make_user("b")
    assert a.put(f"/api/accounts/{acc}/ai", json={"mode": "household", "household_id": create_household(b)}).status_code == 422
    assert b.get(f"/api/accounts/{acc}/ai").status_code in (403, 404)
