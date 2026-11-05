from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_cookie_secure_auto(monkeypatch):
    monkeypatch.setattr(get_settings(), "cookie_secure", "auto")
    http = TestClient(app, base_url="http://tower:8000")
    r = http.post("/api/auth/register", json={"email": "h1@example.com", "display_name": "H", "password": "long enough pw"})
    assert "secure" not in r.headers["set-cookie"].lower()
    https = TestClient(app, base_url="https://ledger.home.arpa")
    r = https.post("/api/auth/register", json={"email": "h2@example.com", "display_name": "H", "password": "long enough pw"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_health_reports_db():
    assert TestClient(app).get("/api/health").json()["db"] == "ok"
