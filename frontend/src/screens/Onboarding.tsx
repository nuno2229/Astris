import { FormEvent, useState } from "react";
import { api, Household } from "../api";
import type { Ctx } from "../App";
import { go } from "../router";

export default function Onboarding({ ctx }: { ctx: Ctx }) {
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");

  const create = async (e: FormEvent) => {
    e.preventDefault(); setError("");
    try { const h = await api.post<Household>("/api/households", { name }); await ctx.reloadHouseholds(h.id); go(""); }
    catch (err) { setError((err as Error).message); }
  };

  return (
    <div className="auth">
      <div className="card stack">
        <div className="brand"><img src="/icon.svg" alt="" />Welcome, {ctx.me.display_name}</div>
        <p className="muted small" style={{ margin: 0 }}>Create a household, or join one with an invite code.</p>
        <form className="stack" onSubmit={create}>
          <label>Household name<input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} placeholder="e.g. Our flat" /></label>
          <button className="btn primary block">Create household</button>
        </form>
        <form className="stack" onSubmit={(e) => { e.preventDefault(); go("join/" + encodeURIComponent(code.trim())); }}>
          <label>Invite code<input value={code} onChange={(e) => setCode(e.target.value)} required /></label>
          <button className="btn block">Join household</button>
        </form>
        {error && <div className="error" role="alert">{error}</div>}
        <button className="btn ghost" onClick={ctx.signOut}>Sign out</button>
      </div>
    </div>
  );
}
