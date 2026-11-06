import { FormEvent, useEffect, useRef, useState } from "react";
import { api, Household } from "../api";
import { cameraAllowed, decodePhoto, startCamera } from "../scan";

interface Lot { id: string; quantity: string; expires_on: string | null; days_left: number | null; state: "fresh" | "soon" | "expired" | "no_date" }
interface Product { id: string; barcode: string | null; name: string; brand: string | null; size_qty: string | null; size_unit: string | null;
  location: string | null; restock_below: string | null; on_hand: string; amount_label: string | null; low: boolean;
  next_expiry: string | null; lots: Lot[]; flags: string[] }
interface Sugg { id: string; generated_at: string; trigger: string; items: { name: string; brand: string | null; have: number; buy: number; reason: string }[] }

const n = (v: string | number) => Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
function lotText(l: Lot): string {
  if (l.state === "no_date") return "no date";
  if (l.state === "expired") return `expired ${-l.days_left!} day${l.days_left === -1 ? "" : "s"} ago`;
  if (l.days_left === 0) return "use today";
  return `${l.days_left} day${l.days_left === 1 ? "" : "s"} left`;
}

export default function Pantry({ household, embedded }: { household: Household; embedded?: boolean }) {
  const [items, setItems] = useState<Product[] | null>(null);
  const [sugg, setSugg] = useState<Sugg[]>([]);
  const [day, setDay] = useState<number | null>(null);
  const [open, setOpen] = useState<Product | "new" | "scan" | null>(null);
  const [note, setNote] = useState("");
  const load = () => {
    api.get<Product[]>(`/api/households/${household.id}/stock`).then(setItems);
    api.get<Sugg[]>(`/api/households/${household.id}/shop-suggestions`).then(setSugg);
    api.get<{ day: number | null }>(`/api/households/${household.id}/shop-day`).then((d) => setDay(d.day));
  };
  useEffect(load, [household.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!items) return <div className="muted">Loading…</div>;
  const expired = items.reduce((t, p) => t + p.lots.filter((l) => l.state === "expired").length, 0);
  const soon = items.filter((p) => p.lots.some((l) => l.state === "soon"));
  const low = items.filter((p) => p.low);
  const latest = sugg[0];

  return (
    <>
      {!embedded && <h1>Pantry</h1>}
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <button className="btn primary" style={{ flex: 1 }} onClick={() => setOpen("scan")}>Scan items</button>
        <button className="btn" style={{ flex: 1 }} onClick={() => setOpen("new")}>Add by hand</button>
      </div>
      {note && <div className="callout" role="status" style={{ marginTop: 12 }}>{note}</div>}
      {expired > 0 && (
        <div className="callout alert-critical review-link" style={{ marginTop: 12 }}>
          <span><strong>{expired}</strong> item{expired === 1 ? " has" : "s have"} passed the use-by date</span>
          <button className="btn quiet" onClick={() => api.post(`/api/households/${household.id}/stock/clear-expired`).then(() => { setNote("Expired items cleared."); load(); })}>Clear</button>
        </div>
      )}
      {soon.length > 0 && <div className="callout" style={{ marginTop: 12 }}><strong>Use soon:</strong> {soon.map((p) => `${p.name} (${lotText(p.lots.find((l) => l.state === "soon")!)})`).join(", ")}</div>}

      {items.length === 0 ? (
        <div className="empty" style={{ marginTop: 14 }}><strong>Your pantry is empty</strong>Scan barcodes as you unpack the shopping, or add things by hand.</div>
      ) : (
        <>
          {low.length > 0 && <><h2>Running low</h2><ProductList list={low} onOpen={setOpen} /></>}
          <h2>Everything</h2>
          <ProductList list={items} onOpen={setOpen} />
        </>
      )}

      <h2>Weekly shop</h2>
      <div className="panel stack">
        {latest ? (
          <>
            <span className="muted small">{latest.trigger === "weekly" ? "Made automatically" : "Made"} {new Date(latest.generated_at).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" })}</span>
            {latest.items.length === 0 ? <span>Nothing's running low.</span> : (
              <ul className="list" style={{ background: "var(--bg)" }}>
                {latest.items.map((i) => (
                  <li key={i.name}><div className="main"><div className="title">{i.name}</div><div className="meta">{i.reason}, have {n(i.have)}</div></div>
                    <span className="amt">× {n(i.buy)}</span></li>
                ))}
              </ul>
            )}
          </>
        ) : <span className="muted small">Set restock levels on items, then make a list of what's running low.</span>}
        <button className="btn" onClick={() => api.post(`/api/households/${household.id}/shop-suggestions`).then(load)}>Make the list now</button>
        <label>Make it automatically every
          <select value={day ?? ""} disabled={household.role !== "owner"} onChange={(e) => {
            const d = e.target.value === "" ? null : Number(e.target.value);
            api.put(`/api/households/${household.id}/shop-day`, { day: d }).then(() => setDay(d)); }}>
            <option value="">Don't make it automatically</option>
            {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
          </select>
        </label>
        <span className="muted small">Shop prices and a proper shopping list come later.</span>
      </div>

      {open === "scan" && <Scanner household={household} onClose={() => { setOpen(null); load(); }} />}
      {open === "new" && <ProductSheet household={household} product={null} onClose={() => { setOpen(null); load(); }} />}
      {open && typeof open === "object" && <ProductSheet household={household} product={open} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}

function ProductList({ list, onOpen }: { list: Product[]; onOpen: (p: Product) => void }) {
  return (
    <ul className="list">
      {list.map((p) => {
        const soonest = p.lots.find((l) => l.state !== "no_date");
        return (
          <li key={p.id} className="tap">
            <button className="rowbtn" onClick={() => onOpen(p)}>
              <div className="main">
                <div className="title">{p.name}{p.brand ? <span className="muted">, {p.brand}</span> : null}</div>
                <div className="meta">
                  {soonest ? <span className={soonest.state === "fresh" ? "" : "flag"}>{lotText(soonest)}</span> : "no date"}
                  {p.low && <span className="flag">, running low</span>}
                  {p.location && <>, {p.location}</>}
                </div>
              </div>
              <div className="amt">{p.amount_label ?? `× ${n(p.on_hand)}`}</div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function ProductSheet({ household, product, onClose, barcode, quantity }: {
  household: Household; product: Product | null; onClose: () => void; barcode?: string; quantity?: number }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [p, setP] = useState(product);
  const [f, setF] = useState({ name: product?.name ?? "", brand: product?.brand ?? "", size_qty: product?.size_qty ? n(product.size_qty) : "",
    size_unit: product?.size_unit ?? "item", restock_below: product?.restock_below ? n(product.restock_below) : "", location: product?.location ?? "",
    quantity: String(quantity ?? 1), expires_on: "" });
  const [addQty, setAddQty] = useState("1");
  const [addExp, setAddExp] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { ref.current?.showModal(); }, []);
  const run = async (fn: () => Promise<Product | void>) => { setError(""); try { const r = await fn(); if (r) setP(r); } catch (e) { setError((e as Error).message); } };
  const details = { name: f.name, brand: f.brand || null, size_qty: f.size_qty || null, size_unit: f.size_unit,
    restock_below: f.restock_below || null, location: f.location || null };
  const create = (e: FormEvent) => { e.preventDefault(); run(async () => {
    await api.post(`/api/households/${household.id}/products`, { ...details, barcode: barcode ?? null, quantity: f.quantity, expires_on: f.expires_on || null });
    ref.current?.close(); }); };

  return (
    <dialog ref={ref} className="sheet" onClose={(e) => { if (e.target === e.currentTarget) onClose(); }} onClick={(e) => e.target === ref.current && ref.current?.close()} aria-labelledby="ps">
      <div className="sheet-body stack">
        <div className="grabber" aria-hidden="true" />
        <h2 id="ps" style={{ margin: 0 }}>{p ? p.name : barcode ? "New item" : "Add an item"}</h2>
        {barcode && !p && <span className="muted small">Barcode {barcode} isn't in your pantry yet. What is it?</span>}
        {p && (
          <>
            <div className="goal-line"><strong>{p.amount_label ?? `× ${n(p.on_hand)}`}</strong> <span className="muted">in stock{p.restock_below !== null ? `, restock at ${n(p.restock_below)}` : ""}</span></div>
            {p.flags.length > 0 && <span className="muted small">{p.flags.join(", ")}</span>}
            <div className="row">
              <button className="btn primary" disabled={Number(p.on_hand) <= 0} onClick={() => run(() => api.post<Product>(`/api/products/${p.id}/use`, { quantity: 1 }))}>Used one</button>
            </div>
            {p.lots.length > 0 && (
              <ul className="list" style={{ background: "var(--bg)" }}>
                {p.lots.map((l) => (
                  <li key={l.id}>
                    <div className="main"><div className="title">× {n(l.quantity)}</div>
                      <div className="meta"><span className={l.state === "fresh" || l.state === "no_date" ? "" : "flag"}>{lotText(l)}</span>
                        {l.expires_on && <>, {new Date(l.expires_on + "T00:00:00").toLocaleDateString()}</>}</div></div>
                    <input type="date" aria-label="Use-by date" value={l.expires_on ?? ""} style={{ width: 150 }}
                      onChange={(e) => run(() => api.patch<Product>(`/api/lots/${l.id}`, { expires_on: e.target.value || null }))} />
                    <button className="iconbtn" aria-label="Throw away" onClick={() => run(async () => { await api.del(`/api/lots/${l.id}`); return api.get<Product[]>(`/api/households/${household.id}/stock`).then((all) => all.find((x) => x.id === p.id)); })}>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg></button>
                  </li>
                ))}
              </ul>
            )}
            <div className="row" style={{ flexWrap: "nowrap", alignItems: "flex-end" }}>
              <label style={{ width: 80 }}>Add<input value={addQty} onChange={(e) => setAddQty(e.target.value)} inputMode="decimal" /></label>
              <label style={{ flex: 1 }}>Use by (optional)<input type="date" value={addExp} onChange={(e) => setAddExp(e.target.value)} /></label>
              <button className="btn" onClick={() => run(() => api.post<Product>(`/api/products/${p.id}/lots`, { quantity: addQty, expires_on: addExp || null }))}>Add</button>
            </div>
          </>
        )}
        <form className="stack" onSubmit={p ? (e) => { e.preventDefault(); run(() => api.patch<Product>(`/api/products/${p.id}`, details)); } : create} style={{ gap: 10 }}>
          <label>Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required maxLength={120} placeholder="e.g. Semi-skimmed milk" /></label>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <label style={{ flex: 1 }}>Brand<input value={f.brand} onChange={(e) => setF({ ...f, brand: e.target.value })} maxLength={80} /></label>
            <label style={{ flex: 1 }}>Where<input value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} maxLength={40} placeholder="Fridge" /></label>
          </div>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <label style={{ flex: 1 }}>Size of one<input value={f.size_qty} onChange={(e) => setF({ ...f, size_qty: e.target.value })} inputMode="decimal" placeholder="500" /></label>
            <label style={{ flex: 1 }}>Unit<select value={f.size_unit} onChange={(e) => setF({ ...f, size_unit: e.target.value })}>
              {["item", "g", "kg", "ml", "l"].map((u) => <option key={u}>{u}</option>)}</select></label>
            <label style={{ flex: 1 }}>Restock at<input value={f.restock_below} onChange={(e) => setF({ ...f, restock_below: e.target.value })} inputMode="decimal" placeholder="1" /></label>
          </div>
          {!p && (
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <label style={{ flex: 1 }}>How many<input value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} inputMode="decimal" required /></label>
              <label style={{ flex: 2 }}>Use by (optional)<input type="date" value={f.expires_on} onChange={(e) => setF({ ...f, expires_on: e.target.value })} /></label>
            </div>
          )}
          {error && <div className="error" role="alert">{error}</div>}
          <div className="row">
            <button className="btn primary">{p ? "Save details" : "Add to pantry"}</button>
            <button type="button" className="btn quiet" onClick={() => ref.current?.close()}>Close</button>
            {p && <button type="button" className="btn danger" onClick={() => confirm(`Remove ${p.name} from the pantry?`) && run(async () => { await api.del(`/api/products/${p.id}`); ref.current?.close(); })}>Remove</button>}
          </div>
        </form>
      </div>
    </dialog>
  );
}

function Scanner({ household, onClose }: { household: Household; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const [codes, setCodes] = useState<Record<string, number>>({});
  const [live, setLive] = useState(false);
  const [manual, setManual] = useState("");
  const [msg, setMsg] = useState("");
  const [unknown, setUnknown] = useState<{ code: string; qty: number }[]>([]);
  const [naming, setNaming] = useState<{ code: string; qty: number } | null>(null);
  const add = (c: string) => setCodes((x) => ({ ...x, [c]: (x[c] ?? 0) + 1 }));
  useEffect(() => { ref.current?.showModal(); return () => stopRef.current?.(); }, []);

  const camera = async () => {
    setMsg("");
    try { stopRef.current = await startCamera(video.current!, (c) => { add(c); navigator.vibrate?.(40); }); setLive(true); }
    catch { setMsg("Couldn't open the camera. Allow camera access, or take a photo instead."); }
  };
  const photo = async (file: File) => {
    setMsg("Reading barcode…");
    const found = await decodePhoto(file);
    found.forEach(add);
    setMsg(found.length ? `Found ${found.length} barcode${found.length === 1 ? "" : "s"}.` : "No barcode found. Try a closer, sharper photo.");
  };
  const submit = async () => {
    const list = Object.entries(codes).flatMap(([c, k]) => Array(k).fill(c));
    if (!list.length) return;
    const r = await api.post<{ added: { name: string; quantity: number }[]; unknown: string[] }>(`/api/households/${household.id}/scan`, { codes: list });
    setMsg(r.added.length ? "Added " + r.added.map((a) => `${a.quantity}× ${a.name}`).join(", ") + "." : "");
    setUnknown(r.unknown.map((c) => ({ code: c, qty: codes[c] })));
    setCodes({});
  };

  return (
    <dialog ref={ref} className="sheet" onClose={(e) => { if (e.target === e.currentTarget) (() => { stopRef.current?.(); onClose(); })(); }} aria-labelledby="sc">
      <div className="sheet-body stack">
        <div className="grabber" aria-hidden="true" />
        <h2 id="sc" style={{ margin: 0 }}>Scan items</h2>
        <video ref={video} className="scan-video" playsInline muted hidden={!live} />
        <div className="row">
          {cameraAllowed() ? !live && <button className="btn primary" onClick={camera}>Use camera</button>
            : <span className="muted small">Live scanning needs a secure (https) address. Taking a photo works anywhere.</span>}
          <label className="btn">Take a photo<input type="file" accept="image/*" capture="environment" hidden onChange={(e) => e.target.files?.[0] && photo(e.target.files[0])} /></label>
        </div>
        <form className="row" style={{ flexWrap: "nowrap" }} onSubmit={(e) => { e.preventDefault(); if (manual.trim()) { add(manual.trim()); setManual(""); } }}>
          <input value={manual} onChange={(e) => setManual(e.target.value)} placeholder="Or type the numbers" inputMode="numeric" aria-label="Barcode number" />
          <button className="btn">Add</button>
        </form>
        {msg && <div className="ok" role="status">{msg}</div>}
        {Object.keys(codes).length > 0 && (
          <>
            <ul className="list" style={{ background: "var(--bg)" }}>
              {Object.entries(codes).map(([c, k]) => (
                <li key={c}><div className="main"><div className="title code">{c}</div></div>
                  <div className="row" style={{ flexWrap: "nowrap", gap: 4 }}>
                    <button className="iconbtn" aria-label="One fewer" onClick={() => setCodes((x) => { const y = { ...x }; if (y[c] > 1) y[c]--; else delete y[c]; return y; })}>−</button>
                    <span className="amt">× {k}</span>
                    <button className="iconbtn" aria-label="One more" onClick={() => add(c)}>+</button>
                  </div></li>
              ))}
            </ul>
            <button className="btn primary" onClick={submit}>Add {Object.values(codes).reduce((a, b) => a + b, 0)} to the pantry</button>
          </>
        )}
        {unknown.length > 0 && (
          <div className="callout stack" style={{ gap: 8 }}>
            <strong>New to your pantry</strong>
            {unknown.map((u) => (
              <div key={u.code} className="row"><span className="code" style={{ flex: 1 }}>{u.code} × {u.qty}</span>
                <button className="btn" onClick={() => setNaming(u)}>Name it</button>
                <button className="btn quiet" onClick={() => setUnknown(unknown.filter((x) => x.code !== u.code))}>Skip</button></div>
            ))}
          </div>
        )}
        <button className="btn quiet" onClick={() => ref.current?.close()}>Done</button>
      </div>
      {naming && <ProductSheet household={household} product={null} barcode={naming.code} quantity={naming.qty}
        onClose={() => { setUnknown((x) => x.filter((y) => y.code !== naming.code)); setNaming(null); }} />}
    </dialog>
  );
}
