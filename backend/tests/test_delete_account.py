"""P-13: delete my account and data."""
from datetime import date, timedelta

from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

D = (date.today() - timedelta(days=2)).isoformat()


def test_delete_account_removes_personal_data_and_hands_over_ownership(owner_engine):
    from sqlalchemy import text
    make_user("admin")                          # first account is the server admin
    a, b = make_user("a"), make_user("b")
    shared = create_household(a, "Shared")
    solo = create_household(a, "Solo")
    invite_and_join(a, b, shared)
    acc = create_account(a, "A current")
    a.put(f"/api/households/{shared}/accounts/{acc}", json={"attribution": "include"})
    upload(a, acc, "s.csv", f"Date,Description,Amount\n{D},TESCO STORES 2041,-40.00\n".encode())
    goal = a.post(f"/api/households/{shared}/goals", json={"name": "Trip", "target": "100", "currency": "GBP"}).json()
    a.post(f"/api/goals/{goal['id']}/contributions", json={"amount": "25"})
    assert a.post("/api/me/delete", json={"password": "nope", "confirm": "DELETE"}).status_code == 403
    assert a.post("/api/me/delete", json={"password": "correct horse battery", "confirm": "yes"}).status_code == 422
    assert a.post("/api/me/delete", json={"password": "correct horse battery", "confirm": "DELETE"}).status_code == 204
    # signed out, can't sign back in, and the email is free again
    assert a.get("/api/me").status_code == 401
    # b now owns the shared household; a's account, transactions and contributions are gone
    hh = {h["id"]: h for h in b.get("/api/me/households").json()}
    assert hh[shared]["role"] == "owner"
    assert b.get(f"/api/households/{shared}/transactions").json() == []
    assert b.get(f"/api/goals/{goal['id']}").json()["saved"] == "0.00"
    hist = b.get(f"/api/audit?household_id={shared}").json()
    assert any("deleted their account" in e["summary"] for e in hist)
    assert b.get("/api/audit/verify").status_code == 200
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM households WHERE id = :h"), {"h": solo}).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM accounts WHERE id = :a"), {"a": acc}).scalar() == 0
        u = c.execute(text("SELECT email, display_name, deleted_at FROM users WHERE id = :u"), {"u": a.me["id"]}).one()
        assert u.email.startswith("deleted-") and u.display_name == "Former member" and u.deleted_at is not None
    r = b.post("/api/auth/login", json={"email": a.me["email"], "password": "correct horse battery"})
    assert r.status_code == 401
    from fastapi.testclient import TestClient
    from app.main import app
    again = TestClient(app).post("/api/auth/register", json={"email": a.me["email"], "display_name": "New", "password": "correct horse battery"})
    assert again.status_code == 201                                  # the address is free again


def test_last_server_admin_must_hand_over_first():
    a = make_user("admin")         # first account = server admin
    make_user("other")
    assert a.post("/api/me/delete", json={"password": "correct horse battery", "confirm": "DELETE"}).status_code == 409
