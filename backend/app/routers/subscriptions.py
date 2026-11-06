"""Subscription registry (E07): separate from the ledger, linked to statements by merchant.
Renewal dates follow the detected regular payment when linked; trials and cancellations are checked
against real charges on the household's shared accounts."""
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text

from app import audit
from app.categorise.rules import merchant_key
from app.deps import DB, User, not_found
from app.recurring import add_period
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["subscriptions"])

Cycle = Literal["weekly", "fortnightly", "monthly", "quarterly", "yearly"]
PER_MONTH = {"weekly": Decimal(52) / 12, "fortnightly": Decimal(26) / 12, "monthly": Decimal(1),
             "quarterly": Decimal(1) / 3, "yearly": Decimal(1) / 12}
SUGGEST_CATEGORIES = ("Subscriptions", "Entertainment")
CANCEL_GRACE = timedelta(days=3)


class SubscriptionIn(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    plan: str | None = Field(default=None, max_length=80)
    amount: Decimal = Field(ge=0, lt=Decimal("1e9"), decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    cycle: Cycle
    next_renewal: date | None = None
    auto_renew: bool = True
    trial_ends: date | None = None
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("currency", mode="before")
    @classmethod
    def up(cls, v):
        return v.upper() if isinstance(v, str) else v


class SubscriptionUpdate(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    plan: str | None = Field(default=None, max_length=80)
    amount: Decimal | None = Field(default=None, ge=0, lt=Decimal("1e9"), decimal_places=2)
    cycle: Cycle | None = None
    next_renewal: date | None = None
    auto_renew: bool | None = None
    trial_ends: date | None = None
    notes: str | None = Field(default=None, max_length=500)


class CancelIn(BaseModel):
    cancelled_on: date
    access_until: date | None = None

    @model_validator(mode="after")
    def order(self):
        if self.access_until and self.access_until < self.cancelled_on:
            raise ValueError("Access can't end before the cancellation date")
        return self


class FromRecurringIn(BaseModel):
    recurring_id: UUID


class Alert(BaseModel):
    kind: Literal["trial_ending", "trial_converted", "trial_over", "charged_after_cancel", "renewing", "ending"]
    text: str
    severity: Literal["info", "warning", "critical"]


class SubscriptionOut(BaseModel):
    id: UUID
    provider: str
    plan: str | None
    amount: Decimal
    currency: str
    cycle: str
    auto_renew: bool
    status: str
    trial_ends: date | None
    cancelled_on: date | None
    access_until: date | None
    next_renewal: date | None          # effective: follows the linked regular payment when there is one
    monthly_cost: Decimal
    linked: bool
    notes: str | None
    alerts: list[Alert]


class Suggestion(BaseModel):
    recurring_id: UUID
    name: str
    amount: Decimal
    currency: str
    cadence: str
    next_date: date


def _fmt_day(d: date, today: date) -> str:
    n = (d - today).days
    if n == 0:
        return "today"
    if n == 1:
        return "tomorrow"
    if 1 < n < 7:
        return f"in {n} days"
    return d.strftime("%-d %b")


def _first_charge_after(db: DB, hid: UUID, key: str | None, after: date):
    if not key:
        return None
    return db.execute(text("""
        SELECT t.posted_on, t.amount, t.currency FROM transactions t
          JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h
          JOIN accounts a ON a.id = t.account_id
          JOIN memberships ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
         WHERE t.merchant_key = :k AND t.amount < 0 AND t.posted_on > :d
         ORDER BY t.posted_on LIMIT 1"""), {"h": hid, "k": key, "d": after}).first()


def _money(v, cur: str) -> str:
    sym = {"GBP": "£", "EUR": "€", "USD": "$"}.get(cur, cur + " ")
    return f"{sym}{abs(Decimal(v)):,.2f}"


def build(db: DB, hid: UUID, r, today: date) -> SubscriptionOut:
    alerts: list[Alert] = []
    nxt: date | None = None
    if r.status == "trial":
        nxt = r.trial_ends
        left = (r.trial_ends - today).days
        if 0 <= left <= 7:
            alerts.append(Alert(kind="trial_ending", severity="warning",
                                text=f"Free trial ends {_fmt_day(r.trial_ends, today)}. Cancel before then if you don't want to pay."))
        elif left < 0:
            ch = _first_charge_after(db, hid, r.merchant_key, r.trial_ends - timedelta(days=1))
            if ch:
                alerts.append(Alert(kind="trial_converted", severity="warning",
                                    text=f"Trial became a paid plan on {ch.posted_on:%-d %b} ({_money(ch.amount, ch.currency)})."))
            else:
                alerts.append(Alert(kind="trial_over", severity="info", text="Trial ended and no charge has shown up."))
    elif r.status == "cancelled":
        ch = _first_charge_after(db, hid, r.merchant_key, r.cancelled_on + CANCEL_GRACE)
        if ch:
            alerts.append(Alert(kind="charged_after_cancel", severity="critical",
                                text=f"Charged {_money(ch.amount, ch.currency)} on {ch.posted_on:%-d %b}, after you cancelled."))
    else:
        if r.rec_next and r.rec_status != "dismissed":
            nxt = r.rec_next
        elif r.next_renewal:
            nxt = r.next_renewal
            if r.auto_renew:
                while nxt < today:  # roll forward unconfirmed dates by the billing cycle
                    nxt = (nxt + timedelta(days=91)) if r.cycle == "quarterly" else add_period(
                        nxt, "monthly" if r.cycle == "monthly" else r.cycle, nxt.day)
        if nxt and r.auto_renew and 0 <= (nxt - today).days <= 7:
            alerts.append(Alert(kind="renewing", severity="info",
                                text=f"Renews {_fmt_day(nxt, today)} for {_money(r.amount, r.currency)}."))
        if nxt and not r.auto_renew and 0 <= (nxt - today).days <= 14:
            alerts.append(Alert(kind="ending", severity="info", text=f"Ends {_fmt_day(nxt, today)} unless you renew."))
    monthly = Decimal(0) if r.status != "active" else (Decimal(r.amount) * PER_MONTH[r.cycle]).quantize(Decimal("0.01"))
    return SubscriptionOut(id=r.id, provider=r.provider, plan=r.plan, amount=r.amount, currency=r.currency, cycle=r.cycle,
                           auto_renew=r.auto_renew, status=r.status, trial_ends=r.trial_ends, cancelled_on=r.cancelled_on,
                           access_until=r.access_until, next_renewal=nxt, monthly_cost=monthly,
                           linked=r.recurring_id is not None, notes=r.notes, alerts=alerts)


_SELECT = """
SELECT s.*, rs.next_date AS rec_next, rs.status AS rec_status FROM subscriptions s
  LEFT JOIN recurring_series rs ON rs.id = s.recurring_id
"""


def household_subscriptions(db: DB, hid: UUID) -> list[SubscriptionOut]:
    today = date.today()
    rows = db.execute(text(_SELECT + " WHERE s.household_id = :h ORDER BY s.status = 'cancelled', lower(s.provider)"),
                      {"h": hid}).all()
    return [build(db, hid, r, today) for r in rows]


def _get(db: DB, sid: UUID):
    r = db.execute(text(_SELECT + " WHERE s.id = :id"), {"id": sid}).first()
    if r is None:
        raise not_found("Subscription not found")
    return r


@router.get("/households/{hid}/subscriptions", response_model=list[SubscriptionOut])
def list_subscriptions(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    return household_subscriptions(db, hid)


@router.post("/households/{hid}/subscriptions", response_model=SubscriptionOut, status_code=201)
def create_subscription(hid: UUID, body: SubscriptionIn, user: User, db: DB):
    my_role(db, hid)
    sid = db.execute(text("""
        INSERT INTO subscriptions (household_id, created_by, provider, plan, amount, currency, cycle, next_renewal,
                                   auto_renew, status, trial_ends, merchant_key, notes)
        VALUES (:h, :u, :p, :plan, :a, :c, :cy, :n, :ar, :st, :te, :k, :notes) RETURNING id"""),
        {"h": hid, "u": user.id, "p": body.provider.strip(), "plan": body.plan, "a": body.amount, "c": body.currency,
         "cy": body.cycle, "n": body.next_renewal, "ar": body.auto_renew,
         "st": "trial" if body.trial_ends else "active", "te": body.trial_ends,
         "k": merchant_key(body.provider), "notes": body.notes}).scalar_one()
    audit.log(db, user.id, "subscription.create", "subscription", f"Added subscription {body.provider.strip()}",
              entity_id=sid, household_id=hid)
    return build(db, hid, _get(db, sid), date.today())


@router.post("/households/{hid}/subscriptions/from-recurring", response_model=SubscriptionOut, status_code=201)
def create_from_recurring(hid: UUID, body: FromRecurringIn, user: User, db: DB):
    """Track a detected regular payment as a subscription (E07-05)."""
    my_role(db, hid)
    rs = db.execute(text("""
        SELECT rs.* FROM recurring_series rs
          JOIN household_accounts ha ON ha.account_id = rs.account_id AND ha.household_id = :h
          JOIN accounts a ON a.id = rs.account_id
          JOIN memberships ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
         WHERE rs.id = :r AND rs.direction = 'out'"""), {"h": hid, "r": body.recurring_id}).first()
    if rs is None:
        raise not_found("Regular payment not found")
    if db.execute(text("SELECT 1 FROM subscriptions WHERE household_id = :h AND recurring_id = :r"),
                  {"h": hid, "r": rs.id}).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Already tracked as a subscription")
    sid = db.execute(text("""
        INSERT INTO subscriptions (household_id, created_by, provider, amount, currency, cycle, next_renewal,
                                   merchant_key, recurring_id)
        VALUES (:h, :u, :p, :a, :c, :cy, :n, :k, :r) RETURNING id"""),
        {"h": hid, "u": user.id, "p": rs.name, "a": abs(rs.expected_amount), "c": rs.currency, "cy": rs.cadence,
         "n": rs.next_date, "k": rs.merchant_key, "r": rs.id}).scalar_one()
    audit.log(db, user.id, "subscription.create", "subscription", f"Tracking {rs.name} as a subscription",
              entity_id=sid, household_id=hid)
    return build(db, hid, _get(db, sid), date.today())


@router.get("/households/{hid}/subscriptions/suggestions", response_model=list[Suggestion])
def suggestions(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    rows = db.execute(text("""
        SELECT rs.id AS recurring_id, rs.name, abs(rs.expected_amount) AS amount, rs.currency, rs.cadence, rs.next_date
          FROM recurring_series rs
          JOIN household_accounts ha ON ha.account_id = rs.account_id AND ha.household_id = :h
          JOIN accounts a ON a.id = rs.account_id
          JOIN memberships ow ON ow.household_id = :h AND ow.user_id = a.owner_user_id
         WHERE rs.direction = 'out' AND rs.status <> 'dismissed' AND rs.category = ANY(:cats)
           AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.household_id = :h
                            AND (s.recurring_id = rs.id OR s.merchant_key = rs.merchant_key))
         ORDER BY rs.name"""), {"h": hid, "cats": list(SUGGEST_CATEGORIES)})
    return [Suggestion(**r._mapping) for r in rows]


@router.patch("/subscriptions/{sid}", response_model=SubscriptionOut)
def update_subscription(sid: UUID, body: SubscriptionUpdate, user: User, db: DB):
    r = _get(db, sid)
    fields = body.model_dump(exclude_unset=True)
    if "provider" in fields:
        fields["merchant_key"] = merchant_key(fields["provider"]) if not r.recurring_id else r.merchant_key
    if "trial_ends" in fields:
        if fields["trial_ends"] is None and r.status == "trial":
            fields["status"] = "active"
        elif fields["trial_ends"] is not None and r.status == "active":
            fields["status"] = "trial"
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)  # keys from the schema (+2 internal), never the client
        db.execute(text(f"UPDATE subscriptions SET {sets}, updated_at = now() WHERE id = :id"), {**fields, "id": sid})
        audit.log(db, user.id, "subscription.update", "subscription", f"Updated {r.provider}", entity_id=sid,
                  household_id=r.household_id, before={k: getattr(r, k, None) for k in fields}, after=fields)
    return build(db, r.household_id, _get(db, sid), date.today())


@router.post("/subscriptions/{sid}/cancel", response_model=SubscriptionOut)
def cancel_subscription(sid: UUID, body: CancelIn, user: User, db: DB):
    """E07-02: record a cancellation; later charges from this provider get flagged."""
    r = _get(db, sid)
    db.execute(text("""UPDATE subscriptions SET status = 'cancelled', cancelled_on = :c, access_until = :a,
                              auto_renew = false, updated_at = now() WHERE id = :id"""),
               {"c": body.cancelled_on, "a": body.access_until, "id": sid})
    audit.log(db, user.id, "subscription.cancel", "subscription", f"Marked {r.provider} cancelled on {body.cancelled_on}",
              entity_id=sid, household_id=r.household_id)
    return build(db, r.household_id, _get(db, sid), date.today())


@router.post("/subscriptions/{sid}/reactivate", response_model=SubscriptionOut)
def reactivate_subscription(sid: UUID, user: User, db: DB):
    r = _get(db, sid)
    db.execute(text("""UPDATE subscriptions SET status = 'active', cancelled_on = NULL, access_until = NULL,
                              auto_renew = true, trial_ends = NULL, updated_at = now() WHERE id = :id"""), {"id": sid})
    audit.log(db, user.id, "subscription.reactivate", "subscription", f"Resubscribed to {r.provider}",
              entity_id=sid, household_id=r.household_id)
    return build(db, r.household_id, _get(db, sid), date.today())


@router.delete("/subscriptions/{sid}", status_code=204)
def delete_subscription(sid: UUID, user: User, db: DB):
    r = _get(db, sid)
    db.execute(text("DELETE FROM subscriptions WHERE id = :id"), {"id": sid})
    audit.log(db, user.id, "subscription.delete", "subscription", f"Removed {r.provider} from the list",
              entity_id=sid, household_id=r.household_id)
