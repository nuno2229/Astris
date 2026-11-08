import { FormEvent, useCallback, useEffect, useState } from "react";
import { Account, api, Household, HouseholdAccount, money } from "../api";
import type { Ctx } from "../App";
import CashWallet from "./CashWallet";

type Share = "none" | "include" | "hide";
const SHARE_HELP: Record<Share, string> = {
  none: "Only you can see this account.",
  include: "Members can see it, and its spending counts in household totals.",
  hide: "Members can see it, but its spending is shown apart from household totals.",
};
const TYPE_LABEL = { bank: "Bank account", card: "Card", savings: "Savings", cash: "Cash" } as const;

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
                  <div className="meta">{a.until
                    ? `${a.owner_name} left; their transactions up to ${new Date(a.until + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })} still count`
                    : `${a.owner_name}'s, ${a.attribution === "include" ? "counted in totals" : "kept separate"}`}</div>
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
      {a.is_mine && <AiChoice account={a} household={household} />}
      {a.is_mine && <KeptHistory account={a} />}
      {a.type === "cash" ? <CashWallet a={a} onChanged={onChanged} /> : <>
      <a className="btn" href={`#/import/${a.id}`}>Import statements</a>
      <p className="muted small" style={{ marginTop: -6 }}>CSV, OFX, MT940 or PDF from your bank, or a photo of a paper statement.</p>
      </>}
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

interface AiChoiceData { mode: "auto" | "household" | "off"; household_id: string | null; options: { household_id: string; name: string; ai_on: boolean }[] }

/** Which household's AI labels this account's transactions (G-14). Only shown when the account is in a household with AI on. */
function AiChoice({ account: a, household }: { account: Account; household: Household }) {
  const [c, setC] = useState<AiChoiceData | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.get<AiChoiceData>(`/api/accounts/${a.id}/ai`).then(setC).catch(() => setC(null)); }, [a.id, household.id]);
  if (!c || !c.options.some((o) => o.ai_on)) return null;
  const value = c.mode === "household" ? c.household_id! : c.mode;
  const set = async (v: string) => {
    setError("");
    try {
      setC(await api.put<AiChoiceData>(`/api/accounts/${a.id}/ai`, v === "auto" || v === "off" ? { mode: v } : { mode: "household", household_id: v }));
    } catch (e) { setError((e as Error).message); }
  };
  const first = c.options.find((o) => o.ai_on);
  return (
    <label className="small">AI labelling for {a.name}
      <select value={value} onChange={(e) => set(e.target.value)}>
        <option value="auto">Automatic ({first?.name}'s AI)</option>
        {c.options.filter((o) => o.ai_on).map((o) => <option key={o.household_id} value={o.household_id}>{o.name}'s AI</option>)}
        <option value="off">Off: never sent to an AI</option>
      </select>
      {error && <div className="error" role="alert">{error}</div>}
    </label>
  );
}

/** Households you left but let keep this account's past transactions (G-9). You can take them back. */
function KeptHistory({ account: a }: { account: Account }) {
  const [kept, setKept] = useState<{ household_id: string; household: string | null; until: string }[]>([]);
  const load = () => api.get<typeof kept>(`/api/accounts/${a.id}/kept-history`).then(setKept).catch(() => setKept([]));
  useEffect(() => { load(); }, [a.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!kept.length) return null;
  return (
    <div className="stack" style={{ gap: 6 }}>
      {kept.map((k) => (
        <div key={k.household_id} className="row small" style={{ justifyContent: "space-between" }}>
          <span className="muted">{k.household ?? "A household you left"} still sees this account's transactions up to {new Date(k.until + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}.</span>
          <button className="btn quiet" onClick={async () => { if (confirm("Take these transactions back? They'll disappear from that household's history and totals.")) { await api.del(`/api/accounts/${a.id}/kept-history/${k.household_id}`); load(); } }}>Take back</button>
        </div>
      ))}
    </div>
  );
}
