"""Budgets & spending limits (E12). Warnings only: Astris can't block card payments (D-7).

kind: category | merchant (shop) | account (card, personal only); period: daily | weekly | monthly.
Household budgets count accounts shared 'in totals'; personal budgets count your own accounts.
Regular payments still due in the period are 'committed' so a scheduled bill can't silently blow it (E12-05).
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import audit
from app.categorise.rules import CATEGORIES
from app.deps import DB, User, not_found
from app.routers.fx import convert, rate_table
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["budgets"])
Q = Decimal("0.01")
Level = Literal["ok", "warning", "at_risk", "over"]


class BudgetIn(BaseModel):
    scope: Literal["household", "personal"] = "household"
    kind: Literal["category", "merchant", "account"]
    category: str | None = None
    merchant_key: str | None = Field(default=None, max_length=100)
    merchant_name: str | None = Field(default=None, max_length=100)
    account_id: UUID | None = None
    period: Literal["daily", "weekly", "monthly"] = "monthly"
    amount: Decimal = Field(gt=0, lt=Decimal("1e12"), decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    warn_percent: int | None = Field(default=80, ge=1, le=99)
    warn_amount: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e12"), decimal_places=2)

    @field_validator("currency", mode="before")
    @classmethod
    def up(cls, v):
        return v.upper() if isinstance(v, str) else v

    @model_validator(mode="after")
    def target(self):
        if self.kind == "category" and self.category not in CATEGORIES:
            raise ValueError("Pick a category from the list")
        if self.kind == "merchant" and not self.merchant_key:
            raise ValueError("Pick a shop")
        if self.kind == "account":
            if not self.account_id:
                raise ValueError("Pick a card or account")
            self.scope = "personal"   # a card limit is about your own card
        return self


class BudgetUpdate(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e12"), decimal_places=2)
    period: Literal["daily", "weekly", "monthly"] | None = None
    warn_percent: int | None = Field(default=None, ge=1, le=99)
    warn_amount: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e12"), decimal_places=2)


class BudgetOut(BaseModel):
    id: UUID
    scope: str
    kind: str
    label: str
    period: str
    amount: Decimal
    currency: str
    warn_percent: int | None
    warn_amount: Decimal | None
    period_start: date
    period_end: date          # exclusive
    spent: Decimal
    committed: Decimal        # regular payments still due this period
    left: Decimal
    used: Decimal             # 0..n (can exceed 1)
    level: Level
    message: str
    unconverted: list[str]    # currencies with spending but no exchange rate (left out)
    mine: bool
    can_edit: bool = False


def window(period: str, today: date) -> tuple[date, date]:
    if period == "daily":
        return today, today + timedelta(days=1)
    if period == "weekly":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=7)
    start = today.replace(day=1)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def _match(b) -> tuple[str, dict]:
    if b.kind == "category":
        return "t.category = :m", {"m": b.category}
    if b.kind == "merchant":
        return "t.merchant_key = :m", {"m": b.merchant_key}
    return "t.account_id = :m", {"m": b.account_id}


def _accounts_clause(b) -> str:
    if b.scope == "personal":
        return "a.owner_user_id = :owner"
    return """t.account_id IN (SELECT ha.account_id FROM household_accounts ha
                 JOIN accounts a2 ON a2.id = ha.account_id
                 JOIN memberships ow ON ow.household_id = ha.household_id AND ow.user_id = a2.owner_user_id
                WHERE ha.household_id = :h AND ha.attribution = 'include')"""


def _money(v: Decimal, cur: str) -> str:
    sym = {"GBP": "£", "EUR": "€", "USD": "$"}.get(cur, cur + " ")
    return f"{sym}{abs(v):,.2f}"


def build(db: Session, b, me: UUID, today: date | None = None) -> BudgetOut:
    today = today or date.today()
    start, end = window(b.period, today)
    cond, params = _match(b)
    rates = rate_table(db, b.household_id)
    rows = db.execute(text(f"""
        SELECT t.currency, sum(-t.amount) AS spent FROM transactions t JOIN accounts a ON a.id = t.account_id
         WHERE {cond} AND {_accounts_clause(b)} AND (t.amount < 0 OR t.refund_of IS NOT NULL) AND t.transfer_group IS NULL
           AND t.posted_on >= :s AND t.posted_on < :e
         GROUP BY t.currency"""), {**params, "h": b.household_id, "owner": b.owner_user_id, "s": start, "e": end}).all()
    spent, missing = Decimal(0), []
    for r in rows:
        c = convert(Decimal(r.spent), r.currency, b.currency, rates)
        if c is None:
            missing.append(r.currency)
        else:
            spent += c
    # regular payments still to come this period (E12-05)
    rcond = {"category": "rs.category = :m", "merchant": "rs.merchant_key = :m", "account": "rs.account_id = :m"}[b.kind]
    racc = "a.owner_user_id = :owner" if b.scope == "personal" else _accounts_clause(b).replace("t.account_id", "rs.account_id")
    committed = Decimal(0)
    for r in db.execute(text(f"""
        SELECT rs.currency, rs.expected_amount FROM recurring_series rs JOIN accounts a ON a.id = rs.account_id
         WHERE {rcond} AND {racc} AND rs.direction = 'out' AND rs.status = 'active'
           AND rs.next_date >= :today AND rs.next_date < :e"""),
            {**params, "h": b.household_id, "owner": b.owner_user_id, "today": today, "e": end}):
        c = convert(abs(Decimal(r.expected_amount)), r.currency, b.currency, rates)
        if c is not None:
            committed += c
    limit = Decimal(b.amount)
    used = (spent / limit) if limit else Decimal(0)
    warn_hit = (b.warn_percent is not None and used * 100 >= b.warn_percent) or \
               (b.warn_amount is not None and spent >= Decimal(b.warn_amount))
    if spent > limit:
        level, msg = "over", f"Over by {_money(spent - limit, b.currency)}"
    elif spent + committed > limit:
        level, msg = "at_risk", f"Bills still due ({_money(committed, b.currency)}) will take this over"
    elif warn_hit:
        level, msg = "warning", f"{int(used * 100)}% used, {_money(limit - spent, b.currency)} left"
    else:
        level, msg = "ok", f"{_money(limit - spent, b.currency)} left"
    return BudgetOut(id=b.id, scope=b.scope, kind=b.kind, label=_label(db, b), period=b.period, amount=limit,
                     currency=b.currency, warn_percent=b.warn_percent, warn_amount=b.warn_amount, period_start=start,
                     period_end=end, spent=spent.quantize(Q), committed=committed.quantize(Q),
                     left=(limit - spent).quantize(Q), used=used.quantize(Decimal("0.001")), level=level, message=msg,
                     unconverted=sorted(missing), mine=b.owner_user_id == me or b.created_by == me)


def _label(db: Session, b) -> str:
    if b.kind == "category":
        return b.category
    if b.kind == "merchant":
        return b.merchant_name or b.merchant_key.title()
    name = db.execute(text("SELECT name FROM accounts WHERE id = :a"), {"a": b.account_id}).scalar()
    return name or "Card"


def visible_budgets(db: Session, hid: UUID, me: UUID) -> list[BudgetOut]:
    """Household budgets + your personal ones in this household (RLS hides other people's personal budgets)."""
    rows = db.execute(text("SELECT * FROM budgets WHERE household_id = :h ORDER BY scope, created_at"), {"h": hid}).all()
    role = db.execute(text("SELECT app_my_role(:h)"), {"h": hid}).scalar()
    out = []
    for b in rows:
        o = build(db, b, me)
        o.can_edit = b.owner_user_id == me if b.scope == "personal" else (b.created_by == me or role in ("owner", "admin"))
        out.append(o)
    order = {"over": 0, "at_risk": 1, "warning": 2, "ok": 3}
    return sorted(out, key=lambda x: (order[x.level], x.scope, x.label))


def _get(db: DB, bid: UUID):
    b = db.execute(text("SELECT * FROM budgets WHERE id = :b"), {"b": bid}).first()
    if b is None:
        raise not_found("Budget not found")
    return b


def _require_manager(db: DB, b, user) -> None:
    if b.scope == "household" and b.created_by != user.id and my_role(db, b.household_id) not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only whoever set up this budget, or the household owner or an admin, can change it")


@router.get("/households/{hid}/budgets", response_model=list[BudgetOut])
def list_budgets(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    return visible_budgets(db, hid, user.id)


@router.post("/households/{hid}/budgets", response_model=BudgetOut, status_code=201)
def create_budget(hid: UUID, body: BudgetIn, user: User, db: DB):
    my_role(db, hid)
    if body.kind == "account":
        own = db.execute(text("SELECT 1 FROM accounts WHERE id = :a AND owner_user_id = :u"),
                         {"a": body.account_id, "u": user.id}).first()
        if not own:
            raise not_found("Account not found")
    bid = db.execute(text("""
        INSERT INTO budgets (household_id, scope, owner_user_id, created_by, kind, category, merchant_key, merchant_name,
                             account_id, period, amount, currency, warn_percent, warn_amount)
        VALUES (:h, :scope, :owner, :u, :kind, :cat, :mk, :mn, :acc, :p, :amt, :cur, :wp, :wa) RETURNING id"""),
        {"h": hid, "scope": body.scope, "owner": user.id if body.scope == "personal" else None, "u": user.id,
         "kind": body.kind, "cat": body.category if body.kind == "category" else None,
         "mk": body.merchant_key if body.kind == "merchant" else None,
         "mn": body.merchant_name if body.kind == "merchant" else None,
         "acc": body.account_id if body.kind == "account" else None, "p": body.period, "amt": body.amount,
         "cur": body.currency, "wp": body.warn_percent, "wa": body.warn_amount}).scalar_one()
    b = _get(db, bid)
    if body.scope == "household":
        audit.log(db, user.id, "budget.create", "budget", f"New {body.period} budget: {_label(db, b)} {body.amount} {body.currency}",
                  entity_id=bid, household_id=hid)
    return build(db, b, user.id)


@router.patch("/budgets/{bid}", response_model=BudgetOut)
def update_budget(bid: UUID, body: BudgetUpdate, user: User, db: DB):
    b = _get(db, bid)
    _require_manager(db, b, user)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if not (k in ("amount", "period") and v is None)}
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)   # keys from the schema only
        db.execute(text(f"UPDATE budgets SET {sets} WHERE id = :id"), {**fields, "id": bid})
        if b.scope == "household":
            audit.log(db, user.id, "budget.update", "budget", f"Changed budget {_label(db, b)}", entity_id=bid,
                      household_id=b.household_id, before={k: getattr(b, k) for k in fields}, after=fields)
    return build(db, _get(db, bid), user.id)


@router.delete("/budgets/{bid}", status_code=204)
def delete_budget(bid: UUID, user: User, db: DB):
    b = _get(db, bid)
    _require_manager(db, b, user)
    db.execute(text("DELETE FROM budgets WHERE id = :b"), {"b": bid})
    if b.scope == "household":
        audit.log(db, user.id, "budget.delete", "budget", f"Removed budget {_label(db, b)}", entity_id=bid,
                  household_id=b.household_id)


@router.get("/households/{hid}/merchants")
def top_merchants(hid: UUID, user: User, db: DB):
    """Shops seen in the last 90 days (for picking a shop budget)."""
    my_role(db, hid)
    rows = db.execute(text("""
        SELECT t.merchant_key, max(coalesce(t.merchant, initcap(t.merchant_key))) AS name, sum(-t.amount) AS spent
          FROM transactions t
          JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h
         WHERE t.amount < 0 AND t.merchant_key IS NOT NULL AND t.posted_on >= current_date - 90
         GROUP BY t.merchant_key ORDER BY spent DESC LIMIT 40"""), {"h": hid})
    return [{"merchant_key": r.merchant_key, "name": r.name} for r in rows]
