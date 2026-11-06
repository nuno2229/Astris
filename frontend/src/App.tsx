import { useCallback, useEffect, useState } from "react";
import { api, ApiError, Household, Me } from "./api";
import { go, store, useRoute } from "./router";
import { BillsIcon, ChevronDown, GearIcon, HomeIcon, ListIcon, StarMark, UsersIcon, WalletIcon } from "./icons";
import AuthScreen from "./screens/Auth";
import Dashboard from "./screens/Dashboard";
import Activity from "./screens/Activity";
import Accounts from "./screens/Accounts";
import HouseholdScreen from "./screens/Household";
import Onboarding from "./screens/Onboarding";
import Join from "./screens/Join";
import Settings from "./screens/Settings";
import Bills from "./screens/Bills";
import ImportWizard from "./screens/ImportWizard";
import History from "./screens/History";

export interface Ctx {
  me: Me;
  households: Household[];
  household: Household | null;
  reloadHouseholds: (select?: string) => Promise<void>;
  signOut: () => Promise<void>;
}

export default function App() {
  const route = useRoute();
  const routeKey = route.join("/");
  useEffect(() => { scrollTo(0, 0); }, [routeKey]); // each tab opens at the top
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [households, setHouseholds] = useState<Household[]>([]);
  const [hid, setHid] = useState<string | null>(null);
  const [version, setVersion] = useState("");
  useEffect(() => { api.get<{ version: string }>("/api/health").then((h) => setVersion(h.version)).catch(() => {}); }, []);

  const loadMe = useCallback(async () => {
    try { setMe(await api.get<Me>("/api/me")); }
    catch (e) { if (e instanceof ApiError && e.status === 401) setMe(null); else throw e; }
  }, []);

  const reloadHouseholds = useCallback(async (select?: string) => {
    const hs = await api.get<Household[]>("/api/me/households");
    setHouseholds(hs);
    const remembered = select ?? (me ? store("household:" + me.id) : null);
    const pick = hs.find((h) => h.id === remembered) ?? hs[0] ?? null;
    setHid(pick?.id ?? null);
    if (pick && me) store("household:" + me.id, pick.id);
  }, [me]);

  useEffect(() => { loadMe(); }, [loadMe]);
  useEffect(() => { if (me) reloadHouseholds(); }, [me, reloadHouseholds]);

  const signOut = async () => {
    await api.post("/api/auth/logout").catch(() => {});
    setMe(null); setHouseholds([]); setHid(null); go("");
  };

  if (me === undefined) return <div className="auth muted">Loading…</div>;
  if (me === null) return <AuthScreen onDone={loadMe} />;

  const household = households.find((h) => h.id === hid) ?? null;
  const ctx: Ctx = { me, households, household, reloadHouseholds, signOut };

  if (route[0] === "join") return <Join code={route[1] ?? ""} ctx={ctx} />;
  if (!households.length) return <Onboarding ctx={ctx} />;

  const tab = route[0] ?? "";
  const tabs = [
    { path: "", label: "Home", icon: <HomeIcon /> },
    { path: "activity", label: "Activity", icon: <ListIcon /> },
    { path: "bills", label: "Bills", icon: <BillsIcon /> },
    { path: "accounts", label: "Accounts", icon: <WalletIcon /> },
    { path: "household", label: "Household", icon: <UsersIcon /> },
  ];

  return (
    <div className="shell">
      <header className="topbar">
        <StarMark />
        <label className="switcher">
          <span>{household?.name}</span><ChevronDown />
          <select aria-label="Switch household" value={hid ?? ""}
            onChange={(e) => { setHid(e.target.value); store("household:" + me.id, e.target.value); }}>
            {households.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
          </select>
        </label>
        <span className="spacer" />
        <a className="iconbtn" href="#/settings" aria-label="Settings" aria-current={tab === "settings" ? "page" : undefined}><GearIcon /></a>
      </header>
      <nav className="bottomnav" aria-label="Main">
        {tabs.map((t) => (
          <a key={t.path} href={"#/" + t.path} aria-current={tab === t.path ? "page" : undefined}>
            {t.icon}<span>{t.label}</span>
          </a>
        ))}
        {version && <a className="version" href="#/settings" aria-label={`Astris version ${version}`}>Astris {version}</a>}
      </nav>
      <main key={hid /* remount per household: isolated context per screen (§21) */}>
        {household && tab === "" && <Dashboard ctx={ctx} household={household} />}
        {household && tab === "activity" && <Activity household={household} reviewFirst={route[1] === "review"} />}
        {household && tab === "accounts" && <Accounts ctx={ctx} household={household} />}
        {household && tab === "household" && <HouseholdScreen ctx={ctx} household={household} />}
        {household && tab === "settings" && <Settings household={household} />}
        {household && tab === "import" && <ImportWizard accountId={route[1]} />}
        {household && tab === "history" && <History household={household} />}
        {household && (tab === "bills" || tab === "recurring") && <Bills household={household} currency={me.home_currency} initial={tab === "recurring" ? "regular" : route[1]} />}
      </main>
    </div>
  );
}
