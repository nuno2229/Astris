"""Savings goals (E11): household goals, per-member contributions, progress from the ledger."""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import audit
from app.deps import DB, User, not_found
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["goals"])
PACE_DAYS = 90
MIN_PACE_WINDOW_DAYS = 30
Q = Decimal("0.01")


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    target: Decimal = Field(gt=0, lt=Decimal("1e12"), decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    deadline: date | None = None
    account_id: UUID | None = None      # follow this savings account's balance (G-6); yours, shared into the household


    @field_validator("currency", mode="before")
    @classmethod
    def up(cls, v):
        return v.upper() if isinstance(v, str) else v


class GoalAccountIn(BaseModel):
    account_id: UUID | None = None


class GoalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    target: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e12"), decimal_places=2)
    deadline: date | None = None
    archived: bool | None = None


class ContributionIn(BaseModel):
    amount: Decimal = Field(gt=Decimal("-1e12"), lt=Decimal("1e12"), decimal_places=2)   # negative = taken out
    made_on: date | None = None
    note: str | None = Field(default=None, max_length=200)


class FromTxIn(BaseModel):
    transaction_id: UUID
    always: bool = False    # also count future statement lines from the same payee (auto-match rule)


class Member(BaseModel):
    user_id: UUID
    name: str
    saved: Decimal
    share: Decimal          # 0..1 of everything saved


class Contribution(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    amount: Decimal
    made_on: date
    note: str | None
    from_statement: bool
    mine: bool


class Rule(BaseModel):
    id: UUID
    label: str


class GoalOut(BaseModel):
    id: UUID
    name: str
    target: Decimal
    currency: str
    deadline: date | None
    archived: bool
    saved: Decimal
    remaining: Decimal
    progress: Decimal                     # 0..1 (capped)
    achieved: bool
    days_left: int | None
    per_month_needed: Decimal | None      # to hit the deadline from today
    pace_per_month: Decimal               # last 90 days (or since the goal started)
    projected_date: date | None           # at current pace
    on_track: bool | None
    members: list[Member]
    recent: list[Contribution] = []
    my_rules: list[Rule] = []
    account_id: UUID | None = None        # progress follows this account's balance (G-6)
    account_name: str | None = None
    account_status: str | None = None     # "ok" | "no_balance" (no statement with balances yet) | "not_shared"


def _goal(db: DB, gid: UUID):
    g = db.execute(text("SELECT * FROM goals WHERE id = :g"), {"g": gid}).first()
    if g is None:
        raise not_found("Goal not found")
    return g


def _require_manager(db: DB, g, user) -> None:
    """Changing or deleting a shared goal: its creator, or the household owner/admin."""
    if g.created_by != user.id and my_role(db, g.household_id) not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only whoever created this goal, or the household owner or an admin, can change it")


def _balance(db: Session, aid: UUID, on: date | None = None) -> Decimal | None:
    """Account balance (today, or at the end of a past day): cash wallets from their entries, banks from statements."""
    a = db.execute(text("SELECT type, opening_balance, opening_date FROM accounts WHERE id = :a"), {"a": aid}).first()
    if a is None:
        return None
    if a.type in ("cash", "savings"):
        if on and a.opening_date and on < a.opening_date:
            return None
        return Decimal(a.opening_balance) + Decimal(db.execute(text("""
            SELECT COALESCE(sum(amount), 0) FROM transactions WHERE account_id = :a
               AND (CAST(:o AS date) IS NULL OR posted_on >= CAST(:o AS date)) AND (CAST(:d AS date) IS NULL OR posted_on <= CAST(:d AS date))"""),
            {"a": aid, "o": a.opening_date, "d": on}).scalar())
    v = db.execute(text("""SELECT running_balance FROM transactions WHERE account_id = :a AND running_balance IS NOT NULL
                             AND (CAST(:d AS date) IS NULL OR posted_on <= CAST(:d AS date))
                           ORDER BY posted_on DESC, created_at DESC LIMIT 1"""), {"a": aid, "d": on}).scalar()
    return Decimal(v) if v is not None else None


def _build_account(db: Session, g, today: date) -> GoalOut:
    acc = db.execute(text("""SELECT a.name, a.owner_user_id, u.display_name FROM accounts a JOIN users u ON u.id = a.owner_user_id
                              JOIN household_accounts ha ON ha.account_id = a.id AND ha.household_id = :h WHERE a.id = :a"""),
                     {"a": g.account_id, "h": g.household_id}).first()
    target = Decimal(g.target)
    status_ = "ok"
    saved = Decimal(0)
    pace = Decimal(0)
    if acc is None:
        status_ = "not_shared"
    else:
        now = _balance(db, g.account_id)
        if now is None:
            status_ = "no_balance"
        else:
            saved = now
            start = max(g.created_at.date() - timedelta(days=PACE_DAYS), today - timedelta(days=PACE_DAYS))
            then = _balance(db, g.account_id, start)
            if then is None:   # account history starts inside the window: measure from its first statement line
                first = db.execute(text("""SELECT posted_on, running_balance - amount AS before FROM transactions
                                            WHERE account_id = :a AND running_balance IS NOT NULL AND posted_on > :s
                                            ORDER BY posted_on, created_at LIMIT 1"""), {"a": g.account_id, "s": start}).first()
                if first is not None:
                    start, then = first.posted_on, Decimal(first.before)
            if then is not None:
                days = max((today - start).days, MIN_PACE_WINDOW_DAYS)
                pace = ((now - then) / days * Decimal("30.44")).quantize(Q)
    remaining = max(target - saved, Decimal(0))
    achieved = saved >= target
    if achieved and g.achieved_at is None:
        db.execute(text("UPDATE goals SET achieved_at = now() WHERE id = :g"), {"g": g.id})
    per_day = pace / Decimal("30.44")
    projected = today + timedelta(days=int((remaining / per_day).to_integral_value(ROUND_HALF_UP))) \
        if not achieved and per_day > 0 else None
    days_left = (g.deadline - today).days if g.deadline else None
    needed = None
    if g.deadline and not achieved:
        needed = (remaining / max(Decimal(max(days_left, 0)) / Decimal("30.44"), Decimal(1))).quantize(Q)
    on_track = (projected is not None and projected <= g.deadline) if g.deadline and not achieved else None
    members = [Member(user_id=acc.owner_user_id, name=acc.display_name, saved=saved.quantize(Q), share=Decimal(1))] \
        if acc is not None and saved > 0 else []
    return GoalOut(id=g.id, name=g.name, target=target, currency=g.currency, deadline=g.deadline, archived=g.archived,
                   saved=saved.quantize(Q), remaining=remaining.quantize(Q),
                   progress=min(saved / target, Decimal(1)).quantize(Decimal("0.001")) if saved > 0 else Decimal(0),
                   achieved=achieved, days_left=days_left, per_month_needed=needed, pace_per_month=pace,
                   projected_date=projected, on_track=on_track if not achieved else True, members=members,
                   account_id=g.account_id, account_name=acc.name if acc else None, account_status=status_)


def build(db: Session, g, me: UUID, detail: bool = False) -> GoalOut:
    today = date.today()
    if g.account_id:
        return _build_account(db, g, today)
    rows = db.execute(text("""SELECT c.id, c.user_id, u.display_name AS name, c.amount, c.made_on, c.note,
                                     (c.transaction_id IS NOT NULL) AS from_statement
                                FROM goal_contributions c JOIN users u ON u.id = c.user_id
                               WHERE c.goal_id = :g ORDER BY c.made_on DESC, c.created_at DESC"""), {"g": g.id}).all()
    saved = sum((Decimal(r.amount) for r in rows), Decimal(0))
    target = Decimal(g.target)
    remaining = max(target - saved, Decimal(0))
    achieved = saved >= target
    if achieved and g.achieved_at is None:
        db.execute(text("UPDATE goals SET achieved_at = now() WHERE id = :g"), {"g": g.id})
    by: dict = {}
    for r in rows:
        by.setdefault(r.user_id, [r.name, Decimal(0)])[1] += Decimal(r.amount)
    members = [Member(user_id=u, name=n, saved=v.quantize(Q),
                      share=(v / saved).quantize(Decimal("0.001")) if saved > 0 else Decimal(0))
               for u, (n, v) in sorted(by.items(), key=lambda kv: -kv[1][1])]
    start = max(g.created_at.date(), today - timedelta(days=PACE_DAYS))
    if rows and min(r.made_on for r in rows) < start:
        start = max(min(r.made_on for r in rows), today - timedelta(days=PACE_DAYS))
    window = max((today - start).days, 30)   # at least 30 days: one deposit today mustn't look like a daily habit
    recent_sum = sum((Decimal(r.amount) for r in rows if r.made_on >= start), Decimal(0))
    per_day = recent_sum / window
    pace = (per_day * Decimal("30.44")).quantize(Q)
    projected = None
    if not achieved and per_day > 0:
        projected = today + timedelta(days=int((remaining / per_day).to_integral_value(ROUND_HALF_UP)))
    days_left = (g.deadline - today).days if g.deadline else None
    needed = None
    if g.deadline and not achieved:
        months = max(Decimal(max(days_left, 0)) / Decimal("30.44"), Decimal(1))
        needed = (remaining / months).quantize(Q)
    on_track = None
    if g.deadline and not achieved:
        on_track = projected is not None and projected <= g.deadline
    out = GoalOut(id=g.id, name=g.name, target=target, currency=g.currency, deadline=g.deadline, archived=g.archived,
                  saved=saved.quantize(Q), remaining=remaining.quantize(Q),
                  progress=min(saved / target, Decimal(1)).quantize(Decimal("0.001")) if saved > 0 else Decimal(0),
                  achieved=achieved, days_left=days_left, per_month_needed=needed, pace_per_month=pace,
                  projected_date=projected, on_track=on_track if not achieved else True, members=members)
    if detail:
        out.recent = [Contribution(id=r.id, user_id=r.user_id, name=r.name, amount=r.amount, made_on=r.made_on,
                                   note=r.note, from_statement=r.from_statement, mine=r.user_id == me) for r in rows[:30]]
        out.my_rules = [Rule(id=r.id, label=r.label) for r in db.execute(
            text("SELECT id, label FROM goal_rules WHERE goal_id = :g AND user_id = :u"), {"g": g.id, "u": me})]
    return out


def household_goals(db: Session, hid: UUID, me: UUID, include_archived: bool = False) -> list[GoalOut]:
    rows = db.execute(text("SELECT * FROM goals WHERE household_id = :h AND (:all OR NOT archived) ORDER BY created_at"),
                      {"h": hid, "all": include_archived}).all()
    return [build(db, g, me) for g in rows]


def apply_rules(db: Session, user_id: UUID) -> int:
    """After an import: the user's own outgoing statement lines matching their goal rules become contributions."""
    return db.execute(text("""
        INSERT INTO goal_contributions (goal_id, user_id, amount, made_on, transaction_id, note)
        SELECT DISTINCT ON (t.id) r.goal_id, :u, -t.amount, t.posted_on, t.id, 'From your statement'
          FROM goal_rules r
          JOIN transactions t ON t.merchant_key = r.merchant_key AND t.amount < 0
          JOIN accounts a ON a.id = t.account_id AND a.owner_user_id = :u
          JOIN goals g ON g.id = r.goal_id AND NOT g.archived AND t.posted_on >= g.created_at::date - 31
         WHERE r.user_id = :u
           AND NOT EXISTS (SELECT 1 FROM goal_contributions c WHERE c.transaction_id = t.id)
         ORDER BY t.id, r.goal_id
        ON CONFLICT (transaction_id) DO NOTHING"""), {"u": user_id}).rowcount


@router.get("/households/{hid}/goals", response_model=list[GoalOut])
def list_goals(hid: UUID, user: User, db: DB, include_archived: bool = False):
    my_role(db, hid)
    return household_goals(db, hid, user.id, include_archived)


@router.post("/households/{hid}/goals", response_model=GoalOut, status_code=201)
def create_goal(hid: UUID, body: GoalIn, user: User, db: DB):
    my_role(db, hid)
    if body.deadline and body.deadline <= date.today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick a deadline in the future")
    if body.account_id:
        _check_account(db, user, hid, body.account_id, body.currency)
    gid = db.execute(text("""INSERT INTO goals (household_id, created_by, name, target, currency, deadline, account_id)
                             VALUES (:h, :u, :n, :t, :c, :d, :a) RETURNING id"""),
                     {"h": hid, "u": user.id, "n": body.name.strip(), "t": body.target, "c": body.currency,
                      "d": body.deadline, "a": body.account_id}).scalar_one()
    audit.log(db, user.id, "goal.create", "goal", f"New goal {body.name.strip()}: {body.target} {body.currency}",
              entity_id=gid, household_id=hid)
    return build(db, _goal(db, gid), user.id, True)


def _check_account(db: DB, user, hid: UUID, aid: UUID, currency: str) -> None:
    """Only your own account, already shared into this household, in the goal's currency."""
    a = db.execute(text("""SELECT a.owner_user_id, a.currency FROM accounts a
                            JOIN household_accounts ha ON ha.account_id = a.id AND ha.household_id = :h WHERE a.id = :a"""),
                   {"a": aid, "h": hid}).first()
    if a is None:
        raise not_found("Account not found in this household")
    if a.owner_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the account owner can link it to a goal")
    if a.currency != currency:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"This goal is in {currency}")


@router.put("/goals/{gid}/account", response_model=GoalOut)
def set_goal_account(gid: UUID, body: GoalAccountIn, user: User, db: DB):
    """Follow a savings account's balance (G-6), or go back to counting contributions (account_id null)."""
    g = _goal(db, gid)
    _require_manager(db, g, user)
    if body.account_id:
        _check_account(db, user, g.household_id, body.account_id, g.currency)
    db.execute(text("UPDATE goals SET account_id = :a WHERE id = :g"), {"a": body.account_id, "g": gid})
    audit.log(db, user.id, "goal.update", "goal", f"{g.name} now {'follows an account balance' if body.account_id else 'counts contributions'}",
              entity_id=gid, household_id=g.household_id)
    return build(db, _goal(db, gid), user.id, True)


@router.get("/goals/{gid}", response_model=GoalOut)
def get_goal(gid: UUID, user: User, db: DB):
    return build(db, _goal(db, gid), user.id, True)


@router.patch("/goals/{gid}", response_model=GoalOut)
def update_goal(gid: UUID, body: GoalUpdate, user: User, db: DB):
    g = _goal(db, gid)
    _require_manager(db, g, user)
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is None:
        fields.pop("name")
    if "target" in fields and fields["target"] is None:
        fields.pop("target")
    if "archived" in fields and fields["archived"] is None:
        fields.pop("archived")
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)  # keys from the schema only
        db.execute(text(f"UPDATE goals SET {sets} WHERE id = :id"), {**fields, "id": gid})
        audit.log(db, user.id, "goal.update", "goal", f"Updated goal {g.name}", entity_id=gid, household_id=g.household_id,
                  before={k: getattr(g, k) for k in fields}, after=fields)
    return build(db, _goal(db, gid), user.id, True)


@router.delete("/goals/{gid}", status_code=204)
def delete_goal(gid: UUID, user: User, db: DB):
    g = _goal(db, gid)
    _require_manager(db, g, user)
    if db.execute(text("SELECT 1 FROM goal_contributions WHERE goal_id = :g AND user_id <> :u LIMIT 1"),
                  {"g": gid, "u": user.id}).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Other members have saved toward this goal. Archive it instead so their record is kept.")
    db.execute(text("DELETE FROM goals WHERE id = :g"), {"g": gid})
    audit.log(db, user.id, "goal.delete", "goal", f"Deleted goal {g.name}", entity_id=gid, household_id=g.household_id)


@router.post("/goals/{gid}/contributions", response_model=GoalOut, status_code=201)
def contribute(gid: UUID, body: ContributionIn, user: User, db: DB):
    """Record money you put aside (or took back out, negative). Always as yourself."""
    g = _goal(db, gid)
    if g.account_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "This goal follows an account balance. Pay into the account instead.")
    if body.amount == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter an amount")
    db.execute(text("""INSERT INTO goal_contributions (goal_id, user_id, amount, made_on, note)
                       VALUES (:g, :u, :a, :d, :n)"""),
               {"g": gid, "u": user.id, "a": body.amount, "d": body.made_on or date.today(), "n": body.note})
    audit.log(db, user.id, "goal.contribute", "goal",
              f"{'Added' if body.amount > 0 else 'Took'} {abs(body.amount)} {g.currency} {'to' if body.amount > 0 else 'from'} {g.name}",
              entity_id=gid, household_id=g.household_id, reason=body.note)
    return build(db, _goal(db, gid), user.id, True)


@router.post("/goals/{gid}/from-transaction", response_model=GoalOut, status_code=201)
def from_transaction(gid: UUID, body: FromTxIn, user: User, db: DB):
    """Count one of your own outgoing statement lines toward the goal (E11-03 'from the ledger')."""
    g = _goal(db, gid)
    t = db.execute(text("""SELECT t.id, t.amount, t.posted_on, t.merchant_key, t.merchant, t.description, t.currency,
                                  a.owner_user_id FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = :t"""),
                   {"t": body.transaction_id}).first()
    if t is None:
        raise not_found("Transaction not found")
    if t.owner_user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the account owner can count this toward a goal")
    if t.amount >= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick money going out (a transfer into savings)")
    if t.currency != g.currency:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"This goal is in {g.currency}")
    if db.execute(text("SELECT 1 FROM goal_contributions WHERE transaction_id = :t"), {"t": t.id}).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Already counted toward a goal")
    db.execute(text("""INSERT INTO goal_contributions (goal_id, user_id, amount, made_on, transaction_id, note)
                       VALUES (:g, :u, :a, :d, :t, 'From your statement')"""),
               {"g": gid, "u": user.id, "a": -t.amount, "d": t.posted_on, "t": t.id})
    if body.always and t.merchant_key:
        db.execute(text("""INSERT INTO goal_rules (goal_id, user_id, merchant_key, label) VALUES (:g, :u, :k, :l)
                           ON CONFLICT DO NOTHING"""),
                   {"g": gid, "u": user.id, "k": t.merchant_key, "l": (t.merchant or t.description)[:100]})
        apply_rules(db, user.id)
    audit.log(db, user.id, "goal.contribute", "goal", f"Counted {abs(t.amount)} {g.currency} from a statement toward {g.name}",
              entity_id=gid, household_id=g.household_id)
    return build(db, _goal(db, gid), user.id, True)


@router.delete("/goal-contributions/{cid}", status_code=204)
def remove_contribution(cid: UUID, user: User, db: DB):
    c = db.execute(text("""SELECT c.*, g.name, g.household_id, g.currency FROM goal_contributions c JOIN goals g ON g.id = c.goal_id
                            WHERE c.id = :c"""), {"c": cid}).first()
    if c is None:
        raise not_found("Contribution not found")
    if c.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only remove your own contributions")
    db.execute(text("DELETE FROM goal_contributions WHERE id = :c"), {"c": cid})
    audit.log(db, user.id, "goal.uncontribute", "goal", f"Removed a {c.amount} {c.currency} contribution from {c.name}",
              entity_id=c.goal_id, household_id=c.household_id)


@router.delete("/goal-rules/{rid}", status_code=204)
def remove_rule(rid: UUID, user: User, db: DB):
    if not db.execute(text("DELETE FROM goal_rules WHERE id = :r"), {"r": rid}).rowcount:
        raise not_found("Rule not found")
