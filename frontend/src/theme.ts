import { store } from "./router";

export type ThemePref = "dark" | "light" | "system";
const mq = () => matchMedia("(prefers-color-scheme: light)");

export function getThemePref(): ThemePref {
  const t = store("theme");
  return t === "light" || t === "system" ? t : "dark"; // dark is the default (§21)
}

function apply(pref: ThemePref) {
  const resolved = pref === "system" ? (mq().matches ? "light" : "dark") : pref;
  document.documentElement.dataset.theme = resolved;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", resolved === "light" ? "#f6f7f9" : "#0f1115");
}

export function setThemePref(pref: ThemePref) {
  store("theme", pref);
  apply(pref);
}

export function initTheme() {
  apply(getThemePref());
  mq().addEventListener("change", () => { if (getThemePref() === "system") apply("system"); });
}
