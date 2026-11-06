import { useEffect, useRef, useState } from "react";
import { Account, api, money, Tx } from "../api";

const SOURCE: Record<string, string> = {
  rule: "Matched from the merchant name", llm: "Suggested by your AI assistant",
  user: "Set by you", user_rule: "From a rule you set",
};

/** Bottom sheet to see a transaction and fix its category. Uses <dialog> for focus trapping + Esc. */
export default function TxSheet({ tx, categories, wallets = [], onClose, onSaved, onChanged }: {
  tx: Tx; categories: string[]; wallets?: Account[]; onClose: () => void; onSaved: (t: Tx, alsoUpdated: number) => void;
  onChanged?: (message: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [cat, setCat] = useState(tx.category ?? "");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { ref.current?.showModal(); }, []);
  const n = Number(tx.amount);
  const name = tx.merchant ?? tx.description;
  // what a remembered rule actually matches: the cleaned merchant, without store numbers or refs
  const ruleName = tx.merchant ?? (tx.merchant_key ? tx.merchant_key.replace(/\b\w/g, (c) => c.toUpperCase()) : name);

  const save = async () => {
    setBusy(true); setError("");
    try {
      const r = await api.patch<{ transaction: Tx; also_updated: number }>(`/api/transactions/${tx.id}`, { category: cat, remember });
      onSaved(r.transaction, r.also_updated);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  };

  return (
    <dialog ref={ref} className="sheet" onClose={onClose} onClick={(e) => e.target === ref.current && ref.current?.close()}
      aria-labelledby="sheet-title">
      <div className="sheet-body stack">
        <div className="grabber" aria-hidden="true" />
        <div className="acct-head">
          <div style={{ minWidth: 0 }}>
            <h2 id="sheet-title" style={{ margin: 0 }}>{name}</h2>
            <div className="muted small">{new Date(tx.posted_on + "T00:00:00").toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</div>
          </div>
          <div className={"acct-bal" + (n > 0 ? " in" : "")}>{money(n, tx.currency, true)}</div>
        </div>
        {tx.merchant && <div className="muted small">On your statement: {tx.description}</div>}
        {tx.original_currency && <div className="muted small">Paid {money(tx.original_amount, tx.original_currency)} abroad
          ({(Math.abs(Number(tx.amount)) / Math.abs(Number(tx.original_amount))).toFixed(4)} {tx.currency} per {tx.original_currency})</div>}
        {tx.category_source && <div className="muted small">{SOURCE[tx.category_source]}{tx.needs_review ? ", but it's a guess" : ""}.</div>}
        {tx.editable ? (
          <>
            <div className="chips" role="radiogroup" aria-label="Category">
              {categories.map((c) => (
                <button key={c} role="radio" aria-checked={cat === c} className="chip" onClick={() => setCat(c)}>{c}</button>
              ))}
            </div>
            <label className="check">
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
              <span>Always use this for {ruleName}</span>
            </label>
            {error && <div className="error" role="alert">{error}</div>}
            <div className="row">
              <button className="btn primary" disabled={!cat || busy} onClick={save}>Save category</button>
              <button className="btn quiet" onClick={() => ref.current?.close()}>Cancel</button>
            </div>
            <CashActions tx={tx} wallets={wallets} onDone={(m) => { ref.current?.close(); onChanged?.(m); }} />
          </>
        ) : (
          <>
            <p className="muted small">Only the account owner can change this category.</p>
            <button className="btn" onClick={() => ref.current?.close()}>Close</button>
          </>
        )}
      </div>
    </dialog>
  );
}

function CashActions({ tx, wallets, onDone }: { tx: Tx; wallets: Account[]; onDone: (m: string) => void }) {
  const usable = wallets.filter((w) => w.id !== tx.account_id);
  const [wallet, setWallet] = useState(usable[0]?.id ?? "");
  const [error, setError] = useState("");
  const run = async (fn: () => Promise<unknown>, m: string) => { setError(""); try { await fn(); onDone(m); } catch (e) { setError((e as Error).message); } };
  const isWithdrawal = Number(tx.amount) < 0 && !tx.transfer_group && tx.source !== "transfer" && !wallets.some((w) => w.id === tx.account_id);
  return (
    <>
      {tx.transfer_group && (
        <div className="panel stack" style={{ background: "var(--bg)", gap: 8 }}>
          <span className="small">{tx.source === "transfer" ? "Cash taken out of your bank account." : "Moved into your cash wallet, so it isn't counted as spending twice."}</span>
          <button className="btn" onClick={() => run(() => api.del(`/api/transfers/${tx.transfer_group}`), "Move undone.")}>Undo the move</button>
        </div>
      )}
      {isWithdrawal && tx.category === "Cash" && usable.length > 0 && (
        <div className="panel stack" style={{ background: "var(--bg)", gap: 8 }}>
          <strong>Took out cash?</strong>
          <span className="muted small">Move it into a cash wallet and track what you spend from there.</span>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            {usable.length > 1 && (
              <select value={wallet} onChange={(e) => setWallet(e.target.value)} aria-label="Wallet">
                {usable.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            )}
            <button className="btn" onClick={() => run(() => api.post(`/api/transactions/${tx.id}/move-to-wallet`, { wallet_id: wallet }), "Moved into your cash wallet.")}>
              Move into {usable.length === 1 ? usable[0].name : "wallet"}</button>
          </div>
        </div>
      )}
      {(tx.source === "manual" || tx.source === "adjustment") && (
        <button className="btn danger" onClick={() => confirm("Delete this entry?") && run(() => api.del(`/api/transactions/${tx.id}`), "Entry deleted.")}>Delete this entry</button>
      )}
      {error && <div className="error" role="alert">{error}</div>}
    </>
  );
}
