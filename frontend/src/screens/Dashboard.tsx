import { useEffect, useState } from "react";
import { api, Household, HouseholdAccount, money, Summary } from "../api";
import type { Ctx } from "../App";
import TxList from "./TxList";

export default function Dashboard({ ctx, household }: { ctx: Ctx; household: Household }) {
  const [s, setS] = useState<Summary | null>(null);
  const [accounts, setAccounts] = useState<HouseholdAccount[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api.get<Summary>(`/api/households/${household.id}/summary`),
      api.get<HouseholdAccount[]>(`/api/households/${household.id}/accounts`),
    ]).then(([sum, accs]) => { setS(sum); setAccounts(accs); }).catch((e) => setError(e.message));
  }, [household.id]);

  if (error) return <div className="error">{error}</div>;
  if (!s) return <div className="muted">Loading…</div>;

  const month = new Date(s.period_start + "T00:00:00").toLocaleDateString(undefined, { month: "long" });
  const names = Object.fromEntries(accounts.map((a) => [a.id, a.name]));
  const totals = s.included.length ? s.included : [{ currency: ctx.me.home_currency, spent: "0", received: "0", spent_today: "0" }];

  return (
    <>
      <h1>{household.name}</h1>
      {totals.map((t) => (
        <div className="grid" key={t.currency} style={{ marginBottom: 12 }}>
          <div className="card stat"><div className="label">Spent today</div><div className="value">{money(t.spent_today, t.currency)}</div></div>
          <div className="card stat"><div className="label">Spent in {month}</div><div className="value">{money(t.spent, t.currency)}</div></div>
          <div className="card stat"><div className="label">Money in, {month}</div><div className="value">{money(t.received, t.currency)}</div></div>
        </div>
      ))}
      <p className="muted small">
        Household totals include {s.accounts_included} shared account{s.accounts_included === 1 ? "" : "s"}.
        Transfers between your own accounts are currently counted as spending.
      </p>
      {s.excluded.length > 0 && (
        <div className="notice">
          <strong>Not in totals</strong> ({s.accounts_excluded} account{s.accounts_excluded === 1 ? "" : "s"} set to “separate”):{" "}
          {s.excluded.map((t) => `${money(t.spent, t.currency)} spent`).join(", ")}
        </div>
      )}
      {accounts.length === 0 && (
        <div className="notice" style={{ marginTop: 12 }}>
          No accounts are shared with this household yet. Go to <a href="#/accounts">Accounts</a> to add one and import a statement.
        </div>
      )}
      <h2>Recent</h2>
      <TxList txs={s.recent} accountNames={names} />
    </>
  );
}
