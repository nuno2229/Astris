from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import fx_ecb
from app.categorise import worker
from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()
XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time='{T.isoformat()}'>
<Cube currency='USD' rate='1.0800'/><Cube currency='JPY' rate='162.50'/><Cube currency='GBP' rate='0.8500'/>
<Cube currency='CHF' rate='0.9400'/><Cube currency='SEK' rate='11.40'/><Cube currency='XX1' rate='evil'/>
</Cube></Cube></gesmes:Envelope>"""


@pytest.fixture(autouse=True)
def clean_ecb(owner_engine, monkeypatch):
    monkeypatch.setattr(fx_ecb, "_last_attempt", 0.0)
    yield
    with owner_engine.begin() as c:
        c.execute(text("DELETE FROM fx_ecb"))


@pytest.fixture
def ecb(monkeypatch):
    calls = []

    def fake(url, method="GET", body=None, timeout=5, max_bytes=0, headers=None, **kw):
        calls.append(url)
        return 200, XML.encode()
    monkeypatch.setattr(fx_ecb, "pinned_request", fake)
    return calls


def test_parse_ignores_junk():
    d, rates = fx_ecb.parse(XML)
    assert d == T and rates["GBP"] == "0.8500" and "XX1" not in rates and len(rates) == 5
    with pytest.raises(ValueError):
        fx_ecb.parse("<Cube time='2026-01-01'><Cube currency='USD' rate='1'/></Cube>")   # too few


def test_no_fetch_unless_a_household_opted_in(ecb):
    make_user("a")
    assert worker.run_fx() is False and ecb == []


def setup_multi():
    a = make_user("a")
    hid = create_household(a)
    usd = create_account(a, "USD", currency="USD")
    a.put(f"/api/households/{hid}/accounts/{usd}", json={"attribution": "include"})
    upload(a, usd, "u.csv", f"Date,Description,Amount\n{T.isoformat()},WALMART,-10.80\n".encode())
    return a, hid


def test_opt_in_fetch_once_and_convert_through_eur(ecb):
    a, hid = setup_multi()
    month = T.strftime("%Y-%m")
    assert a.get(f"/api/households/{hid}/summary?month={month}").json()["converted"]["missing"] == ["USD"]
    r = a.put(f"/api/households/{hid}/fx-auto", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert worker.run_fx(force=True) is True and ecb == [fx_ecb.ECB_URL]
    assert worker.run_fx() is False and len(ecb) == 1                 # cache is fresh: no second fetch
    c = a.get(f"/api/households/{hid}/summary?month={month}").json()["converted"]
    # 10.80 USD -> 10 EUR -> 8.50 GBP
    assert c["missing"] == [] and Decimal(c["spent"]) == Decimal("8.50")
    info = a.get(f"/api/households/{hid}/fx-auto").json()
    assert info["as_of"] == T.isoformat() and info["currencies"] == 5


def test_manual_rate_overrides_ecb_and_opt_out_stops_use(ecb):
    a, hid = setup_multi()
    a.put(f"/api/households/{hid}/fx-auto", json={"enabled": True})
    worker.run_fx(force=True)
    a.put(f"/api/households/{hid}/fx-rates", json={"from_currency": "USD", "to_currency": "GBP", "rate": "0.50"})
    month = T.strftime("%Y-%m")
    assert Decimal(a.get(f"/api/households/{hid}/summary?month={month}").json()["converted"]["spent"]) == Decimal("5.40")
    a.delete(f"/api/households/{hid}/fx-rates/USD/GBP")
    a.put(f"/api/households/{hid}/fx-auto", json={"enabled": False})
    assert a.get(f"/api/households/{hid}/summary?month={month}").json()["converted"]["missing"] == ["USD"]


def test_offline_keeps_cached_rates(ecb, monkeypatch):
    a, hid = setup_multi()
    a.put(f"/api/households/{hid}/fx-auto", json={"enabled": True})
    worker.run_fx(force=True)
    monkeypatch.setattr(fx_ecb, "pinned_request", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert worker.run_fx(force=True) is False
    month = T.strftime("%Y-%m")
    assert a.get(f"/api/households/{hid}/summary?month={month}").json()["converted"]["missing"] == []


def test_only_owner_toggles(ecb):
    a, hid = setup_multi()
    b, x = make_user("b"), make_user("x")
    invite_and_join(a, b, hid, role="admin")
    assert b.put(f"/api/households/{hid}/fx-auto", json={"enabled": True}).status_code == 403
    assert x.put(f"/api/households/{hid}/fx-auto", json={"enabled": True}).status_code == 404
    assert b.get(f"/api/households/{hid}/fx-auto").json()["can_edit"] is False
