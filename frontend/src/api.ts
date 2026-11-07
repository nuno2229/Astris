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
export interface Me { id: string; email: string; display_name: string; home_currency: string; is_server_admin?: boolean }
export interface Household { id: string; name: string; role: Role }
export interface Member { user_id: string; display_name: string; email: string; role: Role; joined_at: string }
export interface HouseholdDetail extends Household { members: Member[] }
export interface Invite { id: string; email: string | null; role: Role; created_at: string; expires_at: string;
  accepted_at: string | null; revoked_at: string | null; code?: string }
export interface Account { id: string; name: string; type: "bank" | "card" | "cash"; currency: string;
  institution: string | null; owner_user_id: string; owner_name: string; is_mine: boolean; balance: string | null;
  opening_balance: string; opening_date: string | null }
export interface HouseholdAccount extends Account { attribution: "include" | "hide"; until?: string | null }
export interface Tx { id: string; account_id: string; posted_on: string; amount: string; currency: string;
  description: string; merchant: string | null; counterparty: string | null; category: string | null;
  running_balance: string | null; category_source: "rule" | "llm" | "user" | "user_rule" | null;
  category_confidence: string | null; needs_review: boolean; editable: boolean; merchant_key: string | null;
  source: "import" | "manual" | "adjustment" | "transfer"; original_amount: string | null; original_currency: string | null;
  transfer_group: string | null; refund_of?: string | null }
export interface Totals { currency: string; spent: string; received: string; spent_today: string }
export interface Summary { household_id: string; period_start: string; period_end: string; included: Totals[];
  excluded: Totals[]; accounts_included: number; accounts_excluded: number; recent: Tx[]; needs_review: number;
  upcoming: Recurring[]; missed: Recurring[];
  goals: { id: string; name: string; target: string; currency: string; saved: string; progress: string; achieved: boolean;
    deadline: string | null; on_track: boolean | null; projected_date: string | null }[];
  budgets: { id: string; label: string; scope: string; period: "daily" | "weekly" | "monthly"; amount: string; currency: string;
    spent: string; committed: string; used: string; level: "ok" | "warning" | "at_risk" | "over"; message: string }[];
  stock?: { products: number; low: string[]; expiring: { name: string; days_left: number }[]; expired: number };
  converted: { currency: string; spent: string; received: string; spent_today: string; missing: string[];
    daily: { currency: string; day: string; spent: string }[] } | null; subscription_alerts: (SubAlert & { subscription_id: string; provider: string })[] }
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

export interface Recurring { id: string; account_id: string; name: string; category: string | null;
  cadence: "weekly" | "fortnightly" | "monthly" | "yearly"; direction: "in" | "out"; expected_amount: string;
  previous_amount: string | null; currency: string; occurrences: number; last_date: string; next_date: string;
  status: "active" | "missed" | "dismissed"; awaiting_statement: boolean; editable: boolean }

export function relDay(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  const t = new Date(); t.setHours(0, 0, 0, 0);
  const n = Math.round((+d - +t) / 864e5);
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n === -1) return "yesterday";
  if (n > 1 && n < 7) return `in ${n} days`;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export interface SubAlert { kind: string; text: string; severity: "info" | "warning" | "critical" }
export interface Subscription { id: string; provider: string; plan: string | null; amount: string; currency: string;
  cycle: "weekly" | "fortnightly" | "monthly" | "quarterly" | "yearly"; auto_renew: boolean;
  status: "active" | "trial" | "cancelled"; trial_ends: string | null; cancelled_on: string | null;
  access_until: string | null; next_renewal: string | null; monthly_cost: string; linked: boolean;
  notes: string | null; alerts: SubAlert[] }
export interface SubSuggestion { recurring_id: string; name: string; amount: string; currency: string; cadence: string; next_date: string }
export const CYCLE_LABEL = { weekly: "a week", fortnightly: "every 2 weeks", monthly: "a month", quarterly: "a quarter", yearly: "a year" } as const;
export interface TransferLeg { id: string; account_id: string; account: string; description: string; posted_on: string }
export interface TransferSuggestion { out: TransferLeg; into: TransferLeg; amount: string; currency: string }
/** A wallet move (ATM cash into a wallet, E08-03) vs a transfer between two accounts (P-1). */
export const isWalletMove = (t: Tx) => t.source === "transfer" || (t.category === "Cash" && !!t.transfer_group);
export interface RefundCandidate { id: string; posted_on: string; amount: string; name: string; category: string | null; account: string }
