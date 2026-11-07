import { useCallback, useEffect, useState } from "react";
import { Account, api, Household, HouseholdAccount, TransferSuggestion, Tx } from "../api";
import { TransferSheet } from "./Transfers";
import TxList from "./TxList";
import TxSheet from "./TxSheet";

export default function Activity({ household, reviewFirst }: { household: Household; reviewFirst?: boolean }) {
  const [q, setQ] = useState("");
  const [review, setReview] = useState(!!reviewFirst);
  const [txs, setTxs] = useState<Tx[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [cats, setCats] = useState<string[]>([]);
  const [wallets, setWallets] = useState<Account[]>([]);
  const [goals, setGoals] = useState<{ id: string; name: string; currency: string }[]>([]);
  const [open, setOpen] = useState<Tx | null>(null);
  const [toast, setToast] = useState("");
  const [transfers, setTransfers] = useState<TransferSuggestion[]>([]);
  const [showTransfers, setShowTransfers] = useState(false);

  useEffect(() => {
    api.get<HouseholdAccount[]>(`/api/households/${household.id}/accounts`)
      .then((a) => setNames(Object.fromEntries(a.map((x) => [x.id, x.name]))));
    api.get<string[]>(`/api/categories?household_id=${household.id}`).then(setCats);
    api.get<Account[]>("/api/accounts").then((a) => setWallets(a.filter((x) => x.type === "cash")));
    api.get<{ id: string; name: string; currency: string }[]>(`/api/households/${household.id}/goals`).then(setGoals);
  }, [household.id]);

  const load = useCallback(() => {
    const qs = (q.trim() ? `&q=${encodeURIComponent(q.trim())}` : "") + (review ? "&review=true" : "");
    return api.get<Tx[]>(`/api/households/${household.id}/transactions?limit=200${qs}`).then(setTxs);
  }, [household.id, q, review]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  const loadTransfers = useCallback(() => api.get<TransferSuggestion[]>(`/api/transfers/suggestions?household_id=${household.id}`)
    .then(setTransfers).catch(() => setTransfers([])), [household.id]);
  useEffect(() => { loadTransfers(); }, [loadTransfers]);

  return (
    <>
      <h1>Activity</h1>
      <div className="stack" style={{ gap: 10, marginBottom: 14 }}>
        <input type="search" placeholder="Search by shop or description" value={q} onChange={(e) => setQ(e.target.value)}
          aria-label="Search transactions" />
        <div className="seg" role="group" aria-label="Filter">
          <button aria-pressed={!review} onClick={() => setReview(false)}>All</button>
          <button aria-pressed={review} onClick={() => setReview(true)}>Needs a category</button>
        </div>
      </div>
      {toast && <div className="callout" role="status" style={{ marginBottom: 12 }}>{toast}</div>}
      {transfers.length > 0 && (
        <button className="callout rowbtn" style={{ marginBottom: 12, textAlign: "left", width: "100%" }} onClick={() => setShowTransfers(true)}>
          <strong>{transfers.length === 1 ? "1 possible transfer" : `${transfers.length} possible transfers`} between your accounts</strong>
          <div className="small muted">Link them so moving money isn't counted as spending.</div>
        </button>
      )}
      {showTransfers && (
        <TransferSheet items={transfers} onClose={() => { setShowTransfers(false); loadTransfers(); }}
          onChanged={(_, m) => { setToast(m); load(); }} />
      )}
      {txs === null ? <div className="muted">Loading…</div> : (
        <TxList txs={txs} accountNames={names} onOpen={setOpen}
          empty={review ? <div className="empty"><strong>All sorted</strong>Every transaction has a category.</div> : undefined} />
      )}
      {open && (
        <TxSheet tx={open} categories={cats} wallets={wallets} goals={goals} onClose={() => setOpen(null)}
          onChanged={(m) => { setOpen(null); setToast(m); load(); loadTransfers(); }}
          onSaved={(t, n) => {
            setOpen(null);
            setToast(`Saved as ${t.category}.` + (n ? ` Also updated ${n} other transaction${n === 1 ? "" : "s"} from ${t.merchant ?? "this merchant"}.` : ""));
            load();
          }} />
      )}
    </>
  );
}
