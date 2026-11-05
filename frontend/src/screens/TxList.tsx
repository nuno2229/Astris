import { Fragment } from "react";
import { money, Tx } from "../api";

function dayLabel(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  const t = new Date(); t.setHours(0, 0, 0, 0);
  const diff = Math.round((+t - +d) / 864e5);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short",
    year: d.getFullYear() === t.getFullYear() ? undefined : "numeric" });
}

/** Transactions grouped under date headings. Money out = text ink with a minus; money in = sky blue with a plus. */
export default function TxList({ txs, accountNames, empty }: {
  txs: Tx[]; accountNames?: Record<string, string>; empty?: React.ReactNode;
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
              return (
                <li key={t.id}>
                  <div className="main">
                    <div className="title">{t.description}</div>
                    {accountNames?.[t.account_id] && <div className="meta">{accountNames[t.account_id]}</div>}
                  </div>
                  <div className={"amt" + (n > 0 ? " in" : "")}>{money(n, t.currency, true)}</div>
                </li>
              );
            })}
          </ul>
        </Fragment>
      ))}
    </>
  );
}
