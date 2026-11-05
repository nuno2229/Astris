import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Account, api, Household, HouseholdAccount, ImportResult, money } from "../api";
import type { Ctx } from "../App";

type Share = "none" | "include" | "hide";
const SHARE_HELP: Record<Share, string> = {
  none: "Only you can see this account.",
  include: "Members can see it, and its spending counts in household totals.",
  hide: "Members can see it, but its spending is shown apart from household totals.",
};
const TYPE_LABEL = { bank: "Bank account", card: "Card", cash: "Cash" } as const;

export default function Accounts({ ctx, household }: { ctx: Ctx; household: Household }) {
  const [mine, setMine] = useState<Account[]>([]);
  const [shared, setShared] = useState<HouseholdAccount[]>([]);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [m, s] = await Promise.all([
      api.get<Account[]>("/api/accounts"),
      api.get<HouseholdAccount[]>(`/api/households/${household.id}/accounts`),
    ]);
    setMine(m); setShared(s);
  }, [household.id]);
  useEffect(() => { load().catch((e) => setError(e.message)); }, [load]);

  const shareOf = (id: string): Share => shared.find((s) => s.id === id)?.attribution ?? "none";
  const setShare = async (id: string, v: Share) => {
    setError("");
    try {
      if (v === "none") await api.del(`/api/households/${household.id}/accounts/${id}`);
      else await api.put(`/api/households/${household.id}/accounts/${id}`, { attribution: v });
      await load();
    } catch (e) { setError((e as Error).message); }
  };
  const others = shared.filter((s) => !s.is_mine);

  return (
    <>
      <h1>Accounts</h1>
      {error && <div className="error" role="alert">{error}</div>}
      <h2>Yours</h2>
      <div className="stack">
        {mine.length === 0 && <div className="empty"><strong>No accounts yet</strong>Add your first account, then import a statement from your bank.</div>}
        {mine.map((a) => (
          <AccountCard key={a.id} account={a} household={household} share={shareOf(a.id)}
            onShare={(v) => setShare(a.id, v)} onChanged={load} />
        ))}
        {adding
          ? <AddAccount currency={ctx.me.home_currency} onDone={async () => { setAdding(false); await load(); }} onCancel={() => setAdding(false)} />
          : <button className="btn block" onClick={() => setAdding(true)}>+ Add account</button>}
      </div>
      {others.length > 0 && (
        <>
          <h2>Shared by others</h2>
          <ul className="list">
            {others.map((a) => (
              <li key={a.id}>
                <div className="main">
                  <div className="title">{a.name}</div>
                  <div className="meta">{a.owner_name}'s, {a.attribution === "include" ? "counted in totals" : "kept separate"}</div>
                </div>
                <div className="amt">{money(a.balance, a.currency)}</div>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

function AccountCard({ account: a, household, share, onShare, onChanged }: {
  account: Account; household: Household; share: Share; onShare: (v: Share) => void; onChanged: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const upload = async (f: File) => {
    setBusy(true); setError(""); setResult(null);
    const fd = new FormData(); fd.append("file", f);
    try { setResult(await api.post<ImportResult>(`/api/accounts/${a.id}/imports`, fd)); onChanged(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };
  const undo = async () => {
    if (!result) return;
    await api.del(`/api/imports/${result.batch_id}`); setResult(null); onChanged();
  };

  return (
    <div className="panel stack">
      <div className="acct-head">
        <div>
          <div className="acct-name">{a.name}</div>
          <div className="muted small">{TYPE_LABEL[a.type]} in {a.currency}{a.institution ? ` at ${a.institution}` : ""}</div>
        </div>
        <div className="acct-bal" title="Latest balance from imported statements">{money(a.balance, a.currency)}</div>
      </div>
      <div>
        <div className="muted small" style={{ marginBottom: 8 }}>Share with {household.name}</div>
        <div className="seg full" role="group" aria-label={`Share ${a.name} with ${household.name}`}>
          <button aria-pressed={share === "none"} onClick={() => onShare("none")}>Not shared</button>
          <button aria-pressed={share === "include"} onClick={() => onShare("include")}>In totals</button>
          <button aria-pressed={share === "hide"} onClick={() => onShare("hide")}>Separate</button>
        </div>
        <p className="muted small" style={{ marginTop: 8 }}>{SHARE_HELP[share]}</p>
      </div>
      <input ref={fileRef} type="file" accept=".csv,.ofx,.qfx,.txt,.sta,.mt940,.940,.pdf,image/*,text/csv,application/pdf" hidden
        onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      <button className="btn" disabled={busy} onClick={() => fileRef.current?.click()}>
        {busy ? "Reading statement…" : "Import a statement"}
      </button>
      <p className="muted small" style={{ marginTop: -6 }}>CSV, OFX, MT940 or PDF from your bank, or a photo of a paper statement.</p>
      {error && <div className="error" role="alert">{error}</div>}
      {result && (
        <div className="callout stack" style={{ gap: 6 }}>
          <div><strong>{result.rows_new}</strong> new, {result.rows_duplicate} already imported</div>
          {result.warnings.map((w) => <div key={w} className="muted small">⚠ {w}</div>)}
          {result.rows_new > 0 && <button className="btn danger" onClick={undo}>Undo this import</button>}
        </div>
      )}
    </div>
  );
}

function AddAccount({ currency, onDone, onCancel }: { currency: string; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState({ name: "", type: "bank", currency, institution: "" });
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setError("");
    try { await api.post("/api/accounts", { ...f, institution: f.institution || null }); onDone(); }
    catch (err) { setError((err as Error).message); }
  };
  return (
    <form className="panel stack" onSubmit={submit}>
      <label>Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required maxLength={100} placeholder="e.g. Joint current" /></label>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <label style={{ flex: 1 }}>Type
          <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>
            <option value="bank">Bank</option><option value="card">Card</option><option value="cash">Cash</option>
          </select>
        </label>
        <label style={{ width: 110 }}>Currency
          <input value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} pattern="[A-Za-z]{3}" maxLength={3} required />
        </label>
      </div>
      <label>Bank (optional)<input value={f.institution} onChange={(e) => setF({ ...f, institution: e.target.value })} maxLength={100} /></label>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="row"><button className="btn primary">Add</button><button type="button" className="btn quiet" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}
