import { money, shortDate, Tx } from "../api";

export default function TxList({ txs, accountNames }: { txs: Tx[]; accountNames?: Record<string, string> }) {
  if (!txs.length) return <div className="notice muted">No transactions yet. Import a statement from Accounts.</div>;
  return (
    <ul className="list">
      {txs.map((t) => {
        const n = Number(t.amount);
        return (
          <li key={t.id}>
            <div className="main">
              <div className="title">{t.description}</div>
              <div className="meta">{shortDate(t.posted_on)}{accountNames?.[t.account_id] ? " · " + accountNames[t.account_id] : ""}</div>
            </div>
            <div className={"amt " + (n < 0 ? "out" : "in")}>{money(n, t.currency, true)}</div>
          </li>
        );
      })}
    </ul>
  );
}
