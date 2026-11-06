"""In-app assistant (E15). Understands a request (household's local LLM if set up, otherwise rules), answers from
the database with sources, and proposes actions that only run after the user confirms (D-9 b). Runs entirely as
the asking user, so it can never read or change anything outside their households and accounts."""
import json
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import audit
from app.copilot.intent import from_llm, rules
from app.copilot.tools import ACTIONS, HANDLERS, HELP, Ctx, propose
from app.deps import DB, User, not_found
from app.routers.households import my_role

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


class AskIn(BaseModel):
    household_id: UUID
    message: str = Field(min_length=1, max_length=500)


class Citation(BaseModel):
    label: str


class ActionOut(BaseModel):
    id: UUID
    kind: str
    summary: str


class AskOut(BaseModel):
    answer: str
    citations: list[Citation] = []
    link: str | None = None
    action: ActionOut | None = None
    understood_by: Literal["ai", "rules"]


class DoneOut(BaseModel):
    answer: str
    link: str | None = None


@router.post("/ask", response_model=AskOut)
def ask(body: AskIn, user: User, db: DB):
    my_role(db, body.household_id)
    h = db.execute(text("SELECT name FROM households WHERE id = :h"), {"h": body.household_id}).scalar_one()
    home = db.execute(text("SELECT home_currency FROM users WHERE id = :u"), {"u": user.id}).scalar_one()
    it = None
    cfg = db.execute(text("""SELECT llm_base_url, llm_model, llm_allow_internet FROM household_settings
                              WHERE household_id = :h AND llm_enabled AND llm_base_url IS NOT NULL"""), {"h": body.household_id}).first()
    if cfg:
        it = from_llm(body.message, cfg.llm_base_url, cfg.llm_model, cfg.llm_allow_internet)
    if it is None or it.tool == "help":
        r = rules(body.message)
        if it is None or r.tool != "help":
            it = r
    c = Ctx(db, user.id, body.household_id, h, home)
    if it.tool in HANDLERS:
        res = HANDLERS[it.tool](c, it)
        return AskOut(answer=res["answer"], citations=[Citation(**x) for x in res.get("citations", [])],
                      link=res.get("link"), understood_by=it.source)
    if it.tool in ACTIONS:
        res = propose(c, it)
        if "action" not in res:
            return AskOut(answer=res["answer"], understood_by=it.source)
        a = res["action"]
        db.execute(text("DELETE FROM copilot_actions WHERE user_id = :u AND expires_at < now()"), {"u": user.id})
        aid = db.execute(text("""INSERT INTO copilot_actions (user_id, household_id, kind, params, summary)
                                 VALUES (:u, :h, :k, CAST(:p AS jsonb), :s) RETURNING id"""),
                         {"u": user.id, "h": body.household_id, "k": a["kind"], "p": json.dumps(a["params"]), "s": a["summary"]}).scalar_one()
        return AskOut(answer="Here's what I'd do. Confirm to go ahead.", action=ActionOut(id=aid, kind=a["kind"], summary=a["summary"]),
                      understood_by=it.source)
    return AskOut(answer=HELP, understood_by=it.source)


@router.post("/actions/{aid}/confirm", response_model=DoneOut)
def confirm(aid: UUID, user: User, db: DB):
    a = db.execute(text("SELECT * FROM copilot_actions WHERE id = :a AND expires_at > now()"), {"a": aid}).first()
    if a is None:
        raise not_found("That suggestion has expired. Ask again.")
    db.execute(text("DELETE FROM copilot_actions WHERE id = :a"), {"a": aid})
    p, hid = a.params, a.household_id
    my_role(db, hid)   # still a member?
    try:
        if a.kind == "set_budget":
            from app.routers.budgets import BudgetIn, BudgetUpdate, create_budget, update_budget
            existing = db.execute(text("""SELECT id FROM budgets WHERE household_id = :h AND scope = 'household' AND kind = 'category'
                                           AND category = :c AND period = :p"""), {"h": hid, "c": p["category"], "p": p["period"]}).scalar()
            if existing:
                update_budget(existing, BudgetUpdate(amount=Decimal(str(p["amount"]))), user, db)
            else:
                create_budget(hid, BudgetIn(kind="category", category=p["category"], amount=Decimal(str(p["amount"])),
                                            currency=p["currency"], period=p["period"]), user, db)
            link = "#/budgets"
        elif a.kind == "pantry_add":
            from app.routers.inventory import LotIn, ProductIn, add_lot, create_product
            if p.get("product_id"):
                add_lot(UUID(p["product_id"]), LotIn(quantity=Decimal(str(p["quantity"]))), user, db)
            else:
                create_product(hid, ProductIn(name=p["name"], quantity=Decimal(str(p["quantity"]))), user, db)
            link = "#/pantry"
        elif a.kind == "pantry_use":
            from app.routers.inventory import UseIn, use
            use(UUID(p["product_id"]), UseIn(quantity=Decimal(str(p["quantity"]))), user, db)
            link = "#/pantry"
        elif a.kind == "list_add":
            from app.routers.shopping import ItemIn, _list, _log, add_item, default_list
            lst = _list(db, default_list(db, hid, user.id))
            add_item(db, lst, user.id, ItemIn(name=p["name"], quantity=Decimal(str(p["quantity"]))), "assistant")
            _log(db, user.id, lst, "list.add", f"Added {p['quantity']:g}× {p['name']} to {lst.name}")
            link = "#/list"
        elif a.kind == "goal_contribute":
            from app.routers.goals import ContributionIn, contribute
            contribute(UUID(p["goal_id"]), ContributionIn(amount=Decimal(str(p["amount"])), note="via the assistant"), user, db)
            link = "#/goals"
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown action")
    except HTTPException as e:
        return DoneOut(answer=f"I couldn't do that: {e.detail}")
    audit.log(db, user.id, "assistant.action", "assistant", f"Assistant: {a.summary.rstrip('?')}", household_id=hid)
    return DoneOut(answer="Done.", link=link)


@router.delete("/actions/{aid}", status_code=204)
def cancel(aid: UUID, user: User, db: DB):
    db.execute(text("DELETE FROM copilot_actions WHERE id = :a"), {"a": aid})
