import { useEffect, useRef, useState } from "react";
import { Account, api, isWalletMove, money, RefundCandidate, TransferSuggestion, Tx } from "../api";

const SOURCE: Record<string, string> = {
  rule: "Matched from the merchant name", llm: "Suggested by your AI assistant",
  user: "Set by you", user_rule: "From a rule you set",
};

/** Bottom sheet to see a transaction and fix its category. Uses <dialog> for focus trapping + Esc. */
export default function TxSheet({ tx, categories, wallets = [], goals = [], onClose, onSaved, onChanged }: {
  tx: Tx; categories: string[]; wallets?: Account[]; goals?: { id: string; name: string; currency: string }[];
  onClose: () => void; onSaved: (t: Tx, alsoUpdated: number) => void;
  onChanged?: (message: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [cat, setCat] = useState(tx.category ?? "");
  const [remember, setRemember] = useState(true);
  const [reason, setReason] = useState("");
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
      const r = await api.patch<{ transaction: Tx; also_updated: number }>(`/api/transactions/${tx.id}`, { category: cat, remember, reason: reason || null });
      onSaved(r.transaction, r.also_updated);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  };

  return (
    <dialog ref={ref} className="sheet" onClose={(e) => { if (e.target === e.currentTarget) onClose(); }} onClick={(e) => e.target === ref.current && ref.current?.close()}
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
        {tx.refund_of ? <div className="muted small">A refund, so it comes off your {tx.category ?? ""} spending.</div>
          : tx.category_source && <div className="muted small">{SOURCE[tx.category_source]}{tx.needs_review ? ", but it's a guess" : ""}.</div>}
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
            <label>Why? (optional, kept in the history)<input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} /></label>
            {error && <div className="error" role="alert">{error}</div>}
            <div className="row">
              <button className="btn primary" disabled={!cat || busy} onClick={save}>Save category</button>
              <button className="btn quiet" onClick={() => ref.current?.close()}>Cancel</button>
            </div>
            <CashActions tx={tx} wallets={wallets} onDone={(m) => { ref.current?.close(); onChanged?.(m); }} />
            <GoalAction tx={tx} goals={goals} onDone={(m) => { ref.current?.close(); onChanged?.(m); }} />
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
      <TransferAction tx={tx} run={run} />
      <RefundAction tx={tx} run={run} />
      {tx.transfer_group && isWalletMove(tx) && (
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

function GoalAction({ tx, goals, onDone }: { tx: Tx; goals: { id: string; name: string; currency: string }[]; onDone: (m: string) => void }) {
  const usable = goals.filter((g) => g.currency === tx.currency);
  const [goal, setGoal] = useState(usable[0]?.id ?? "");
  const [always, setAlways] = useState(true);
  const [error, setError] = useState("");
  if (Number(tx.amount) >= 0 || tx.transfer_group || !usable.length) return null;
  const go = async () => {
    setError("");
    try { await api.post(`/api/goals/${goal}/from-transaction`, { transaction_id: tx.id, always });
      onDone(`Counted toward ${usable.find((g) => g.id === goal)?.name}.`); }
    catch (e) { setError((e as Error).message); }
  };
  return (
    <div className="panel stack" style={{ background: "var(--bg)", gap: 8 }}>
      <strong>Saving towards something?</strong>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        {usable.length > 1 && <select value={goal} onChange={(e) => setGoal(e.target.value)} aria-label="Goal">
          {usable.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</select>}
        <button className="btn" onClick={go}>Count towards {usable.length === 1 ? usable[0].name : "goal"}</button>
      </div>
      <label className="check"><input type="checkbox" checked={always} onChange={(e) => setAlways(e.target.checked)} />
        <span>Also count future transfers to {tx.merchant ?? tx.description}</span></label>
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}

function TransferAction({ tx, run }: { tx: Tx; run: (fn: () => Promise<unknown>, m: string) => void }) {
  const [matches, setMatches] = useState<TransferSuggestion[]>([]);
  useEffect(() => {
    if (!tx.transfer_group) api.get<TransferSuggestion[]>(`/api/transfers/suggestions?tx=${tx.id}`).then(setMatches).catch(() => setMatches([]));
  }, [tx.id, tx.transfer_group]);
  if (tx.transfer_group) {
    if (isWalletMove(tx)) return null;
    return (
      <div className="panel stack" style={{ background: "var(--bg)", gap: 8 }}>
        <span className="small">Money moved between your accounts, so it isn't counted as spending or income.</span>
        <button className="btn" onClick={() => run(() => api.del(`/api/transfers/${tx.transfer_group}`), "No longer counted as a transfer.")}>Not a transfer</button>
      </div>
    );
  }
  if (!matches.length) return null;
  return (
    <div className="panel stack" style={{ background: "var(--bg)", gap: 8 }}>
      <strong>Moved between your accounts?</strong>
      {matches.slice(0, 3).map((m) => {
        const other = m.out.id === tx.id ? `arrived in ${m.into.account}` : `came from ${m.out.account}`;
        return (
          <div key={m.out.id + m.into.id} className="row" style={{ justifyContent: "space-between" }}>
            <span className="small">The same amount {other} on {new Date((m.out.id === tx.id ? m.into : m.out).posted_on + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" })}.</span>
            <button className="btn" onClick={() => run(() => api.post("/api/transfers", { out_id: m.out.id, in_id: m.into.id }), "Linked as a transfer. It no longer counts as spending.")}>Link as transfer</button>
          </div>
        );
      })}
    </div>
  );
}

const shortDay = (d: string) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** Money back from a shop: linked to the purchase, it reduces that category's spending instead of counting as income (P-2). */
function RefundAction({ tx, run }: { tx: Tx; run: (fn: () => Promise<unknown>, m: string) => void }) {
  const [cands, setCands] = useState<RefundCandidate[]>([]);
  const [pick, setPick] = useState("");
  const moneyIn = Number(tx.amount) > 0 && !tx.transfer_group;
  useEffect(() => {
    if (moneyIn) api.get<RefundCandidate[]>(`/api/transactions/${tx.id}/refund-candidates`)
      .then((c) => { setCands(c); setPick(c[0]?.id ?? ""); }).catch(() => setCands([]));
  }, [tx.id, moneyIn]);
  if (!moneyIn) return null;
  if (tx.refund_of) {
    const o = cands.find((c) => c.id === tx.refund_of);
    return (
      <div className="panel stack" style={{ background: "var(--bg)", gap: 8 }}>
        <span className="small">Refund{o ? ` for ${o.name} on ${shortDay(o.posted_on)} (${money(o.amount, tx.currency)})` : ""}. It lowers your {tx.category ?? ""} spending instead of counting as income.</span>
        <button className="btn" onClick={() => run(() => api.del(`/api/transactions/${tx.id}/refund`), "Counted as money in, not a refund.")}>Not a refund</button>
      </div>
    );
  }
  if (!cands.length) return null;
  return (
    <div className="panel stack" style={{ background: "var(--bg)", gap: 8 }}>
      <strong>Money back for a purchase?</strong>
      <span className="muted small">Link it and it comes off that purchase's category instead of counting as income.</span>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        {cands.length > 1 ? (
          <select value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Original purchase">
            {cands.map((c) => <option key={c.id} value={c.id}>{c.name}, {shortDay(c.posted_on)}, {money(c.amount, tx.currency)}</option>)}
          </select>
        ) : <span className="small" style={{ flex: 1 }}>{cands[0].name}, {shortDay(cands[0].posted_on)}, {money(cands[0].amount, tx.currency)}</span>}
        <button className="btn" onClick={() => run(() => api.post(`/api/transactions/${tx.id}/refund`, { original_id: pick }), "Linked as a refund.")}>Link refund</button>
      </div>
    </div>
  );
}
