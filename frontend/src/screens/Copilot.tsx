import { FormEvent, useEffect, useRef, useState } from "react";
import { api, Household } from "../api";

interface Msg { who: "me" | "astris"; text: string; citations?: { label: string }[]; link?: string | null;
  action?: { id: string; summary: string; state: "pending" | "done" | "cancelled" }; via?: "ai" | "rules" }

const STARTERS = ["How much did we spend this week?", "What's running low?", "Any bills due?", "Who spent the most this month?"];

/** Assistant (E15): available on every screen. The conversation stays in this browser tab only. */
export default function Copilot({ household }: { household: Household }) {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDialogElement>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) ref.current?.showModal(); }, [open]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs, busy]);
  useEffect(() => { setMsgs([]); }, [household.id]);

  const send = async (q: string) => {
    if (!q.trim() || busy) return;
    setMsgs((m) => [...m, { who: "me", text: q }]); setText(""); setBusy(true);
    try {
      const r = await api.post<{ answer: string; citations: { label: string }[]; link: string | null;
        action: { id: string; summary: string } | null; understood_by: "ai" | "rules" }>("/api/copilot/ask", { household_id: household.id, message: q });
      setMsgs((m) => [...m, { who: "astris", text: r.answer, citations: r.citations, link: r.link, via: r.understood_by,
        action: r.action ? { ...r.action, state: "pending" } : undefined }]);
    } catch (e) { setMsgs((m) => [...m, { who: "astris", text: (e as Error).message }]); }
    finally { setBusy(false); }
  };
  const decide = async (i: number, yes: boolean) => {
    const a = msgs[i].action!;
    const upd = (state: "done" | "cancelled", extra?: Msg) => setMsgs((m) => [...m.map((x, k) => k === i ? { ...x, action: { ...a, state } } : x), ...(extra ? [extra] : [])]);
    if (!yes) { await api.del(`/api/copilot/actions/${a.id}`).catch(() => {}); upd("cancelled"); return; }
    try { const r = await api.post<{ answer: string; link: string | null }>(`/api/copilot/actions/${a.id}/confirm`); upd("done", { who: "astris", text: r.answer, link: r.link }); }
    catch (e) { upd("cancelled", { who: "astris", text: (e as Error).message }); }
  };
  const submit = (e: FormEvent) => { e.preventDefault(); send(text); };

  return (
    <>
      <button className="fab" aria-label="Ask Astris" onClick={() => setOpen(true)}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z" /></svg>
      </button>
      {open && (
        <dialog ref={ref} className="sheet chat" aria-labelledby="chat-title"
          onClose={(e) => { if (e.target === e.currentTarget) setOpen(false); }} onClick={(e) => e.target === ref.current && ref.current?.close()}>
          <div className="sheet-body chat-body">
            <div className="grabber" aria-hidden="true" />
            <div className="acct-head"><h2 id="chat-title" style={{ margin: 0 }}>Ask Astris</h2>
              <button className="iconbtn" aria-label="Close" onClick={() => ref.current?.close()}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg></button></div>
            <div className="chat-log" aria-live="polite">
              {msgs.length === 0 && (
                <div className="stack" style={{ gap: 10 }}>
                  <p className="muted small" style={{ margin: 0 }}>Ask about {household.name}'s spending, bills, budgets, goals or pantry.
                    Answers come straight from your data. I'll always ask before changing anything.</p>
                  <div className="chips">{STARTERS.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}</div>
                </div>
              )}
              {msgs.map((m, i) => (
                <div key={i} className={"bubble " + m.who}>
                  <div>{m.text}</div>
                  {m.citations && m.citations.length > 0 && (
                    <details className="sources"><summary>Sources</summary><ul>{m.citations.map((c, k) => <li key={k}>{c.label}</li>)}</ul></details>
                  )}
                  {m.action && (
                    <div className="action-card">
                      <strong>{m.action.summary}</strong>
                      {m.action.state === "pending" ? (
                        <div className="row"><button className="btn primary" onClick={() => decide(i, true)}>Confirm</button>
                          <button className="btn quiet" onClick={() => decide(i, false)}>Cancel</button></div>
                      ) : <span className="muted small">{m.action.state === "done" ? "Done" : "Cancelled"}</span>}
                    </div>
                  )}
                  {m.link && <a className="small" href={m.link} onClick={() => ref.current?.close()}>Open</a>}
                </div>
              ))}
              {busy && <div className="bubble astris muted">Thinking…</div>}
              <div ref={end} />
            </div>
            <form className="chat-input" onSubmit={submit}>
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask anything…" maxLength={500} aria-label="Message" autoFocus />
              <button className="btn primary" disabled={!text.trim() || busy}>Send</button>
            </form>
          </div>
        </dialog>
      )}
    </>
  );
}
