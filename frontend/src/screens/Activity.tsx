import { useEffect, useState } from "react";
import { api, Household, HouseholdAccount, Tx } from "../api";
import TxList from "./TxList";

export default function Activity({ household }: { household: Household }) {
  const [q, setQ] = useState("");
  const [txs, setTxs] = useState<Tx[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});

  useEffect(() => {
    api.get<HouseholdAccount[]>(`/api/households/${household.id}/accounts`)
      .then((a) => setNames(Object.fromEntries(a.map((x) => [x.id, x.name]))));
  }, [household.id]);

  useEffect(() => {
    const t = setTimeout(() => {
      const qs = q.trim() ? `&q=${encodeURIComponent(q.trim())}` : "";
      api.get<Tx[]>(`/api/households/${household.id}/transactions?limit=200${qs}`).then(setTxs);
    }, 200);
    return () => clearTimeout(t);
  }, [household.id, q]);

  return (
    <>
      <h1>Activity</h1>
      <input type="search" placeholder="Search transactions" value={q} onChange={(e) => setQ(e.target.value)}
        aria-label="Search transactions" style={{ marginBottom: 12 }} />
      {txs === null ? <div className="muted">Loading…</div> : <TxList txs={txs} accountNames={names} />}
    </>
  );
}
