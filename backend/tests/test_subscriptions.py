from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()


def csv(rows) -> bytes:
    return ("Date,Description,Amount\n" + "".join(f"{d.isoformat()},{desc},{amt}\n" for d, desc, amt in rows)).encode()


def setup():
    a = make_user("a")
    hid = create_household(a)
    acc = create_account(a)
    a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    return a, hid, acc


def add(c, hid, **kw):
    body = {"provider": "Disney+", "amount": "7.99", "currency": "GBP", "cycle": "monthly", **kw}
    r = c.post(f"/api/households/{hid}/subscriptions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def kinds(s):
    return [a["kind"] for a in s["alerts"]]


def test_manual_subscription_rolls_renewal_forward_and_alerts():
    a, hid, _ = setup()
    s = add(a, hid, next_renewal=(T - timedelta(days=45)).isoformat())   # over a cycle ago
    assert date.fromisoformat(s["next_renewal"]) >= T
    soon = add(a, hid, provider="Spotify", next_renewal=(T + timedelta(days=2)).isoformat())
    assert kinds(soon) == ["renewing"] and Decimal(soon["monthly_cost"]) == Decimal("7.99")
    yearly = add(a, hid, provider="Prime", amount="95.00", cycle="yearly")
    assert Decimal(yearly["monthly_cost"]) == Decimal("7.92")
    summ = a.get(f"/api/households/{hid}/summary").json()
    assert [x["provider"] for x in summ["subscription_alerts"]] == ["Spotify"]


def test_trial_ending_converted_and_quiet_end():
    a, hid, acc = setup()
    ending = add(a, hid, provider="Audible", trial_ends=(T + timedelta(days=3)).isoformat())
    assert ending["status"] == "trial" and kinds(ending) == ["trial_ending"] and Decimal(ending["monthly_cost"]) == 0
    converted = add(a, hid, provider="Paramount Plus", trial_ends=(T - timedelta(days=10)).isoformat())
    quiet = add(a, hid, provider="Calm App", trial_ends=(T - timedelta(days=10)).isoformat())
    upload(a, acc, "s.csv", csv([(T - timedelta(days=9), "PARAMOUNT PLUS 4421", "-6.99")]))
    subs = {s["provider"]: s for s in a.get(f"/api/households/{hid}/subscriptions").json()}
    assert kinds(subs["Paramount Plus"]) == ["trial_converted"] and "6.99" in subs["Paramount Plus"]["alerts"][0]["text"]
    assert kinds(subs["Calm App"]) == ["trial_over"]


def test_charge_after_cancel_is_flagged():
    a, hid, acc = setup()
    s = add(a, hid, provider="Now TV")
    r = a.post(f"/api/subscriptions/{s['id']}/cancel", json={"cancelled_on": (T - timedelta(days=20)).isoformat()})
    assert r.json()["status"] == "cancelled" and r.json()["alerts"] == [] and Decimal(r.json()["monthly_cost"]) == 0
    upload(a, acc, "s.csv", csv([(T - timedelta(days=5), "NOW TV LTD", "-9.99")]))
    s2 = a.get(f"/api/households/{hid}/subscriptions").json()[0]
    assert kinds(s2) == ["charged_after_cancel"] and s2["alerts"][0]["severity"] == "critical"
    assert a.post(f"/api/subscriptions/{s['id']}/reactivate").json()["status"] == "active"


def test_suggestions_from_regular_payments_and_linking():
    a, hid, acc = setup()
    rows = [(T - timedelta(days=30 * i + 3), "NETFLIX.COM", "-10.99") for i in range(3)]
    rows += [(T - timedelta(days=30 * i + 5), "STANDING ORDER TO LANDLORD RENT", "-1200") for i in range(3)]
    upload(a, acc, "s.csv", csv(rows))
    sug = a.get(f"/api/households/{hid}/subscriptions/suggestions").json()
    assert [s["name"] for s in sug] == ["Netflix"]          # rent is a regular payment, not a subscription
    r = a.post(f"/api/households/{hid}/subscriptions/from-recurring", json={"recurring_id": sug[0]["recurring_id"]})
    assert r.status_code == 201 and r.json()["linked"] and r.json()["cycle"] == "monthly"
    rec_next = a.get(f"/api/households/{hid}/recurring").json()
    assert r.json()["next_renewal"] == [x for x in rec_next if x["name"] == "Netflix"][0]["next_date"]
    assert a.get(f"/api/households/{hid}/subscriptions/suggestions").json() == []
    again = a.post(f"/api/households/{hid}/subscriptions/from-recurring", json={"recurring_id": sug[0]["recurring_id"]})
    assert again.status_code == 409


def test_household_members_share_registry_outsiders_cannot():
    a, hid, _ = setup()
    b, x = make_user("b"), make_user("x")
    invite_and_join(a, b, hid)
    s = add(a, hid)
    assert [y["provider"] for y in b.get(f"/api/households/{hid}/subscriptions").json()] == ["Disney+"]
    assert b.patch(f"/api/subscriptions/{s['id']}", json={"auto_renew": False}).json()["auto_renew"] is False
    assert x.get(f"/api/households/{hid}/subscriptions").status_code == 404
    assert x.patch(f"/api/subscriptions/{s['id']}", json={"amount": "0"}).status_code == 404
    assert x.delete(f"/api/subscriptions/{s['id']}").status_code == 404
    assert x.post(f"/api/households/{hid}/subscriptions", json={"provider": "x", "amount": "1", "currency": "GBP",
                                                                 "cycle": "monthly"}).status_code == 404


def test_validation():
    a, hid, _ = setup()
    bad = {"provider": "X", "amount": "-1", "currency": "GBP", "cycle": "monthly"}
    assert a.post(f"/api/households/{hid}/subscriptions", json=bad).status_code == 422
    assert a.post(f"/api/households/{hid}/subscriptions", json={**bad, "amount": "1", "cycle": "daily"}).status_code == 422
    s = add(a, hid)
    r = a.post(f"/api/subscriptions/{s['id']}/cancel", json={"cancelled_on": T.isoformat(),
                                                             "access_until": (T - timedelta(days=1)).isoformat()})
    assert r.status_code == 422


def test_trial_hints_from_card_checks():
    from datetime import date, timedelta
    from tests.conftest import create_account, create_household, make_user, upload
    d = lambda n: (date.today() - timedelta(days=n)).isoformat()   # noqa: E731
    a, outsider = make_user("a"), make_user("x")
    hid = create_household(a)
    acc = create_account(a)
    a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    upload(a, acc, "s.csv", (
        "Date,Description,Amount\n"
        f"{d(3)},DISNEY PLUS,0.00\n"           # free trial card check
        f"{d(5)},AUDIBLE UK,-1.00\n"           # £1 check
        f"{d(20)},SPOTIFY P1234,-1.00\n{d(2)},SPOTIFY P1234,-11.99\n"   # already became paid: not a trial hint
        f"{d(4)},TESCO STORES 2041,-1.00\n"    # groceries: never a trial
        f"{d(90)},HULU,0.00\n"                 # too long ago
    ).encode())
    hints = a.get(f"/api/households/{hid}/subscriptions/trial-hints").json()
    assert [h["provider"].lower()[:6] for h in hints] == ["disney", "audibl"]
    assert outsider.get(f"/api/households/{hid}/subscriptions/trial-hints").status_code == 404
    # confirming one adds a trial that matches its statement lines; dismissing hides the other
    h = hints[0]
    r = a.post(f"/api/households/{hid}/subscriptions", json={"provider": "Disney+", "amount": "7.99", "currency": "GBP",
               "cycle": "monthly", "trial_ends": h["suggested_end"], "merchant_key": h["merchant_key"]})
    assert r.status_code == 201 and r.json()["status"] == "trial"
    assert a.post(f"/api/households/{hid}/subscriptions/trial-hints/dismiss", json={"merchant_key": hints[1]["merchant_key"]}).status_code == 204
    assert a.get(f"/api/households/{hid}/subscriptions/trial-hints").json() == []
