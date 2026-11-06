import { useEffect, useState } from "react";
import { api, Household, HouseholdAccount, money, Summary } from "../api";
import type { Ctx } from "../App";
import Constellation from "./Constellation";
import TxList from "./TxList";
import { RecurringRow } from "./Recurring";
import { Meter } from "./Goals";

type SummaryWithDaily = Summary & { daily: { currency: string; day: string; spent: string }[] };

export default function Dashboard({ ctx, household }: { ctx: Ctx; household: Household }) {
  const [s, setS] = useState<SummaryWithDaily | null>(null);
  const [accounts, setAccounts] = useState<HouseholdAccount[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api.get<SummaryWithDaily>(`/api/households/${household.id}/summary`),
      api.get<HouseholdAccount[]>(`/api/households/${household.id}/accounts`),
    ]).then(([sum, accs]) => { setS(sum); setAccounts(accs); }).catch((e) => setError(e.message));
  }, [household.id]);

  if (error) return <div className="error">{error}</div>;
  if (!s) return <div className="muted" style={{ padding: "24px 4px" }}>Loading…</div>;

  const month = new Date(s.period_start + "T00:00:00").toLocaleDateString(undefined, { month: "long" });
  const names = Object.fromEntries(accounts.map((a) => [a.id, a.name]));
  // Main currency = the one with the most spending; others listed below (no FX conversion until E08)
  // Home currency (E08-07): converted totals when exchange rates cover the currencies; never guessed.
  const conv = s.converted;
  const totals = [...s.included].sort((a, b) => Number(b.spent) - Number(a.spent));
  const useConv = !!conv && s.included.some((t) => !conv.missing.includes(t.currency));
  const main = useConv ? { currency: conv!.currency, spent: conv!.spent, received: conv!.received, spent_today: conv!.spent_today }
    : totals[0] ?? { currency: ctx.me.home_currency, spent: "0", received: "0", spent_today: "0" };
  const others = useConv ? totals.filter((t) => conv!.missing.includes(t.currency)) : totals.slice(1);
  const daily = useConv ? conv!.daily : s.daily.filter((d) => d.currency === main.currency);
  const converting = useConv && s.included.some((t) => t.currency !== conv!.currency && !conv!.missing.includes(t.currency));

  return (
    <>
      <section className="hero" aria-labelledby="hero-label">
        <div className="label" id="hero-label">Spent in {month}</div>
        <div className="figure">{money(main.spent, main.currency)}</div>
        <div className="facts">
          <span><strong>{money(main.spent_today, main.currency)}</strong> today</span>
          <span className="in"><strong>{money(main.received, main.currency, true)}</strong> came in</span>
        </div>
      </section>

      {accounts.length > 0 && (
        <Constellation daily={daily}
          start={s.period_start} end={s.period_end} currency={main.currency} />
      )}

      {converting && <p className="note">Other currencies converted to {main.currency} at your household's exchange rates.</p>}
      {others.map((t) => (
        <p key={t.currency} className="note">Also {money(t.spent, t.currency)} spent in {t.currency}
          {useConv && <>: no exchange rate set, <a href="#/settings">add one</a></>}.</p>
      ))}
      {s.missed.map((m) => (
        <a key={m.id} className="callout review-link" href="#/bills/regular">
          <span><strong>{m.name}</strong> didn't arrive as expected ({money(m.expected_amount, m.currency, true)})</span>
          <span aria-hidden="true">›</span>
        </a>
      ))}
      {s.subscription_alerts.map((a) => (
        <a key={a.subscription_id + a.kind} className={"callout review-link" + (a.severity === "critical" ? " alert-critical" : "")} href="#/bills">
          <span><strong>{a.provider}</strong>: {a.text}</span>
          <span aria-hidden="true">›</span>
        </a>
      ))}
      {s.needs_review > 0 && (
        <a className="callout review-link" href="#/activity/review">
          <span><strong>{s.needs_review}</strong> transaction{s.needs_review === 1 ? " needs" : "s need"} a category</span>
          <span aria-hidden="true">›</span>
        </a>
      )}
      {s.excluded.length > 0 && (
        <div className="callout" style={{ marginTop: 14 }}>
          {s.excluded.map((t) => money(t.spent, t.currency)).join(" and ")} spent on {s.accounts_excluded} separate
          account{s.accounts_excluded === 1 ? "" : "s"}, not counted above.
        </div>
      )}
      {accounts.length === 0 ? (
        <div className="empty" style={{ marginTop: 16 }}>
          <strong>Share an account to get started</strong>
          Add a bank account in <a href="#/accounts">Accounts</a>, import a statement, and choose to include it in {household.name}.
        </div>
      ) : (
        <p className="note">
          Counts {s.accounts_included} shared account{s.accounts_included === 1 ? "" : "s"}. Transfers between your own accounts
          currently count as spending.
        </p>
      )}

      <h2>Savings goals</h2>
      {s.goals.length === 0 ? (
        <p className="note" style={{ marginTop: 0 }}>Saving for something together? <a href="#/goals">Set a goal</a>.</p>
      ) : (
        <a className="panel goal-card stack" href="#/goals" style={{ gap: 14 }}>
          {s.goals.slice(0, 3).map((g) => (
            <div key={g.id} className="stack" style={{ gap: 6 }}>
              <div className="acct-head"><span style={{ fontWeight: 600 }}>{g.name}</span>
                <span className="small muted">{money(g.saved, g.currency)} of {money(g.target, g.currency)}</span></div>
              <Meter value={Number(g.progress)} label={`${g.name} progress`} />
              {g.on_track === false && <span className="small flag">Behind schedule</span>}
            </div>
          ))}
        </a>
      )}

      {s.upcoming.length > 0 && (
        <>
          <h2>Coming up</h2>
          <ul className="list">{s.upcoming.map((r) => <RecurringRow key={r.id} r={r} />)}</ul>
          <p style={{ marginTop: 12 }}><a href="#/bills/regular">All regular payments</a></p>
        </>
      )}

      <h2>Recent</h2>
      <TxList txs={s.recent} accountNames={names} />
      {s.recent.length > 0 && <p style={{ marginTop: 12 }}><a href="#/activity">See all activity</a></p>}
    </>
  );
}
