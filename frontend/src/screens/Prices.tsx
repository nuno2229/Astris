import { useEffect, useState } from "react";
import { api, Household } from "../api";

interface Seen { store: string; unit_price: string; basis: string; price: string; seen_on: string | null; source: string; stale: boolean }
interface Prod { key: string; name: string; basis: string; cheapest: Seen; stores: Seen[]; saving: string | null }
interface Data { online_enabled: boolean; can_edit: boolean; products: Prod[] }

const BASIS: Record<string, string> = { "100g": "/100 g", "100ml": "/100 ml", item: " each" };
export const unitLabel = (s: Pick<Seen, "unit_price" | "basis">) => {
  const n = Number(s.unit_price);
  return (n < 1 ? `${(n * 100).toFixed(1)}p` : `£${n.toFixed(2)}`) + BASIS[s.basis];
};

export default function Prices({ household }: { household: Household }) {
  const [d, setD] = useState<Data | null>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const load = () => api.get<Data>(`/api/households/${household.id}/prices${q ? `?q=${encodeURIComponent(q)}` : ""}`).then(setD);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [household.id, q]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!d) return <div className="muted">Loading…</div>;
  const toggle = async (enabled: boolean) => { await api.put(`/api/households/${household.id}/prices-online`, { enabled }); load(); };
  const refresh = async () => {
    setMsg("Looking up prices…");
    try { const r = await api.post<{ updated: number }>(`/api/households/${household.id}/prices/refresh`); setMsg(`${r.updated} price${r.updated === 1 ? "" : "s"} updated.`); load(); }
    catch (e) { setMsg((e as Error).message); }
  };

  return (
    <>
      <p className="note" style={{ marginTop: -6 }}>Compares what the same product costs at different UK shops, per 100 g, per 100 ml or each.
        Prices come from your own receipts{d.online_enabled ? " and Open Prices" : ""}. Shop prices change often, so check the dates.</p>
      <div className="panel stack" style={{ marginTop: 12 }}>
        <label className="check">
          <input type="checkbox" checked={d.online_enabled} disabled={!d.can_edit} onChange={(e) => toggle(e.target.checked)} />
          <span>Also look up prices from Open Prices <span className="pill">Uses internet</span>
            <span className="muted small"> An open, crowd-sourced price database by Open Food Facts. Only the barcodes of items in your
              pantry are sent. Coverage is patchy.{!d.can_edit && " Only the household owner can change this."}</span></span>
        </label>
        {d.online_enabled && <button className="btn" onClick={refresh}>Update online prices</button>}
        {msg && <div className="ok" role="status">{msg}</div>}
        <span className="muted small">Astris doesn't copy prices from supermarket websites: their terms don't allow it.</span>
      </div>
      <input type="search" placeholder="Search products" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search products" style={{ margin: "14px 0" }} />
      {d.products.length === 0 ? (
        <div className="empty"><strong>No prices yet</strong>Scan receipts from different shops and the same products will be compared here.</div>
      ) : (
        <ul className="list">
          {d.products.map((p) => (
            <li key={p.key + p.basis} className="tap price-row">
              <button className="rowbtn" aria-expanded={open === p.key + p.basis} onClick={() => setOpen(open === p.key + p.basis ? null : p.key + p.basis)}>
                <div className="main">
                  <div className="title">{p.name}</div>
                  <div className="meta wrap">{p.stores.length > 1 ? <>Cheapest at <strong className="best">{p.cheapest.store}</strong>{p.saving && Number(p.saving) > 0.01 ? `, ${Math.round(Number(p.saving) * 100)}% less than the dearest` : ""}</>
                    : <>Only seen at {p.cheapest.store}</>}</div>
                </div>
                <div className="amt">{unitLabel(p.cheapest)}</div>
              </button>
              {open === p.key + p.basis && (
                <table className="compare">
                  <thead><tr><th>Shop</th><th>Price</th><th>Unit price</th><th>Seen</th></tr></thead>
                  <tbody>{p.stores.map((s) => (
                    <tr key={s.store} className={s === p.stores[0] ? "best-row" : ""}>
                      <td>{s.store}{s === p.stores[0] && p.stores.length > 1 ? " (cheapest)" : ""}</td>
                      <td>£{Number(s.price).toFixed(2)}</td><td>{unitLabel(s)}</td>
                      <td className={s.stale ? "flag" : "muted"}>{s.seen_on ? new Date(s.seen_on + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "?"}
                        {s.stale ? ", may be out of date" : ""}<div className="small muted">{s.source}</div></td>
                    </tr>))}</tbody>
                </table>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
