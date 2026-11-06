import { useCallback, useEffect, useState } from "react";
import { Account, api, Household, HouseholdAccount, Tx } from "../api";
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

  useEffect(() => {
    api.get<HouseholdAccount[]>(`/api/households/${household.id}/accounts`)
      .then((a) => setNames(Object.fromEntries(a.map((x) => [x.id, x.name]))));
    api.get<string[]>("/api/categories").then(setCats);
    api.get<Account[]>("/api/accounts").then((a) => setWallets(a.filter((x) => x.type === "cash")));
    api.get<{ id: string; name: string; currency: string }[]>(`/api/households/${household.id}/goals`).then(setGoals);
  }, [household.id]);

  const load = useCallback(() => {
    const qs = (q.trim() ? `&q=${encodeURIComponent(q.trim())}` : "") + (review ? "&review=true" : "");
    return api.get<Tx[]>(`/api/households/${household.id}/transactions?limit=200${qs}`).then(setTxs);
  }, [household.id, q, review]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);

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
      {txs === null ? <div className="muted">Loading…</div> : (
        <TxList txs={txs} accountNames={names} onOpen={setOpen}
          empty={review ? <div className="empty"><strong>All sorted</strong>Every transaction has a category.</div> : undefined} />
      )}
      {open && (
        <TxSheet tx={open} categories={cats} wallets={wallets} goals={goals} onClose={() => setOpen(null)}
          onChanged={(m) => { setOpen(null); setToast(m); load(); }}
          onSaved={(t, n) => {
            setOpen(null);
            setToast(`Saved as ${t.category}.` + (n ? ` Also updated ${n} other transaction${n === 1 ? "" : "s"} from ${t.merchant ?? "this merchant"}.` : ""));
            load();
          }} />
      )}
    </>
  );
}
