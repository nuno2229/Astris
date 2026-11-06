import { useEffect, useRef, useState } from "react";
import { Account, api, money, shortDate } from "../api";
import { go } from "../router";

interface Preview {
  id: string; filename: string; account_id: string; ok: boolean; error: string | null; format: string | null;
  rows_total: number; rows_new: number; rows_duplicate: number; rows_duplicate_in_batch: number;
  date_from: string | null; date_to: string | null; warnings: string[];
  balance: { checked_rows: number; mismatched_rows: number; gap_expected: string | null; gap_found: string | null; gap_before: string | null } | null;
  mapping: { headers: string[]; columns: Record<string, string | null>; day_first: boolean | null } | null;
  sample: { posted_on: string; description: string; amount: string; running_balance: string | null }[];
}
interface Run { id: string; files: number; rows_new: number; created_at?: string | null; rows_present?: number | null;
  details?: { filename: string; rows_new: number; rows_duplicate: number }[] }

const ROLE_LABEL: Record<string, string> = { date: "Date", description: "Description", amount: "Amount (+/−)",
  out: "Money out", in: "Money in", balance: "Balance" };

export default function ImportWizard({ accountId }: { accountId?: string }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [target, setTarget] = useState(accountId ?? "");
  const [previews, setPreviews] = useState<Preview[]>([]);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Run | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get<Account[]>("/api/accounts").then((a) => {
      const banks = a.filter((x) => x.type !== "cash"); setAccounts(banks);
      if (!target && banks[0]) setTarget(banks[0].id);
    });
    api.get<Run[]>("/api/import-runs").then(setRuns);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const name = (id: string) => accounts.find((a) => a.id === id)?.name ?? "account";
  const cur = (id: string) => accounts.find((a) => a.id === id)?.currency ?? "GBP";

  const upload = async (files: FileList) => {
    setBusy(true); setError(""); setDone(null);
    const fd = new FormData(); [...files].forEach((f) => fd.append("files", f));
    try { setPreviews([...previews, ...await api.post<Preview[]>(`/api/accounts/${target}/imports/preview`, fd)]); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };
  const adjust = async (p: Preview, body: object) => {
    const np = await api.patch<Preview>(`/api/import-previews/${p.id}`, body);
    setPreviews(previews.map((x) => (x.id === p.id ? np : x)));
  };
  const remove = async (p: Preview) => { await api.del(`/api/import-previews/${p.id}`); setPreviews(previews.filter((x) => x.id !== p.id)); };
  const ready = previews.filter((p) => p.ok);
  const totalNew = ready.reduce((t, p) => t + p.rows_new, 0);
  const commit = async () => {
    setBusy(true); setError("");
    try {
      setDone(await api.post<Run>("/api/import-runs", { preview_ids: ready.map((p) => p.id) }));
      setPreviews(previews.filter((p) => !p.ok));
      api.get<Run[]>("/api/import-runs").then(setRuns);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const undo = async (id: string) => {
    if (!confirm("Remove everything this import added?")) return;
    await api.del(`/api/import-runs/${id}`);
    if (done?.id === id) setDone(null);
    api.get<Run[]>("/api/import-runs").then(setRuns);
  };

  return (
    <>
      <h1>Import statements</h1>
      <div className="panel stack">
        <label>Into
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
        <input ref={fileRef} type="file" multiple hidden accept=".csv,.ofx,.qfx,.txt,.sta,.mt940,.940,.pdf,image/*,text/csv,application/pdf"
          onChange={(e) => e.target.files?.length && upload(e.target.files)} />
        <button className="btn" disabled={busy || !target} onClick={() => fileRef.current?.click()}>
          {busy && !previews.length ? "Reading files…" : previews.length ? "Add more files" : "Choose files"}</button>
        <p className="muted small">CSV, OFX, MT940, PDF or photos. Nothing is saved until you confirm.</p>
      </div>
      {error && <div className="error" role="alert" style={{ marginTop: 12 }}>{error}</div>}

      {previews.map((p) => (
        <section key={p.id} className="panel stack preview" style={{ marginTop: 14 }}>
          <div className="acct-head">
            <div style={{ minWidth: 0 }}>
              <div className="acct-name" style={{ overflowWrap: "anywhere" }}>{p.filename}</div>
              <div className="muted small">{p.ok ? `${p.format?.toUpperCase()} into ${name(p.account_id)}, ${shortDate(p.date_from!)} to ${shortDate(p.date_to!)}` : "Couldn't read this file"}</div>
            </div>
            <button className="iconbtn" aria-label={`Remove ${p.filename}`} onClick={() => remove(p)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg></button>
          </div>
          {!p.ok && <div className="error">{p.error}</div>}
          {p.ok && (
            <div className="facts-row">
              <span><strong>{p.rows_new}</strong> new</span>
              {p.rows_duplicate > 0 && <span><strong>{p.rows_duplicate}</strong> already imported</span>}
              {p.rows_duplicate_in_batch > 0 && <span><strong>{p.rows_duplicate_in_batch}</strong> also in another file</span>}
            </div>
          )}
          {p.balance && (
            p.balance.mismatched_rows === 0 && !p.balance.gap_expected
              ? <div className="ok">Balances add up{p.balance.checked_rows ? ` across ${p.balance.checked_rows + 1} rows` : ""}.</div>
              : <div className="callout stack" style={{ gap: 4 }}>
                  {p.balance.mismatched_rows > 0 && <span>{p.balance.mismatched_rows} row{p.balance.mismatched_rows === 1 ? "'s" : "s'"} balance doesn't match the amount. Check the in/out columns.</span>}
                  {p.balance.gap_expected && <span>This statement starts at {money(p.balance.gap_found, cur(p.account_id))}, but the account was at {money(p.balance.gap_expected, cur(p.account_id))} before {shortDate(p.balance.gap_before!)}. A statement may be missing.</span>}
                </div>
          )}
          {p.warnings.map((w) => <div key={w} className="muted small">{w}</div>)}
          {p.ok && p.sample.length > 0 && (
            <details className="table-view">
              <summary>See the first rows</summary>
              <table><tbody>{p.sample.map((r, i) => (
                <tr key={i}><td>{shortDate(r.posted_on)}</td><td>{r.description}</td><td>{money(r.amount, cur(p.account_id), true)}</td></tr>
              ))}</tbody></table>
            </details>
          )}
          <details className="table-view">
            <summary>Adjust</summary>
            <div className="stack" style={{ gap: 10, padding: "4px 8px 8px" }}>
              <label>Import into
                <select value={p.account_id} onChange={(e) => adjust(p, { account_id: e.target.value })}>
                  {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </label>
              {(p.mapping?.day_first != null) && (
                <div className="seg" role="group" aria-label="Date order">
                  <button aria-pressed={p.mapping!.day_first === true} onClick={() => adjust(p, { day_first: true })}>Day/month</button>
                  <button aria-pressed={p.mapping!.day_first === false} onClick={() => adjust(p, { day_first: false })}>Month/day</button>
                </div>
              )}
              {(p.mapping || (!p.ok && p.error?.includes("Headers seen"))) && <ColumnPicker p={p} onApply={(mapping) => adjust(p, { mapping })} />}
            </div>
          </details>
        </section>
      ))}

      {ready.length > 0 && (
        <div className="commit-bar">
          <button className="btn primary block" disabled={busy || totalNew === 0} onClick={commit}>
            {totalNew === 0 ? "Nothing new to import" : `Import ${totalNew} new transaction${totalNew === 1 ? "" : "s"}`}</button>
          <button className="btn quiet block" onClick={() => { previews.forEach(remove); go("accounts"); }}>Cancel</button>
        </div>
      )}

      {done && (
        <div className="callout stack" style={{ marginTop: 14 }} role="status">
          <strong>Imported {done.rows_new} transaction{done.rows_new === 1 ? "" : "s"} from {done.files} file{done.files === 1 ? "" : "s"}.</strong>
          <div className="row"><a className="btn" href="#/activity">See activity</a><button className="btn danger" onClick={() => undo(done.id)}>Undo this import</button></div>
        </div>
      )}

      {runs.length > 0 && (
        <>
          <h2>Recent imports</h2>
          <ul className="list">
            {runs.map((r) => (
              <li key={r.id}>
                <div className="main"><div className="title">{r.files} file{r.files === 1 ? "" : "s"}, {r.rows_new} new</div>
                  <div className="meta">{r.created_at ? new Date(r.created_at).toLocaleString() : ""}</div></div>
                {(r.rows_present ?? 0) > 0 && <button className="btn quiet" onClick={() => undo(r.id)}>Undo</button>}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

function ColumnPicker({ p, onApply }: { p: Preview; onApply: (m: Record<string, string | null>) => void }) {
  const headers = p.mapping?.headers ?? (p.error?.split("Headers seen: ")[1]?.split(", ") ?? []);
  const [m, setM] = useState<Record<string, string | null>>(p.mapping?.columns ?? {});
  return (
    <div className="stack" style={{ gap: 8 }}>
      <span className="muted small">Which column is which? Use Amount, or Money out + Money in.</span>
      {Object.keys(ROLE_LABEL).map((role) => (
        <label key={role} className="maprow">{ROLE_LABEL[role]}
          <select value={m[role] ?? ""} onChange={(e) => setM({ ...m, [role]: e.target.value || null })}>
            <option value="">Not in this file</option>
            {headers.map((h) => <option key={h} value={h}>{h}</option>)}
          </select>
        </label>
      ))}
      <button className="btn" onClick={() => onApply(m)}>Apply columns</button>
    </div>
  );
}
