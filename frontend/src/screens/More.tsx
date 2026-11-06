import type { Ctx } from "../App";

const LINKS = [
  ["accounts", "Accounts", "Bank accounts, cards, cash and imports"],
  ["budgets", "Budgets", "Limits and warnings"],
  ["goals", "Savings goals", "Who's saved what toward each goal"],
  ["insights", "Insights", "Where the money goes, trends, seasons"],
  ["household", "Household", "Members and invites"],
  ["history", "History", "Every change, with undo"],
  ["settings", "Settings", "Theme, notifications, AI, exchange rates"],
] as const;

export default function More({ ctx }: { ctx: Ctx }) {
  return (
    <>
      <h1>More</h1>
      <ul className="list">
        {LINKS.map(([path, title, sub]) => (
          <li key={path} className="tap"><a className="rowbtn" href={`#/${path}`}>
            <div className="main"><div className="title">{title}</div><div className="meta">{sub}</div></div>
            <span aria-hidden="true" className="muted">›</span></a></li>
        ))}
      </ul>
      <button className="btn quiet block" style={{ marginTop: 16 }} onClick={ctx.signOut}>Sign out</button>
    </>
  );
}
