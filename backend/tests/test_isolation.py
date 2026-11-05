"""Household isolation is absolute (§2, §3.3): every cross-tenant read/write must fail.
Tested at the API layer AND directly against Postgres as the runtime role (the RLS backstop)."""
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from app.config import get_settings
from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

CSV = b"Date,Description,Amount,Balance\n01/11/2026,TESCO STORES,-12.50,987.50\n02/11/2026,SALARY,2000.00,2987.50\n"


@pytest.fixture
def two_households():
    a, b = make_user("alice"), make_user("bob")
    ha, hb = create_household(a, "A"), create_household(b, "B")
    acc_a = create_account(a, "Alice current")
    a.put(f"/api/households/{ha}/accounts/{acc_a}", json={"attribution": "include"})
    imp = upload(a, acc_a, "s.csv", CSV)
    assert imp.status_code == 201, imp.text
    return a, b, ha, hb, acc_a, imp.json()["batch_id"]


def test_api_cross_household_reads_are_404(two_households):
    a, b, ha, hb, acc_a, batch = two_households
    for path in (f"/api/households/{ha}", f"/api/households/{ha}/accounts", f"/api/households/{ha}/transactions",
                 f"/api/households/{ha}/summary", f"/api/households/{ha}/invites",
                 f"/api/accounts/{acc_a}", f"/api/accounts/{acc_a}/transactions"):
        assert b.get(path).status_code == 404, path
    assert b.get("/api/accounts").json() == []
    assert len(a.get(f"/api/households/{ha}/transactions").json()) == 2


def test_api_cross_household_writes_fail(two_households):
    a, b, ha, hb, acc_a, batch = two_households
    assert upload(b, acc_a, "s.csv", CSV).status_code == 404
    assert b.delete(f"/api/imports/{batch}").status_code == 404
    assert b.patch(f"/api/accounts/{acc_a}", json={"name": "pwned"}).status_code == 404
    assert b.delete(f"/api/accounts/{acc_a}").status_code == 404
    assert b.put(f"/api/households/{hb}/accounts/{acc_a}", json={"attribution": "include"}).status_code == 404
    assert b.post(f"/api/households/{ha}/invites", json={}).status_code == 404
    assert b.patch(f"/api/households/{ha}", json={"name": "pwned"}).status_code == 404
    assert b.delete(f"/api/households/{ha}/members/{a.me['id']}").status_code == 404
    # nothing changed for alice
    assert a.get(f"/api/accounts/{acc_a}").json()["name"] == "Alice current"
    assert len(a.get(f"/api/accounts/{acc_a}/transactions").json()) == 2


def test_member_cannot_import_into_or_edit_others_account(two_households):
    """Same household, different owner: can read (shared), can't write (§3.5 ownership)."""
    a, b, ha, hb, acc_a, batch = two_households
    invite_and_join(a, b, ha)
    assert b.get(f"/api/accounts/{acc_a}").status_code == 200
    assert upload(b, acc_a, "s.csv", CSV).status_code == 403
    assert b.patch(f"/api/accounts/{acc_a}", json={"name": "x"}).status_code == 403
    assert b.delete(f"/api/imports/{batch}").status_code == 404


def test_removed_members_accounts_leave_household(two_households):
    a, b, ha, hb, acc_a, batch = two_households
    owner = make_user("owner")
    hid = create_household(owner)
    invite_and_join(owner, a, hid)
    a.put(f"/api/households/{hid}/accounts/{acc_a}", json={"attribution": "include"})
    assert len(owner.get(f"/api/households/{hid}/transactions").json()) == 2
    owner.delete(f"/api/households/{hid}/members/{a.me['id']}")
    assert owner.get(f"/api/households/{hid}/transactions").json() == []
    assert owner.get(f"/api/accounts/{acc_a}").status_code == 404
    # and alice still has her own account
    assert len(a.get(f"/api/accounts/{acc_a}/transactions").json()) == 2


def _app_conn(user_id):
    eng = create_engine(get_settings().app_db_url)
    conn = eng.connect()
    conn.begin()
    if user_id is not None:
        conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(user_id)})
    return eng, conn


TABLES = ["households", "memberships", "invitations", "accounts", "household_accounts",
          "import_batches", "transactions"]


@pytest.mark.parametrize("who", ["bob", "nobody", "random"])
def test_db_backstop_rls(two_households, who):
    """Even raw SQL with NO WHERE clause, as the runtime role, sees no other-household rows."""
    a, b, *_ = two_households
    uid = {"bob": b.me["id"], "nobody": None, "random": uuid.uuid4()}[who]
    eng, conn = _app_conn(uid)
    try:
        for t in TABLES:
            rows = conn.execute(text(f"SELECT * FROM {t}")).all()
            if who == "bob" and t in ("households", "memberships"):
                assert all(str(r._mapping.get("created_by", r._mapping.get("user_id"))) == b.me["id"] for r in rows), t
            else:
                assert rows == [], t
        users = conn.execute(text("SELECT id FROM users")).scalars().all()
        assert [str(u) for u in users] == ([b.me["id"]] if who == "bob" else [])
    finally:
        conn.close()
        eng.dispose()


def test_db_runtime_role_cannot_read_password_hashes(two_households):
    a, *_ = two_households
    eng, conn = _app_conn(a.me["id"])
    try:
        with pytest.raises(ProgrammingError):
            conn.execute(text("SELECT password_hash FROM users")).all()
    finally:
        conn.close()
        eng.dispose()


def test_db_cannot_self_join_household(two_households):
    """Joining is only possible through accept_invite(); a direct INSERT is refused."""
    a, b, ha, *_ = two_households
    eng, conn = _app_conn(b.me["id"])
    try:
        with pytest.raises(ProgrammingError):
            conn.execute(text("INSERT INTO memberships (household_id, user_id, role) VALUES (:h, :u, 'owner')"),
                         {"h": ha, "u": b.me["id"]})
    finally:
        conn.close()
        eng.dispose()
