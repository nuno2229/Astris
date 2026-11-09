import json
import threading
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.receipts.parse import parse
from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()
RECEIPT = f"""TESCO
Superstore Holloway
TESCO SEMI SKIMMED MILK 2L      1.65
HEINZ BEANS 4X415G               1.70
 2 x 0.85
BANANAS LOOSE
 0.856 kg @ £0.94/kg             0.80
CLUBCARD PRICE                  -0.30
TOTAL                            3.85
VISA CONTACTLESS                 3.85
{T.strftime('%d/%m/%Y')} 14:32""".encode()


def setup():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    acc = create_account(a)
    a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    upload(a, acc, "s.csv", f"Date,Description,Amount\n{T.isoformat()},CARD PAYMENT TO TESCO STORES 2041,-3.85\n".encode())
    return a, b, hid, acc


def send(c, hid, body=RECEIPT, name="receipt.txt", keep=False):
    r = c.post(f"/api/households/{hid}/receipts", files=[("files", (name, body))], data={"keep_original": str(keep).lower()})
    assert r.status_code == 201, r.text
    return r.json()


def test_parser_unit_prices_discounts_and_split():
    r = parse(RECEIPT.decode().splitlines())[0]
    assert (r.store, r.total, r.adds_up()) == ("Tesco", Decimal("3.85"), True)
    milk, beans, bananas = r.items
    assert milk.unit_price() == (Decimal("0.0825"), "100ml")                 # £1.65 / 2000 ml * 100
    assert beans.quantity == 2 and beans.size_qty == 1660
    assert bananas.name == "Bananas Loose" and bananas.discount == Decimal("-0.30") and bananas.size_unit == "kg"
    two = parse("ALDI STORES\nMILK 1L 0.95\nBREAD 800G 0.75\nTOTAL 1.70\nCARD 1.70\n01/11/2026\nLIDL\nEGGS 6PK 1.20\nTOTAL 1.20\n02/11/2026".splitlines())
    assert [(x.store, str(x.purchased_on), x.total) for x in two] == [("Aldi", "2026-11-01", Decimal("1.70")), ("Lidl", "2026-11-02", Decimal("1.20"))]


def test_upload_review_match_and_save_into_pantry_and_list():
    a, b, hid, acc = setup()
    r = send(a, hid)[0]
    assert r["status"] == "review" and r["adds_up"] is True and len(r["items"]) == 3
    tx = a.get(f"/api/accounts/{acc}/transactions").json()[0]
    assert [c["id"] for c in r["candidates"]] == [tx["id"]]                   # reconciles onto the bank payment
    beans = [i for i in r["items"] if i["name"].startswith("Heinz")][0]
    saved = b.post(f"/api/receipts/{r['id']}/save", json={"transaction_id": tx["id"], "to_pantry": True, "to_list": [beans["id"]]}).json()
    assert saved["status"] == "saved" and saved["transaction_id"] == tx["id"]
    pantry = {p["name"]: p for p in a.get(f"/api/households/{hid}/stock").json()}
    assert Decimal(pantry["Heinz Beans 4X415G"]["on_hand"]) == 2
    lst = a.get(f"/api/households/{hid}/shopping-lists").json()[0]
    assert [i["name"] for i in a.get(f"/api/shopping-lists/{lst['id']}").json()["items"]] == ["Heinz Beans 4X415G"]
    again = send(a, hid)[0]
    assert again["candidates"] == []                                          # that payment already has a receipt


def test_editing_recomputes_unit_price_and_total_check():
    a, b, hid, _ = setup()
    r = send(a, hid)[0]
    milk = r["items"][0]
    out = a.patch(f"/api/receipt-items/{milk['id']}", json={"line_price": "2.00"}).json()
    assert out["adds_up"] is False
    assert Decimal([i for i in out["items"] if i["id"] == milk["id"]][0]["unit_price"]) == Decimal("0.1")
    out = a.patch(f"/api/receipts/{r['id']}", json={"total": "4.20"}).json()
    assert out["adds_up"] is True


def test_keep_original_receipt_and_statement_privacy():
    a, b, hid, acc = setup()
    r = send(a, hid, keep=True)[0]
    doc = r["documents"][0]
    d = b.get(f"/api/documents/{doc['id']}")                                  # household can see a kept receipt
    assert d.status_code == 200 and d.content == RECEIPT
    assert d.headers["content-disposition"].startswith("attachment") and "sandbox" in d.headers["content-security-policy"]
    x = make_user("x")
    assert x.get(f"/api/documents/{doc['id']}").status_code == 404
    # statements: owner only, even though the account is shared with b
    s = a.post(f"/api/accounts/{acc}/imports", files={"file": ("st.csv", b"Date,Description,Amount\n2026-11-02,ALDI,-1.00\n")},
               data={"keep_original": "true"})
    assert s.status_code == 201
    docs = a.get(f"/api/accounts/{acc}/documents").json()
    assert len(docs) == 1
    assert b.get(f"/api/documents/{docs[0]['id']}").status_code == 404
    assert b.get(f"/api/accounts/{acc}/documents").json() == []
    assert a.delete(f"/api/documents/{docs[0]['id']}").status_code == 204
    # without the option nothing is kept
    assert send(a, hid)[0]["documents"] == []


def test_html_disguised_as_receipt_is_never_served_inline():
    a, b, hid, _ = setup()
    evil = b"<script>alert(1)</script>\nTESCO\nMILK 1.00\nTOTAL 1.00\n"
    r = send(a, hid, body=evil, name="x.html", keep=True)[0]
    d = a.get(f"/api/documents/{r['documents'][0]['id']}")
    assert d.headers["content-type"].startswith("text/plain") and d.headers["content-disposition"].startswith("attachment")


@pytest.fixture
def fake_llm():
    state = {"reply": None}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            out = json.dumps({"choices": [{"message": {"content": json.dumps(state["reply"])}}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(out)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_port}"
    yield state
    srv.shutdown()


def test_ai_helps_with_messy_receipts_but_cannot_invent_prices(fake_llm):
    a, b, hid, _ = setup()
    a.put(f"/api/households/{hid}/settings", json={"llm_enabled": True, "llm_base_url": fake_llm["url"]})
    messy = b"T E S C 0\nmlk semi 2l . . . 1.65\nbred wht . . 0.95\nTTL 2.60\n"     # rules can't find the total here
    fake_llm["reply"] = {"store": "Tesco", "date": T.isoformat(), "total": 2.60, "items": [
        {"name": "Semi skimmed milk 2L", "quantity": 1, "line_price": 1.65},
        {"name": "White bread", "quantity": 1, "line_price": 0.95},
        {"name": "Champagne", "quantity": 1, "line_price": 99.99}]}                # not on the receipt -> dropped
    r = send(a, hid, body=messy)[0]
    assert r["read_by"] == "ai" and [i["name"] for i in r["items"]] == ["Semi Skimmed Milk 2L", "White Bread"]
    assert r["adds_up"] is True


def test_isolation():
    a, b, hid, _ = setup()
    x = make_user("x")
    r = send(a, hid)[0]
    assert x.get(f"/api/receipts/{r['id']}").status_code == 404
    assert x.post(f"/api/households/{hid}/receipts", files=[("files", ("r.txt", RECEIPT))]).status_code == 404
    assert x.patch(f"/api/receipt-items/{r['items'][0]['id']}", json={"line_price": "0"}).status_code == 404


def test_only_uploader_or_admin_deletes_receipt_and_its_photo():
    a, b, hid, _ = setup()
    m = make_user("m")
    invite_and_join(a, m, hid)
    r = send(b, hid, keep=True)[0]                     # b (member) uploads with a kept photo
    assert m.delete(f"/api/receipts/{r['id']}").status_code == 403
    assert m.get(f"/api/documents/{r['documents'][0]['id']}").status_code == 200   # photo still there
    assert a.delete(f"/api/receipts/{r['id']}").status_code == 204                  # household owner may


def test_quota_counts_files_from_households_you_left(monkeypatch):
    from app import documents
    a, b, hid, _ = setup()
    send(b, hid, keep=True)
    b.delete(f"/api/households/{hid}/members/{b.me['id']}")             # b leaves: that photo is no longer visible to b
    hid2 = create_household(b)
    monkeypatch.setattr(documents, "USER_QUOTA", len(RECEIPT) + 10)
    r = b.post(f"/api/households/{hid2}/receipts", files=[("files", ("r.txt", RECEIPT))], data={"keep_original": "true"})
    assert r.status_code == 413


MIXED = f"""TESCO
Superstore Holloway
TESCO SEMI SKIMMED MILK 2L      1.65
ANDREX TOILET ROLL 9PK           5.00
COLGATE TOOTHPASTE 75ML          2.00
PARACETAMOL 16S                  0.45
TOTAL                            9.10
VISA CONTACTLESS                 9.10
{T.strftime('%d/%m/%Y')} 14:32""".encode()


def test_saving_a_mixed_receipt_splits_the_payment():
    from app.receipts.kinds import item_category
    assert item_category("Andrex Toilet Roll 9pk") == "Shopping" and item_category("Colgate toothpaste") == "Personal care"
    assert item_category("Whiskas cat food 12x85g") == "Pets" and item_category("Hovis bread") == "Groceries"
    a, b, hid, acc = setup()
    upload(a, acc, "s2.csv", f"Date,Description,Amount\n{T.isoformat()},TESCO STORES 2041 HOLLOWAY,-9.00\n".encode())
    tx = next(t for t in a.get(f"/api/accounts/{acc}/transactions").json() if t["amount"] == "-9.0000")
    r = send(a, hid, MIXED)[0]
    a.post(f"/api/receipts/{r['id']}/save", json={"transaction_id": tx["id"]})      # paid £9.00: 10p loyalty saving shared out
    parts = {p["category"]: p["amount"] for p in a.get(f"/api/transactions/{tx['id']}/splits").json()}
    assert set(parts) == {"Shopping", "Personal care", "Groceries", "Health"}
    assert sum(Decimal(v) for v in parts.values()) == Decimal("-9.00")
    assert Decimal(parts["Shopping"]) == Decimal("-4.95")


def test_food_only_or_someone_elses_payment_is_not_split():
    a, b, hid, acc = setup()
    tx = a.get(f"/api/accounts/{acc}/transactions").json()[0]
    r = send(b, hid)[0]
    b.post(f"/api/receipts/{r['id']}/save", json={"transaction_id": tx["id"]})       # all food, and b doesn't own the account
    assert a.get(f"/api/transactions/{tx['id']}/splits").json() == []
