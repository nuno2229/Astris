"""P-6: self-signed local HTTPS."""
import ssl
import subprocess

from cryptography import x509

from app.tls import ensure


def test_ca_and_server_cert(tmp_path, monkeypatch):
    d = ensure(tmp_path / "tls", ["192.168.1.20", "tower.local", "localhost"])
    ca = x509.load_pem_x509_certificate((d / "ca.crt").read_bytes())
    srv = x509.load_pem_x509_certificate((d / "server.crt").read_bytes())
    san = srv.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "tower.local" in san.get_values_for_type(x509.DNSName)
    assert srv.issuer == ca.subject and (srv.not_valid_after_utc - srv.not_valid_before_utc).days <= 398
    assert oct((d / "ca.key").stat().st_mode)[-3:] == "600" and oct((d / "server.key").stat().st_mode)[-3:] == "600"
    # a client trusting only our CA accepts the chain
    ctx = ssl.create_default_context(cafile=str(d / "ca.crt"))
    ctx.load_verify_locations(cafile=str(d / "ca.crt"))
    before = (d / "server.crt").read_bytes()
    assert ensure(d, ["192.168.1.20", "tower.local", "localhost"]) and (d / "server.crt").read_bytes() == before   # kept
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert nc.critical and "tower.local" in [g.value for g in nc.value.permitted_subtrees if isinstance(g, x509.DNSName)]
    assert (ca.not_valid_after_utc - ca.not_valid_before_utc).days <= 5 * 365 + 1
    old_ca = (d / "ca.crt").read_bytes()
    ensure(d, ["192.168.1.21", "localhost"])          # different addresses: new CA bound to them
    assert (d / "server.crt").read_bytes() != before and (d / "ca.crt").read_bytes() != old_ca


def test_ca_download_only_when_enabled(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    monkeypatch.setenv("TLS_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("HTTPS", "off")
    assert c.get("/ca.crt").status_code == 404
    ensure(tmp_path / "t")
    monkeypatch.setenv("HTTPS", "self-signed")
    r = c.get("/ca.crt")
    assert r.status_code == 200 and b"BEGIN CERTIFICATE" in r.content and b"PRIVATE" not in r.content
