import { FormEvent, useEffect, useRef, useState } from "react";
import { api, CYCLE_LABEL, Household, money, relDay, Subscription, SubSuggestion } from "../api";

type Draft = { provider: string; amount: string; currency: string; cycle: Subscription["cycle"];
  next_renewal: string; trial: boolean; trial_ends: string; auto_renew: boolean };

function statusLine(s: Subscription): string {
  if (s.status === "cancelled") return s.access_until && new Date(s.access_until) >= new Date(new Date().toDateString())
    ? `Cancelled, access until ${relDay(s.access_until)}` : "Cancelled";
  if (s.status === "trial") return `Free trial until ${relDay(s.trial_ends!)}`;
  if (!s.next_renewal) return s.auto_renew ? "Renewal date not set" : "Won't renew";
  return s.auto_renew ? `Renews ${relDay(s.next_renewal)}` : `Ends ${relDay(s.next_renewal)}`;
}

export default function Subscriptions({ household, currency }: { household: Household; currency: string }) {
  const [subs, setSubs] = useState<Subscription[] | null>(null);
  const [sugs, setSugs] = useState<SubSuggestion[]>([]);
  const [open, setOpen] = useState<Subscription | "new" | null>(null);
  const load = () => Promise.all([
    api.get<Subscription[]>(`/api/households/${household.id}/subscriptions`).then(setSubs),
    api.get<SubSuggestion[]>(`/api/households/${household.id}/subscriptions/suggestions`).then(setSugs),
  ]);
  useEffect(() => { load(); }, [household.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const track = async (id: string) => { await api.post(`/api/households/${household.id}/subscriptions/from-recurring`, { recurring_id: id }); load(); };
  if (!subs) return <div className="muted">Loading…</div>;
  const active = subs.filter((s) => s.status !== "cancelled");
  const cancelled = subs.filter((s) => s.status === "cancelled");
  const perMonth = active.reduce((t, s) => t + Number(s.monthly_cost), 0);
  const cur = active[0]?.currency ?? currency;

  return (
    <>
      {active.length > 0 && (
        <div className="hero" style={{ paddingTop: 0 }}>
          <div className="label">Subscriptions cost about</div>
          <div className="figure" style={{ fontSize: "clamp(40px, 11vw, 56px)" }}>{money(perMonth, cur)}<span className="per"> a month</span></div>
          <div className="facts"><span><strong>{money(perMonth * 12, cur)}</strong> a year</span></div>
        </div>
      )}
      {sugs.length > 0 && (
        <>
          <h2>Found in your statements</h2>
          <ul className="list">
            {sugs.map((g) => (
              <li key={g.recurring_id}>
                <div className="main"><div className="title">{g.name}</div>
                  <div className="meta">{money(g.amount, g.currency)} {CYCLE_LABEL[g.cadence as Subscription["cycle"]] ?? g.cadence}</div></div>
                <button className="btn" onClick={() => track(g.recurring_id)}>Track</button>
              </li>
            ))}
          </ul>
        </>
      )}
      <h2>Tracked</h2>
      {active.length === 0 ? (
        <div className="empty"><strong>No subscriptions yet</strong>Add one, or track one found in your statements.</div>
      ) : (
        <ul className="list">
          {active.map((s) => (
            <li key={s.id} className="tap">
              <button className="rowbtn" onClick={() => setOpen(s)}>
                <div className="main">
                  <div className="title">{s.provider}{s.plan ? ` ${s.plan}` : ""}</div>
                  <div className="meta">{s.alerts.length ? <span className={s.alerts[0].severity === "info" ? "" : "flag"}>{s.alerts[0].text}</span> : statusLine(s)}</div>
                </div>
                <div className="amt">{money(s.amount, s.currency)}<div className="meta" style={{ fontWeight: 400, textAlign: "right" }}>{CYCLE_LABEL[s.cycle]}</div></div>
              </button>
            </li>
          ))}
        </ul>
      )}
      <button className="btn block" style={{ marginTop: 14 }} onClick={() => setOpen("new")}>+ Add subscription</button>
      {cancelled.length > 0 && (
        <>
          <h2>Cancelled</h2>
          <ul className="list">
            {cancelled.map((s) => (
              <li key={s.id} className="tap">
                <button className="rowbtn" onClick={() => setOpen(s)}>
                  <div className="main"><div className="title">{s.provider}</div>
                    <div className="meta">{s.alerts.length ? <span className="flag">{s.alerts[0].text}</span> : statusLine(s)}</div></div>
                  <div className="amt muted">{money(s.amount, s.currency)}</div>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {open && <SubSheet household={household} sub={open === "new" ? null : open} currency={currency}
        onClose={() => setOpen(null)} onDone={() => { setOpen(null); load(); }} />}
    </>
  );
}

function SubSheet({ household, sub, currency, onClose, onDone }: {
  household: Household; sub: Subscription | null; currency: string; onClose: () => void; onDone: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  const [d, setD] = useState<Draft>({
    provider: sub?.provider ?? "", amount: sub?.amount ? String(Number(sub.amount)) : "", currency: sub?.currency ?? currency,
    cycle: sub?.cycle ?? "monthly", next_renewal: sub?.next_renewal ?? "", trial: sub?.status === "trial",
    trial_ends: sub?.trial_ends ?? "", auto_renew: sub?.auto_renew ?? true,
  });
  const [cancelOn, setCancelOn] = useState(new Date().toISOString().slice(0, 10));
  const [error, setError] = useState("");
  const run = async (fn: () => Promise<unknown>) => { setError(""); try { await fn(); onDone(); } catch (e) { setError((e as Error).message); } };

  const save = (e: FormEvent) => {
    e.preventDefault();
    const body = { provider: d.provider, amount: d.amount, cycle: d.cycle, auto_renew: d.auto_renew,
      next_renewal: d.next_renewal || null, trial_ends: d.trial ? d.trial_ends || null : null };
    run(() => sub ? api.patch(`/api/subscriptions/${sub.id}`, body)
      : api.post(`/api/households/${household.id}/subscriptions`, { ...body, currency: d.currency }));
  };

  return (
    <dialog ref={ref} className="sheet" onClose={(e) => { if (e.target === e.currentTarget) onClose(); }} onClick={(e) => e.target === ref.current && ref.current?.close()} aria-labelledby="sub-title">
      <form className="sheet-body stack" onSubmit={save}>
        <div className="grabber" aria-hidden="true" />
        <h2 id="sub-title" style={{ margin: 0 }}>{sub ? sub.provider : "Add a subscription"}</h2>
        {sub?.alerts.map((a) => <div key={a.kind} className={a.severity === "critical" ? "callout alert-critical" : "callout"}>{a.text}</div>)}
        <label>Service<input value={d.provider} onChange={(e) => setD({ ...d, provider: e.target.value })} required maxLength={80} placeholder="e.g. Netflix" /></label>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <label style={{ flex: 1 }}>Price<input value={d.amount} onChange={(e) => setD({ ...d, amount: e.target.value })} required inputMode="decimal" pattern="\d+(\.\d{1,2})?" /></label>
          <label style={{ flex: 1 }}>Billed
            <select value={d.cycle} onChange={(e) => setD({ ...d, cycle: e.target.value as Draft["cycle"] })}>
              <option value="weekly">Weekly</option><option value="fortnightly">Every 2 weeks</option>
              <option value="monthly">Monthly</option><option value="quarterly">Quarterly</option><option value="yearly">Yearly</option>
            </select>
          </label>
        </div>
        {!sub?.linked && <label>Next payment<input type="date" value={d.next_renewal} onChange={(e) => setD({ ...d, next_renewal: e.target.value })} /></label>}
        {sub?.linked && <p className="muted small">Payment dates follow your bank statements.</p>}
        <label className="check"><input type="checkbox" checked={d.auto_renew} onChange={(e) => setD({ ...d, auto_renew: e.target.checked })} />Renews automatically</label>
        <label className="check"><input type="checkbox" checked={d.trial} onChange={(e) => setD({ ...d, trial: e.target.checked })} />It's a free trial</label>
        {d.trial && <label>Trial ends<input type="date" value={d.trial_ends} onChange={(e) => setD({ ...d, trial_ends: e.target.value })} required /></label>}
        {error && <div className="error" role="alert">{error}</div>}
        <div className="row">
          <button className="btn primary">{sub ? "Save changes" : "Add subscription"}</button>
          <button type="button" className="btn quiet" onClick={() => ref.current?.close()}>Close</button>
        </div>
        {sub && sub.status !== "cancelled" && (
          <div className="panel stack" style={{ background: "var(--bg)", gap: 10 }}>
            <strong>Cancelled it?</strong>
            <p className="muted small">Record the date and we'll warn you if it charges again.</p>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input type="date" value={cancelOn} onChange={(e) => setCancelOn(e.target.value)} aria-label="Cancelled on" />
              <button type="button" className="btn" onClick={() => run(() => api.post(`/api/subscriptions/${sub.id}/cancel`, { cancelled_on: cancelOn }))}>Mark cancelled</button>
            </div>
          </div>
        )}
        {sub?.status === "cancelled" && (
          <button type="button" className="btn" onClick={() => run(() => api.post(`/api/subscriptions/${sub.id}/reactivate`))}>I've resubscribed</button>
        )}
        {sub && <button type="button" className="btn danger" onClick={() => confirm(`Remove ${sub.provider} from the list?`) && run(() => api.del(`/api/subscriptions/${sub.id}`))}>Remove from list</button>}
      </form>
    </dialog>
  );
}
