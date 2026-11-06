import { FormEvent, useEffect, useRef, useState } from "react";
import { Account, api, Household, money } from "../api";

export interface Budget { id: string; scope: "household" | "personal"; kind: "category" | "merchant" | "account"; label: string;
  period: "daily" | "weekly" | "monthly"; amount: string; currency: string; warn_percent: number | null; warn_amount: string | null;
  spent: string; committed: string; left: string; used: string; level: "ok" | "warning" | "at_risk" | "over"; message: string;
  unconverted: string[]; mine: boolean; can_edit: boolean }

const PER = { daily: "today", weekly: "this week", monthly: "this month" } as const;
const LEVEL_TEXT = { ok: "On budget", warning: "Nearly used", at_risk: "Bills will take it over", over: "Over budget" } as const;

/** Spent (solid) + still-due bills (striped), against the limit. Status is always in words too, not colour alone. */
export function BudgetBar({ b }: { b: Pick<Budget, "spent" | "committed" | "amount" | "level" | "label"> }) {
  const lim = Number(b.amount), spent = Number(b.spent), due = Number(b.committed);
  const scale = Math.max(lim, spent + due);
  return (
    <div className={"bbar lvl-" + b.level} role="img" aria-label={`${b.label}: ${spent.toFixed(2)} spent of ${lim.toFixed(2)}${due > 0 ? `, ${due.toFixed(2)} still due` : ""}`}>
      <div className="bbar-spent" style={{ width: `${(Math.min(spent, scale) / scale) * 100}%` }} />
      {due > 0 && <div className="bbar-due" style={{ width: `${(due / scale) * 100}%` }} />}
      {spent + due > lim && <div className="bbar-limit" style={{ left: `${(lim / scale) * 100}%` }} />}
    </div>
  );
}

export default function Budgets({ household, currency }: { household: Household; currency: string }) {
  const [items, setItems] = useState<Budget[] | null>(null);
  const [adding, setAdding] = useState(false);
  const load = () => api.get<Budget[]>(`/api/households/${household.id}/budgets`).then(setItems);
  useEffect(() => { load(); }, [household.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const remove = async (b: Budget) => { if (confirm(`Remove the ${b.label} budget?`)) { await api.del(`/api/budgets/${b.id}`); load(); } };
  if (!items) return <div className="muted">Loading…</div>;
  const groups = [["household", `${household.name}`], ["personal", "Just me"]] as const;
  return (
    <>
      <h1>Budgets</h1>
      <p className="note" style={{ marginTop: -8 }}>Astris warns you as you get close. It can't block card payments.</p>
      {items.length === 0 && <div className="empty" style={{ marginTop: 12 }}><strong>No budgets yet</strong>Set a monthly limit for groceries, a cap for a shop, or a weekly limit on a card.</div>}
      {groups.map(([scope, title]) => {
        const list = items.filter((b) => b.scope === scope);
        if (!list.length) return null;
        return (
          <section key={scope}>
            <h2>{title}</h2>
            <ul className="list">
              {list.map((b) => (
                <li key={b.id} className="budget-row">
                  <div className="main stack" style={{ gap: 6 }}>
                    <div className="acct-head"><span className="title">{b.label}</span>
                      <span className="small muted">{money(b.spent, b.currency)} of {money(b.amount, b.currency)} {PER[b.period]}</span></div>
                    <BudgetBar b={b} />
                    <div className="small"><span className={b.level === "ok" ? "muted" : "flag"}>{LEVEL_TEXT[b.level]}</span>
                      <span className="muted">: {b.message}</span>
                      {b.unconverted.length > 0 && <span className="muted">. Not counted: {b.unconverted.join(", ")} (no exchange rate)</span>}</div>
                  </div>
                  {b.can_edit && <button className="iconbtn" aria-label={`Remove ${b.label} budget`} onClick={() => remove(b)}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg></button>}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
      <button className="btn block" style={{ marginTop: 16 }} onClick={() => setAdding(true)}>+ New budget</button>
      {adding && <NewBudget household={household} currency={currency} onClose={() => setAdding(false)} onDone={() => { setAdding(false); load(); }} />}
    </>
  );
}

function NewBudget({ household, currency, onClose, onDone }: { household: Household; currency: string; onClose: () => void; onDone: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [cats, setCats] = useState<string[]>([]);
  const [shops, setShops] = useState<{ merchant_key: string; name: string }[]>([]);
  const [cards, setCards] = useState<Account[]>([]);
  const [f, setF] = useState({ kind: "category" as Budget["kind"], scope: "household" as Budget["scope"], category: "Groceries",
    merchant_key: "", account_id: "", period: "monthly" as Budget["period"], amount: "", warn_percent: "80" });
  const [error, setError] = useState("");
  useEffect(() => {
    ref.current?.showModal();
    api.get<string[]>("/api/categories").then(setCats);
    api.get<{ merchant_key: string; name: string }[]>(`/api/households/${household.id}/merchants`).then((s) => { setShops(s); if (s[0]) setF((x) => ({ ...x, merchant_key: s[0].merchant_key })); });
    api.get<Account[]>("/api/accounts").then((a) => { const c = a.filter((x) => x.type !== "cash"); setCards(c); if (c[0]) setF((x) => ({ ...x, account_id: c[0].id })); });
  }, [household.id]);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setError("");
    const shop = shops.find((s) => s.merchant_key === f.merchant_key);
    try {
      await api.post(`/api/households/${household.id}/budgets`, {
        kind: f.kind, scope: f.kind === "account" ? "personal" : f.scope, period: f.period, amount: f.amount, currency,
        warn_percent: f.warn_percent ? Number(f.warn_percent) : null,
        category: f.kind === "category" ? f.category : null,
        merchant_key: f.kind === "merchant" ? f.merchant_key : null, merchant_name: f.kind === "merchant" ? shop?.name : null,
        account_id: f.kind === "account" ? f.account_id : null });
      onDone();
    } catch (err) { setError((err as Error).message); }
  };
  return (
    <dialog ref={ref} className="sheet" onClose={onClose} onClick={(e) => e.target === ref.current && ref.current?.close()} aria-labelledby="nb">
      <form className="sheet-body stack" onSubmit={submit}>
        <div className="grabber" aria-hidden="true" />
        <h2 id="nb" style={{ margin: 0 }}>New budget</h2>
        <div className="seg full" role="group" aria-label="Budget for">
          <button type="button" aria-pressed={f.kind === "category"} onClick={() => setF({ ...f, kind: "category" })}>Category</button>
          <button type="button" aria-pressed={f.kind === "merchant"} onClick={() => setF({ ...f, kind: "merchant" })}>Shop</button>
          <button type="button" aria-pressed={f.kind === "account"} onClick={() => setF({ ...f, kind: "account", period: "weekly" })}>Card</button>
        </div>
        {f.kind === "category" && <label>Category<select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{cats.map((c) => <option key={c}>{c}</option>)}</select></label>}
        {f.kind === "merchant" && (shops.length ? <label>Shop<select value={f.merchant_key} onChange={(e) => setF({ ...f, merchant_key: e.target.value })}>{shops.map((s) => <option key={s.merchant_key} value={s.merchant_key}>{s.name}</option>)}</select></label>
          : <p className="muted small">Shops appear here once you've imported some spending.</p>)}
        {f.kind === "account" && <label>Card or account<select value={f.account_id} onChange={(e) => setF({ ...f, account_id: e.target.value })}>{cards.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>}
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <label style={{ flex: 1 }}>Limit ({currency})<input value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} required inputMode="decimal" pattern="\d+(\.\d{1,2})?" /></label>
          <label style={{ flex: 1 }}>Per<select value={f.period} onChange={(e) => setF({ ...f, period: e.target.value as Budget["period"] })}>
            <option value="daily">Day</option><option value="weekly">Week</option><option value="monthly">Month</option></select></label>
        </div>
        <label>Warn me at<select value={f.warn_percent} onChange={(e) => setF({ ...f, warn_percent: e.target.value })}>
          <option value="">Only when it goes over</option><option value="50">50% used</option><option value="75">75% used</option>
          <option value="80">80% used</option><option value="90">90% used</option></select></label>
        {f.kind !== "account" && (
          <div className="seg full" role="group" aria-label="Who">
            <button type="button" aria-pressed={f.scope === "household"} onClick={() => setF({ ...f, scope: "household" })}>{household.name}</button>
            <button type="button" aria-pressed={f.scope === "personal"} onClick={() => setF({ ...f, scope: "personal" })}>Just me</button>
          </div>
        )}
        <p className="muted small">{f.kind === "account" ? "Card limits are just for you." : f.scope === "household"
          ? "Counts spending from accounts shared in household totals. Everyone in the household sees it."
          : "Counts only your own accounts. Only you see it."} Astris warns you; it can't block payments.</p>
        {error && <div className="error" role="alert">{error}</div>}
        <div className="row"><button className="btn primary">Create budget</button><button type="button" className="btn quiet" onClick={() => ref.current?.close()}>Cancel</button></div>
      </form>
    </dialog>
  );
}
