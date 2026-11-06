import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.netcheck import is_local_url
from tests.conftest import create_household, invite_and_join, make_user


@pytest.mark.parametrize("url,local", [
    ("http://192.168.1.50:8080", True), ("http://10.0.0.2", True), ("http://127.0.0.1:8080", True),
    ("http://llama:8080", True), ("http://tower.local:8080", True), ("http://localhost", True),
    ("https://8.8.8.8", False), ("https://api.example.com", False),
])
def test_is_local_url(url, local):
    assert is_local_url(url) is local


def test_owner_only_and_members_can_read():
    o, m, x = make_user("o"), make_user("m"), make_user("x")
    hid = create_household(o)
    invite_and_join(o, m, hid, role="admin")
    body = {"llm_enabled": True, "llm_base_url": "http://192.168.1.50:8080/", "llm_model": "qwen"}
    assert m.put(f"/api/households/{hid}/settings", json=body).status_code == 403
    r = o.put(f"/api/households/{hid}/settings", json=body)
    assert r.status_code == 200 and r.json()["llm_base_url"] == "http://192.168.1.50:8080"
    got = m.get(f"/api/households/{hid}/settings").json()
    assert got["llm_model"] == "qwen" and got["can_edit"] is False and got["llm_is_local"] is True
    assert x.get(f"/api/households/{hid}/settings").status_code == 404


def test_internet_address_needs_explicit_opt_in():
    o = make_user("o")
    hid = create_household(o)
    body = {"llm_enabled": True, "llm_base_url": "https://8.8.8.8"}
    assert o.put(f"/api/households/{hid}/settings", json=body).status_code == 422
    r = o.put(f"/api/households/{hid}/settings", json={**body, "llm_allow_internet": True})
    assert r.status_code == 200 and r.json()["llm_is_local"] is False


def test_enable_requires_url_and_valid_scheme():
    o = make_user("o")
    hid = create_household(o)
    assert o.put(f"/api/households/{hid}/settings", json={"llm_enabled": True}).status_code == 422
    assert o.put(f"/api/households/{hid}/settings", json={"llm_base_url": "ftp://x"}).status_code == 422


@pytest.fixture
def fake_llama():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/v1/models":
                b = json.dumps({"object": "list", "data": [{"id": "qwen2.5-7b-instruct"}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b)
            else:
                self.send_response(404); self.end_headers()

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_connection_test(fake_llama):
    o = make_user("o")
    hid = create_household(o)
    r = o.post(f"/api/households/{hid}/settings/llm/test", json={"llm_base_url": fake_llama}).json()
    assert r["ok"] and r["models"] == ["qwen2.5-7b-instruct"]
    r = o.post(f"/api/households/{hid}/settings/llm/test", json={"llm_base_url": "http://127.0.0.1:1"}).json()
    assert not r["ok"] and r["message"].startswith("Couldn't connect")


@pytest.mark.parametrize("url", ["http://169.254.169.254", "http://[fe80::1]:8080", "http://0.0.0.0:8080",
                                 "http://224.0.0.1", "http://no-such-host.invalid",
                                 "http://100.100.100.200", "http://[fd00:ec2::254]",
                                 "http://[64:ff9b::a9fe:a9fe]", "http://[2002:a9fe:a9fe::1]",
                                 "http://[::ffff:169.254.169.254]"])
def test_blocked_addresses(url):
    o = make_user("o")
    hid = create_household(o)
    body = {"llm_base_url": url, "llm_allow_internet": True}
    assert o.post(f"/api/households/{hid}/settings/llm/test", json=body).status_code == 422
    assert o.put(f"/api/households/{hid}/settings", json=body).status_code == 422


def test_pinned_get_connects_to_checked_ip(monkeypatch, fake_llama):
    """DNS rebinding: resolution happens once; a later different answer is never used."""
    from app import netcheck
    calls = []
    real = netcheck.resolve
    def fake_resolve(host):
        calls.append(host)
        return {"127.0.0.1"} if len(calls) == 1 else {"169.254.169.254"}
    monkeypatch.setattr(netcheck, "resolve", fake_resolve)
    port = fake_llama.rsplit(":", 1)[1]
    code, body = netcheck.pinned_get(f"http://rebind.example:{port}/v1/models")
    assert code == 200 and b"qwen" in body and len(calls) == 1
    monkeypatch.setattr(netcheck, "resolve", real)


@pytest.mark.parametrize("url", ["http://192.168.1.5:8080/cgi-bin/reboot", "http://192.168.1.5/?x=1",
                                 "http://user:pw@192.168.1.5", "http://192.168.1.5/#frag", "http://192.168.1.5:99999"])
def test_llm_address_must_be_origin_only(url):
    o = make_user("o")
    hid = create_household(o)
    assert o.put(f"/api/households/{hid}/settings", json={"llm_base_url": url}).status_code == 422


def test_pinned_request_total_deadline():
    """A server that trickles bytes forever is cut off by the total deadline, not kept alive per read."""
    import socket as s_
    import time
    from app.netcheck import pinned_request
    srv = s_.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]

    def slowloris():
        c, _ = srv.accept()
        c.recv(65536)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
        try:
            for _ in range(100):
                c.sendall(b"x"); time.sleep(0.2)
        except OSError:
            pass
        c.close()
    threading.Thread(target=slowloris, daemon=True).start()
    t0 = time.monotonic()
    with pytest.raises((TimeoutError, OSError)):
        pinned_request(f"http://127.0.0.1:{port}/v1/models", timeout=1.5)
    assert time.monotonic() - t0 < 4
    srv.close()
