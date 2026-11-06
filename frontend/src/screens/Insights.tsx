import { useEffect, useState } from "react";
import { api, Household, money } from "../api";

interface Ins { currency: string; month: string; total: string;
  categories: { category: string; spent: string; typical: string | null; change: string | null; budget: string | null; weekly_avg: string }[];
  trend: { month: string; spent: string; received: string }[];
  top_shops: { name: string; spent: string; count: number }[]; by_member: { name: string; spent: string; count: number }[];
  seasonal: { category: string; month: string; last_year: string; typical: string; ratio: string }[];
  excluded: { name: string; spent: string; count: number }[]; unconverted: string[];
  consumption: { product: string; unit: "l" | "kg" | "items"; weekly_avg: string; total: string; purchases: number; weeks: string[] }[] }

const monthName = (k: string, short = false) => new Date(k + "-01T00:00:00").toLocaleDateString(undefined, { month: short ? "short" : "long", ...(short ? {} : { year: "numeric" }) });
const shift = (k: string, n: number) => { const [y, m] = k.split("-").map(Number); const d = new Date(y, m - 1 + n, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; };

export default function Insights({ household }: { household: Household }) {
  const now = new Date(); const cur = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const [month, setMonth] = useState(cur);
  const [d, setD] = useState<Ins | null>(null);
  const [sel, setSel] = useState<number | null>(null);
  useEffect(() => { setD(null); api.get<Ins>(`/api/households/${household.id}/insights?month=${month}`).then(setD); }, [household.id, month]);
  if (!d) return <div className="muted">Loading…</div>;
  const c = d.currency;
  const cats = d.categories.filter((x) => Number(x.spent) > 0 || Number(x.typical ?? 0) > 0);
  const catMax = Math.max(...cats.map((x) => Math.max(Number(x.spent), Number(x.typical ?? 0), Number(x.budget ?? 0))), 1);
  const tMax = Math.max(...d.trend.map((t) => Number(t.spent)), 1);
  const s = sel !== null ? d.trend[sel] : null;
  // a month in progress is compared with what's usual *by this day*, not with a whole month
  const isNow = month === cur;
  const frac = isNow ? now.getDate() / new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate() : 1;

  return (
    <>
      <h1>Insights</h1>
      <div className="row month-nav">
        <button className="iconbtn" aria-label="Previous month" onClick={() => setMonth(shift(month, -1))}>‹</button>
        <strong>{monthName(month)}</strong>
        <button className="iconbtn" aria-label="Next month" disabled={month >= cur} onClick={() => setMonth(shift(month, 1))}>›</button>
      </div>
      <p className="note" style={{ marginTop: 4 }}>{money(d.total, c)} spent from accounts in {household.name}'s totals.
        {d.unconverted.length > 0 && <> Not counted: {d.unconverted.join(", ")} (no exchange rate).</>}</p>

      {d.seasonal.length > 0 && d.seasonal.map((x) => (
        <div key={x.category + x.month} className="callout" style={{ marginTop: 12 }}>
          <strong>Heads-up for {monthName(x.month, true)}:</strong> last year {x.category} was {Number(x.ratio).toFixed(1)}× your usual
          ({money(x.last_year, c)} against about {money(x.typical, c)}).
        </div>
      ))}

      <h2>Where it went</h2>
      {cats.length === 0 ? <div className="empty"><strong>No spending this month</strong>Import a statement to see the breakdown.</div> : (
        <ul className="list catlist">
          {cats.map((x) => {
            const usual = x.typical !== null ? Number(x.typical) * frac : null;
            const ch = usual ? Number(x.spent) / usual - 1 : null;
            return (
              <li key={x.category}>
                <div className="main stack" style={{ gap: 6 }}>
                  <div className="acct-head"><span className="title">{x.category}</span><span className="amt">{money(x.spent, c)}</span></div>
                  <div className="cbar" role="img" aria-label={`${x.category}: ${money(x.spent, c)}${x.typical ? `, usually ${money(x.typical, c)}` : ""}${x.budget ? `, budget ${money(x.budget, c)}` : ""}`}>
                    <div className="cbar-fill" style={{ width: `${(Number(x.spent) / catMax) * 100}%` }} />
                    {usual !== null && <div className="cbar-typical" style={{ left: `${(usual / catMax) * 100}%` }} />}
                    {x.budget !== null && <div className="cbar-budget" style={{ left: `${(Number(x.budget) / catMax) * 100}%` }} />}
                  </div>
                  <div className="small muted">
                    {ch === null ? "No earlier months to compare" : Math.abs(ch) < 0.1 ? (isNow ? "About usual for this point in the month" : "About usual")
                      : <span className={ch > 0 ? "flag" : ""}>{ch > 0 ? "▲" : "▼"} {Math.round(Math.abs(ch) * 100)}% vs usual{isNow ? " by now" : ""} ({money(usual, c)})</span>}
                    {x.budget !== null && <>, budget {money(x.budget, c)}</>}
                    {Number(x.weekly_avg) > 0 && <>. About {money(x.weekly_avg, c)} a week</>}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <p className="muted small" style={{ margin: "6px 4px 0" }}>Thin line: your usual {isNow ? "spending by this day of the month" : "month"} (from the 3 months before). Dashed: budget.</p>

      <h2>Last 12 months</h2>
      <div className="sky" style={{ padding: "14px 10px 8px" }}>
        <div className="trend" role="img" aria-label="Spending per month, last 12 months">
          {d.trend.map((t, i) => (
            <button key={t.month} className={"tbar" + (sel === i ? " sel" : "") + (t.month === month ? " now" : "")}
              onClick={() => setSel(sel === i ? null : i)} onMouseEnter={() => setSel(i)} aria-label={`${monthName(t.month)}: ${money(t.spent, c)}`}>
              <span style={{ height: `${Math.max((Number(t.spent) / tMax) * 100, Number(t.spent) > 0 ? 2 : 0)}%` }} />
              <em>{monthName(t.month, true).slice(0, 1)}</em>
            </button>
          ))}
        </div>
        <div className="readout">{s ? <><span>{monthName(s.month)}</span><span><strong>{money(s.spent, c)}</strong> <span className="muted">out, {money(s.received, c)} in</span></span></>
          : <span className="muted">Tap a month to see it.</span>}</div>
        <details className="table-view"><summary>Show as a table</summary>
          <table><tbody>{d.trend.map((t) => <tr key={t.month}><td>{monthName(t.month)}</td><td>{money(t.spent, c)}</td></tr>)}</tbody></table>
        </details>
      </div>

      <div className="two-col">
        <section><h2>Top shops</h2><Ranked rows={d.top_shops} c={c} empty="No shops yet" /></section>
        <section><h2>Who spent it</h2><Ranked rows={d.by_member} c={c} empty="No spending yet" /></section>
      </div>
      {d.excluded.length > 0 && (
        <section><h2>Kept separate</h2>
          <p className="muted small" style={{ margin: "0 4px 8px" }}>Accounts set to “Separate”: not in the totals above.</p>
          <Ranked rows={d.excluded} c={c} empty="" /></section>
      )}
      <h2>What you go through</h2>
      {d.consumption.length === 0 ? (
        <div className="empty">Scan a few receipts (Shopping → Receipts) and you'll see things like how much milk you get through a week.</div>
      ) : (
        <ul className="list">
          {d.consumption.map((x) => {
            const u = { l: "L", kg: "kg", items: "" }[x.unit];
            const amt = (v: string | number) => `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 })}${u ? " " + u : ""}`;
            const max = Math.max(...x.weeks.map(Number), 0.0001);
            return (
              <li key={x.product + x.unit}>
                <div className="main stack" style={{ gap: 6 }}>
                  <div className="acct-head"><span className="title">{x.product}</span>
                    <span className="amt">{amt(x.weekly_avg)}{x.unit === "items" ? "" : ""} a week</span></div>
                  <div className="spark" role="img" aria-label={`${x.product}, last 12 weeks: ${x.weeks.map(amt).join(", ")}`}>
                    {x.weeks.map((w, i) => <span key={i} style={{ height: `${Math.max((Number(w) / max) * 100, Number(w) > 0 ? 8 : 2)}%` }} className={Number(w) > 0 ? "" : "zero"} />)}
                  </div>
                  <span className="meta">{amt(x.total)} over {x.purchases} purchases, from your receipts</span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <p className="muted small" style={{ margin: "6px 4px 0" }}>Last 12 weeks, newest on the right. Same product from different shops is counted together.</p>
    </>
  );
}

function Ranked({ rows, c, empty }: { rows: { name: string; spent: string; count: number }[]; c: string; empty: string }) {
  if (!rows.length) return <div className="empty">{empty}</div>;
  const max = Math.max(...rows.map((r) => Number(r.spent)), 1);
  return (
    <ul className="list">
      {rows.map((r) => (
        <li key={r.name}>
          <div className="main stack" style={{ gap: 4 }}>
            <div className="acct-head"><span className="title">{r.name}</span><span className="amt">{money(r.spent, c)}</span></div>
            <div className="meter" style={{ height: 6 }}><div className="meter-fill" style={{ width: `${(Number(r.spent) / max) * 100}%` }} /></div>
            <span className="meta">{r.count} payment{r.count === 1 ? "" : "s"}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}
