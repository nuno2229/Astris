import json
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.categorise import worker
from app.db import get_engine
from app.notify import channels
from app.routers.inventory import human_amount, run_weekly
from tests.conftest import create_household, invite_and_join, make_user

T = date.today()
MILK = "5000112637922"


def setup():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    return a, b, hid


def product(c, hid, **kw):
    body = {"name": "Semi-skimmed milk", "barcode": MILK, "size_qty": "1", "size_unit": "l", "restock_below": "1",
            "quantity": "1", "expires_on": (T + timedelta(days=2)).isoformat(), **kw}
    r = c.post(f"/api/households/{hid}/products", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def stock(c, hid):
    return {p["name"]: p for p in c.get(f"/api/households/{hid}/stock").json()}


def test_scan_known_unknown_and_merge():
    a, b, hid = setup()
    product(a, hid)
    r = b.post(f"/api/households/{hid}/scan", json={"codes": [MILK, MILK, "123456789012", "bad code!", MILK]}).json()
    assert r["added"] == [{"product_id": r["added"][0]["product_id"], "name": "Semi-skimmed milk", "quantity": 3}]
    assert r["unknown"] == ["123456789012"]
    milk = stock(a, hid)["Semi-skimmed milk"]
    assert Decimal(milk["on_hand"]) == 4 and milk["amount_label"] == "4 l"
    # scans without a date merge into one undated batch, not three rows
    assert len(milk["lots"]) == 2 and "no expiry date" in milk["flags"]
    assert a.post(f"/api/households/{hid}/products", json={"name": "Dup", "barcode": MILK}).status_code == 409
    assert any(e["action"] == "stock.scan" for e in a.get(f"/api/audit?household_id={hid}").json())


def test_use_takes_soonest_expiring_first_and_expiry_states():
    a, b, hid = setup()
    p = product(a, hid, quantity="2", expires_on=(T + timedelta(days=10)).isoformat())
    a.post(f"/api/products/{p['id']}/lots", json={"quantity": "1", "expires_on": (T + timedelta(days=1)).isoformat()})
    a.post(f"/api/products/{p['id']}/lots", json={"quantity": "1", "expires_on": (T - timedelta(days=1)).isoformat()})
    m = stock(a, hid)["Semi-skimmed milk"]
    assert [l["state"] for l in m["lots"]] == ["expired", "soon", "fresh"]
    r = a.post(f"/api/products/{p['id']}/use", json={"quantity": "1.5"}).json()   # expired one, then half of 'soon'
    assert [(l["state"], Decimal(l["quantity"])) for l in r["lots"]] == [("soon", Decimal("0.5")), ("fresh", Decimal(2))]
    a.post(f"/api/products/{p['id']}/lots", json={"quantity": "1", "expires_on": (T - timedelta(days=3)).isoformat()})
    after = a.post(f"/api/households/{hid}/stock/clear-expired").json()
    assert all(l["state"] != "expired" for l in after[0]["lots"])
    s = a.get(f"/api/households/{hid}/summary").json()["stock"]
    assert s["expiring"] == [{"name": "Semi-skimmed milk", "days_left": 1}] and s["expired"] == 0


def test_units_standardised():
    assert human_amount(Decimal("3"), "g") == "3 g" and human_amount(Decimal("1500"), "g") == "1.5 kg"
    assert human_amount(Decimal("0.5"), "kg") == "500 g" and human_amount(Decimal("2.25"), "l") == "2.25 l"
    assert human_amount(Decimal("4"), "item") is None


def test_weekly_shop_suggestions_low_and_trigger():
    a, b, hid = setup()
    product(a, hid, quantity="1", restock_below="1")                         # at threshold -> low
    product(a, hid, name="Eggs", barcode=None, quantity="12", restock_below="4", size_unit="item", size_qty="1")
    s = a.post(f"/api/households/{hid}/shop-suggestions").json()
    assert [(i["name"], i["reason"], i["buy"]) for i in s["items"]] == [("Semi-skimmed milk", "running low", 1.0)]
    assert b.put(f"/api/households/{hid}/shop-day", json={"day": T.weekday()}).status_code == 403
    a.put(f"/api/households/{hid}/shop-day", json={"day": T.weekday()})
    assert run_weekly(lambda: Session(get_engine())) == 1
    assert run_weekly(lambda: Session(get_engine())) == 0                    # once a day
    hist = a.get(f"/api/households/{hid}/shop-suggestions").json()
    assert [h["trigger"] for h in hist] == ["weekly", "manual"]


def test_expiry_notification(monkeypatch):
    sent = []
    monkeypatch.setattr(channels, "pinned_request", lambda url, method="GET", body=None, **k: (sent.append(json.loads(body)), (204, b""))[1])
    a, b, hid = setup()
    a.post("/api/me/notifications/channels", json={"kind": "discord", "allow_internet": True, "include_details": True,
           "config": {"webhook_url": "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_ABCDEF-123"}})
    product(a, hid)
    worker.run_notifications()
    assert "Semi-skimmed milk (" in sent[-1]["content"] and "expires in 2 days" in sent[-1]["content"]


def test_isolation():
    a, b, hid = setup()
    x = make_user("x")
    p = product(a, hid)
    xh = create_household(x)
    assert x.get(f"/api/households/{hid}/stock").status_code == 404
    assert x.post(f"/api/households/{hid}/scan", json={"codes": [MILK]}).status_code == 404
    assert x.post(f"/api/products/{p['id']}/use", json={"quantity": "1"}).status_code == 404
    assert x.delete(f"/api/lots/{p['lots'][0]['id']}").status_code == 404
    # same barcode in another household is a different product
    r = x.post(f"/api/households/{xh}/scan", json={"codes": [MILK]}).json()
    assert r["unknown"] == [MILK]
