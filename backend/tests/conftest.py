import os

TEST_DB = os.environ.get("TEST_DB", "finance_tracker_test")
os.environ["DATABASE_URL"] = f"postgresql+psycopg://ft_app:ft_app_dev@localhost:5432/{TEST_DB}"
os.environ["MIGRATION_DATABASE_URL"] = f"postgresql+psycopg://ft_owner:ft_owner_dev@localhost:5432/{TEST_DB}"
os.environ["COOKIE_SECURE"] = "false"
os.environ["ALLOW_REGISTRATION"] = "true"
os.environ["AI_WORKER"] = "false"  # tests drive the worker explicitly

import itertools  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402

_counter = itertools.count()


@pytest.fixture(scope="session", autouse=True)
def migrated():
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def owner_engine():
    eng = create_engine(get_settings().owner_db_url)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean(owner_engine):
    yield
    with owner_engine.begin() as c:
        c.execute(text("TRUNCATE users, households, sessions, memberships, invitations, accounts, "
                       "household_accounts, import_batches, transactions, household_settings, merchant_rules, recurring_series, subscriptions, notification_channels, notification_prefs, notification_log, fx_rates CASCADE"))


def make_user(name: str = "user") -> TestClient:
    c = TestClient(app)
    n = next(_counter)
    r = c.post("/api/auth/register", json={"email": f"{name}{n}@example.com", "display_name": name.title(),
                                           "password": "correct horse battery"})
    assert r.status_code == 201, r.text
    c.me = r.json()
    return c


@pytest.fixture
def new_user():
    return make_user


def create_household(c: TestClient, name="Home") -> str:
    r = c.post("/api/households", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def invite_and_join(owner: TestClient, member: TestClient, hid: str, role="member"):
    code = owner.post(f"/api/households/{hid}/invites", json={"role": role}).json()["code"]
    r = member.post("/api/invites/accept", json={"code": code})
    assert r.status_code == 200, r.text


def create_account(c: TestClient, name="Current", currency="GBP", type_="bank") -> str:
    r = c.post("/api/accounts", json={"name": name, "type": type_, "currency": currency})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def upload(c: TestClient, aid: str, filename: str, content: bytes):
    return c.post(f"/api/accounts/{aid}/imports", files={"file": (filename, content)})
