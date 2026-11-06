from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from app.config import get_settings
from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()
CSV = f"Date,Description,Amount\n{T.isoformat()},ZORBLAX GADGETS,-19.99\n{T.isoformat()},TESCO,-5.00\n".encode()


def hist(c, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return c.get(f"/api/audit?{qs}").json()


def setup():
    a = make_user("a")
    hid = create_household(a)
    acc = create_account(a)
    a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    return a, hid, acc


def test_category_change_logged_with_reason_and_undone():
    a, hid, acc = setup()
    upload(a, acc, "s.csv", CSV)
    z = [t for t in a.get(f"/api/accounts/{acc}/transactions").json() if t["description"].startswith("ZORBLAX")][0]
    a.patch(f"/api/transactions/{z['id']}", json={"category": "Kids", "reason": "school supplies"})
    e = hist(a)[0]
    assert e["action"] == "tx.category" and "→ Kids" in e["summary"] and e["reason"] == "school supplies" and e["can_undo"]
    r = a.post(f"/api/audit/{e['id']}/undo", json={"reason": "wrong one"})
    assert r.status_code == 200 and r.json()["is_reversal"]
    t = [t for t in a.get(f"/api/accounts/{acc}/transactions").json() if t["id"] == z["id"]][0]
    assert t["category"] is None and t["needs_review"] is True
    assert a.post(f"/api/audit/{e['id']}/undo", json={}).status_code == 409          # only once
    # the learned rule was removed too: a new import isn't auto-filed as Kids
    upload(a, acc, "s2.csv", f"Date,Description,Amount\n{T.isoformat()},ZORBLAX GADGETS 2,-1.00\n".encode())
    assert [t["category"] for t in a.get(f"/api/accounts/{acc}/transactions").json() if t["description"] == "ZORBLAX GADGETS 2"] == [None]


def test_undo_refuses_when_changed_since():
    a, hid, acc = setup()
    upload(a, acc, "s.csv", CSV)
    z = a.get(f"/api/accounts/{acc}/transactions").json()[0]
    a.patch(f"/api/transactions/{z['id']}", json={"category": "Kids", "remember": False})
    first = hist(a)[0]
    a.patch(f"/api/transactions/{z['id']}", json={"category": "Pets", "remember": False})
    assert a.post(f"/api/audit/{first['id']}/undo", json={}).status_code == 409


def test_deleted_entry_restored_exactly_and_import_undo():
    a, hid, _ = setup()
    wallet = create_account(a, "Wallet", type_="cash")
    t = a.post(f"/api/accounts/{wallet}/transactions", json={"posted_on": T.isoformat(), "description": "Market", "amount": "-7.00"}).json()
    assert a.delete(f"/api/transactions/{t['id']}?reason=typo").status_code == 204
    d = hist(a)[0]
    assert d["action"] == "tx.delete" and d["reason"] == "typo"
    a.post(f"/api/audit/{d['id']}/undo", json={})
    back = a.get(f"/api/accounts/{wallet}/transactions").json()
    assert [(x["id"], x["description"], x["amount"]) for x in back] == [(t["id"], "Market", t["amount"])]
    # cash count + starting balance are undoable
    a.put(f"/api/accounts/{wallet}/opening", json={"balance": "20", "date": (T - timedelta(days=3)).isoformat()})
    a.post(f"/api/accounts/{wallet}/cash-count", json={"counted": "10", "date": T.isoformat()})
    count, opening = hist(a)[0], hist(a)[1]
    assert (count["action"], opening["action"]) == ("cash.count", "account.opening")
    a.post(f"/api/audit/{count['id']}/undo", json={})
    a.post(f"/api/audit/{opening['id']}/undo", json={})
    w = a.get(f"/api/accounts/{wallet}").json()
    assert float(w["opening_balance"]) == 0 and float(w["balance"]) == -7.0


def test_import_logged_and_undone_from_history():
    a, hid, acc = setup()
    upload(a, acc, "s.csv", CSV)
    e = hist(a, account_id=acc)[0]
    assert e["action"] == "import.batch"
    a.post(f"/api/audit/{e['id']}/undo", json={})
    assert a.get(f"/api/accounts/{acc}/transactions").json() == []


def test_append_only_and_tamper_evident(owner_engine):
    a, hid, acc = setup()
    upload(a, acc, "s.csv", CSV)
    for t in a.get(f"/api/accounts/{acc}/transactions").json():
        a.patch(f"/api/transactions/{t['id']}", json={"category": "Other", "remember": False})
    v = {c["label"]: c for c in a.get("/api/audit/verify").json()}
    assert v["Current"]["intact"] and v["Current"]["entries"] == 3
    # the app's own database role can't rewrite history
    eng = create_engine(get_settings().app_db_url)
    with eng.connect() as c:
        c.execute(text("SELECT set_config('app.user_id', :u, false)"), {"u": a.me["id"]})
        for sql in ("UPDATE audit_log SET summary = 'x'", "DELETE FROM audit_log"):
            with pytest.raises(ProgrammingError):
                c.execute(text(sql))
            c.rollback()
            c.execute(text("SELECT set_config('app.user_id', :u, false)"), {"u": a.me["id"]})
    eng.dispose()
    # someone with direct database access edits an entry: the chain shows it
    with owner_engine.begin() as c:
        c.execute(text("UPDATE audit_log SET summary = 'nothing to see' WHERE id = (SELECT min(id) FROM audit_log)"))
    v = {c["label"]: c for c in a.get("/api/audit/verify").json()}
    assert v["Current"]["intact"] is False and v["Current"]["broken_id"] is not None


def test_visibility():
    a, hid, acc = setup()
    b, m, x = make_user("b"), make_user("m"), make_user("x")
    invite_and_join(a, b, hid, role="admin")
    invite_and_join(a, m, hid)
    upload(a, acc, "s.csv", CSV)
    sub = a.post(f"/api/households/{hid}/subscriptions", json={"provider": "Netflix", "amount": "1", "currency": "GBP", "cycle": "monthly"})
    assert sub.status_code == 201
    actions_b = {e["action"] for e in hist(b)}
    assert "subscription.create" in actions_b and "import.batch" not in actions_b       # admin: household, not a's account
    assert {e["action"] for e in hist(m)} <= {"member.join"}                               # member: only own actions
    assert hist(x) == []
    e = hist(a, account_id=acc)[0]
    assert b.post(f"/api/audit/{e['id']}/undo", json={}).status_code == 404
    hh = [h for h in hist(b) if h["action"] == "subscription.create"][0]
    assert hh["can_undo"] is False
