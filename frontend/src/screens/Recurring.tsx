import { useCallback, useEffect, useState } from "react";
import { api, CalendarItem, Household, money, Recurring, relDay } from "../api";

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

export default function RecurringScreen({ household, embedded }: { household: Household; embedded?: boolean }) {
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
      {!embedded && <h1>Regular payments</h1>}
      <CalendarSection household={household} />
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

/** A known bill or income from a calendar (G-5). */
export function CalendarRow({ c }: { c: CalendarItem }) {
  return (
    <li>
      <div className="main">
        <div className="title">{c.name}</div>
        <div className="meta">{c.next_date ? `${c.direction === "in" ? "Expected" : "Due"} ${relDay(c.next_date)}` : "No more dates"}, from {c.calendar}</div>
      </div>
      {c.amount && <div className={"amt" + (c.direction === "in" ? " in" : "")}>{money(Number(c.amount) * (c.direction === "in" ? 1 : -1), c.currency, true)}</div>}
    </li>
  );
}

interface CalendarData { feeds: { id: string; name: string; has_link: boolean; mine: boolean; last_error: string | null; items: number }[]; items: CalendarItem[] }

function CalendarSection({ household }: { household: Household }) {
  const [d, setD] = useState<CalendarData | null>(null);
  const [adding, setAdding] = useState(false);
  const [link, setLink] = useState({ name: "", url: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.get<CalendarData>(`/api/households/${household.id}/calendar`).then(setD).catch(() => setD(null)), [household.id]);
  useEffect(() => { load(); }, [load]);
  const run = async (fn: () => Promise<CalendarData | void>) => {
    setError(""); setBusy(true);
    try { const r = await fn(); if (r) setD(r); else await load(); setAdding(false); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const upload = (f: File) => { const fd = new FormData(); fd.append("file", f); run(() => api.post<CalendarData>(`/api/households/${household.id}/calendar/upload`, fd)); };
  if (!d) return null;
  return (
    <section>
      <h2>From your calendar</h2>
      {d.items.length > 0 && <ul className="list">{d.items.slice(0, 20).map((c) => <CalendarRow key={c.id} c={c} />)}</ul>}
      <div className="panel stack" style={{ marginTop: d.items.length ? 12 : 0 }}>
        {d.feeds.map((f) => (
          <div key={f.id} className="row small" style={{ justifyContent: "space-between" }}>
            <span>{f.name} <span className="muted">({f.items} item{f.items === 1 ? "" : "s"}{f.has_link ? ", refreshed at most daily" : ""})</span>
              {f.last_error && <span className="flag"> {f.last_error}</span>}</span>
            {f.mine && <button className="btn quiet" onClick={() => confirm(`Remove ${f.name}?`) && run(() => api.del(`/api/calendar-feeds/${f.id}`))}>Remove</button>}
          </div>
        ))}
        {!adding ? (
          <>
            {d.feeds.length === 0 && <span className="muted small">Keep rent, council tax or payday in a calendar? Add it and they'll show in Coming up. Put the amount in the event title, like “Rent £950”.</span>}
            <div className="row">
              <label className="btn">{busy ? "Reading…" : "Add a calendar file (.ics)"}<input type="file" accept=".ics,text/calendar" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>
              <button className="btn quiet" onClick={() => setAdding(true)}>Add a calendar link</button>
            </div>
          </>
        ) : (
          <form className="stack" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); run(() => api.post<CalendarData>(`/api/households/${household.id}/calendar/link`, link)); }}>
            <label>Name<input value={link.name} onChange={(e) => setLink({ ...link, name: e.target.value })} required maxLength={80} placeholder="Bills" /></label>
            <label>Calendar link (https:// or webcal://)<input value={link.url} onChange={(e) => setLink({ ...link, url: e.target.value })} required maxLength={1000} /></label>
            <span className="small"><span className="pill">Uses internet</span> Your server fetches the link when you add it, then again when you open Bills, at most once a day. The link stays private: the app never shows it again.</span>
            <div className="row"><button className="btn primary" disabled={busy}>{busy ? "Fetching…" : "Add calendar"}</button><button type="button" className="btn quiet" onClick={() => setAdding(false)}>Cancel</button></div>
          </form>
        )}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
    </section>
  );
}
