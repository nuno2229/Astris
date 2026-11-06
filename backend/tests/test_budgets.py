import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.categorise import worker
from app.notify import channels
from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()
M1 = T.replace(day=1)


def csv(rows):
    return ("Date,Description,Amount\n" + "".join(f"{d.isoformat()},{x},{a}\n" for d, x, a in rows)).encode()


def setup():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    acc_a, acc_b = create_account(a, "A current"), create_account(b, "B current")
    a.put(f"/api/households/{hid}/accounts/{acc_a}", json={"attribution": "include"})
    b.put(f"/api/households/{hid}/accounts/{acc_b}", json={"attribution": "include"})
    return a, b, hid, acc_a, acc_b


def budget(c, hid, **kw):
    r = c.post(f"/api/households/{hid}/budgets", json={"kind": "category", "category": "Groceries", "amount": "100",
                                                        "currency": "GBP", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_household_budget_counts_shared_spending_and_levels():
    a, b, hid, acc_a, acc_b = setup()
    upload(a, acc_a, "a.csv", csv([(M1, "TESCO", "-50.00"), (M1, "NETFLIX.COM", "-9.99")]))
    upload(b, acc_b, "b.csv", csv([(M1, "ALDI", "-35.00")]))
    g = budget(a, hid)
    assert Decimal(g["spent"]) == Decimal("85.00") and g["level"] == "warning" and "85% used" in g["message"]
    upload(b, acc_b, "b2.csv", csv([(M1, "LIDL", "-20.00")]))
    g = [x for x in b.get(f"/api/households/{hid}/budgets").json() if x["id"] == g["id"]][0]
    assert g["level"] == "over" and "Over by £5.00" in g["message"]
    assert g["level"] == a.get(f"/api/households/{hid}/summary").json()["budgets"][0]["level"]


def test_personal_budget_is_private_and_counts_only_own_accounts():
    a, b, hid, acc_a, acc_b = setup()
    upload(a, acc_a, "a.csv", csv([(M1, "TESCO", "-30.00")]))
    upload(b, acc_b, "b.csv", csv([(M1, "ALDI", "-70.00")]))
    p = budget(a, hid, scope="personal", amount="50")
    assert Decimal(p["spent"]) == Decimal("30.00") and p["level"] == "ok"
    assert b.get(f"/api/households/{hid}/budgets").json() == []
    assert b.delete(f"/api/budgets/{p['id']}").status_code == 404


def test_upcoming_bills_put_budget_at_risk():
    a, b, hid, acc_a, _ = setup()
    # rent monthly on the 28th for 3 months; this month's not yet paid
    rows = []
    for k in (3, 2, 1):
        y, m = divmod(T.month - 1 - k, 12)
        rows.append((date(T.year + y, m + 1, 28), "STANDING ORDER TO LANDLORD RENT", "-900.00"))
    upload(a, acc_a, "rent.csv", csv(rows))
    budget_ = budget(a, hid, category="Housing", amount="800", warn_percent=None)
    if T.day < 28:   # rent still due this month
        assert budget_["level"] == "at_risk" and Decimal(budget_["committed"]) == Decimal("900.00")


def test_card_limit_weekly_and_validation():
    a, b, hid, acc_a, acc_b = setup()
    start = T - timedelta(days=T.weekday())
    upload(a, acc_a, "a.csv", csv([(start, "PRET", "-30.00"), (start - timedelta(days=1), "PRET", "-99.00")]))
    lim = budget(a, hid, kind="account", account_id=acc_a, period="weekly", amount="35", category=None)
    assert lim["scope"] == "personal" and Decimal(lim["spent"]) == Decimal("30.00") and lim["level"] == "warning"
    assert a.post(f"/api/households/{hid}/budgets", json={"kind": "account", "account_id": acc_b, "amount": "5",
                                                          "currency": "GBP"}).status_code == 404     # not my card
    assert a.post(f"/api/households/{hid}/budgets", json={"kind": "category", "category": "Nope", "amount": "5",
                                                          "currency": "GBP"}).status_code == 422
    m = budget(a, hid, kind="merchant", merchant_key="pret", merchant_name="Pret", category=None, period="daily", amount="10")
    assert m["label"] == "Pret"
    x = make_user("x")
    assert x.get(f"/api/households/{hid}/budgets").status_code == 404


def test_transfers_excluded_and_foreign_converted():
    a, b, hid, acc_a, _ = setup()
    eur = create_account(a, "EUR", currency="EUR")
    a.put(f"/api/households/{hid}/accounts/{eur}", json={"attribution": "include"})
    upload(a, eur, "e.csv", csv([(M1, "LIDL BERLIN", "-20.00")]))
    g = budget(a, hid)
    assert Decimal(g["spent"]) == 0 and g["unconverted"] == ["EUR"]
    a.put(f"/api/households/{hid}/fx-rates", json={"from_currency": "EUR", "to_currency": "GBP", "rate": "0.85"})
    g = a.get(f"/api/households/{hid}/budgets").json()[0]
    assert Decimal(g["spent"]) == Decimal("17.00") and g["unconverted"] == []


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(channels, "pinned_request", lambda url, method="GET", body=None, **k: (sent.append(json.loads(body)), (204, b""))[1])
    return sent


def test_budget_alerts_notify_once_per_level(outbox):
    a, b, hid, acc_a, _ = setup()
    a.post("/api/me/notifications/channels", json={"kind": "discord", "allow_internet": True, "include_details": True,
           "config": {"webhook_url": "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_ABCDEF-123"}})
    budget(a, hid)
    upload(a, acc_a, "a.csv", csv([(M1, "TESCO", "-85.00")]))
    worker.run_notifications()
    assert "Groceries" in outbox[-1]["content"] and "85% used" in outbox[-1]["content"]
    worker.run_notifications()
    assert len(outbox) == 1
    upload(a, acc_a, "b.csv", csv([(M1, "ALDI", "-30.00")]))
    worker.run_notifications()
    assert len(outbox) == 2 and "Over by" in outbox[-1]["content"]


def test_only_creator_or_admin_manage_household_budget():
    a, b, hid, *_ = setup()
    m = make_user("m")
    invite_and_join(a, m, hid)
    g = budget(b, hid)                                    # set up by member b
    assert m.patch(f"/api/budgets/{g['id']}", json={"amount": "1"}).status_code == 403
    assert m.delete(f"/api/budgets/{g['id']}").status_code == 403
    assert [x["can_edit"] for x in m.get(f"/api/households/{hid}/budgets").json()] == [False]
    assert a.patch(f"/api/budgets/{g['id']}", json={"amount": "150"}).status_code == 200   # household owner may
    assert b.delete(f"/api/budgets/{g['id']}").status_code == 204                          # creator may
