import { FormEvent, useState } from "react";
import { Account, api, money } from "../api";

const today = () => new Date().toISOString().slice(0, 10);

/** Cash wallet tools (E08): add entries, count cash, set a starting balance. */
export default function CashWallet({ a, onChanged }: { a: Account; onChanged: () => void }) {
  const [mode, setMode] = useState<"none" | "entry" | "count" | "start">("none");
  const [dir, setDir] = useState<"out" | "in">("out");
  const [f, setF] = useState({ description: "", amount: "", date: today(), counted: "", start: a.opening_balance ? String(Number(a.opening_balance)) : "0", startDate: a.opening_date ?? today() });
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const run = async (fn: () => Promise<string>) => { setError(""); try { setNote(await fn()); setMode("none"); onChanged(); } catch (e) { setError((e as Error).message); } };

  const addEntry = (e: FormEvent) => { e.preventDefault(); run(async () => {
    const amt = (dir === "out" ? "-" : "") + f.amount;
    await api.post(`/api/accounts/${a.id}/transactions`, { posted_on: f.date, description: f.description, amount: amt });
    setF({ ...f, description: "", amount: "" });
    return `Added ${dir === "out" ? "spending" : "cash in"} of ${money(f.amount, a.currency)}.`;
  }); };
  const count = (e: FormEvent) => { e.preventDefault(); run(async () => {
    const r = await api.post<{ adjustment: string }>(`/api/accounts/${a.id}/cash-count`, { counted: f.counted, date: today() });
    const adj = Number(r.adjustment);
    return adj === 0 ? "Spot on: your wallet matches." : adj < 0
      ? `${money(-adj, a.currency)} less than expected, recorded as unlogged spending.`
      : `${money(adj, a.currency)} more than expected, recorded as extra cash.`;
  }); };
  const start = (e: FormEvent) => { e.preventDefault(); run(async () => {
    await api.put(`/api/accounts/${a.id}/opening`, { balance: f.start, date: f.startDate });
    return "Starting balance saved.";
  }); };

  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row">
        <button className={"btn" + (mode === "entry" ? " primary" : "")} onClick={() => setMode(mode === "entry" ? "none" : "entry")}>Add cash entry</button>
        <button className={"btn" + (mode === "count" ? " primary" : "")} onClick={() => setMode(mode === "count" ? "none" : "count")}>Count my cash</button>
        <button className="btn quiet" onClick={() => setMode(mode === "start" ? "none" : "start")}>Starting balance</button>
      </div>
      {mode === "entry" && (
        <form className="stack" onSubmit={addEntry} style={{ gap: 10 }}>
          <div className="seg full" role="group" aria-label="Direction">
            <button type="button" aria-pressed={dir === "out"} onClick={() => setDir("out")}>Spent</button>
            <button type="button" aria-pressed={dir === "in"} onClick={() => setDir("in")}>Received</button>
          </div>
          <label>What for<input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} required maxLength={200} placeholder="e.g. Market veg" /></label>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <label style={{ flex: 1 }}>Amount<input value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} required inputMode="decimal" pattern="\d+(\.\d{1,2})?" /></label>
            <label style={{ flex: 1 }}>Date<input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} required /></label>
          </div>
          <button className="btn primary">Add entry</button>
        </form>
      )}
      {mode === "count" && (
        <form className="stack" onSubmit={count} style={{ gap: 10 }}>
          <p className="muted small">Count what's in your wallet. Any difference is recorded so the balance matches.</p>
          <label>Cash in hand now<input value={f.counted} onChange={(e) => setF({ ...f, counted: e.target.value })} required inputMode="decimal" pattern="\d+(\.\d{1,2})?" /></label>
          <button className="btn primary">Save count</button>
        </form>
      )}
      {mode === "start" && (
        <form className="stack" onSubmit={start} style={{ gap: 10 }}>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <label style={{ flex: 1 }}>Cash you had<input value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} required inputMode="decimal" pattern="-?\d+(\.\d{1,2})?" /></label>
            <label style={{ flex: 1 }}>On<input type="date" value={f.startDate} onChange={(e) => setF({ ...f, startDate: e.target.value })} required /></label>
          </div>
          <button className="btn primary">Save starting balance</button>
        </form>
      )}
      {note && <div className="ok" role="status">{note}</div>}
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}
