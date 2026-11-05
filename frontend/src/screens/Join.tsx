import { useState } from "react";
import { api, Household } from "../api";
import type { Ctx } from "../App";
import { StarMark } from "../icons";
import { go } from "../router";

export default function Join({ code, ctx }: { code: string; ctx: Ctx }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const accept = async () => {
    setBusy(true); setError("");
    try {
      const h = await api.post<Household>("/api/invites/accept", { code: decodeURIComponent(code) });
      await ctx.reloadHouseholds(h.id); go("");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <div className="auth">
      <div className="wordmark"><StarMark className="" /><span>Astris</span></div>
      <div className="panel stack">
        <h1 style={{ margin: 0 }}>Join a household</h1>
        <p className="muted small" style={{ margin: 0 }}>
          Signed in as <strong>{ctx.me.email}</strong>. Joining lets members of this household see the accounts you choose to share with it.
        </p>
        {error && <div className="error" role="alert">{error}</div>}
        <button className="btn primary block" onClick={accept} disabled={busy || !code}>Accept invite</button>
        <button className="btn quiet block" onClick={() => go("")}>Cancel</button>
      </div>
    </div>
  );
}
