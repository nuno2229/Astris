import { Fragment } from "react";
import { isWalletMove, money, Tx } from "../api";

function dayLabel(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  const t = new Date(); t.setHours(0, 0, 0, 0);
  const diff = Math.round((+t - +d) / 864e5);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short",
    year: d.getFullYear() === t.getFullYear() ? undefined : "numeric" });
}

/** Transactions grouped under date headings. Title = clean merchant name when known. */
export default function TxList({ txs, accountNames, empty, onOpen }: {
  txs: Tx[]; accountNames?: Record<string, string>; empty?: React.ReactNode; onOpen?: (t: Tx) => void;
}) {
  if (!txs.length) return <>{empty ?? <div className="empty"><strong>Nothing here yet</strong>Import a statement from Accounts to see transactions.</div>}</>;
  const groups: [string, Tx[]][] = [];
  for (const t of txs) {
    const last = groups[groups.length - 1];
    if (last && last[0] === t.posted_on) last[1].push(t); else groups.push([t.posted_on, [t]]);
  }
  return (
    <>
      {groups.map(([day, items]) => (
        <Fragment key={day}>
          <div className="day">{dayLabel(day)}</div>
          <ul className="list">
            {items.map((t) => {
              const n = Number(t.amount);
              const acct = accountNames?.[t.account_id];
              const body = (
                <>
                  <div className="main">
                    <div className="title">{t.merchant ?? t.description}</div>
                    <div className="meta">
                      {t.needs_review ? <span className="flag">Needs a category</span> : t.category}
                      {acct && <span className="muted">{(t.category || t.needs_review) ? ", " : ""}{acct}</span>}
                    </div>
                  </div>
                  <div className={"amt" + (n > 0 ? " in" : "")}>
                    {money(n, t.currency, true)}
                    {t.original_currency && <div className="meta fx">{money(t.original_amount, t.original_currency)}</div>}
                    {t.transfer_group && <div className="meta fx">{!isWalletMove(t) ? "Transfer" : t.source === "transfer" ? "From bank" : "To wallet"}</div>}
                  </div>
                </>
              );
              return (
                <li key={t.id} className={onOpen ? "tap" : undefined}>
                  {onOpen ? <button className="rowbtn" onClick={() => onOpen(t)}>{body}</button> : body}
                </li>
              );
            })}
          </ul>
        </Fragment>
      ))}
    </>
  );
}
