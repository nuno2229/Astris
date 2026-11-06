from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()
D = lambda n: (T - timedelta(days=n)).isoformat()   # noqa: E731


def setup():
    a = make_user("a")
    hid = create_household(a)
    bank = create_account(a, "Current")
    wallet = create_account(a, "Wallet", type_="cash")
    for acc in (bank, wallet):
        a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    return a, hid, bank, wallet


def bal(c, acc):
    return Decimal(c.get(f"/api/accounts/{acc}").json()["balance"])


def manual(c, acc, amount, desc="Corner shop", n=1, **kw):
    r = c.post(f"/api/accounts/{acc}/transactions", json={"posted_on": D(n), "description": desc, "amount": amount, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_cash_wallet_balance_entries_and_count():
    a, hid, bank, wallet = setup()
    assert a.put(f"/api/accounts/{wallet}/opening", json={"balance": "50.00", "date": D(10)}).status_code == 200
    manual(a, wallet, "-4.50", "Bakery")
    manual(a, wallet, "20.00", "Birthday money", category="Gifts & donations")
    assert bal(a, wallet) == Decimal("65.50")
    r = a.post(f"/api/accounts/{wallet}/cash-count", json={"counted": "60.00", "date": T.isoformat()}).json()
    assert Decimal(r["expected"]) == Decimal("65.50") and Decimal(r["adjustment"]) == Decimal("-5.50")
    assert bal(a, wallet) == Decimal("60.00")
    # counting again with the same amount changes nothing
    assert Decimal(a.post(f"/api/accounts/{wallet}/cash-count", json={"counted": "60.00", "date": T.isoformat()}).json()["adjustment"]) == 0
    assert a.post(f"/api/accounts/{bank}/cash-count", json={"counted": "1", "date": T.isoformat()}).status_code == 422


def test_manual_entries_categorised_and_only_manual_deletable():
    a, hid, bank, wallet = setup()
    t = manual(a, wallet, "-3.20", "COSTA COFFEE")
    assert t["category"] == "Eating out" and t["source"] == "manual"
    upload(a, bank, "s.csv", f"Date,Description,Amount\n{D(2)},TESCO,-10.00\n".encode())
    imported = a.get(f"/api/accounts/{bank}/transactions").json()[0]
    assert a.delete(f"/api/transactions/{imported['id']}").status_code == 409
    assert a.delete(f"/api/transactions/{t['id']}").status_code == 204


def test_atm_withdrawal_moved_to_wallet_is_not_double_counted():
    a, hid, bank, wallet = setup()
    upload(a, bank, "s.csv", f"Date,Description,Amount\n{D(3)},CASH WITHDRAWAL LINK ATM,-100.00\n".encode())
    atm = a.get(f"/api/accounts/{bank}/transactions").json()[0]
    month = T.strftime("%Y-%m")
    before = a.get(f"/api/households/{hid}/summary?month={month}").json()
    r = a.post(f"/api/transactions/{atm['id']}/move-to-wallet", json={"wallet_id": wallet})
    assert r.status_code == 200 and r.json()["transfer_group"]
    assert bal(a, wallet) == Decimal("100.00")
    manual(a, wallet, "-30.00", "Market stall", n=2)
    s = a.get(f"/api/households/{hid}/summary?month={month}").json()
    spent = Decimal(s["included"][0]["spent"])
    assert spent == Decimal(before["included"][0]["spent"]) - 100 + 30   # ATM no longer spending; the stall is
    assert a.post(f"/api/transactions/{atm['id']}/move-to-wallet", json={"wallet_id": wallet}).status_code == 409
    # undo the move
    assert a.delete(f"/api/transfers/{r.json()['transfer_group']}").status_code == 204
    assert bal(a, wallet) == Decimal("-30.00")


def test_wallet_permissions():
    a, hid, bank, wallet = setup()
    b = make_user("b")
    invite_and_join(a, b, hid)
    b_wallet = create_account(b, "B cash", type_="cash")
    upload(a, bank, "s.csv", f"Date,Description,Amount\n{D(3)},ATM,-20.00\n".encode())
    atm = a.get(f"/api/accounts/{bank}/transactions").json()[0]
    assert a.post(f"/api/transactions/{atm['id']}/move-to-wallet", json={"wallet_id": b_wallet}).status_code == 404
    assert b.post(f"/api/transactions/{atm['id']}/move-to-wallet", json={"wallet_id": b_wallet}).status_code == 403
    assert b.post(f"/api/accounts/{wallet}/transactions", json={"posted_on": D(1), "description": "x", "amount": "-1"}).status_code == 403
    assert b.put(f"/api/accounts/{wallet}/opening", json={"balance": "1", "date": D(1)}).status_code == 403


def test_foreign_amounts_from_csv_and_atm_abroad():
    a, hid, bank, _ = setup()
    eur_wallet = create_account(a, "Euros", currency="EUR", type_="cash")
    csv_ = ("Date,Description,Amount,Original amount,Original currency\n"
            f"{D(4)},CAFE DE FLORE PARIS,-39.10,45.00,EUR\n"
            f"{D(4)},ATM PARIS,-173.80,200.00,EUR\n"
            f"{D(4)},TESCO,-12.00,,\n").encode()
    upload(a, bank, "s.csv", csv_)
    txs = {t["description"]: t for t in a.get(f"/api/accounts/{bank}/transactions").json()}
    cafe = txs["CAFE DE FLORE PARIS"]
    assert (Decimal(cafe["original_amount"]), cafe["original_currency"]) == (Decimal("-45"), "EUR")
    assert txs["TESCO"]["original_currency"] is None
    r = a.post(f"/api/transactions/{txs['ATM PARIS']['id']}/move-to-wallet", json={"wallet_id": eur_wallet})
    assert r.status_code == 200 and bal(a, eur_wallet) == Decimal("200.00")


def test_home_currency_conversion_never_guesses():
    a, hid, bank, _ = setup()
    eur = create_account(a, "EUR acc", currency="EUR")
    usd = create_account(a, "USD acc", currency="USD")
    for acc in (eur, usd):
        a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    upload(a, bank, "g.csv", f"Date,Description,Amount\n{D(0)},TESCO,-10.00\n".encode())
    upload(a, eur, "e.csv", f"Date,Description,Amount\n{D(0)},LIDL,-20.00\n".encode())
    upload(a, usd, "u.csv", f"Date,Description,Amount\n{D(0)},WALMART,-5.00\n".encode())
    month = T.strftime("%Y-%m")
    c = a.get(f"/api/households/{hid}/summary?month={month}").json()["converted"]
    assert c["currency"] == "GBP" and c["missing"] == ["EUR", "USD"] and Decimal(c["spent"]) == Decimal("10")
    assert a.put(f"/api/households/{hid}/fx-rates", json={"from_currency": "eur", "to_currency": "GBP", "rate": "0.85"}).status_code == 200
    a.put(f"/api/households/{hid}/fx-rates", json={"from_currency": "GBP", "to_currency": "USD", "rate": "1.25"})   # inverse used
    c = a.get(f"/api/households/{hid}/summary?month={month}").json()["converted"]
    assert c["missing"] == [] and Decimal(c["spent"]) == Decimal("10") + Decimal("17.00") + Decimal("4.00")
    assert Decimal(c["daily"][0]["spent"]) == Decimal("31.00")


def test_fx_rates_permissions():
    a, hid, *_ = setup()
    b, x = make_user("b"), make_user("x")
    invite_and_join(a, b, hid)
    body = {"from_currency": "EUR", "to_currency": "GBP", "rate": "0.85"}
    assert b.put(f"/api/households/{hid}/fx-rates", json=body).status_code == 403
    assert x.put(f"/api/households/{hid}/fx-rates", json=body).status_code == 404
    assert a.put(f"/api/households/{hid}/fx-rates", json={**body, "rate": "0"}).status_code == 422
    a.put(f"/api/households/{hid}/fx-rates", json=body)
    assert [r["from_currency"] for r in b.get(f"/api/households/{hid}/fx-rates").json()] == ["EUR"]
    assert x.get(f"/api/households/{hid}/fx-rates").status_code == 404
