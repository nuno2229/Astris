"""Works out which alerts each user should get and sends each one once (E22-03, E22-04).
Runs as each user (RLS): a user only ever hears about households they belong to."""
import logging
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.notify.channels import ChannelError, send
from app.routers.recurring import household_recurring
from app.routers.subscriptions import household_subscriptions

log = logging.getLogger("astris.notify")


def _money(v, cur):
    sym = {"GBP": "£", "EUR": "€", "USD": "$"}.get(cur, cur + " ")
    return f"{sym}{abs(float(v)):,.2f}"


def _when(d: date, today: date) -> str:
    n = (d - today).days
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"


def events_for(db: Session, user: UUID, today: date | None = None) -> list[dict]:
    """[{key, minimal, detailed}] for this user, across their households."""
    today = today or date.today()
    p = db.execute(text("SELECT * FROM notification_prefs WHERE user_id = :u"), {"u": user}).first()
    renew_days = p.renew_days if p else 3
    want = {k: (getattr(p, k) if p else True) for k in ("renewals", "trials", "cancelled_charges", "missed_payments", "budgets", "stock", "shopping")}
    want["daily_summary"] = bool(p.daily_summary) if p else False
    out = []
    for h in db.execute(text("""SELECT h.id, h.name FROM households h JOIN memberships m ON m.household_id = h.id
                                 WHERE m.user_id = :u"""), {"u": user}):
        for s in household_subscriptions(db, h.id):
            if want["renewals"] and s.status == "active" and s.auto_renew and s.next_renewal \
                    and 0 <= (s.next_renewal - today).days <= renew_days:
                out.append({"key": f"renew:{s.id}:{s.next_renewal}",
                            "minimal": f"A subscription renews {_when(s.next_renewal, today)}.",
                            "detailed": f"{s.provider} renews {_when(s.next_renewal, today)} for {_money(s.amount, s.currency)} ({h.name})."})
            for a in s.alerts:
                if a.kind == "trial_ending" and want["trials"]:
                    out.append({"key": f"trial_ending:{s.id}:{s.trial_ends}",
                                "minimal": "A free trial is ending soon.", "detailed": f"{s.provider}: {a.text}"})
                elif a.kind == "trial_converted" and want["trials"]:
                    out.append({"key": f"trial_converted:{s.id}:{s.trial_ends}",
                                "minimal": "A free trial has turned into a paid plan.", "detailed": f"{s.provider}: {a.text}"})
                elif a.kind == "charged_after_cancel" and want["cancelled_charges"]:
                    out.append({"key": f"cac:{s.id}:{s.cancelled_on}",
                                "minimal": "A cancelled subscription charged you again.", "detailed": f"{s.provider}: {a.text}"})
        if want["budgets"]:
            from app.routers.budgets import visible_budgets
            for b in visible_budgets(db, h.id, user):
                if b.level == "ok":
                    continue
                key = f"budget:{b.id}:{b.period_start}:{b.level}"   # once per budget, period and level
                minimal = {"warning": "A budget is nearly used up.", "at_risk": "Upcoming bills will take a budget over.",
                           "over": "A budget has gone over."}[b.level]
                out.append({"key": key, "minimal": minimal,
                            "detailed": f"{b.label} ({'you' if b.scope == 'personal' else h.name}): {b.message}. "
                                        f"{_money(b.spent, b.currency)} of {_money(b.amount, b.currency)} this "
                                        f"{ {'daily': 'day', 'weekly': 'week', 'monthly': 'month'}[b.period] }."})
        if want["stock"]:
            from app.routers.inventory import household_stock
            for pr in household_stock(db, h.id):
                for lot in pr.lots:
                    if lot.state == "soon":
                        out.append({"key": f"expiry:{lot.id}:{lot.expires_on}", "minimal": "Something in the pantry expires soon.",
                                    "detailed": f"{pr.name} ({h.name}) expires {_when(lot.expires_on, today)}."})
        if want["shopping"]:
            # items other people added to lists you can see, in the last day (one message per batch)
            rows = db.execute(text("""
                SELECT l.id AS list_id, l.name AS list_name, i.id, i.name, u.display_name AS who FROM shopping_items i
                  JOIN shopping_lists l ON l.id = i.list_id JOIN users u ON u.id = i.added_by
                 WHERE l.household_id = :h AND (l.scope = 'household' OR l.owner_user_id = :me)   -- also enforced by RLS
                   AND i.added_by <> :me AND NOT i.checked AND i.added_at > now() - interval '1 day'
                 ORDER BY i.added_at"""), {"h": h.id, "me": user}).all()
            by_list: dict = {}
            for r in rows:
                by_list.setdefault((r.list_id, r.list_name), []).append(r)
            for (lid, lname), items in by_list.items():
                who = ", ".join(dict.fromkeys(r.who for r in items))
                out.append({"key": f"shop:{lid}:{items[-1].id}", "minimal": "The shopping list has new items.",
                            "detailed": f"{lname} ({h.name}): {who} added " + ", ".join(r.name for r in items[:8]) + "."})
        if want["daily_summary"]:
            from datetime import datetime
            if datetime.now().hour >= 8:   # send in the morning, about yesterday
                y = today - timedelta(days=1)
                row = db.execute(text("""
                    SELECT t.currency, count(*) AS n, sum(-t.amount) AS spent FROM transactions t
                      JOIN household_accounts ha ON ha.account_id = t.account_id AND ha.household_id = :h
                       AND ha.attribution = 'include'
                     WHERE (t.amount < 0 OR t.refund_of IS NOT NULL) AND t.transfer_group IS NULL AND t.posted_on = :y
                     GROUP BY t.currency ORDER BY spent DESC LIMIT 1"""), {"h": h.id, "y": y}).first()
                out.append({"key": f"daily:{h.id}:{y}", "minimal": "Yesterday's spending summary is ready in Astris.",
                            "detailed": (f"{h.name} yesterday: {_money(row.spent, row.currency)} across {row.n} payment"
                                         f"{'' if row.n == 1 else 's'}." if row else f"{h.name}: no spending recorded yesterday.")})
        if want["missed_payments"]:
            for r in household_recurring(db, h.id):
                if r.status == "missed":
                    out.append({"key": f"missed:{r.id}:{r.next_date}",
                                "minimal": "A regular payment didn't arrive as expected.",
                                "detailed": f"{r.name} ({_money(r.expected_amount, r.currency)}) was expected {r.next_date:%-d %b} but didn't arrive ({h.name})."})
    if p:
        from app.notify import roundup
        out += roundup.events(db, user, p)
    return out


def notify_user(db: Session, user: UUID, today: date | None = None) -> int:
    """Send this user's new alerts to each enabled channel. Returns messages sent."""
    sent_keys = {r[0] for r in db.execute(text("SELECT event_key FROM notification_log WHERE user_id = :u"), {"u": user})}
    events = [e for e in events_for(db, user, today) if e["key"] not in sent_keys]
    if not events:
        return 0
    channels = db.execute(text("SELECT id, kind, config, include_details FROM notification_channels "
                               "WHERE user_id = :u AND enabled"), {"u": user}).all()
    n = 0
    delivered_any = False
    for ch in channels:
        lines = [e["detailed"] if ch.include_details else e["minimal"] for e in events]
        body = "\n".join(f"• {x}" for x in dict.fromkeys(lines))   # minimal lines can repeat: show once
        try:
            from app.crypto import decrypt_json
            send(ch.kind, decrypt_json(ch.config, str(ch.id)), "Astris", body + "\nOpen Astris for details.")
            db.execute(text("UPDATE notification_channels SET last_sent_at = now(), last_error = NULL WHERE id = :id"), {"id": ch.id})
            delivered_any = True
            n += 1
        except ChannelError as e:
            log.warning("notification to %s channel failed: %s", ch.kind, e)
            db.execute(text("UPDATE notification_channels SET last_error = :e WHERE id = :id"), {"e": str(e)[:200], "id": ch.id})
    if delivered_any:  # if every channel failed, retry next round
        for e in events:
            db.execute(text("INSERT INTO notification_log (user_id, event_key) VALUES (:u, :k) ON CONFLICT DO NOTHING"),
                       {"u": user, "k": e["key"]})
    return n
