import { Fragment, useEffect, useState } from "react";
import { api, Household } from "../api";

interface Entry { id: number; at: string; actor: string | null; is_me: boolean; action: string; summary: string;
  reason: string | null; account_name: string | null; household_name: string | null; reversed: boolean;
  is_reversal: boolean; can_undo: boolean }
interface Chain { label: string; entries: number; intact: boolean; broken_id: number | null; head: string | null }

export default function History({ household }: { household: Household }) {
  const [scope, setScope] = useState<"all" | "household">("all");
  const [items, setItems] = useState<Entry[] | null>(null);
  const [chains, setChains] = useState<Chain[] | null>(null);
  const [note, setNote] = useState("");
  const load = () => {
    api.get<Entry[]>(`/api/audit?limit=100${scope === "household" ? `&household_id=${household.id}` : ""}`).then(setItems);
    api.get<Chain[]>("/api/audit/verify").then(setChains);
  };
  useEffect(load, [scope, household.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const undo = async (e: Entry) => {
    const reason = prompt(`Undo "${e.summary}"? Add a note for the history (optional):`);
    if (reason === null) return;
    try { await api.post(`/api/audit/${e.id}/undo`, { reason: reason || null }); setNote("Undone."); load(); }
    catch (err) { setNote((err as Error).message); }
  };
  const broken = chains?.filter((c) => !c.intact) ?? [];
  const days: [string, Entry[]][] = [];
  for (const e of items ?? []) {
    const d = new Date(e.at).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
    const last = days[days.length - 1];
    if (last && last[0] === d) last[1].push(e); else days.push([d, [e]]);
  }

  return (
    <>
      <h1>History</h1>
      {chains && (broken.length === 0
        ? <p className="ok" style={{ marginTop: -8 }}>History intact: {chains.reduce((t, c) => t + c.entries, 0)} entries checked, none altered.</p>
        : <div className="callout alert-critical" role="alert"><strong>History was altered.</strong> {broken.map((c) => c.label).join(", ")}: an entry was changed or removed outside the app.</div>)}
      {household.role !== "member" && (
        <div className="seg" role="group" aria-label="Show" style={{ margin: "12px 0 6px" }}>
          <button aria-pressed={scope === "all"} onClick={() => setScope("all")}>Everything I can see</button>
          <button aria-pressed={scope === "household"} onClick={() => setScope("household")}>{household.name} only</button>
        </div>
      )}
      {note && <div className="callout" role="status" style={{ margin: "10px 0" }}>{note}</div>}
      {items === null ? <div className="muted">Loading…</div> : items.length === 0 ? (
        <div className="empty"><strong>Nothing yet</strong>Changes to your accounts and household show up here.</div>
      ) : days.map(([d, list]) => (
        <Fragment key={d}>
          <div className="day">{d}</div>
          <ul className="list">
            {list.map((e) => (
              <li key={e.id}>
                <div className="main">
                  <div className="title wrap">{e.summary}</div>
                  <div className="meta wrap">
                    {e.is_me ? "You" : e.actor ?? "Someone"}, {new Date(e.at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
                    {e.account_name ? `, ${e.account_name}` : e.household_name ? `, ${e.household_name}` : ""}
                    {e.reason && <>: “{e.reason}”</>}
                    {e.reversed && <span className="flag">, undone</span>}
                  </div>
                </div>
                {e.can_undo && <button className="btn quiet" onClick={() => undo(e)}>Undo</button>}
              </li>
            ))}
          </ul>
        </Fragment>
      ))}
    </>
  );
}
