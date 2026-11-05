import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Household, HouseholdDetail, Invite } from "../api";
import type { Ctx } from "../App";

export default function HouseholdScreen({ ctx, household }: { ctx: Ctx; household: Household }) {
  const [d, setD] = useState<HouseholdDetail | null>(null);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [fresh, setFresh] = useState<Invite | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"member" | "admin">("member");
  const [newName, setNewName] = useState("");
  const [error, setError] = useState("");
  const manager = household.role !== "member";

  const load = useCallback(async () => {
    setD(await api.get<HouseholdDetail>(`/api/households/${household.id}`));
    if (manager) setInvites(await api.get<Invite[]>(`/api/households/${household.id}/invites`));
  }, [household.id, manager]);
  useEffect(() => { load().catch((e) => setError(e.message)); }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setError("");
    try { await fn(); } catch (e) { setError((e as Error).message); }
  };
  const invite = (e: FormEvent) => { e.preventDefault(); act(async () => {
    setFresh(await api.post<Invite>(`/api/households/${household.id}/invites`, { email: email || null, role }));
    setEmail(""); await load();
  }); };
  const link = fresh?.code ? `${location.origin}/#/join/${encodeURIComponent(fresh.code)}` : "";
  const pending = invites.filter((i) => !i.accepted_at && !i.revoked_at && new Date(i.expires_at) > new Date());

  return (
    <>
      <h1>{household.name}</h1>
      {error && <div className="error" role="alert">{error}</div>}
      <h2>Members</h2>
      <ul className="list">
        {d?.members.map((m) => {
          const isMe = m.user_id === ctx.me.id;
          const canRemove = !isMe && m.role !== "owner" && (household.role === "owner" || (household.role === "admin" && m.role === "member"));
          return (
            <li key={m.user_id}>
              <div className="main">
                <div className="title">{m.display_name}{isMe ? " (you)" : ""}</div>
                <div className="meta">{m.email}</div>
              </div>
              <span className="pill">{m.role}</span>
              {canRemove && (
                <button className="btn ghost danger" onClick={() => confirm(`Remove ${m.display_name}? They lose access immediately.`)
                  && act(async () => { await api.del(`/api/households/${household.id}/members/${m.user_id}`); await load(); })}>Remove</button>
              )}
            </li>
          );
        })}
      </ul>

      {manager && (
        <>
          <h2>Invite someone</h2>
          <form className="card stack" onSubmit={invite}>
            <label>Email (optional, restricts who can use it)<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            {household.role === "owner" && (
              <label>Role<select value={role} onChange={(e) => setRole(e.target.value as "member" | "admin")}>
                <option value="member">Member</option><option value="admin">Admin (can invite and remove members)</option>
              </select></label>
            )}
            <button className="btn primary">Create invite</button>
            {fresh?.code && (
              <div className="notice stack" style={{ gap: 8 }}>
                <div>Share this link or code. It's shown <strong>only once</strong>, works once, and expires {new Date(fresh.expires_at).toLocaleDateString()}.</div>
                <div className="code">{link}</div>
                <div className="row">
                  <button type="button" className="btn" onClick={() => navigator.clipboard?.writeText(link)}>Copy link</button>
                  <button type="button" className="btn" onClick={() => navigator.clipboard?.writeText(fresh.code!)}>Copy code</button>
                </div>
              </div>
            )}
          </form>
          {pending.length > 0 && (
            <>
              <h2>Pending invites</h2>
              <ul className="list">
                {pending.map((i) => (
                  <li key={i.id}>
                    <div className="main"><div className="title">{i.email ?? "Anyone with the code"}</div>
                      <div className="meta">{i.role} · expires {new Date(i.expires_at).toLocaleDateString()}</div></div>
                    <button className="btn ghost danger" onClick={() => act(async () => {
                      await api.del(`/api/households/${household.id}/invites/${i.id}`); await load(); })}>Revoke</button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}

      <h2>You</h2>
      <div className="card stack">
        <div><strong>{ctx.me.display_name}</strong> <span className="muted small">{ctx.me.email}</span></div>
        <form className="row" style={{ flexWrap: "nowrap" }} onSubmit={(e) => { e.preventDefault(); act(async () => {
          const h = await api.post<Household>("/api/households", { name: newName }); setNewName(""); await ctx.reloadHouseholds(h.id); }); }}>
          <input placeholder="New household name" value={newName} onChange={(e) => setNewName(e.target.value)} required maxLength={100} />
          <button className="btn">Create</button>
        </form>
        {household.role !== "owner" && (
          <button className="btn danger" onClick={() => confirm(`Leave ${household.name}? Your shared accounts will no longer be visible to its members.`)
            && act(async () => { await api.del(`/api/households/${household.id}/members/${ctx.me.id}`); await ctx.reloadHouseholds(); })}>
            Leave {household.name}
          </button>
        )}
        <button className="btn ghost" onClick={ctx.signOut}>Sign out</button>
      </div>
    </>
  );
}
