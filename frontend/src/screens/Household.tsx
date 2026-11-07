import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Household, HouseholdDetail, Invite } from "../api";
import type { Ctx } from "../App";

export default function HouseholdScreen({ ctx, household }: { ctx: Ctx; household: Household }) {
  const [leaving, setLeaving] = useState(false);
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
                <button className="btn danger" onClick={() => confirm(`Remove ${m.display_name}? They lose access immediately.`)
                  && act(async () => { await api.del(`/api/households/${household.id}/members/${m.user_id}`); await load(); })}>Remove</button>
              )}
            </li>
          );
        })}
      </ul>

      {manager && (
        <>
          <h2>Invite someone</h2>
          <form className="panel stack" onSubmit={invite}>
            <label>Email (optional, restricts who can use it)<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            {household.role === "owner" && (
              <label>Role<select value={role} onChange={(e) => setRole(e.target.value as "member" | "admin")}>
                <option value="member">Member</option><option value="admin">Admin (can invite and remove members)</option>
              </select></label>
            )}
            <button className="btn primary">Create invite</button>
            {fresh?.code && (
              <div className="callout stack" style={{ gap: 8 }}>
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
                      <div className="meta">{i.role === "admin" ? "Admin" : "Member"}, expires {new Date(i.expires_at).toLocaleDateString()}</div></div>
                    <button className="btn danger" onClick={() => act(async () => {
                      await api.del(`/api/households/${household.id}/invites/${i.id}`); await load(); })}>Revoke</button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}

      <Categories household={household} />

      <p style={{ marginTop: 18 }}><a href="#/history">Change history</a></p>

      <h2>You</h2>
      <div className="panel stack">
        <div><strong>{ctx.me.display_name}</strong> <span className="muted small">{ctx.me.email}</span></div>
        <form className="row" style={{ flexWrap: "nowrap" }} onSubmit={(e) => { e.preventDefault(); act(async () => {
          const h = await api.post<Household>("/api/households", { name: newName }); setNewName(""); await ctx.reloadHouseholds(h.id); }); }}>
          <input placeholder="New household name" value={newName} onChange={(e) => setNewName(e.target.value)} required maxLength={100} />
          <button className="btn">Create</button>
        </form>
        {household.role !== "owner" && (leaving ? (
          <div className="panel stack" style={{ background: "var(--bg)", gap: 8 }}>
            <strong>Leave {household.name}?</strong>
            <span className="small muted">You'll lose access straight away. What should happen to the transactions you shared?</span>
            <button className="btn danger" onClick={() => act(async () => { await api.del(`/api/households/${household.id}/members/${ctx.me.id}`); await ctx.reloadHouseholds(); })}>
              Leave and take my transactions with me</button>
            <button className="btn danger" onClick={() => act(async () => { await api.del(`/api/households/${household.id}/members/${ctx.me.id}?keep_history=true`); await ctx.reloadHouseholds(); })}>
              Leave, but keep my past transactions here</button>
            <span className="small muted">Kept transactions stop at today: nothing you import later is shown. You can take them back any time from Accounts.</span>
            <button className="btn quiet" onClick={() => setLeaving(false)}>Cancel</button>
          </div>
        ) : (
          <button className="btn danger" onClick={() => setLeaving(true)}>Leave {household.name}</button>
        ))}
        <button className="btn quiet" onClick={ctx.signOut}>Sign out</button>
      </div>
    </>
  );
}

interface Category { id: string | null; name: string; built_in: boolean; can_remove: boolean }

/** The household's own categories (G-13), offered alongside the built-in list everywhere a category is picked. */
function Categories({ household }: { household: Household }) {
  const [cats, setCats] = useState<Category[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const load = () => api.get<Category[]>(`/api/households/${household.id}/categories`).then(setCats);
  useEffect(() => { load(); }, [household.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const own = cats.filter((c) => !c.built_in);
  const run = async (fn: () => Promise<unknown>) => { setError(""); try { await fn(); await load(); } catch (e) { setError((e as Error).message); } };
  return (
    <>
      <h2>Categories</h2>
      <div className="panel stack">
        <p className="muted small" style={{ margin: 0 }}>Add your own alongside the built-in ones, such as Allotment or Childcare. Everyone in {household.name} can use them.</p>
        {own.length > 0 && (
          <div className="chips">
            {own.map((c) => (
              <span key={c.id} className="chip" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                {c.name}
                {c.can_remove && <button className="linkish" aria-label={`Remove ${c.name}`}
                  onClick={() => confirm(`Remove “${c.name}”? Transactions keep it, but it won't be offered any more.`) && run(() => api.del(`/api/households/${household.id}/categories/${c.id}`))}>×</button>}
              </span>
            ))}
          </div>
        )}
        <form className="row" style={{ flexWrap: "nowrap" }} onSubmit={(e) => { e.preventDefault(); run(async () => { await api.post(`/api/households/${household.id}/categories`, { name }); setName(""); }); }}>
          <input placeholder="New category" aria-label="New category" value={name} onChange={(e) => setName(e.target.value)} required maxLength={40} />
          <button className="btn">Add</button>
        </form>
        <details className="small muted"><summary>Built-in categories</summary>{cats.filter((c) => c.built_in).map((c) => c.name).join(", ")}</details>
        {error && <div className="error" role="alert">{error}</div>}
      </div>
    </>
  );
}
