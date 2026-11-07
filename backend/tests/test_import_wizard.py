from decimal import Decimal

from tests.conftest import create_account, create_household, make_user, upload

OCT = (b"Date,Description,Paid out,Paid in,Balance\n"
       b"01/10/2026,TESCO,10.00,,990.00\n02/10/2026,SALARY,,500.00,1490.00\n05/10/2026,NETFLIX,10.99,,1479.01\n")
OCT_LATE = (b"Date,Description,Paid out,Paid in,Balance\n"                   # overlaps OCT by one row
            b"05/10/2026,NETFLIX,10.99,,1479.01\n20/10/2026,ALDI,20.00,,1459.01\n")
NOV_GAP = (b"Date,Description,Paid out,Paid in,Balance\n"                    # starts at 1000, not 1459.01
           b"03/11/2026,PRET,5.00,,995.00\n04/11/2026,BOOTS,5.00,,999.00\n")  # and the 2nd row doesn't add up
WEIRD = b"When,What,Value\n2026-10-03,Thing,-4.00\n"


def files(*pairs):
    return [("files", (n, b)) for n, b in pairs]


def test_preview_saves_nothing_and_reports_duplicates_across_files():
    a = make_user("a")
    acc = create_account(a)
    r = a.post(f"/api/accounts/{acc}/imports/preview", files=files(("oct.csv", OCT), ("oct2.csv", OCT_LATE)))
    assert r.status_code == 201, r.text
    p1, p2 = r.json()
    assert (p1["rows_new"], p1["rows_duplicate"]) == (3, 0)
    assert (p2["rows_new"], p2["rows_duplicate_in_batch"]) == (1, 1)
    assert p1["balance"] == {"checked_rows": 2, "mismatched_rows": 0, "gap_expected": None, "gap_found": None, "gap_before": None}
    assert p1["mapping"]["columns"]["out"] == "Paid out" and p1["mapping"]["day_first"] is True
    assert a.get(f"/api/accounts/{acc}/transactions").json() == []          # dry run


def test_balance_gap_and_mismatch_detected():
    a = make_user("a")
    acc = create_account(a)
    upload(a, acc, "oct.csv", OCT)
    p = a.post(f"/api/accounts/{acc}/imports/preview", files=files(("nov.csv", NOV_GAP))).json()[0]
    assert p["balance"]["mismatched_rows"] == 1
    assert Decimal(p["balance"]["gap_expected"]) == Decimal("1479.01") and Decimal(p["balance"]["gap_found"]) == Decimal("1000.00")


def test_adjust_mapping_and_date_order_then_commit_and_rollback():
    a = make_user("a")
    hid = create_household(a)
    acc, other = create_account(a, "Main"), create_account(a, "Other")
    previews = a.post(f"/api/accounts/{acc}/imports/preview", files=files(("w.csv", WEIRD), ("oct.csv", OCT))).json()
    weird = previews[0]
    assert weird["ok"] is False and "Headers seen" in weird["error"]
    fixed = a.patch(f"/api/import-previews/{weird['id']}",
                    json={"mapping": {"date": "When", "description": "What", "amount": "Value"}, "account_id": other}).json()
    assert fixed["ok"] and fixed["rows_new"] == 1 and fixed["account_id"] == other
    us = a.patch(f"/api/import-previews/{previews[1]['id']}", json={"day_first": False}).json()
    assert us["ok"] is False or us["date_from"] != previews[1]["date_from"]   # 05/10 read as May 10th (or invalid)
    a.patch(f"/api/import-previews/{previews[1]['id']}", json={"day_first": True})
    run = a.post("/api/import-runs", json={"preview_ids": [weird["id"], previews[1]["id"]]})
    assert run.status_code == 201 and run.json()["rows_new"] == 4 and run.json()["files"] == 2
    assert len(a.get(f"/api/accounts/{acc}/transactions").json()) == 3
    assert len(a.get(f"/api/accounts/{other}/transactions").json()) == 1
    assert a.patch(f"/api/import-previews/{weird['id']}", json={}).status_code == 404   # previews consumed
    # earlier data is untouched by rollback
    upload(a, acc, "late.csv", OCT_LATE)
    assert len(a.get(f"/api/accounts/{acc}/transactions").json()) == 4
    runs = a.get("/api/import-runs").json()
    assert runs[0]["rows_present"] == 4
    assert a.delete(f"/api/import-runs/{run.json()['id']}").status_code == 204
    left = a.get(f"/api/accounts/{acc}/transactions").json()
    assert [t["description"] for t in left] == ["ALDI"]          # only the separately imported new row remains
    assert a.get(f"/api/accounts/{other}/transactions").json() == []


def test_wizard_isolation():
    a, b = make_user("a"), make_user("b")
    acc = create_account(a)
    b_acc = create_account(b)
    p = a.post(f"/api/accounts/{acc}/imports/preview", files=files(("oct.csv", OCT))).json()[0]
    assert b.post(f"/api/accounts/{acc}/imports/preview", files=files(("oct.csv", OCT))).status_code == 404
    assert b.patch(f"/api/import-previews/{p['id']}", json={}).status_code == 404
    assert b.post("/api/import-runs", json={"preview_ids": [p["id"]]}).status_code == 404
    assert a.patch(f"/api/import-previews/{p['id']}", json={"account_id": b_acc}).status_code == 404
    run = a.post("/api/import-runs", json={"preview_ids": [p["id"]]}).json()
    assert b.delete(f"/api/import-runs/{run['id']}").status_code == 404
    assert b.get("/api/import-runs").json() == []


def test_upload_budgets(monkeypatch):
    from app.routers import import_wizard as w
    a = make_user("a")
    acc = create_account(a)
    monkeypatch.setattr(w, "MAX_REQUEST_BYTES", len(OCT) + 10)
    r = a.post(f"/api/accounts/{acc}/imports/preview", files=files(("a.csv", OCT), ("b.csv", OCT)))
    assert r.status_code == 413 and "too large together" in r.json()["detail"]
    monkeypatch.setattr(w, "MAX_REQUEST_BYTES", 10 ** 9)
    monkeypatch.setattr(w, "MAX_USER_PREVIEWS", 2)
    assert a.post(f"/api/accounts/{acc}/imports/preview", files=files(("a.csv", OCT), ("b.csv", OCT))).status_code == 201
    assert a.post(f"/api/accounts/{acc}/imports/preview", files=files(("c.csv", OCT))).status_code == 429


def test_expired_previews_purged_globally(owner_engine):
    from sqlalchemy import text
    a = make_user("a")
    acc = create_account(a)
    a.post(f"/api/accounts/{acc}/imports/preview", files=files(("a.csv", OCT)))
    with owner_engine.begin() as c:
        c.execute(text("UPDATE import_previews SET expires_at = now() - interval '1 minute'"))
    from app.db import get_engine
    with get_engine().begin() as c:   # runtime role, no user bound: only the definer function can do this
        assert c.execute(text("SELECT purge_expired_previews()")).scalar() == 1


def test_concurrent_uploads_cannot_exceed_quota(monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    from app.main import app
    from app.routers import import_wizard as w
    a = make_user("a")
    acc = create_account(a)
    monkeypatch.setattr(w, "MAX_USER_PREVIEWS", 1)
    clients = [TestClient(app, cookies=a.cookies) for _ in range(4)]
    codes = []
    ts = [threading.Thread(target=lambda c=c: codes.append(
        c.post(f"/api/accounts/{acc}/imports/preview", files=files(("a.csv", OCT))).status_code)) for c in clients]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes) == [201, 429, 429, 429]


def test_saved_layout_is_reused_next_time_and_can_be_forgotten():
    a, b = make_user("a"), make_user("b")
    acc = create_account(a, "Main")
    p = a.post(f"/api/accounts/{acc}/imports/preview", files=files(("w.csv", WEIRD))).json()[0]
    a.patch(f"/api/import-previews/{p['id']}", json={"mapping": {"date": "When", "description": "What", "amount": "Value"}})
    assert a.post("/api/import-runs", json={"preview_ids": [p["id"]]}).status_code == 201
    prof = a.get("/api/import-profiles").json()
    assert len(prof) == 1 and prof[0]["headers"] == "When, What, Value"
    # next month's file from the same bank (with a junk line on top) just works, in the wizard and the quick import
    nxt = b"Exported by MyBank\nWhen,What,Value\n2026-11-03,Other thing,-6.00\n"
    again = a.post(f"/api/accounts/{acc}/imports/preview", files=files(("w2.csv", nxt))).json()[0]
    assert again["ok"] and again["profile"] == prof[0]["signature"] and "saved" in again["warnings"][0]
    assert upload(a, acc, "w3.csv", b"When,What,Value\n2026-11-09,Third,-1.00\n").status_code == 201
    # profiles are personal
    other = create_account(b, "Theirs")
    assert b.post(f"/api/accounts/{other}/imports/preview", files=files(("w.csv", WEIRD))).json()[0]["ok"] is False
    assert b.delete(f"/api/import-profiles/{prof[0]['signature']}").status_code == 404
    assert a.delete(f"/api/import-profiles/{prof[0]['signature']}").status_code == 204
    assert a.post(f"/api/accounts/{acc}/imports/preview", files=files(("w4.csv", nxt))).json()[0]["ok"] is False
