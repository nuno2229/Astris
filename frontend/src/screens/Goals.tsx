import { FormEvent, useEffect, useRef, useState } from "react";
import { api, Household, money, relDay } from "../api";

export interface Goal { id: string; name: string; target: string; currency: string; deadline: string | null; archived: boolean;
  saved: string; remaining: string; progress: string; achieved: boolean; days_left: number | null;
  per_month_needed: string | null; pace_per_month: string; projected_date: string | null; on_track: boolean | null;
  members: { user_id: string; name: string; saved: string; share: string }[];
  recent: { id: string; user_id: string; name: string; amount: string; made_on: string; note: string | null; from_statement: boolean; mine: boolean }[];
  my_rules: { id: string; label: string }[] }

export function Meter({ value, label }: { value: number; label: string }) {
  return (
    <div className="meter" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)} aria-label={label}>
      <div className="meter-fill" style={{ width: `${Math.max(value * 100, value > 0 ? 2 : 0)}%` }} />
    </div>
  );
}

function verdict(g: Goal): string {
  if (g.achieved) return "Reached. Well done!";
  if (!g.deadline) return Number(g.pace_per_month) > 0 ? `At this pace, done ${g.projected_date ? relDay(g.projected_date) : "eventually"}` : "No deadline";
  const need = `${money(g.per_month_needed, g.currency)} a month to make ${relDay(g.deadline)}`;
  if (g.on_track) return `On track. ${need}`;
  return `Behind: ${need}`;
}

export default function Goals({ household, currency }: { household: Household; currency: string }) {
  const [goals, setGoals] = useState<Goal[] | null>(null);
  const [open, setOpen] = useState<string | "new" | null>(null);
  const load = () => api.get<Goal[]>(`/api/households/${household.id}/goals`).then(setGoals);
  useEffect(() => { load(); }, [household.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!goals) return <div className="muted">Loading…</div>;
  return (
    <>
      <h1>Savings goals</h1>
      {goals.length === 0 && <div className="empty"><strong>No goals yet</strong>Saving for a holiday or a new boiler? Set a target and track who's put in what.</div>}
      <div className="stack">
        {goals.map((g) => (
          <button key={g.id} className="panel goal-card" onClick={() => setOpen(g.id)}>
            <div className="acct-head">
              <div className="acct-name">{g.name}</div>
              <div className="muted small">{Math.round(Number(g.progress) * 100)}%</div>
            </div>
            <Meter value={Number(g.progress)} label={`${g.name} progress`} />
            <div className="goal-line"><strong>{money(g.saved, g.currency)}</strong> <span className="muted">of {money(g.target, g.currency)}</span></div>
            <div className={"small " + (g.on_track === false ? "flag" : "muted")}>{verdict(g)}</div>
          </button>
        ))}
      </div>
      <button className="btn block" style={{ marginTop: 14 }} onClick={() => setOpen("new")}>+ New goal</button>
      {open === "new" && <NewGoal household={household} currency={currency} onClose={() => setOpen(null)} onDone={(id) => { load(); setOpen(id); }} />}
      {open && open !== "new" && <GoalSheet id={open} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}

function NewGoal({ household, currency, onClose, onDone }: { household: Household; currency: string; onClose: () => void; onDone: (id: string) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  const [f, setF] = useState({ name: "", target: "", deadline: "" });
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setError("");
    try { const g = await api.post<Goal>(`/api/households/${household.id}/goals`, { name: f.name, target: f.target, currency, deadline: f.deadline || null }); onDone(g.id); }
    catch (err) { setError((err as Error).message); }
  };
  return (
    <dialog ref={ref} className="sheet" onClose={onClose} onClick={(e) => e.target === ref.current && ref.current?.close()} aria-labelledby="ng">
      <form className="sheet-body stack" onSubmit={submit}>
        <div className="grabber" aria-hidden="true" />
        <h2 id="ng" style={{ margin: 0 }}>New savings goal</h2>
        <label>What for<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required maxLength={80} placeholder="e.g. Summer holiday" /></label>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <label style={{ flex: 1 }}>Target ({currency})<input value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })} required inputMode="decimal" pattern="\d+(\.\d{1,2})?" /></label>
          <label style={{ flex: 1 }}>By (optional)<input type="date" value={f.deadline} onChange={(e) => setF({ ...f, deadline: e.target.value })} /></label>
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        <div className="row"><button className="btn primary">Create goal</button><button type="button" className="btn quiet" onClick={() => ref.current?.close()}>Cancel</button></div>
      </form>
    </dialog>
  );
}

function GoalSheet({ id, onClose }: { id: string; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [g, setG] = useState<Goal | null>(null);
  const [dir, setDir] = useState<"in" | "out">("in");
  const [amt, setAmt] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { ref.current?.showModal(); api.get<Goal>(`/api/goals/${id}`).then(setG); }, [id]);
  const run = async (fn: () => Promise<Goal | void>) => { setError(""); try { const r = await fn(); if (r) setG(r); else setG(await api.get<Goal>(`/api/goals/${id}`)); } catch (e) { setError((e as Error).message); } };
  const add = (e: FormEvent) => { e.preventDefault(); run(async () => { const r = await api.post<Goal>(`/api/goals/${id}/contributions`, { amount: (dir === "out" ? "-" : "") + amt, note: note || null }); setAmt(""); setNote(""); return r; }); };

  return (
    <dialog ref={ref} className="sheet" onClose={onClose} onClick={(e) => e.target === ref.current && ref.current?.close()} aria-labelledby="gs">
      <div className="sheet-body stack">
        <div className="grabber" aria-hidden="true" />
        {!g ? <div className="muted">Loading…</div> : <>
          <h2 id="gs" style={{ margin: 0 }}>{g.name}</h2>
          <Meter value={Number(g.progress)} label={`${g.name} progress`} />
          <div className="goal-line"><strong>{money(g.saved, g.currency)}</strong> <span className="muted">of {money(g.target, g.currency)}, {money(g.remaining, g.currency)} to go</span></div>
          <div className={"small " + (g.on_track === false ? "flag" : "muted")}>{verdict(g)}</div>
          {g.members.length > 0 && (
            <div className="stack" style={{ gap: 6 }}>
              <strong className="small">Who's put in what</strong>
              {g.members.map((m) => (
                <div key={m.user_id} className="member-row">
                  <span>{m.name}</span><Meter value={Number(m.share)} label={`${m.name}'s share`} /><span className="amt">{money(m.saved, g.currency)}</span>
                </div>
              ))}
            </div>
          )}
          <form className="stack" onSubmit={add} style={{ gap: 10 }}>
            <div className="seg full" role="group" aria-label="Direction">
              <button type="button" aria-pressed={dir === "in"} onClick={() => setDir("in")}>Put money aside</button>
              <button type="button" aria-pressed={dir === "out"} onClick={() => setDir("out")}>Took some out</button>
            </div>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <label style={{ flex: 1 }}>Amount<input value={amt} onChange={(e) => setAmt(e.target.value)} required inputMode="decimal" pattern="\d+(\.\d{1,2})?" /></label>
              <label style={{ flex: 2 }}>Note (optional)<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} /></label>
            </div>
            <button className="btn primary">Save</button>
          </form>
          <p className="muted small">Paying into a savings pot from your bank? Open that transfer in Activity and choose “Count towards a goal” to have it counted automatically from now on.</p>
          {g.my_rules.map((r) => (
            <div key={r.id} className="row small muted">Counting your transfers to “{r.label}” automatically.
              <button className="btn quiet" onClick={() => run(() => api.del(`/api/goal-rules/${r.id}`))}>Stop</button></div>
          ))}
          {g.recent.length > 0 && (
            <ul className="list" style={{ background: "var(--bg)" }}>
              {g.recent.map((c) => (
                <li key={c.id}>
                  <div className="main"><div className="title">{c.name}{c.note ? `: ${c.note}` : ""}</div>
                    <div className="meta">{new Date(c.made_on + "T00:00:00").toLocaleDateString()}</div></div>
                  <div className={"amt" + (Number(c.amount) > 0 ? " in" : "")}>{money(c.amount, g.currency, true)}</div>
                  {c.mine && <button className="iconbtn" aria-label="Remove this contribution" onClick={() => run(() => api.del(`/api/goal-contributions/${c.id}`))}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg></button>}
                </li>
              ))}
            </ul>
          )}
          {error && <div className="error" role="alert">{error}</div>}
          <div className="row">
            <button className="btn quiet" onClick={() => ref.current?.close()}>Close</button>
            <button className="btn quiet" onClick={() => run(async () => { await api.patch(`/api/goals/${id}`, { archived: true }); ref.current?.close(); })}>Archive goal</button>
          </div>
        </>}
      </div>
    </dialog>
  );
}
