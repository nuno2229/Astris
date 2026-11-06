import { useEffect, useRef, useState } from "react";
import { api, Household, money } from "../api";

interface Item { id: string; name: string; quantity: string; size_qty: string | null; size_unit: string | null; line_price: string;
  discount: string; paid: string; unit_price: string | null; unit_basis: string | null; category: string | null }
interface Cand { id: string; posted_on: string; description: string; amount: string; account: string }
interface Rec { id: string; store: string | null; purchased_on: string | null; total: string | null; currency: string; status: string;
  adds_up: boolean | null; read_by: string; warnings: string[]; transaction_id: string | null; items: Item[]; candidates: Cand[];
  documents: { id: string; filename: string; size: number }[] }

const BASIS: Record<string, string> = { "100g": "per 100 g", "100ml": "per 100 ml", item: "each" };
/** Unit prices need precision to compare packs: under £1 show pence with one decimal (8.3p), else £x.xx. */
const unit = (v: string, cur: string) => { const n = Number(v); return cur === "GBP" && n < 1 ? `${(n * 100).toFixed(1)}p` : money(n, cur); };

export default function Receipts({ household }: { household: Household }) {
  const [list, setList] = useState<Rec[] | null>(null);
  const [keep, setKeep] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const load = () => api.get<Rec[]>(`/api/households/${household.id}/receipts`).then(setList);
  useEffect(() => { load(); }, [household.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const upload = async (files: FileList) => {
    setBusy(true); setError("");
    const fd = new FormData(); [...files].forEach((f) => fd.append("files", f)); fd.append("keep_original", String(keep));
    try { const r = await api.post<Rec[]>(`/api/households/${household.id}/receipts`, fd); await load(); if (r[0]) setOpen(r[0].id); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); if (file.current) file.current.value = ""; }
  };

  return (
    <>
      <div className="panel stack">
        <input ref={file} type="file" accept="image/*,application/pdf,.txt" multiple hidden onChange={(e) => e.target.files?.length && upload(e.target.files)} />
        <button className="btn primary" disabled={busy} onClick={() => file.current?.click()}>{busy ? "Reading receipt…" : "Scan a receipt"}</button>
        <label className="check"><input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
          <span>Keep a copy of the photo <span className="muted small">(stored on your server; everyone in {household.name} can see it)</span></span></label>
        <p className="muted small" style={{ margin: 0 }}>Read on your server. You can check every line before it's saved.</p>
        {error && <div className="error" role="alert">{error}</div>}
      </div>
      {list && list.length > 0 && (
        <ul className="list" style={{ marginTop: 14 }}>
          {list.map((r) => (
            <li key={r.id} className="tap"><button className="rowbtn" onClick={() => setOpen(r.id)}>
              <div className="main"><div className="title">{r.store ?? "Receipt"}</div>
                <div className="meta">{r.purchased_on ? new Date(r.purchased_on + "T00:00:00").toLocaleDateString() : "no date"}
                  {r.status === "review" ? <span className="flag">, needs checking</span> : r.transaction_id ? ", matched to a payment" : ""}</div></div>
              <div className="amt">{r.total ? money(r.total, r.currency) : "—"}</div></button></li>
          ))}
        </ul>
      )}
      {open && <ReceiptSheet id={open} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}

function ReceiptSheet({ id, onClose }: { id: string; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [r, setR] = useState<Rec | null>(null);
  const [tx, setTx] = useState<string>("");
  const [toPantry, setToPantry] = useState(true);
  const [again, setAgain] = useState<string[]>([]);
  const [error, setError] = useState("");
  useEffect(() => { ref.current?.showModal(); api.get<Rec>(`/api/receipts/${id}`).then((x) => { setR(x); setTx(x.candidates[0]?.id ?? ""); }); }, [id]);
  const run = async (fn: () => Promise<Rec | void>) => { setError(""); try { const x = await fn(); if (x) setR(x); } catch (e) { setError((e as Error).message); } };
  if (!r) return <dialog ref={ref} className="sheet" aria-label="Receipt"><div className="sheet-body muted">Loading…</div></dialog>;
  const review = r.status === "review";

  return (
    <dialog ref={ref} className="sheet" onClose={(e) => { if (e.target === e.currentTarget) onClose(); }}
      onClick={(e) => e.target === ref.current && ref.current?.close()} aria-labelledby="rc">
      <div className="sheet-body stack">
        <div className="grabber" aria-hidden="true" />
        <div className="acct-head">
          <h2 id="rc" style={{ margin: 0 }}>{r.store ?? "Receipt"}</h2>
          <div className="acct-bal">{r.total ? money(r.total, r.currency) : "—"}</div>
        </div>
        <div className={"small " + (r.adds_up === false ? "flag" : "ok")}>
          {r.adds_up === null ? "No total found: add it so the lines can be checked." : r.adds_up ? "The items add up to the total." : "The items don't add up to the total. Check the lines below."}
        </div>
        {r.warnings.map((w) => <div key={w} className="muted small">{w}</div>)}
        {review && (
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <label style={{ flex: 1 }}>Shop<input defaultValue={r.store ?? ""} onBlur={(e) => run(() => api.patch<Rec>(`/api/receipts/${id}`, { store: e.target.value || null }))} /></label>
            <label style={{ flex: 1 }}>Date<input type="date" defaultValue={r.purchased_on ?? ""} onChange={(e) => run(() => api.patch<Rec>(`/api/receipts/${id}`, { purchased_on: e.target.value || null }))} /></label>
            <label style={{ width: 90 }}>Total<input defaultValue={r.total ?? ""} inputMode="decimal" onBlur={(e) => run(() => api.patch<Rec>(`/api/receipts/${id}`, { total: e.target.value || null }))} /></label>
          </div>
        )}
        <ul className="list" style={{ background: "var(--bg)" }}>
          {r.items.map((i) => (
            <li key={i.id} className="ritem">
              <div className="main stack" style={{ gap: 4 }}>
                {review ? <input defaultValue={i.name} aria-label="Item name" onBlur={(e) => e.target.value !== i.name && run(() => api.patch<Rec>(`/api/receipt-items/${i.id}`, { name: e.target.value }))} />
                  : <div className="title">{i.name}</div>}
                <div className="meta wrap">
                  {Number(i.quantity) !== 1 && <>× {Number(i.quantity)}, </>}
                  {Number(i.discount) < 0 && <span className="flag">saved {money(-Number(i.discount), r.currency)}, </span>}
                  {i.unit_price && <>{unit(i.unit_price, r.currency)} {BASIS[i.unit_basis ?? "item"]}</>}
                </div>
                {!review && <label className="check small"><input type="checkbox" checked={again.includes(i.id)} onChange={(e) => setAgain(e.target.checked ? [...again, i.id] : again.filter((x) => x !== i.id))} />Buy again</label>}
              </div>
              {review ? <input className="price" defaultValue={i.line_price} inputMode="decimal" aria-label="Price"
                onBlur={(e) => e.target.value !== i.line_price && run(() => api.patch<Rec>(`/api/receipt-items/${i.id}`, { line_price: e.target.value }))} />
                : <div className="amt">{money(i.paid, r.currency)}</div>}
              {review && <button className="iconbtn" aria-label={`Remove ${i.name}`} onClick={() => run(() => api.del<Rec>(`/api/receipt-items/${i.id}`))}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg></button>}
            </li>
          ))}
        </ul>
        {review && (
          <>
            <label>Which payment was this?
              <select value={tx} onChange={(e) => setTx(e.target.value)}>
                <option value="">Don't link to a payment</option>
                {r.candidates.map((c) => <option key={c.id} value={c.id}>{new Date(c.posted_on + "T00:00:00").toLocaleDateString()} {c.description} {money(c.amount, r.currency)} ({c.account})</option>)}
              </select>
            </label>
            {r.candidates.length === 0 && <span className="muted small">No matching card payment found yet. Import the statement later and it can still be linked.</span>}
            <label className="check"><input type="checkbox" checked={toPantry} onChange={(e) => setToPantry(e.target.checked)} />Add these items to the pantry</label>
            <button className="btn primary" onClick={() => run(() => api.post<Rec>(`/api/receipts/${id}/save`, { transaction_id: tx || null, to_pantry: toPantry }))}>Save receipt</button>
          </>
        )}
        {!review && again.length > 0 && (
          <button className="btn" onClick={() => run(async () => { await api.post(`/api/receipts/${id}/save`, { transaction_id: r.transaction_id, to_list: again }); setAgain([]); return api.get<Rec>(`/api/receipts/${id}`); })}>
            Add {again.length} to the shopping list</button>
        )}
        {r.documents.map((d) => <a key={d.id} className="small" href={`/api/documents/${d.id}`}>Download the original ({Math.round(d.size / 1024)} KB)</a>)}
        {error && <div className="error" role="alert">{error}</div>}
        <div className="row">
          <button className="btn quiet" onClick={() => ref.current?.close()}>Close</button>
          <button className="btn danger" onClick={() => confirm("Delete this receipt?") && run(async () => { await api.del(`/api/receipts/${id}`); ref.current?.close(); })}>Delete</button>
        </div>
      </div>
    </dialog>
  );
}
