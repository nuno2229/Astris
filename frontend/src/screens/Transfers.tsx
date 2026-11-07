import { useEffect, useRef, useState } from "react";
import { api, money, TransferSuggestion } from "../api";

const day = (d: string) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** Possible transfers between the household's own accounts: link (not spending) or "not a transfer". */
export function TransferSheet({ items, onClose, onChanged }: {
  items: TransferSuggestion[]; onClose: () => void; onChanged: (left: number, message: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [list, setList] = useState(items);
  const [error, setError] = useState("");
  useEffect(() => { ref.current?.showModal(); }, []);
  const act = async (s: TransferSuggestion, link: boolean) => {
    setError("");
    try {
      await api.post(link ? "/api/transfers" : "/api/transfers/dismiss", { out_id: s.out.id, in_id: s.into.id });
      // linking uses up both lines, so drop any other pairing that shared one of them
      const left = list.filter((x) => x !== s && (!link || (x.out.id !== s.out.id && x.into.id !== s.into.id)));
      setList(left);
      onChanged(left.length, link ? "Linked as a transfer. It no longer counts as spending." : "Marked as not a transfer.");
      if (!left.length) ref.current?.close();
    } catch (e) { setError((e as Error).message); }
  };
  return (
    <dialog ref={ref} className="sheet" onClose={(e) => { if (e.target === e.currentTarget) onClose(); }}
      onClick={(e) => e.target === ref.current && ref.current?.close()} aria-labelledby="tr-title">
      <div className="sheet-body stack">
        <div className="grabber" aria-hidden="true" />
        <h2 id="tr-title" style={{ margin: 0 }}>Money moved between your accounts?</h2>
        <p className="muted small" style={{ margin: 0 }}>The same amount left one account and arrived in another. Linked transfers aren't counted as spending or income.</p>
        {error && <div className="error" role="alert">{error}</div>}
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {list.map((s) => (
            <li key={s.out.id + s.into.id} className="stack" style={{ gap: 8, padding: "12px 0", borderTop: "1px solid var(--line)" }}>
              <div className="acct-head">
                <div style={{ minWidth: 0 }}>
                  <div><strong>{s.out.account}</strong> to <strong>{s.into.account}</strong></div>
                  <div className="muted small">{s.out.description}, {day(s.out.posted_on)}
                    {s.into.posted_on !== s.out.posted_on && <>, arrived {day(s.into.posted_on)}</>}</div>
                </div>
                <div className="acct-bal">{money(s.amount, s.currency)}</div>
              </div>
              <div className="row">
                <button className="btn primary" onClick={() => act(s, true)}>Link as transfer</button>
                <button className="btn quiet" onClick={() => act(s, false)}>Not a transfer</button>
              </div>
            </li>
          ))}
        </ul>
        <button className="btn" onClick={() => ref.current?.close()}>Done</button>
      </div>
    </dialog>
  );
}
