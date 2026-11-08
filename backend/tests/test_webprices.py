"""G-17 (PO 2026-11-08): prices from shop websites: sent by you from a product page, or opt-in automatic lookups."""
from decimal import Decimal

from app import webprices
from tests.conftest import create_household, invite_and_join, make_user

MORRISONS = """£0.78
Add
Morrisons British Semi Skimmed Milk 4 Pint
LIFE 1w+
Morrisons British Semi Skimmed Milk 4 Pint
Unbeatable Price
2.272L
Ordinarily 77.0p/litre
(77.0p/litre)
Price
£1.75
Add
Morrisons British Carrots 1kg
(69.0p/kg)
Price
£0.69
Add"""
ALDI = """Plushie friends from only £3.49 each
DESSERT MENU
Evaporated Milk
410 g
(£1.20/1 KG)
Save 5%
£0.49£0.52"""


def test_rules_read_item_price_not_unit_price():
    assert webprices.extract_rules(MORRISONS, "semi skimmed milk") == {"name": "Morrisons British Semi Skimmed Milk 4 Pint", "price": "1.75"}
    assert webprices.extract_rules(ALDI, "evaporated milk") == {"name": "Evaporated Milk", "price": "0.49"}
    assert webprices.extract_rules(ALDI, "semi skimmed milk") is None


def test_shop_from_address_only():
    assert webprices.shop_for("https://www.tesco.com/groceries/en-GB/products/123") == "Tesco"
    assert webprices.shop_for("https://tesco.com.evil.example/x") is None
    assert webprices.clean_url("https://www.sainsburys.co.uk/gol-ui/product/milk?utm=1#x") == "https://www.sainsburys.co.uk/gol-ui/product/milk"
    assert webprices.clean_url("http://www.tesco.com/x") is None


def test_send_price_from_a_product_page():
    a, x = make_user("a"), make_user("x")
    hid = create_household(a)
    r = a.post(f"/api/households/{hid}/web-prices", json={"url": "https://www.tesco.com/groceries/en-GB/products/1?ref=x",
                                                         "name": "Tesco British Semi Skimmed Milk 2.272L, 4 Pints", "price": "1.75"})
    assert r.status_code == 201 and r.json()["store"] == "Tesco"
    assert a.post(f"/api/households/{hid}/web-prices", json={"url": "https://example.com/p", "name": "x", "price": "1"}).status_code == 422
    assert x.post(f"/api/households/{hid}/web-prices", json={"url": "https://www.tesco.com/p", "name": "x", "price": "1"}).status_code == 404
    book = a.get(f"/api/households/{hid}/prices").json()["products"]
    seen = book[0]["stores"][0]
    assert seen["store"] == "Tesco" and seen["source"] == "shop website (you)" and seen["basis"] == "100ml"


def test_automatic_lookups_use_the_shared_list_only(monkeypatch):
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    lists = a.get(f"/api/households/{hid}/shopping-lists").json()
    shared = next(l for l in lists if l["scope"] == "household")
    a.post(f"/api/shopping-lists/{shared['id']}/items", json={"name": "Semi skimmed milk"})
    mine = b.post(f"/api/households/{hid}/shopping-lists", json={"name": "Private", "scope": "personal"}).json()
    b.post(f"/api/shopping-lists/{mine['id']}/items", json={"name": "Secret thing"})
    assert b.put(f"/api/households/{hid}/web-lookups", json={"enabled": True}).status_code == 403
    st = a.put(f"/api/households/{hid}/web-lookups", json={"enabled": True}).json()
    assert st["enabled"] and st["searches"] == ["Semi skimmed milk"]
    pages = {"Morrisons": MORRISONS, "Aldi": ALDI}
    monkeypatch.setattr(webprices, "page_text", lambda url: pages["Morrisons" if "morrisons" in url else "Aldi"])
    from app.routers.prices import run_lookups
    assert run_lookups(a.me["id"], hid) == 1
    assert run_lookups(a.me["id"], hid) == 0               # cached for a week
    p = a.get(f"/api/households/{hid}/prices").json()["products"]
    assert p[0]["stores"][0]["store"] == "Morrisons" and Decimal(p[0]["stores"][0]["price"]) == Decimal("1.75")


def test_ai_answer_must_be_on_the_page(monkeypatch):
    import json
    from app import netcheck
    reply = {"name": "Morrisons British Semi Skimmed Milk 4 Pint", "price": "0.10"}
    monkeypatch.setattr(netcheck, "pinned_request", lambda *a, **k: (200, json.dumps(
        {"choices": [{"message": {"content": json.dumps(reply)}}]}).encode()))
    assert webprices.extract_ai(MORRISONS, "milk", "http://127.0.0.1:8080", None, False) is None     # £0.10 isn't on the page
    reply["price"] = "1.75"
    assert webprices.extract_ai(MORRISONS, "milk", "http://127.0.0.1:8080", None, False)["price"] == "1.75"


def test_run_is_rate_limited_even_if_it_fails(monkeypatch):
    a = make_user("a")
    hid = create_household(a)
    shared = next(l for l in a.get(f"/api/households/{hid}/shopping-lists").json() if l["scope"] == "household")
    a.post(f"/api/shopping-lists/{shared['id']}/items", json={"name": "Milk"})
    a.put(f"/api/households/{hid}/web-lookups", json={"enabled": True})
    from app.routers import prices
    monkeypatch.setattr(prices, "run_lookups", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    import threading
    monkeypatch.setattr(threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: prices._running.discard(args[1])})())
    assert a.post(f"/api/households/{hid}/web-lookups/run").status_code == 202
    assert a.post(f"/api/households/{hid}/web-lookups/run").status_code == 429
