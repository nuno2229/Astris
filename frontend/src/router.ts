import { useEffect, useState } from "react";

/** Tiny hash router: #/, #/activity, #/accounts, #/household, #/join/<code> */
export function useRoute(): string[] {
  const parse = () => (location.hash.replace(/^#\/?/, "") || "").split("/").filter(Boolean);
  const [r, setR] = useState(parse);
  useEffect(() => {
    const on = () => setR(parse());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return r;
}

export const go = (path: string) => { location.hash = "#/" + path.replace(/^\//, ""); };

export function store(key: string, value?: string): string | null {
  try {
    if (value !== undefined) localStorage.setItem(key, value);
    return localStorage.getItem(key);
  } catch { return null; }
}
