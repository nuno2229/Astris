import json
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.categorise import worker
from app.notify import channels
from tests.conftest import create_household, invite_and_join, make_user

T = date.today()
DISCORD = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz_ABCDEF-123"
# Fake, built at runtime so no token-shaped literal sits in the repo (GitHub secret scanning).
TG_TOKEN = "1" * 9 + ":" + "FAKE_test_token_" * 2 + "0123"


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    def fake(url, method="GET", body=None, timeout=5, max_bytes=0, headers=None, **kw):
        sent.append({"url": url, "body": body, "headers": headers or {}})
        return 204, b""
    monkeypatch.setattr(channels, "pinned_request", fake)
    return sent


def add_sub(c, hid, **kw):
    body = {"provider": "Netflix", "amount": "10.99", "currency": "GBP", "cycle": "monthly",
            "next_renewal": (T + timedelta(days=2)).isoformat(), **kw}
    assert c.post(f"/api/households/{hid}/subscriptions", json=body).status_code == 201


def add_channel(c, **kw):
    body = {"kind": "discord", "config": {"webhook_url": DISCORD}, "allow_internet": True, **kw}
    r = c.post("/api/me/notifications/channels", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_validation_and_secrets_never_returned(outbox):
    a = make_user("a")
    bad = [
        {"kind": "discord", "config": {"webhook_url": "https://evil.example/api/webhooks/1/x"}, "allow_internet": True},
        {"kind": "discord", "config": {"webhook_url": "http://discord.com/api/webhooks/123456789/abcdefghijklmnopqrstuv"}, "allow_internet": True},
        {"kind": "telegram", "config": {"bot_token": "nope", "chat_id": "1"}, "allow_internet": True},
        {"kind": "telegram", "config": {"bot_token": TG_TOKEN, "chat_id": "abc; rm"}, "allow_internet": True},
        {"kind": "ntfy", "config": {"server": "http://192.168.1.10:8090/path", "topic": "x"}},
        {"kind": "ntfy", "config": {"server": "https://ntfy.sh", "topic": "astris"}},          # internet, not allowed
        {"kind": "discord", "config": {"webhook_url": DISCORD}},                              # internet not confirmed
    ]
    for b in bad:
        assert a.post("/api/me/notifications/channels", json=b).status_code == 422, b
    ch = add_channel(a)
    assert ch["uses_internet"] is True and "abcdefgh" not in json.dumps(ch) and ch["hint"].endswith("5678")
    got = a.get("/api/me/notifications").json()
    assert "abcdefghijkl" not in json.dumps(got) and TG_TOKEN not in json.dumps(got)


def test_renewal_alert_sent_once_minimal_by_default(outbox):
    a = make_user("a")
    hid = create_household(a)
    add_sub(a, hid)
    add_channel(a)
    assert worker.run_notifications() == 1
    msg = json.loads(outbox[0]["body"])
    assert "A subscription renews in 2 days" in msg["content"] and "Netflix" not in msg["content"] and "10.99" not in msg["content"]
    assert msg["allowed_mentions"] == {"parse": []}
    assert worker.run_notifications() == 0 and len(outbox) == 1          # not repeated


def test_details_mode_and_telegram(outbox):
    a = make_user("a")
    hid = create_household(a)
    add_sub(a, hid, provider="@everyone Spotify")
    add_channel(a, kind="telegram", config={"bot_token": TG_TOKEN, "chat_id": "-100123"}, include_details=True)
    worker.run_notifications()
    assert outbox[0]["url"] == f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    m = json.loads(outbox[0]["body"])
    assert m["chat_id"] == "-100123" and "@everyone Spotify renews in 2 days for £10.99" in m["text"]


def test_prefs_window_and_toggles(outbox):
    a = make_user("a")
    hid = create_household(a)
    add_sub(a, hid, next_renewal=(T + timedelta(days=5)).isoformat())
    add_channel(a)
    assert worker.run_notifications() == 0                               # default window 3 days
    a.put("/api/me/notifications/prefs", json={"renew_days": 7})
    assert worker.run_notifications() == 1
    a.put("/api/me/notifications/prefs", json={"renew_days": 7, "renewals": False})
    add_sub(a, hid, provider="Spotify", next_renewal=(T + timedelta(days=1)).isoformat())
    assert worker.run_notifications() == 0


def test_failed_delivery_is_retried(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return (500, b"") if calls["n"] == 1 else (204, b"")
    monkeypatch.setattr(channels, "pinned_request", flaky)
    a = make_user("a")
    hid = create_household(a)
    add_sub(a, hid)
    add_channel(a)
    assert worker.run_notifications() == 0
    assert "Couldn't deliver" in a.get("/api/me/notifications").json()["channels"][0]["last_error"]
    assert worker.run_notifications() == 1


def test_each_member_gets_own_alerts_and_channels_are_private(outbox):
    a, b, x = make_user("a"), make_user("b"), make_user("x")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    add_sub(a, hid)
    ch = add_channel(a)
    add_channel(b)
    assert worker.run_notifications() == 2
    assert x.patch(f"/api/me/notifications/channels/{ch['id']}", json={"enabled": False}).status_code == 404
    assert x.delete(f"/api/me/notifications/channels/{ch['id']}").status_code == 404
    assert x.post(f"/api/me/notifications/channels/{ch['id']}/test").status_code == 404
    assert x.get("/api/me/notifications").json()["channels"] == []


@pytest.fixture
def fake_ntfy(monkeypatch):
    monkeypatch.setattr(channels, "ALLOW_LOOPBACK", True)  # the test server lives on 127.0.0.1
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append((self.path, dict(self.headers), self.rfile.read(int(self.headers["Content-Length"])).decode()))
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", got
    srv.shutdown()


def test_ntfy_local_end_to_end_and_test_button(fake_ntfy):
    url, got = fake_ntfy
    a = make_user("a")
    hid = create_household(a)
    add_sub(a, hid, trial_ends=(T + timedelta(days=2)).isoformat())
    ch = add_channel(a, kind="ntfy", config={"server": url, "topic": "astris-home", "token": "tk_12345678"}, allow_internet=False)
    assert ch["uses_internet"] is False
    assert a.post(f"/api/me/notifications/channels/{ch['id']}/test").json()["ok"] is True
    worker.run_notifications()
    path, headers, body = got[-1]
    assert path == "/astris-home" and headers["Authorization"] == "Bearer tk_12345678" and "free trial is ending" in body


def test_ntfy_loopback_blocked_and_errors_generic(monkeypatch):
    a = make_user("a")
    r = a.post("/api/me/notifications/channels", json={"kind": "ntfy", "config": {"server": "http://127.0.0.1:6379", "topic": "x"}})
    assert r.status_code == 422 and "can't be used" in r.json()["detail"]
    monkeypatch.setattr(channels, "pinned_request", lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError()))
    ch = add_channel(a, kind="ntfy", config={"server": "http://192.168.1.77:8090", "topic": "x"}, allow_internet=False)
    msg = a.post(f"/api/me/notifications/channels/{ch['id']}/test").json()["message"]
    assert "Refused" not in msg and "HTTP" not in msg and "ConnectionRefused" not in msg


def test_test_button_rate_limited(outbox):
    a = make_user("a")
    ch = add_channel(a)
    codes = [a.post(f"/api/me/notifications/channels/{ch['id']}/test").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_self_check_happens_on_the_connected_address(monkeypatch):
    """Sending resolves exactly once, and the loopback/self check runs on that same answer (no TOCTOU)."""
    from app import netcheck
    calls = []

    def fake_resolve(h):
        calls.append(h)
        return {"127.0.0.1"}
    monkeypatch.setattr(netcheck, "resolve", fake_resolve)
    with pytest.raises(channels.ChannelError):
        channels.send("ntfy", {"server": "http://rebind.example:8090", "topic": "x", "token": None}, "t", "b")
    assert calls == ["rebind.example"]


def test_own_addresses_are_refused():
    from app.netcheck import is_self_or_loopback, self_addresses
    for ip in self_addresses() | {"127.0.0.1", "::1", "::ffff:127.0.0.1"}:
        assert is_self_or_loopback(ip)
    assert not is_self_or_loopback("192.0.2.10")
