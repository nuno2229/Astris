import { useEffect, useRef, useState } from "react";
import { api, Household } from "../api";

export interface MealsData {
  configured: boolean; can_edit: boolean; url: string | null; allow_internet: boolean; error: string | null;
  meals: { date: string; kind: string; title: string; ingredients: { name: string; display: string; in_pantry: boolean }[] }[];
}

const day = (d: string) => {
  const t = new Date(); t.setHours(0, 0, 0, 0);
  const x = new Date(d + "T00:00:00");
  const diff = Math.round((x.getTime() - t.getTime()) / 864e5);
  return diff === 0 ? "Today" : diff === 1 ? "Tomorrow" : x.toLocaleDateString(undefined, { weekday: "long" });
};

/** Home: this week's meals from Mealie (E20-02, E16-01). Tap a meal to check ingredients against the pantry. */
export function CookingThisWeek({ household }: { household: Household }) {
  const [d, setD] = useState<MealsData | null>(null);
  const [open, setOpen] = useState<MealsData["meals"][number] | null>(null);
  useEffect(() => { api.get<MealsData>(`/api/households/${household.id}/meals`).then(setD).catch(() => setD(null)); }, [household.id]);
  if (!d || !d.configured) return null;
  return (
    <>
      <h2>Cooking this week</h2>
      {d.error ? <p className="muted small">{d.error}</p> : d.meals.length === 0 ? <p className="muted small">Nothing planned in Mealie for the next 7 days.</p> : (
        <ul className="list">
          {d.meals.slice(0, 7).map((m, i) => {
            const need = m.ingredients.filter((x) => !x.in_pantry).length;
            return (
              <li key={i} className="tap">
                <button className="rowbtn" onClick={() => setOpen(m)}>
                  <div className="main"><div className="title">{m.title}</div>
                    <div className="meta">{day(m.date)}, {m.kind}{m.ingredients.length ? `, ${need ? `${need} to buy` : "all in the pantry"}` : ""}</div></div>
                  <span aria-hidden="true">›</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {open && <MealSheet household={household} meal={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function MealSheet({ household, meal, onClose }: { household: Household; meal: MealsData["meals"][number]; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [pick, setPick] = useState<Set<number>>(() => new Set(meal.ingredients.map((x, i) => (x.in_pantry ? -1 : i)).filter((i) => i >= 0)));
  const [msg, setMsg] = useState("");
  useEffect(() => { ref.current?.showModal(); }, []);
  const add = async () => {
    try {
      const r = await api.post<{ added: number }>(`/api/households/${household.id}/meals/to-list`, { names: [...pick].map((i) => meal.ingredients[i].display || meal.ingredients[i].name) });
      setMsg(`Added ${r.added} to the shopping list.`); setPick(new Set());
    } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <dialog ref={ref} className="sheet" onClose={(e) => { if (e.target === e.currentTarget) onClose(); }} onClick={(e) => e.target === ref.current && ref.current?.close()} aria-labelledby="meal-title">
      <div className="sheet-body stack">
        <div className="grabber" aria-hidden="true" />
        <h2 id="meal-title" style={{ margin: 0 }}>{meal.title}</h2>
        <span className="muted small">{day(meal.date)}, {meal.kind}</span>
        {meal.ingredients.length === 0 ? <p className="muted small">No ingredients listed in Mealie.</p> : (
          <div className="stack" style={{ gap: 6 }}>
            {meal.ingredients.map((x, i) => (
              <label key={i} className="check">
                <input type="checkbox" checked={pick.has(i)} onChange={(e) => { const s = new Set(pick); if (e.target.checked) s.add(i); else s.delete(i); setPick(s); }} />
                <span>{x.display || x.name} {x.in_pantry && <span className="muted small">(in the pantry)</span>}</span>
              </label>
            ))}
          </div>
        )}
        {msg && <div className="ok" role="status">{msg}</div>}
        <div className="row">
          <button className="btn primary" disabled={!pick.size} onClick={add}>Add {pick.size || ""} to the list</button>
          <button className="btn quiet" onClick={() => ref.current?.close()}>Close</button>
        </div>
      </div>
    </dialog>
  );
}

/** Settings: connect Mealie (owner). The token is stored on the server and never shown again. */
export function MealieSettings({ household }: { household: Household }) {
  const [d, setD] = useState<MealsData | null>(null);
  const [f, setF] = useState({ url: "", token: "", allow_internet: false });
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api.get<MealsData>(`/api/households/${household.id}/meals?days=1`).then((r) => { setD(r); setF({ url: r.url ?? "", token: "", allow_internet: r.allow_internet }); }).catch(() => setD(null));
  }, [household.id]);
  if (!d) return null;
  const save = async (disconnect = false) => {
    setMsg("");
    try {
      const r = await api.put<MealsData>(`/api/households/${household.id}/mealie`, disconnect ? { url: null } :
        { url: f.url || null, allow_internet: f.allow_internet, ...(f.token ? { token: f.token } : {}) });
      setD(r); setF({ ...f, token: "", url: r.url ?? "" });
      setMsg(disconnect ? "Disconnected." : r.error ? r.error : `Connected: ${r.meals.length} meal${r.meals.length === 1 ? "" : "s"} planned today.`);
    } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <>
      <h2>Meal plans: Mealie ({household.name})</h2>
      <div className="panel stack">
        <span className="muted small">Show this week's meals from your Mealie server on Home, check ingredients against the pantry and add what's missing to the list. Astris only reads from Mealie.</span>
        {!d.can_edit ? <span className="muted small">{d.configured ? `Connected to ${d.url}.` : "Not connected."} Only the household owner can change this.</span> : (
          <form className="stack" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); save(); }}>
            <label>Mealie address<input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="http://192.168.1.20:9925" /></label>
            <label>API token {d.configured && <span className="muted small">(saved; leave empty to keep it)</span>}
              <input type="password" autoComplete="off" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} placeholder="From Mealie: profile, API tokens" /></label>
            <label className="check"><input type="checkbox" checked={f.allow_internet} onChange={(e) => setF({ ...f, allow_internet: e.target.checked })} />
              <span>Mealie is outside my home network <span className="pill">Uses internet</span></span></label>
            <div className="row"><button className="btn primary">Save</button>{d.configured && <button type="button" className="btn quiet" onClick={() => save(true)}>Disconnect</button>}</div>
          </form>
        )}
        {msg && <div className="ok" role="status">{msg}</div>}
      </div>
    </>
  );
}
