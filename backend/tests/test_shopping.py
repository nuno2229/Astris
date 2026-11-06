import json
from decimal import Decimal

from app.categorise import worker
from app.notify import channels
from tests.conftest import create_household, invite_and_join, make_user


def setup():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    lst = a.get(f"/api/households/{hid}/shopping-lists").json()[0]
    return a, b, hid, lst


def items(c, lid):
    return {i["name"]: i for i in c.get(f"/api/shopping-lists/{lid}").json()["items"]}


def test_shared_default_list_add_merge_tick_and_remove():
    a, b, hid, lst = setup()
    assert lst["is_default"] and lst["scope"] == "household" and lst["can_delete"] is False
    a.post(f"/api/shopping-lists/{lst['id']}/items", json={"name": "Milk"})
    b.post(f"/api/shopping-lists/{lst['id']}/items", json={"name": "milk", "quantity": "2", "note": "semi-skimmed"})
    it = items(a, lst["id"])["Milk"]
    assert Decimal(it["quantity"]) == 3 and it["note"] == "semi-skimmed" and it["added_by"] == "A"
    d = b.patch(f"/api/shopping-items/{it['id']}", json={"checked": True}).json()
    assert d["open_items"] == 0 and d["items"][0]["checked"]
    assert len(b.delete(f"/api/shopping-items/{it['id']}").json()["items"]) == 0
    assert a.delete(f"/api/shopping-lists/{lst['id']}").status_code == 403          # default list stays


def test_personal_list_private_and_logged_privately():
    a, b, hid, _ = setup()
    p = a.post(f"/api/households/{hid}/shopping-lists", json={"name": "Birthday surprise", "scope": "personal"}).json()
    a.post(f"/api/shopping-lists/{p['id']}/items", json={"name": "Cake"})
    assert [l["name"] for l in b.get(f"/api/households/{hid}/shopping-lists").json()] == ["Shopping"]
    assert b.get(f"/api/shopping-lists/{p['id']}").status_code == 404
    assert not any("Cake" in e["summary"] for e in a.get(f"/api/audit?household_id={hid}").json())   # not household-visible
    assert any("Cake" in e["summary"] for e in a.get("/api/audit").json())


def test_from_pantry_and_finish_into_pantry():
    a, b, hid, lst = setup()
    milk = a.post(f"/api/households/{hid}/products", json={"name": "Milk", "restock_below": "1", "quantity": "1"}).json()
    d = a.post(f"/api/shopping-lists/{lst['id']}/from-pantry").json()
    m = items(a, lst["id"])["Milk"]
    assert m["source"] == "pantry" and Decimal(m["quantity"]) == 1 and Decimal(m["in_pantry"]) == 1
    a.post(f"/api/shopping-lists/{lst['id']}/items", json={"name": "Flowers"})
    a.patch(f"/api/shopping-items/{m['id']}", json={"checked": True})
    left = a.post(f"/api/shopping-lists/{lst['id']}/finish", json={"to_pantry": True}).json()
    assert [i["name"] for i in left["items"]] == ["Flowers"]
    assert Decimal(a.get(f"/api/households/{hid}/stock").json()[0]["on_hand"]) == 2


def test_favourites_scope():
    a, b, hid, lst = setup()
    a.post(f"/api/households/{hid}/favourites", json={"name": "Oat milk", "scope": "personal"})
    a.post(f"/api/households/{hid}/favourites", json={"name": "Coffee", "scope": "household"})
    assert a.post(f"/api/households/{hid}/favourites", json={"name": "coffee", "scope": "household"}).status_code == 409
    assert [f["name"] for f in b.get(f"/api/households/{hid}/favourites").json()] == ["Coffee"]
    a.post(f"/api/shopping-lists/{lst['id']}/items", json={"name": "Coffee"})
    assert items(b, lst["id"])["Coffee"]["favourite"] is True


def test_assistant_adds_to_list_after_confirm():
    a, b, hid, lst = setup()
    r = a.post("/api/copilot/ask", json={"household_id": hid, "message": "add 2 loaves of bread to the shopping list"}).json()
    assert "Add 2× bread" in r["action"]["summary"] and items(a, lst["id"]) == {}
    a.post(f"/api/copilot/actions/{r['action']['id']}/confirm")
    assert items(a, lst["id"])["Bread"]["source"] == "assistant"
    assert "Bread ×2" in a.post("/api/copilot/ask", json={"household_id": hid, "message": "what's on the list?"}).json()["answer"]


def test_others_are_notified_once(monkeypatch):
    sent = []
    monkeypatch.setattr(channels, "pinned_request", lambda url, method="GET", body=None, **k: (sent.append(json.loads(body)), (204, b""))[1])
    a, b, hid, lst = setup()
    for c in (a, b):
        c.post("/api/me/notifications/channels", json={"kind": "discord", "allow_internet": True, "include_details": True,
               "config": {"webhook_url": "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_ABCDEF-123"}})
    a.post(f"/api/shopping-lists/{lst['id']}/items", json={"name": "Eggs"})
    worker.run_notifications()
    assert len(sent) == 1 and "A added Eggs" in sent[0]["content"]           # only B is told, not A
    worker.run_notifications()
    assert len(sent) == 1


def test_isolation():
    a, b, hid, lst = setup()
    x = make_user("x")
    a.post(f"/api/shopping-lists/{lst['id']}/items", json={"name": "Milk"})
    assert x.get(f"/api/shopping-lists/{lst['id']}").status_code == 404
    assert x.post(f"/api/shopping-lists/{lst['id']}/items", json={"name": "x"}).status_code == 404
    iid = items(a, lst["id"])["Milk"]["id"]
    assert x.patch(f"/api/shopping-items/{iid}", json={"checked": True}).status_code == 404
    assert x.get(f"/api/households/{hid}/favourites").status_code == 404


def test_personal_list_items_never_notify_others(monkeypatch):
    sent = []
    monkeypatch.setattr(channels, "pinned_request", lambda url, method="GET", body=None, **k: (sent.append(json.loads(body)), (204, b""))[1])
    a, b, hid, _ = setup()
    b.post("/api/me/notifications/channels", json={"kind": "discord", "allow_internet": True, "include_details": True,
           "config": {"webhook_url": "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_ABCDEF-123"}})
    p = a.post(f"/api/households/{hid}/shopping-lists", json={"name": "Surprise", "scope": "personal"}).json()
    a.post(f"/api/shopping-lists/{p['id']}/items", json={"name": "Birthday cake"})
    worker.run_notifications()
    assert not any("cake" in m["content"].lower() for m in sent)
