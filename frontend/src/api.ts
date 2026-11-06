export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const r = await fetch(path, init);
  if (r.status === 204) return undefined as T;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = (data as { detail?: unknown }).detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x: { msg?: string }) => x.msg).join("; ") : r.statusText;
    throw new ApiError(r.status, msg || "Request failed");
  }
  return data as T;
}

export const api = {
  get: <T>(p: string) => req<T>("GET", p),
  post: <T>(p: string, b?: unknown) => req<T>("POST", p, b),
  put: <T>(p: string, b?: unknown) => req<T>("PUT", p, b),
  patch: <T>(p: string, b?: unknown) => req<T>("PATCH", p, b),
  del: <T = void>(p: string) => req<T>("DELETE", p),
};

export type Role = "owner" | "admin" | "member";
export interface Me { id: string; email: string; display_name: string; home_currency: string }
export interface Household { id: string; name: string; role: Role }
export interface Member { user_id: string; display_name: string; email: string; role: Role; joined_at: string }
export interface HouseholdDetail extends Household { members: Member[] }
export interface Invite { id: string; email: string | null; role: Role; created_at: string; expires_at: string;
  accepted_at: string | null; revoked_at: string | null; code?: string }
export interface Account { id: string; name: string; type: "bank" | "card" | "cash"; currency: string;
  institution: string | null; owner_user_id: string; owner_name: string; is_mine: boolean; balance: string | null }
export interface HouseholdAccount extends Account { attribution: "include" | "hide" }
export interface Tx { id: string; account_id: string; posted_on: string; amount: string; currency: string;
  description: string; merchant: string | null; counterparty: string | null; category: string | null;
  running_balance: string | null; category_source: "rule" | "llm" | "user" | "user_rule" | null;
  category_confidence: string | null; needs_review: boolean; editable: boolean; merchant_key: string | null }
export interface Totals { currency: string; spent: string; received: string; spent_today: string }
export interface Summary { household_id: string; period_start: string; period_end: string; included: Totals[];
  excluded: Totals[]; accounts_included: number; accounts_excluded: number; recent: Tx[]; needs_review: number }
export interface ImportResult { batch_id: string; format: string; rows_total: number; rows_new: number;
  rows_duplicate: number; warnings: string[] }

export function money(v: string | number | null | undefined, currency: string, signed = false): string {
  if (v === null || v === undefined) return "—";
  const n = typeof v === "string" ? Number(v) : v;
  const s = new Intl.NumberFormat(undefined, { style: "currency", currency }).format(Math.abs(n));
  if (!signed) return n < 0 ? "−" + s : s;
  return (n < 0 ? "−" : "+") + s;
}

export function shortDate(iso: string): string {
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
