import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Household } from "../api";

interface Item { id: string; name: string; quantity: string; note: string | null; expires_on: string | null; product_id: string | null;
  in_pantry: string | null; source: string; checked: boolean; added_by: string | null; added_at: string; favourite: boolean }
interface List { id: string; name: string; scope: "household" | "personal"; is_default: boolean; open_items: number; can_delete: boolean; items?: Item[] }
interface Fav { id: string; name: string; scope: "household" | "personal" }

const q = (v: string) => Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });

export default function ShoppingList({ household }: { household: Household }) {
  const [lists, setLists] = useState<List[]>([]);
  const [lid, setLid] = useState<string | null>(null);
  const [list, setList] = useState<List | null>(null);
  const [favs, setFavs] = useState<Fav[]>([]);
  const [favOnly, setFavOnly] = useState(false);
  const [name, setName] = useState("");
  const [qty, setQty] = useState("1");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");

  const loadLists = useCallback(() => api.get<List[]>(`/api/households/${household.id}/shopping-lists`).then((ls) => {
    setLists(ls); setLid((cur) => cur && ls.some((l) => l.id === cur) ? cur : ls[0]?.id ?? null); }), [household.id]);
  const loadList = useCallback(() => lid ? api.get<List>(`/api/shopping-lists/${lid}`).then(setList) : Promise.resolve(), [lid]);
  const loadFavs = useCallback(() => api.get<Fav[]>(`/api/households/${household.id}/favourites`).then(setFavs), [household.id]);
  useEffect(() => { loadLists(); loadFavs(); }, [loadLists, loadFavs]);
  useEffect(() => { loadList(); const t = setInterval(loadList, 10000); return () => clearInterval(t); }, [loadList]);   // shared: refresh

  const add = async (e?: FormEvent, n = name) => {
    e?.preventDefault();
    if (!n.trim() || !lid) return;
    setList(await api.post<List>(`/api/shopping-lists/${lid}/items`, { name: n, quantity: qty || "1", note: note || null }));
    setName(""); setQty("1"); setNote("");
  };
  const toggle = async (i: Item) => setList(await api.patch<List>(`/api/shopping-items/${i.id}`, { checked: !i.checked }));
  const remove = async (i: Item) => setList(await api.del<List>(`/api/shopping-items/${i.id}`));
  const star = async (i: Item) => {
    const f = favs.find((x) => x.name.toLowerCase() === i.name.toLowerCase());
    if (f) await api.del(`/api/favourites/${f.id}`); else await api.post(`/api/households/${household.id}/favourites`, { name: i.name, scope: "household" });
    await loadFavs(); loadList();
  };
  const fromPantry = async () => { const r = await api.post<List>(`/api/shopping-lists/${lid}/from-pantry`); setList(r); setMsg(r.open_items ? "Added what's running low." : "Nothing in the pantry is running low."); };
  const finish = async () => {
    const r = await api.post<List>(`/api/shopping-lists/${lid}/finish`, { to_pantry: true });
    setList(r); setMsg("Cleared ticked items. Anything from the pantry was added back to stock."); loadLists();
  };
  const newList = async () => {
    const n = prompt("Name for your personal list (only you will see it):");
    if (!n) return;
    const l = await api.post<List>(`/api/households/${household.id}/shopping-lists`, { name: n, scope: "personal" });
    await loadLists(); setLid(l.id);
  };

  const shown = (list?.items ?? []).filter((i) => !favOnly || i.favourite);
  const open = shown.filter((i) => !i.checked), done = shown.filter((i) => i.checked);
  const favNotOnList = favs.filter((f) => !(list?.items ?? []).some((i) => !i.checked && i.name.toLowerCase() === f.name.toLowerCase()));

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        {lists.length > 1 || lists.some((l) => !l.is_default) ? (
          <select value={lid ?? ""} onChange={(e) => setLid(e.target.value)} aria-label="List" style={{ flex: 1 }}>
            {lists.map((l) => <option key={l.id} value={l.id}>{l.name}{l.scope === "personal" ? " (just me)" : ""}{l.open_items ? ` (${l.open_items})` : ""}</option>)}
          </select>
        ) : <span className="muted small" style={{ flex: 1 }}>Shared with everyone in {household.name}</span>}
        <button className="btn quiet" onClick={newList}>+ Personal list</button>
      </div>

      <form className="panel stack" onSubmit={add} style={{ gap: 10 }}>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Add an item" aria-label="Item" maxLength={120} />
          <input value={qty} onChange={(e) => setQty(e.target.value)} aria-label="How many" inputMode="decimal" style={{ width: 64 }} />
          <button className="btn primary" disabled={!name.trim()}>Add</button>
        </div>
        {name && <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional), e.g. the big bag" aria-label="Note" maxLength={200} />}
        {favNotOnList.length > 0 && (
          <div className="chips">{favNotOnList.slice(0, 12).map((f) => (
            <button type="button" key={f.id} className="chip" onClick={() => add(undefined, f.name)}>☆ {f.name}</button>))}</div>
        )}
      </form>

      <div className="row" style={{ margin: "14px 0 4px" }}>
        <button className="btn" onClick={fromPantry}>Add what's running low</button>
        <button className={"btn" + (favOnly ? " primary" : " quiet")} aria-pressed={favOnly} onClick={() => setFavOnly(!favOnly)}>Favourites only</button>
      </div>
      {msg && <div className="callout" role="status" style={{ marginTop: 8 }}>{msg}</div>}

      {open.length === 0 && done.length === 0 ? (
        <div className="empty" style={{ marginTop: 12 }}><strong>{favOnly ? "No favourites on the list" : "The list is empty"}</strong>Add things above, or ask Astris: “add milk to the list”.</div>
      ) : (
        <>
          <ul className="list shoplist" style={{ marginTop: 12 }}>
            {open.map((i) => <Row key={i.id} i={i} onToggle={toggle} onRemove={remove} onStar={star} />)}
          </ul>
          {done.length > 0 && (
            <>
              <div className="day" style={{ marginTop: 18 }}>In the trolley ({done.length})</div>
              <ul className="list shoplist">{done.map((i) => <Row key={i.id} i={i} onToggle={toggle} onRemove={remove} onStar={star} />)}</ul>
              <button className="btn primary block" style={{ marginTop: 12 }} onClick={finish}>Done shopping</button>
            </>
          )}
        </>
      )}
      <p className="muted small" style={{ marginTop: 14 }}>Cheapest-shop suggestions will come with price comparison.</p>
    </>
  );
}

function Row({ i, onToggle, onRemove, onStar }: { i: Item; onToggle: (i: Item) => void; onRemove: (i: Item) => void; onStar: (i: Item) => void }) {
  return (
    <li className={i.checked ? "done" : ""}>
      <label className="check" style={{ flex: 1, minWidth: 0 }}>
        <input type="checkbox" checked={i.checked} onChange={() => onToggle(i)} />
        <span className="main">
          <span className="title">{i.name}{Number(i.quantity) !== 1 && <span className="muted"> × {q(i.quantity)}</span>}</span>
          <span className="meta">{[i.note, i.in_pantry !== null ? `have ${q(i.in_pantry)}` : null,
            i.source === "pantry" ? "running low" : i.source === "assistant" ? "added by Astris" : i.added_by ? `added by ${i.added_by}` : null]
            .filter(Boolean).join(", ")}</span>
        </span>
      </label>
      <button className="iconbtn" aria-label={i.favourite ? `Unstar ${i.name}` : `Star ${i.name}`} aria-pressed={i.favourite} onClick={() => onStar(i)}>
        <span className={i.favourite ? "star on" : "star"}>{i.favourite ? "★" : "☆"}</span></button>
      <button className="iconbtn" aria-label={`Remove ${i.name}`} onClick={() => onRemove(i)}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg></button>
    </li>
  );
}
