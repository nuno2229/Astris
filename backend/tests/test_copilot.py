import json
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

T = date.today()


def setup():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    acc = create_account(a)
    a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    wk = T - timedelta(days=T.weekday())
    upload(a, acc, "s.csv", f"Date,Description,Amount,Balance\n{wk.isoformat()},TESCO STORES,-42.10,957.90\n"
                            f"{wk.isoformat()},ALDI,-7.90,950.00\n{wk.isoformat()},PRET,-6.40,943.60\n".encode())
    return a, b, hid, acc


def ask(c, hid, msg):
    r = c.post("/api/copilot/ask", json={"household_id": hid, "message": msg})
    assert r.status_code == 200, r.text
    return r.json()


def test_grounded_spending_answer_with_sources():
    a, b, hid, _ = setup()
    r = ask(a, hid, "How much did we spend on groceries this week?")
    assert "£50.00 on Groceries this week" in r["answer"] and r["understood_by"] == "rules"
    assert len(r["citations"]) == 2 and "Tesco" in r["citations"][0]["label"]
    assert "£6.40" in ask(b, hid, "how much at pret this week")["answer"]
    assert "£943.60" in ask(a, hid, "what's our balance")["answer"]


def test_actions_need_confirmation_and_are_logged():
    a, b, hid, _ = setup()
    r = ask(a, hid, "set a £200 budget for groceries")
    assert r["action"]["summary"].startswith("Set a monthly Groceries budget of £200.00")
    assert a.get(f"/api/households/{hid}/budgets").json() == []                     # nothing until confirmed
    done = a.post(f"/api/copilot/actions/{r['action']['id']}/confirm").json()
    assert done["answer"] == "Done."
    assert [(x["label"], x["amount"]) for x in a.get(f"/api/households/{hid}/budgets").json()] == [("Groceries", "200.0000")]
    # asking again updates rather than duplicating
    r2 = ask(a, hid, "set a £250 budget for groceries")
    a.post(f"/api/copilot/actions/{r2['action']['id']}/confirm")
    assert [x["amount"] for x in a.get(f"/api/households/{hid}/budgets").json()] == ["250.0000"]
    assert any(e["action"] == "assistant.action" for e in a.get(f"/api/audit?household_id={hid}").json())
    # confirming twice / someone else's suggestion does nothing
    assert a.post(f"/api/copilot/actions/{r['action']['id']}/confirm").status_code == 404
    r3 = ask(a, hid, "add 2 cartons of milk to the pantry")
    assert b.post(f"/api/copilot/actions/{r3['action']['id']}/confirm").status_code == 404


def test_pantry_and_goal_actions():
    a, b, hid, _ = setup()
    r = ask(a, hid, "we bought 6 eggs")
    a.post(f"/api/copilot/actions/{r['action']['id']}/confirm")
    assert "Eggs: 6 in stock" in ask(a, hid, "do we have eggs?")["answer"]
    r = ask(a, hid, "used 2 eggs")
    assert "you have 6" in r["action"]["summary"]
    a.post(f"/api/copilot/actions/{r['action']['id']}/confirm")
    assert "4 in stock" in ask(a, hid, "do we have eggs?")["answer"]
    g = a.post(f"/api/households/{hid}/goals", json={"name": "Holiday", "target": "500", "currency": "GBP"}).json()
    r = ask(b, hid, "put £50 towards the holiday")
    b.post(f"/api/copilot/actions/{r['action']['id']}/confirm")
    assert a.get(f"/api/goals/{g['id']}").json()["members"][0]["name"] == "B"
    c = ask(a, hid, "put £50 towards the boat")
    assert c.get("action") is None and "Holiday" in c["answer"]
    a.delete(f"/api/copilot/actions/{r['action']['id']}")


def test_scoped_to_the_asking_user():
    a, b, hid, _ = setup()
    x = make_user("x")
    create_household(x)
    assert x.post("/api/copilot/ask", json={"household_id": hid, "message": "how much did we spend"}).status_code == 404
    assert "£0.00" in ask(x, x.get("/api/me/households").json()[0]["id"], "how much did we spend this week")["answer"]


@pytest.fixture
def fake_llm():
    state = {"reply": None, "requests": []}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            out = json.dumps({"choices": [{"message": {"content": json.dumps(state["reply"])}}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(out)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_port}"
    yield state
    srv.shutdown()


def test_ai_understands_but_numbers_come_from_data(fake_llm):
    a, b, hid, _ = setup()
    a.put(f"/api/households/{hid}/settings", json={"llm_enabled": True, "llm_base_url": fake_llm["url"]})
    fake_llm["reply"] = {"tool": "spending", "category": "Groceries", "period": "this_week", "amount": 999999}
    r = ask(a, hid, "what did the big shop come to lately?")
    assert r["understood_by"] == "ai" and "£50.00" in r["answer"] and "999" not in r["answer"]
    assert fake_llm["requests"][0]["messages"][1]["content"] == "what did the big shop come to lately?"
    fake_llm["reply"] = {"tool": "transfer_all_money", "period": "this_week"}          # invalid -> rules
    r = ask(a, hid, "how much on groceries this week")
    assert r["understood_by"] == "rules" and "£50.00" in r["answer"]
