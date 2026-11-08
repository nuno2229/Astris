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
        Prices come from your own receipts, prices you send from shop websites{d.online_enabled ? ", Open Prices" : ""} and, if turned on, Morrisons and Aldi lookups. Shop prices change often, so check the dates.</p>
      <div className="panel stack" style={{ marginTop: 12 }}>
        <label className="check">
          <input type="checkbox" checked={d.online_enabled} disabled={!d.can_edit} onChange={(e) => toggle(e.target.checked)} />
          <span>Also look up prices from Open Prices <span className="pill">Uses internet</span>
            <span className="muted small"> An open, crowd-sourced price database by Open Food Facts. Only the barcodes of items in your
              pantry are sent. Coverage is patchy.{!d.can_edit && " Only the household owner can change this."}</span></span>
        </label>
        {d.online_enabled && <button className="btn" onClick={refresh}>Update online prices</button>}
        {msg && <div className="ok" role="status">{msg}</div>}
      </div>
      <ShopWebsites household={household} onAdded={load} />
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

const BOOKMARKLET = (origin: string) => "javascript:(()=>{let n,p;for(const s of document.querySelectorAll('script[type=\\'application/ld+json\\']')){try{const j=JSON.parse(s.textContent);for(const o of [].concat(j['@graph']||j)){if(o&&/Product/.test(o['@type'])){n=o.name;const f=[].concat(o.offers||[])[0];p=f&&(f.price||f.lowPrice)}}}catch(e){}}n=n||(document.querySelector('h1')||{}).innerText;if(!p){const h=document.querySelector('h1');const t=(h&&h.closest('main,section,div')||document.body).innerText;p=(t.match(/£\\s?(\\d+\\.\\d{2})/)||[])[1]}const d=btoa(unescape(encodeURIComponent(JSON.stringify({u:location.href,n:(n||'').trim().slice(0,160),p:p?String(p):''}))));const u='" + origin + "/#/prices/add?d='+d;window.open(u,'_blank')||(location.href=u)})()";

interface Lookups { enabled: boolean; last_run: string | null; running: boolean; can_edit: boolean; shops: string[]; searches: string[] }

/** G-17: prices from shop websites. Sent by you from a product page, or opt-in automatic lookups (Morrisons, Aldi). */
function ShopWebsites({ household, onAdded }: { household: Household; onAdded: () => void }) {
  const [lk, setLk] = useState<Lookups | null>(null);
  const [msg, setMsg] = useState("");
  const [incoming, setIncoming] = useState<{ u: string; n: string; p: string } | null>(null);
  const load = () => api.get<Lookups>(`/api/households/${household.id}/web-lookups`).then(setLk).catch(() => setLk(null));
  useEffect(() => {
    load();
    const m = location.hash.match(/prices\/add\?d=([^&]+)/);
    if (m) {
      try { setIncoming(JSON.parse(decodeURIComponent(escape(atob(decodeURIComponent(m[1])))))); } catch { setMsg("Couldn't read that page's details."); }
      history.replaceState(null, "", "#/prices");
    }
  }, [household.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (lk?.running) { const t = setTimeout(load, 5000); return () => clearTimeout(t); } }, [lk?.running]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = async () => {
    try {
      const r = await api.post<{ store: string }>(`/api/households/${household.id}/web-prices`, { url: incoming!.u, name: incoming!.n, price: incoming!.p });
      setMsg(`Saved the ${r.store} price.`); setIncoming(null); onAdded();
    } catch (e) { setMsg((e as Error).message); }
  };
  const run = async () => { setMsg(""); try { await api.post(`/api/households/${household.id}/web-lookups/run`); load(); } catch (e) { setMsg((e as Error).message); } };
  return (
    <div className="panel stack" style={{ marginTop: 12 }}>
      <strong>Prices from shop websites</strong>
      {incoming && (
        <div className="callout stack" style={{ gap: 8 }}>
          <span className="small">From {(() => { try { return new URL(incoming.u).hostname.replace(/^www\./, ""); } catch { return "a shop"; } })()}. Check it, then save.</span>
          <label>Product<input value={incoming.n} maxLength={160} onChange={(e) => setIncoming({ ...incoming, n: e.target.value })} /></label>
          <label>Price (£)<input inputMode="decimal" value={incoming.p} onChange={(e) => setIncoming({ ...incoming, p: e.target.value })} /></label>
          <div className="row"><button className="btn primary" disabled={!incoming.n || !Number(incoming.p)} onClick={save}>Save price</button>
            <button className="btn quiet" onClick={() => setIncoming(null)}>Cancel</button></div>
        </div>
      )}
      <span className="small">On Tesco, Sainsbury's, Asda and other shop websites: add this button to your bookmarks, then tap it on any product page to send its price here.</span>
      <a className="btn" style={{ alignSelf: "flex-start" }} href="#/prices" ref={(el) => el?.setAttribute("href", BOOKMARKLET(location.origin))} onClick={(e) => { e.preventDefault(); setMsg("Drag this button to your bookmarks bar, or see the phone steps below."); }}>Send price to Astris</a>
      <details className="small muted"><summary>On a phone</summary>
        Bookmark any page, then edit the bookmark: name it “Send price to Astris” and replace its address with the code below. On a product page, open your bookmarks and tap it.
        <textarea readOnly rows={3} style={{ width: "100%", marginTop: 6, fontSize: 12 }} value={BOOKMARKLET(location.origin)} onFocus={(e) => e.currentTarget.select()} />
      </details>
      {lk && (
        <>
          <label className="check">
            <input type="checkbox" checked={lk.enabled} disabled={!lk.can_edit}
              onChange={(e) => api.put<Lookups>(`/api/households/${household.id}/web-lookups`, { enabled: e.target.checked }).then(setLk)} />
            <span>Look up {lk.shops.join(" and ")} prices for our shopping list <span className="pill">Uses internet</span>
              <span className="muted small"> Your server opens their websites a few times a week and searches for items on the shared list and household favourites (never personal lists).
                Your local AI, if set up, picks the right product. Tesco, Sainsbury's and Asda block this, so use the button above for them.{!lk.can_edit && " Only the household owner can change this."}</span></span>
          </label>
          {lk.enabled && (
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="muted small">{lk.running ? "Looking up prices…" : lk.last_run ? `Last looked up ${new Date(lk.last_run).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}` : "Not looked up yet"}
                {lk.searches.length ? `, ${lk.searches.length} item${lk.searches.length === 1 ? "" : "s"} to search` : ", add items to the shared list first"}</span>
              <button className="btn" disabled={lk.running || !lk.searches.length} onClick={run}>Look up now</button>
            </div>
          )}
        </>
      )}
      {msg && <div className="ok" role="status">{msg}</div>}
    </div>
  );
}
