import { useCallback, useEffect, useState } from "react";
import { api, Household, money, Recurring, relDay } from "../api";

const CADENCE = { weekly: "Every week", fortnightly: "Every 2 weeks", monthly: "Every month", yearly: "Every year" };

export function RecurringRow({ r, onDismiss }: { r: Recurring; onDismiss?: () => void }) {
  const n = Number(r.expected_amount);
  const changed = r.previous_amount !== null && r.previous_amount !== r.expected_amount;
  let when: string;
  if (r.status === "missed") when = `Expected ${relDay(r.next_date)}, didn't arrive`;
  else if (r.awaiting_statement) when = `Due ${relDay(r.next_date)}, waiting for your next statement`;
  else when = `${r.direction === "in" ? "Expected" : "Due"} ${relDay(r.next_date)}`;
  return (
    <li>
      <div className="main">
        <div className="title">{r.name}</div>
        <div className="meta">
          {r.status === "missed" ? <span className="flag">{when}</span> : when}
          {changed && <span className="flag">, was {money(r.previous_amount, r.currency)}</span>}
        </div>
      </div>
      <div className={"amt" + (n > 0 ? " in" : "")}>{money(n, r.currency, true)}</div>
      {onDismiss && r.editable && (
        <button className="iconbtn" aria-label={`${r.name} isn't a regular payment`} title="Not a regular payment" onClick={onDismiss}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      )}
    </li>
  );
}

export default function RecurringScreen({ household }: { household: Household }) {
  const [items, setItems] = useState<Recurring[] | null>(null);
  const load = useCallback(() => api.get<Recurring[]>(`/api/households/${household.id}/recurring`).then(setItems), [household.id]);
  useEffect(() => { load(); }, [load]);
  const dismiss = async (id: string) => { await api.patch(`/api/recurring/${id}`, { status: "dismissed" }); load(); };

  if (!items) return <div className="muted">Loading…</div>;
  const groups = (["weekly", "fortnightly", "monthly", "yearly"] as const)
    .map((c) => [c, items.filter((i) => i.cadence === c)] as const).filter(([, l]) => l.length);
  const monthlyOut = items.filter((i) => i.direction === "out" && i.status !== "dismissed")
    .reduce((sum, i) => sum + Math.abs(Number(i.expected_amount)) * ({ weekly: 52 / 12, fortnightly: 26 / 12, monthly: 1, yearly: 1 / 12 }[i.cadence]), 0);
  const cur = items[0]?.currency ?? "GBP";

  return (
    <>
      <h1>Regular payments</h1>
      {items.length === 0 ? (
        <div className="empty"><strong>None found yet</strong>Bills, subscriptions and pay show up here once a statement has a few months of history.</div>
      ) : (
        <>
          <p className="note" style={{ marginTop: -8 }}>Found automatically from your statements. Regular outgoings come to about <strong style={{ color: "var(--text)" }}>{money(monthlyOut, cur)}</strong> a month.</p>
          {groups.map(([c, list]) => (
            <section key={c}>
              <h2>{CADENCE[c]}</h2>
              <ul className="list">{list.map((r) => <RecurringRow key={r.id} r={r} onDismiss={() => dismiss(r.id)} />)}</ul>
            </section>
          ))}
        </>
      )}
    </>
  );
}
