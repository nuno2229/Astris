import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import backup
from app.config import get_settings
from app.main import app
from tests.conftest import create_account, create_household, invite_and_join, make_user, upload


@pytest.fixture(autouse=True)
def clean_backups():
    d = Path(get_settings().backup_dir)
    shutil.rmtree(d, ignore_errors=True)
    yield
    shutil.rmtree(d, ignore_errors=True)


def test_first_user_is_admin_and_can_close_signups():
    a, b = make_user("a"), make_user("b")
    assert a.get("/api/me").json()["is_server_admin"] is True and b.get("/api/me").json()["is_server_admin"] is False
    assert b.put("/api/server", json={"registration_open": False}).status_code == 403
    assert a.put("/api/server", json={"registration_open": False}).json()["registration_open"] is False
    r = TestClient(app).post("/api/auth/register", json={"email": "late@example.com", "display_name": "L", "password": "long enough pw"})
    assert r.status_code == 403 and "closed" in r.json()["detail"]
    # invited people still get in via an existing account flow: re-open
    a.put("/api/server", json={"registration_open": True})
    assert b.get("/api/server").json()["backups"] == []                      # non-admins don't see backups


def seed():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    acc = create_account(a)
    a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    upload(a, acc, "s.csv", b"Date,Description,Amount\n2026-11-01,TESCO,-12.00\n2026-11-02,ZORBLAX,-3.00\n")
    z = [t for t in a.get(f"/api/accounts/{acc}/transactions").json() if t["description"] == "ZORBLAX"][0]
    a.patch(f"/api/transactions/{z['id']}", json={"category": "Kids"})
    return a, b, hid, acc


def test_backup_and_restore_round_trip():
    a, b, hid, acc = seed()
    r = a.post("/api/server/backups")
    assert r.status_code == 201
    name = r.json()["name"]
    assert b.post("/api/server/backups").status_code == 403
    dl = a.get(f"/api/server/backups/{name}")
    assert dl.status_code == 200 and zipfile.ZipFile(io.BytesIO(dl.content)).read("manifest.json")
    assert a.get("/api/server/backups/..%2F..%2Fetc%2Fpasswd").status_code == 404
    # disaster: data changes after the backup
    a.delete(f"/api/accounts/{acc}")
    assert a.get("/api/accounts").json() == []
    m = backup.restore(backup.resolve(name))
    assert m["rows"]["transactions"] == 2
    # same people, same session cookies, same data; history chain still verifies
    txs = a.get(f"/api/accounts/{acc}/transactions").json()
    assert sorted(t["description"] for t in txs) == ["TESCO", "ZORBLAX"]
    assert all(c["intact"] for c in a.get("/api/audit/verify").json())
    assert any(e["action"] == "tx.category" for e in a.get("/api/audit").json())


def test_encrypted_backup(monkeypatch):
    a, *_ = seed()
    monkeypatch.setattr(get_settings(), "backup_passphrase", "correct horse battery staple")
    p = backup.create("manual")
    assert p.name.endswith(".enc") and not p.read_bytes()[:20].startswith(b"PK")
    with pytest.raises(ValueError):
        backup.restore(p, "wrong passphrase")
    assert backup.restore(p, "correct horse battery staple")["rows"]["users"] == 2


def test_pre_migrate_backup_only_when_needed():
    seed()
    assert backup.migrations_pending() is False
    backup._alembic("-1", "down")
    try:
        assert backup.migrations_pending() is True
        assert backup.main(["pre-migrate"]) == 0
        assert any("-pre-migrate" in b["name"] for b in backup.list_backups())
    finally:
        backup._alembic("head", "up")


def test_update_check_opt_in(monkeypatch):
    from app.routers import admin as admin_mod
    import app.netcheck as nc
    a = make_user("a")
    calls = []

    def fake(url, method="GET", body=None, timeout=5, max_bytes=0, headers=None, **k):
        calls.append(url)
        return 200, json.dumps({"tag_name": "v9.9.9", "html_url": "https://github.com/nuno2229/Astris/releases/tag/v9.9.9"}).encode()
    monkeypatch.setattr(nc, "pinned_request", fake)
    from sqlalchemy.orm import Session
    from app.db import get_engine
    assert admin_mod.check_for_update(lambda: Session(get_engine())) is None and calls == []     # off by default
    a.put("/api/server", json={"update_check": True})
    assert admin_mod.check_for_update(lambda: Session(get_engine())) == "v9.9.9"
    s = a.get("/api/server").json()
    assert s["update_available"] is True and s["latest_version"] == "v9.9.9"


def test_personal_export_only_contains_what_you_can_see():
    a, b, hid, acc = seed()
    x = make_user("x")
    xa = create_account(x, "Secret")
    upload(x, xa, "x.csv", b"Date,Description,Amount\n2026-11-01,SECRET SHOP,-1.00\n")
    z = zipfile.ZipFile(io.BytesIO(a.get("/api/me/export").content))
    txs = json.loads(z.read("transactions.json"))
    assert sorted(t["description"] for t in txs) == ["TESCO", "ZORBLAX"]
    assert "SECRET" not in z.read("transactions.json").decode()
    assert json.loads(z.read("me.json"))["email"] == a.me["email"]
