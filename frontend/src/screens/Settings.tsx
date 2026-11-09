import { MealieSettings } from "./Meals";
import { FormEvent, useEffect, useState } from "react";
import { api, Household } from "../api";
import { getThemePref, setThemePref, ThemePref } from "../theme";
import Notifications from "./Notifications";
import ServerSettings from "./ServerSettings";

interface SettingsData {
  llm_enabled: boolean; llm_base_url: string | null; llm_model: string | null;
  llm_allow_internet: boolean; llm_is_local: boolean | null; can_edit: boolean;
}
interface TestResult { ok: boolean; message: string; models: string[] }

export default function Settings({ household }: { household: Household }) {
  const [theme, setTheme] = useState<ThemePref>(getThemePref());
  const pickTheme = (t: ThemePref) => { setTheme(t); setThemePref(t); };

  return (
    <>
      <h1>Settings</h1>
      <h2>Appearance</h2>
      <div className="panel stack">
        <div className="seg" role="group" aria-label="Theme">
          {(["dark", "light", "system"] as const).map((t) => (
            <button key={t} aria-pressed={theme === t} onClick={() => pickTheme(t)}>
              {t === "system" ? "Match device" : t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
        <span className="muted small">Saved on this device only.</span>
      </div>
      <Notifications />
      <AiSettings household={household} />
      <MealieSettings household={household} />
      <Rates household={household} />
      <ServerSettings />
      <About />
    </>
  );
}

function AiSettings({ household }: { household: Household }) {
  const [s, setS] = useState<SettingsData | null>(null);
  const [url, setUrl] = useState("");
  const [model, setModel] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [allowNet, setAllowNet] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const fill = (d: SettingsData) => {
    setS(d); setUrl(d.llm_base_url ?? ""); setModel(d.llm_model ?? "");
    setEnabled(d.llm_enabled); setAllowNet(d.llm_allow_internet);
  };
  useEffect(() => {
    api.get<SettingsData>(`/api/households/${household.id}/settings`).then(fill).catch((e) => setError(e.message));
  }, [household.id]);

  const runTest = async () => {
    setBusy(true); setError(""); setTest(null);
    try { setTest(await api.post<TestResult>(`/api/households/${household.id}/settings/llm/test`, { llm_base_url: url, llm_allow_internet: allowNet })); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const save = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError(""); setSaved(false);
    try {
      fill(await api.put<SettingsData>(`/api/households/${household.id}/settings`,
        { llm_enabled: enabled, llm_base_url: url || null, llm_model: model || null, llm_allow_internet: allowNet }));
      setSaved(true);
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };

  if (!s) return error ? <div className="error">{error}</div> : null;
  const ro = !s.can_edit;

  return (
    <>
      <h2>AI assistant: local LLM ({household.name})</h2>
      <form className="panel stack" onSubmit={save}>
        <p className="muted small" style={{ margin: 0 }}>
          Connect your own llama.cpp server for AI categorisation and the in-app assistant. Without it, the app uses
          simple rules instead and everything still works.
          {ro && <> Only the household owner can change this.</>}
        </p>
        <label>Server address
          <input value={url} onChange={(e) => { setUrl(e.target.value); setTest(null); }} disabled={ro}
            placeholder="http://192.168.1.50:8080" inputMode="url" autoCapitalize="off" autoCorrect="off" />
        </label>
        {!ro && (
          <div className="row">
            <button type="button" className="btn" onClick={runTest} disabled={busy || !url}>Test connection</button>
          </div>
        )}
        {test && <div className={test.ok ? "ok" : "error"} role="status">{test.message}</div>}
        <label>Model
          {test?.ok && test.models.length > 0 && !ro ? (
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="">Server default</option>
              {test.models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          ) : (
            <input value={model} onChange={(e) => setModel(e.target.value)} disabled={ro} placeholder="Server default" />
          )}
        </label>
        <label className="check">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} disabled={ro} />
          Use AI for this household
        </label>
        {s.llm_is_local === false && (
          <div className="callout"><strong>Uses internet.</strong> The saved address is outside your home network.</div>
        )}
        <label className="check">
          <input type="checkbox" checked={allowNet} onChange={(e) => setAllowNet(e.target.checked)} disabled={ro} />
          <span>Allow an address outside my home network <span className="muted small">(household data would leave home)</span></span>
        </label>
        {error && <div className="error" role="alert">{error}</div>}
        {saved && <div className="ok" role="status">Saved.</div>}
        {!ro && <button className="btn primary" disabled={busy}>Save</button>}
      </form>
    </>
  );
}

function About() {
  const [v, setV] = useState<{ version: string; db: string } | null>(null);
  useEffect(() => { api.get<{ version: string; db: string }>("/api/health").then(setV).catch(() => {}); }, []);
  return (
    <>
      <h2>About</h2>
      <div className="panel stack" style={{ gap: 6 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span>Astris</span>
          <span className="pill" style={{ fontSize: 14, color: "var(--text)" }} data-testid="version">{v?.version ?? "…"}</span>
        </div>
        <div className="muted small">Server database: {v ? (v.db === "ok" ? "connected" : "unreachable") : "…"}</div>
        <a className="small" href="https://github.com/nuno2229/Astris/releases" target="_blank" rel="noreferrer">Release notes</a>
      </div>
      <PhoneCamera />
    </>
  );
}

interface Rate { from_currency: string; to_currency: string; rate: string; as_of: string; source: string }

function Rates({ household }: { household: Household }) {
  const [rates, setRates] = useState<Rate[]>([]);
  const [auto, setAuto] = useState<{ enabled: boolean; as_of: string | null; currencies: number; can_edit: boolean } | null>(null);
  const [f, setF] = useState({ from_currency: "EUR", to_currency: "GBP", rate: "" });
  const [error, setError] = useState("");
  const can = household.role !== "member";
  const load = () => Promise.all([
    api.get<Rate[]>(`/api/households/${household.id}/fx-rates`).then(setRates),
    api.get<typeof auto>(`/api/households/${household.id}/fx-auto`).then(setAuto),
  ]);
  const toggleAuto = async (enabled: boolean) => {
    setAuto(await api.put<typeof auto>(`/api/households/${household.id}/fx-auto`, { enabled }));
    if (enabled) setTimeout(load, 3000);  // the server fetches in the background
  };
  useEffect(() => { load(); }, [household.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = async (e: FormEvent) => {
    e.preventDefault(); setError("");
    try { await api.put(`/api/households/${household.id}/fx-rates`, f); setF({ ...f, rate: "" }); load(); }
    catch (err) { setError((err as Error).message); }
  };
  return (
    <>
      <h2>Exchange rates ({household.name})</h2>
      <div className="panel stack">
        <p className="muted small" style={{ margin: 0 }}>Used to show totals in each person's home currency.</p>
        {auto && (
          <div className="stack" style={{ gap: 6 }}>
            <label className="check">
              <input type="checkbox" checked={auto.enabled} disabled={!auto.can_edit} onChange={(e) => toggleAuto(e.target.checked)} />
              <span>Update daily from the European Central Bank <span className="pill">Uses internet</span></span>
            </label>
            <span className="muted small">
              {auto.enabled
                ? auto.as_of ? `${auto.currencies} currencies, rates from ${new Date(auto.as_of + "T00:00:00").toLocaleDateString()}. Kept on your server, so they still work offline.` : "Fetching the first rates…"
                : "Only a public rates file is downloaded; nothing about you is sent."}
              {!auto.can_edit && " Only the household owner can change this."}
            </span>
          </div>
        )}
        <strong className="small" style={{ marginTop: 6 }}>Your own rates{auto?.enabled ? " (override the ECB)" : ""}</strong>
        {!can && <span className="muted small">Only the owner or an admin can change these.</span>}
        {rates.length > 0 && (
          <ul className="list" style={{ background: "var(--bg)" }}>
            {rates.map((r) => (
              <li key={r.from_currency + r.to_currency}>
                <div className="main"><div className="title">1 {r.from_currency} = {Number(r.rate)} {r.to_currency}</div>
                  <div className="meta">Set {new Date(r.as_of + "T00:00:00").toLocaleDateString()}</div></div>
                {can && <button className="iconbtn" aria-label={`Remove ${r.from_currency} rate`}
                  onClick={() => api.del(`/api/households/${household.id}/fx-rates/${r.from_currency}/${r.to_currency}`).then(load)}>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg></button>}
              </li>
            ))}
          </ul>
        )}
        {can && (
          <form className="row" onSubmit={save} style={{ flexWrap: "nowrap", alignItems: "flex-end" }}>
            <label style={{ width: 80 }}>1<input value={f.from_currency} onChange={(e) => setF({ ...f, from_currency: e.target.value.toUpperCase() })} maxLength={3} pattern="[A-Za-z]{3}" required /></label>
            <label style={{ flex: 1 }}>equals<input value={f.rate} onChange={(e) => setF({ ...f, rate: e.target.value })} inputMode="decimal" pattern="\d+(\.\d{1,8})?" required placeholder="0.85" /></label>
            <label style={{ width: 80 }}>&nbsp;<input value={f.to_currency} onChange={(e) => setF({ ...f, to_currency: e.target.value.toUpperCase() })} maxLength={3} pattern="[A-Za-z]{3}" required /></label>
            <button className="btn">Save</button>
          </form>
        )}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
    </>
  );
}

/** P-6: how to get live camera scanning on phones (needs https). */
function PhoneCamera() {
  const [selfSigned, setSelfSigned] = useState(false);
  useEffect(() => { api.get<{ self_signed: boolean }>("/api/https").then((r) => setSelfSigned(r.self_signed)).catch(() => {}); }, []);
  const secure = window.isSecureContext;
  return (
    <>
      <h2>Camera scanning on phones</h2>
      <div className="panel stack" style={{ gap: 8 }}>
        {secure && !selfSigned && <span className="small">This address is secure, so live barcode scanning works here.</span>}
        {selfSigned && (
          <>
            <span className="small">This server makes its own certificate. Install it once on each phone so it trusts this address.</span>
            <a className="btn" style={{ alignSelf: "flex-start" }} href="/ca.crt">Download the certificate</a>
            <span className="muted small">iPhone: open it from Settings, install, then General → About → Certificate Trust Settings → turn it on.
              Android: Settings → Security → Encryption &amp; credentials → Install a certificate → CA certificate.</span>
          </>
        )}
        {!secure && !selfSigned && <span className="muted small">Live scanning needs a secure (https) address. Taking a photo of a barcode works anyway.
          Your server admin can turn on HTTPS in the Unraid template (see README).</span>}
      </div>
    </>
  );
}
