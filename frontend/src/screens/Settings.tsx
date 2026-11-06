import { FormEvent, useEffect, useState } from "react";
import { api, Household } from "../api";
import { getThemePref, setThemePref, ThemePref } from "../theme";
import Notifications from "./Notifications";

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
    </>
  );
}
