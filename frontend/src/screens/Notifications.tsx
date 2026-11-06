import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";

type Kind = "discord" | "telegram" | "ntfy";
interface Channel { id: string; kind: Kind; label: string; hint: string; uses_internet: boolean; include_details: boolean;
  enabled: boolean; last_sent_at: string | null; last_error: string | null }
interface Prefs { renew_days: 1 | 3 | 7; renewals: boolean; trials: boolean; cancelled_charges: boolean; missed_payments: boolean;
  budgets: boolean; daily_summary: boolean; stock: boolean; shopping: boolean }

const KIND_LABEL: Record<Kind, string> = { discord: "Discord", telegram: "Telegram", ntfy: "ntfy" };

export default function Notifications() {
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState<Record<string, string>>({});
  const load = () => api.get<{ prefs: Prefs; channels: Channel[] }>("/api/me/notifications").then((d) => { setPrefs(d.prefs); setChannels(d.channels); });
  useEffect(() => { load(); }, []);

  const savePrefs = (p: Prefs) => { setPrefs(p); api.put("/api/me/notifications/prefs", p); };
  const patch = async (id: string, body: object) => { await api.patch(`/api/me/notifications/channels/${id}`, body); load(); };
  const test = async (id: string) => {
    setMsg({ ...msg, [id]: "Sending…" });
    const r = await api.post<{ ok: boolean; message: string }>(`/api/me/notifications/channels/${id}/test`);
    setMsg({ ...msg, [id]: r.message }); load();
  };
  if (!prefs) return null;
  const toggle = (k: keyof Omit<Prefs, "renew_days">, label: string) => (
    <label className="check"><input type="checkbox" checked={prefs[k]} onChange={(e) => savePrefs({ ...prefs, [k]: e.target.checked })} />{label}</label>
  );

  return (
    <>
      <h2>Notifications</h2>
      <div className="panel stack">
        <p className="muted small">Get a message when something needs you. Just for you: other household members set their own.</p>
        {channels.map((c) => (
          <div key={c.id} className="channel stack" style={{ gap: 8 }}>
            <div className="acct-head">
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{c.label}</div>
                <div className="muted small">{c.hint}{c.uses_internet ? "" : ", stays on your network"}</div>
              </div>
              {c.uses_internet && <span className="pill">Uses internet</span>}
            </div>
            <label className="check"><input type="checkbox" checked={c.enabled} onChange={(e) => patch(c.id, { enabled: e.target.checked })} />On</label>
            <label className="check"><input type="checkbox" checked={c.include_details} onChange={(e) => patch(c.id, { include_details: e.target.checked })} />
              <span>Include names and amounts <span className="muted small">(off: “A subscription renews tomorrow”)</span></span></label>
            {c.last_error && <div className="error">{c.last_error}</div>}
            {msg[c.id] && <div className="ok" role="status">{msg[c.id]}</div>}
            <div className="row">
              <button className="btn" onClick={() => test(c.id)}>Send a test</button>
              <button className="btn danger" onClick={() => confirm(`Remove ${c.label}?`) && api.del(`/api/me/notifications/channels/${c.id}`).then(load)}>Remove</button>
            </div>
          </div>
        ))}
        {adding ? <AddChannel onDone={() => { setAdding(false); load(); }} onCancel={() => setAdding(false)} />
          : <button className="btn" onClick={() => setAdding(true)}>+ Add Discord, Telegram or ntfy</button>}
        <div className="stack" style={{ gap: 10, marginTop: 6 }}>
          <strong>Tell me about</strong>
          {toggle("renewals", "Subscriptions about to renew")}
          {prefs.renewals && (
            <div className="seg" role="group" aria-label="How early">
              {([1, 3, 7] as const).map((d) => (
                <button key={d} aria-pressed={prefs.renew_days === d} onClick={() => savePrefs({ ...prefs, renew_days: d })}>
                  {d === 1 ? "1 day before" : `${d} days before`}</button>
              ))}
            </div>
          )}
          {toggle("trials", "Free trials ending or turning paid")}
          {toggle("cancelled_charges", "Charges after I cancelled")}
          {toggle("missed_payments", "Regular payments that didn't arrive")}
          {toggle("budgets", "Budgets nearly used or over")}
          {toggle("stock", "Pantry items about to expire")}
          {toggle("shopping", "Someone adds to a shopping list")}
          {toggle("daily_summary", "A short summary of yesterday's spending, each morning")}
        </div>
      </div>
    </>
  );
}

function AddChannel({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [kind, setKind] = useState<Kind>("ntfy");
  const [f, setF] = useState<Record<string, string>>({ server: "", topic: "", token: "", webhook_url: "", bot_token: "", chat_id: "" });
  const [internet, setInternet] = useState(false);
  const [error, setError] = useState("");
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const external = kind !== "ntfy";

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setError("");
    const config = kind === "discord" ? { webhook_url: f.webhook_url }
      : kind === "telegram" ? { bot_token: f.bot_token, chat_id: f.chat_id } : { server: f.server, topic: f.topic, token: f.token || null };
    try { await api.post("/api/me/notifications/channels", { kind, config, allow_internet: internet, label: KIND_LABEL[kind] }); onDone(); }
    catch (err) { setError((err as Error).message); }
  };

  return (
    <form className="stack" onSubmit={submit} style={{ gap: 12 }}>
      <div className="seg" role="group" aria-label="Service">
        {(["ntfy", "discord", "telegram"] as const).map((k) => (
          <button type="button" key={k} aria-pressed={kind === k} onClick={() => { setKind(k); setError(""); }}>{KIND_LABEL[k]}</button>
        ))}
      </div>
      {kind === "ntfy" && <>
        <p className="muted small">ntfy can run on your own server, so alerts never leave home. Install the ntfy app and subscribe to the same topic.</p>
        <label>Server address<input value={f.server} onChange={set("server")} placeholder="http://192.168.1.10:8090" inputMode="url" autoCapitalize="off" /></label>
        <label>Topic<input value={f.topic} onChange={set("topic")} required placeholder="astris-home" autoCapitalize="off" /></label>
        <label>Access token (optional)<input value={f.token} onChange={set("token")} type="password" autoComplete="off" /></label>
      </>}
      {kind === "discord" && <>
        <p className="muted small">In Discord: Server settings › Integrations › Webhooks › New webhook › Copy URL.</p>
        <label>Webhook URL<input value={f.webhook_url} onChange={set("webhook_url")} required type="password" autoComplete="off" placeholder="https://discord.com/api/webhooks/…" /></label>
      </>}
      {kind === "telegram" && <>
        <p className="muted small">Create a bot with @BotFather, send it a message, then use your chat ID.</p>
        <label>Bot token<input value={f.bot_token} onChange={set("bot_token")} required type="password" autoComplete="off" /></label>
        <label>Chat ID<input value={f.chat_id} onChange={set("chat_id")} required inputMode="numeric" /></label>
      </>}
      <label className="check">
        <input type="checkbox" checked={internet} onChange={(e) => setInternet(e.target.checked)} required={external} />
        <span>{external ? `Send alerts through ${KIND_LABEL[kind]} over the internet` : "Allow an ntfy server outside my home network"}</span>
      </label>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="row"><button className="btn primary">Add</button><button type="button" className="btn quiet" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}
