import { useEffect, useState } from "react";
import { api, Household, HouseholdAccount, money, Summary } from "../api";
import type { Ctx } from "../App";
import Constellation from "./Constellation";
import TxList from "./TxList";

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
  const totals = [...s.included].sort((a, b) => Number(b.spent) - Number(a.spent));
  const main = totals[0] ?? { currency: ctx.me.home_currency, spent: "0", received: "0", spent_today: "0" };
  const others = totals.slice(1);

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
        <Constellation daily={s.daily.filter((d) => d.currency === main.currency)}
          start={s.period_start} end={s.period_end} currency={main.currency} />
      )}

      {others.map((t) => (
        <p key={t.currency} className="note">Also {money(t.spent, t.currency)} spent in {t.currency}.</p>
      ))}
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

      <h2>Recent</h2>
      <TxList txs={s.recent} accountNames={names} />
      {s.recent.length > 0 && <p style={{ marginTop: 12 }}><a href="#/activity">See all activity</a></p>}
    </>
  );
}
