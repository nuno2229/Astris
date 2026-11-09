import json
from datetime import date
from decimal import Decimal

from app.routers import prices as prices_mod
from tests.conftest import create_household, invite_and_join, make_user

T = date.today()


def receipt(c, hid, store, lines, total):
    body = f"{store}\n{lines}\nTOTAL {total}\n{T.strftime('%d/%m/%Y')}\n".encode()
    r = c.post(f"/api/households/{hid}/receipts", files=[("files", ("r.txt", body))]).json()[0]
    c.post(f"/api/receipts/{r['id']}/save", json={})
    return r


def setup():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    receipt(a, hid, "TESCO", "TESCO SEMI SKIMMED MILK 2L 1.65\nHEINZ BEANS 415G 1.40", "3.05")
    receipt(a, hid, "ALDI STORES", "ALDI SEMI SKIMMED MILK 4 PINTS 1.45\nHEINZ BEANS 415G 1.10", "2.55")
    return a, b, hid


def test_price_book_from_receipts_compares_like_for_like():
    a, b, hid = setup()
    book = {p["key"]: p for p in b.get(f"/api/households/{hid}/prices").json()["products"]}
    milk = book["semi skimmed milk"]
    assert milk["basis"] == "100ml" and milk["cheapest"]["store"] == "Aldi"            # 1.45/2272ml beats 1.65/2000ml
    assert [s["store"] for s in milk["stores"]] == ["Aldi", "Tesco"] and Decimal(milk["saving"]) > Decimal("0.2")
    assert milk["cheapest"]["source"] == "your receipt" and milk["cheapest"]["stale"] is False
    assert book["heinz beans"]["cheapest"]["store"] == "Aldi"


def test_shopping_list_tags_cheapest_shop():
    a, b, hid = setup()
    lst = a.get(f"/api/households/{hid}/shopping-lists").json()[0]
    d = a.post(f"/api/shopping-lists/{lst['id']}/items", json={"name": "Semi skimmed milk"}).json()
    assert d["items"][0]["cheapest"]["store"] == "Aldi"
    d = a.post(f"/api/shopping-lists/{lst['id']}/items", json={"name": "Saffron"}).json()
    assert [i["cheapest"] for i in d["items"] if i["name"] == "Saffron"] == [None]


def test_online_prices_opt_in_uk_only(monkeypatch):
    a, b, hid = setup()
    a.post(f"/api/households/{hid}/products", json={"name": "Semi skimmed milk", "barcode": "5000112637922"})
    calls = []

    def fake(code):
        calls.append(code)
        return [
            {"price": 1.30, "currency": "GBP", "date": T.isoformat(), "location": {"osm_brand": "Sainsbury's", "osm_address_country_code": "GB"},
             "product": {"product_quantity": 2272, "product_quantity_unit": "ml"}},
            {"price": 0.50, "currency": "EUR", "date": T.isoformat(), "location": {"osm_name": "Carrefour", "osm_address_country_code": "FR"},
             "product": {"product_quantity": 1000, "product_quantity_unit": "ml"}},
        ]
    monkeypatch.setattr(prices_mod, "_fetch", fake)
    assert a.post(f"/api/households/{hid}/prices/refresh").status_code == 409             # off by default
    assert b.put(f"/api/households/{hid}/prices-online", json={"enabled": True}).status_code == 403
    a.put(f"/api/households/{hid}/prices-online", json={"enabled": True})
    assert a.post(f"/api/households/{hid}/prices/refresh").json()["updated"] == 1
    milk = {p["key"]: p for p in a.get(f"/api/households/{hid}/prices").json()["products"]}["semi skimmed milk"]
    assert milk["cheapest"]["store"] == "Sainsbury's" and milk["cheapest"]["source"] == "Open Prices"
    assert "Carrefour" not in json.dumps(milk)
    a.post(f"/api/households/{hid}/prices/refresh")
    assert calls == ["5000112637922"]                                                   # cached for a week


def test_isolation():
    a, b, hid = setup()
    x = make_user("x")
    assert x.get(f"/api/households/{hid}/prices").status_code == 404
    xh = create_household(x)
    assert x.get(f"/api/households/{xh}/prices").json()["products"] == []


def test_home_shopping_summary():
    from tests.conftest import create_household, make_user
    a = make_user("a")
    hid = create_household(a)
    lst = next(l for l in a.get(f"/api/households/{hid}/shopping-lists").json() if l["scope"] == "household")
    a.post(f"/api/shopping-lists/{lst['id']}/items", json={"name": "Semi skimmed milk"})
    for store, price in (("Tesco", "1.75"), ("Aldi", "1.45")):
        a.post(f"/api/households/{hid}/web-prices", json={"url": f"https://www.{'tesco.com' if store == 'Tesco' else 'aldi.co.uk'}/p",
                                                       "name": "Semi Skimmed Milk 4 Pints", "price": price})
    r = a.get(f"/api/households/{hid}/home-shopping").json()
    assert r["list_items"] == 1 and r["cheapest"] == {"store": "Aldi", "items": 1} and r["trend"] is None
