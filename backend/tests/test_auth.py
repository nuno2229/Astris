from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import make_user


def test_register_me_logout():
    c = make_user("alice")
    assert c.get("/api/me").json()["email"] == c.me["email"]
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/me").status_code == 401


def test_login_and_wrong_password():
    c = make_user("bob")
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"email": c.me["email"], "password": "nope nope nope"}).status_code == 401
    assert fresh.post("/api/auth/login", json={"email": "ghost@example.com", "password": "x" * 12}).status_code == 401
    r = fresh.post("/api/auth/login", json={"email": c.me["email"].upper(), "password": "correct horse battery"})
    assert r.status_code == 200
    assert fresh.get("/api/me").json()["id"] == c.me["id"]


def test_sessions_are_per_login():
    """Logging out one device must not log out another (distinct sessions, §3.4)."""
    c = make_user("carol")
    other = TestClient(app)
    other.post("/api/auth/login", json={"email": c.me["email"], "password": "correct horse battery"})
    c.post("/api/auth/logout")
    assert other.get("/api/me").status_code == 200


def test_duplicate_email_and_weak_password():
    c = make_user("dave")
    fresh = TestClient(app)
    r = fresh.post("/api/auth/register", json={"email": c.me["email"], "display_name": "X", "password": "long enough pw"})
    assert r.status_code == 409
    r = fresh.post("/api/auth/register", json={"email": "new@example.com", "display_name": "X", "password": "short"})
    assert r.status_code == 422


def test_session_cookie_flags():
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"email": "flags@example.com", "display_name": "F", "password": "long enough pw"})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc


def test_cross_origin_post_blocked():
    c = make_user("erin")
    r = c.post("/api/households", json={"name": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
