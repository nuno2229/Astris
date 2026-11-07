import { useEffect, useState } from "react";
import { api } from "../api";

interface Server { is_admin: boolean; version: string; registration_open: boolean; update_check: boolean; latest_version: string | null;
  latest_url: string | null; update_available: boolean; checked_at: string | null;
  backups: { name: string; size: number; created_at: string; encrypted: boolean }[]; backups_encrypted: boolean; backup_location: string }

export function useServer() {
  const [s, setS] = useState<Server | null>(null);
  useEffect(() => { api.get<Server>("/api/server").then(setS).catch(() => {}); }, []);
  return [s, setS] as const;
}

export default function ServerSettings() {
  const [s, setS] = useServer();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  if (!s) return null;
  const put = async (body: object) => setS(await api.put<Server>("/api/server", body));
  const backupNow = async () => {
    setBusy(true); setMsg("");
    try { const r = await api.post<{ name: string }>("/api/server/backups"); setMsg(`Backup saved: ${r.name}`); setS(await api.get<Server>("/api/server")); }
    catch (e) { setMsg((e as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <>
      <h2>Your data</h2>
      <div className="panel stack">
        <p className="muted small" style={{ margin: 0 }}>Download everything you can see in Astris, as JSON files in a zip.</p>
        <a className="btn" href="/api/me/export">Download my data</a>
        <DeleteMe />
      </div>
      {s.is_admin && (
        <>
          <h2>Server</h2>
          <div className="panel stack">
            <span className="muted small">You're the server admin.</span>
            {s.update_available && (
              <div className="callout"><strong>Astris {s.latest_version} is available.</strong> On Unraid, open the Docker tab and apply the
                update. A backup is taken automatically first. {s.latest_url && <a href={s.latest_url} target="_blank" rel="noreferrer">What's new</a>}</div>
            )}
            <label className="check"><input type="checkbox" checked={s.registration_open} onChange={(e) => put({ registration_open: e.target.checked })} />
              <span>Allow new sign-ups <span className="muted small">(turn off once everyone has an account)</span></span></label>
            <label className="check"><input type="checkbox" checked={s.update_check} onChange={(e) => put({ update_check: e.target.checked })} />
              <span>Tell me when an update is available <span className="pill">Uses internet</span>
                <span className="muted small"> Checks GitHub once a day; nothing about you is sent.</span></span></label>
            {s.update_check && s.checked_at && <span className="muted small">Last checked {new Date(s.checked_at).toLocaleString()}: {s.update_available ? `${s.latest_version} available` : "you're up to date"}.</span>}
            <strong style={{ marginTop: 6 }}>Backups</strong>
            <span className="muted small">Made every night, and automatically before an update changes the database.
              {s.backups_encrypted ? " Encrypted with your backup passphrase." : " Not encrypted: set BACKUP_PASSPHRASE in the container to encrypt them."}</span>
            <button className="btn" disabled={busy} onClick={backupNow}>{busy ? "Backing up…" : "Back up now"}</button>
            {msg && <div className="ok" role="status">{msg}</div>}
            {s.backups.length > 0 && (
              <ul className="list" style={{ background: "var(--bg)" }}>
                {s.backups.slice(0, 10).map((b) => (
                  <li key={b.name}><div className="main"><div className="title code">{b.name}</div>
                    <div className="meta">{new Date(b.created_at).toLocaleString()}, {Math.max(1, Math.round(b.size / 1024))} KB{b.encrypted ? ", encrypted" : ""}</div></div>
                  </li>
                ))}
              </ul>
            )}
            <span className="muted small">Backups contain everyone's data, so they stay on the server and can't be downloaded here.
              Find them in <span className="code">{s.backup_location}</span> (your Unraid backups share). Restoring replaces all data and is
              done from the server: see the README (“Rolling back”).</span>
          </div>
        </>
      )}
    </>
  );
}

/** Delete my account and data (P-13). Password + typing DELETE; explains what happens first. */
function DeleteMe() {
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const [confirmText, setConfirmText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (!open) return <button className="btn danger" style={{ alignSelf: "flex-start" }} onClick={() => setOpen(true)}>Delete my account…</button>;
  const go = async () => {
    setBusy(true); setError("");
    try { await api.post("/api/me/delete", { password: pw, confirm: confirmText }); location.hash = ""; location.reload(); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  };
  return (
    <div className="callout stack" style={{ gap: 8 }}>
      <strong>Delete your account and data</strong>
      <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
        <li>Your accounts, their transactions and any kept statement files are deleted.</li>
        <li>Your goal contributions, personal budgets, lists, rules and notification channels are deleted.</li>
        <li>Households where you're the only member are deleted. If you own a shared household, an admin (or the longest-standing member) becomes the owner.</li>
        <li>Household history keeps its entries, shown as “Former member”.</li>
      </ul>
      <span className="small">Download your data first if you want a copy. This can't be undone.</span>
      <label>Your password<input type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
      <label>Type DELETE to confirm<input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} autoComplete="off" /></label>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="row">
        <button className="btn danger" disabled={busy || !pw || confirmText !== "DELETE"} onClick={go}>Delete my account</button>
        <button className="btn quiet" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}
