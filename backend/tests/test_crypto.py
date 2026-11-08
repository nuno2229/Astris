"""P-10: encryption at rest for kept files, notification secrets and calendar links; key rotation."""
import base64
import os

from sqlalchemy import text

from app import crypto
from tests.test_notifications import DISCORD
from tests.conftest import create_account, create_household, make_user

K1 = base64.urlsafe_b64encode(os.urandom(32)).decode()
K2 = base64.urlsafe_b64encode(os.urandom(32)).decode()


def test_round_trip_and_tamper(monkeypatch):
    monkeypatch.setenv("ASTRIS_KEY", K1)
    blob = crypto.encrypt(b"secret", b"id1")
    assert blob.startswith(b"ASTRIS1") and b"secret" not in blob
    assert crypto.decrypt(blob, b"id1") == b"secret"
    import pytest
    with pytest.raises(Exception):
        crypto.decrypt(blob, b"other-row")              # bound to its row: can't be swapped between rows
    assert crypto.decrypt(b"plain from before", b"x") == b"plain from before"
    monkeypatch.delenv("ASTRIS_KEY")
    with pytest.raises(RuntimeError):
        crypto.decrypt(blob, b"id1")


def test_stored_encrypted_readable_through_the_app_and_rotatable(monkeypatch, owner_engine):
    monkeypatch.delenv("ASTRIS_KEY", raising=False)
    a = make_user("a")
    acc = create_account(a)
    raw = b"Date,Description,Amount\n2026-11-02,ALDI,-1.00\n"
    a.post(f"/api/accounts/{acc}/imports", files={"file": ("old.csv", raw)}, data={"keep_original": "true"})   # before a key
    monkeypatch.setenv("ASTRIS_KEY", K1)
    a.post(f"/api/accounts/{acc}/imports", files={"file": ("new.csv", raw.replace(b"ALDI", b"LIDL"))}, data={"keep_original": "true"})
    ch = a.post("/api/me/notifications/channels", json={"kind": "discord", "allow_internet": True,
                "config": {"webhook_url": DISCORD}})
    assert ch.status_code == 201, ch.text
    with owner_engine.begin() as c:
        rows = c.execute(text("SELECT filename, data FROM documents ORDER BY filename")).all()
        assert not crypto.is_encrypted(rows[1].data) and crypto.is_encrypted(rows[0].data)   # new.csv encrypted, old.csv not yet
        cfg = c.execute(text("SELECT config FROM notification_channels")).scalar_one()
        assert set(cfg) == {"enc1"} and "discord.com" not in str(cfg)
    docs = {d["filename"]: d["id"] for d in a.get(f"/api/accounts/{acc}/documents").json()}
    assert a.get(f"/api/documents/{docs['new.csv']}").content.endswith(b"LIDL,-1.00\n")
    assert a.get(f"/api/documents/{docs['old.csv']}").content == raw
    assert a.get("/api/me/notifications").json()["channels"][0]["kind"] == "discord"       # still readable through the app
    # rotate: new key current, old key still readable until rewrapped
    monkeypatch.setenv("ASTRIS_KEY", K2)
    monkeypatch.setenv("ASTRIS_OLD_KEYS", K1)
    n = crypto.rewrap()
    assert n["documents"] == 2 and n["notification_channels"] == 1
    monkeypatch.delenv("ASTRIS_OLD_KEYS")
    assert a.get(f"/api/documents/{docs['new.csv']}").content.endswith(b"LIDL,-1.00\n")
    st = crypto.status()
    assert st["documents"] == {"encrypted": 2, "total": 2} and st["notification_channels"]["encrypted"] == 1
