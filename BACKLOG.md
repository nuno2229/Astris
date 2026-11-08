# Astris (Finance Tracker / Home Dashboard) — Backlog

> **Live document.** Updated as work progresses. Source of truth for scope = *High-Level Requirements & Summary v0.4* (PO spec, 2026-11-05).
> Every item traces back to a spec section (`§n`). Nothing here goes beyond the spec except in **§P Proposed** — and those are **not in scope until the PO approves them**.

| | |
|---|---|
| **Backlog version** | 0.29 |
| **Spec version** | v0.4 (DRAFT) |
| **Last updated** | 2026-11-07 |
| **Owner** | PO |

## How this document works

1. **Scope changes need PO approval.** Claude may suggest new requirements or improvements, but only by adding them to [§P Proposed](#p-proposed--awaiting-po-approval) with status `Awaiting approval`. They move into an epic only after the PO says yes.
2. **Gaps are flagged, not filled.** Where the spec is ambiguous or contradictory, the item goes into [§G Gaps](#g-gaps--ambiguities-in-the-spec), not into a guess.
3. **Blocked items** point to the decision (`D-n`) blocking them.
4. **Every change** gets a line in the [Changelog](#changelog).

**Status legend:** `Todo` · `In progress` · `Review` · `Done` · `Blocked (D-n)`
**Priority:** taken from the spec. The order follows the spec's §25 handoff order. Items marked **Low** are low priority per the spec. Everything else is unranked until the PO sets priorities.

---

## M. Milestones

### M1 · MVP — ✅ `Accepted by PO 2026-11-05` (P-11). Packaging below is in Review.

### M1.1 · Settings + Unraid packaging — `Review`

| Item | Covers | Status |
|---|---|---|
| Settings screen: theme Dark / Light / Match device (dark default) | E16-02 | `Review` |
| Local LLM server configured in Settings (household owner only; test connection; non-LAN address needs explicit opt-in) — PO instruction 2026-11-05 | E05-00 (new), §2 | `Review` |
| Docker image (frontend + backend, non-root, healthcheck), first-run DB setup, Compose file, Unraid template | E19-01 | `Review` (**not built yet**: no Docker on the dev Mac; startup sequence verified without a container) |
| GitHub Actions: tests on push/PR; multi-arch image to GHCR on `v*` tag | E19-02 | `Review` |
| Version visible in Settings → About (git tag in release images, `vX.Y.Z-dev` locally) — PO instruction 2026-11-05 | E19-02 | `Review` |
| Product renamed to **Astris** to match the repo (app title, image, Unraid template) | — | `Review` |
| UI pass (frontend-design + dataviz skills): night-sky identity, self-hosted fonts (offline), month "constellation" spend chart with tap/keyboard readout + table view, date-grouped activity, plain-language copy | E16, E13-06 (partial) | `Review` |
| Security hardening from automated review: strict amount parsing; LLM test resolves once and pins the IP (no DNS rebinding), blocks metadata/link-local incl. IPv6-wrapped | §23 | `Done` |
| Cookies auto-secure: HTTPS-only flag when served over HTTPS, so plain-http LAN login works | E02-06 | `Done` |

### M1 detail
Auth + households/invites + accounts + CSV/OFX import + ledger + basic mobile-first dashboard.

| Item | Covers | Status |
|---|---|---|
| Architecture + stack | E01-01, E01-03 | `Review` (docs/architecture.md) |
| Schema + DB-level tenant isolation (row-level security) | E02-01, E02-02, E02-03 (ledger part), E02-05 | `Review` |
| Per-user login, sessions, self-registration | E02-06, E02-08, E19-04 (partial) | `Review` |
| Households, invites, revocation, roles | E02-07 | `Review` |
| Accounts + per-household attribution | E02-02, E02-04 | `Review` |
| CSV + OFX/QFX import into the ledger, with dedupe + undo | E03-01, E03-02, E03-06 (partial), E09-04 (single import) | `Review` |
| Mobile-first dashboard shell, dark mode, household switcher, PWA | E16-02, E16-03, E16-04, E16-05 (partial) | `Review` |
| **Remaining before M1 is done:** PO review/acceptance; Docker image (E19-01) so it runs on Unraid | | `Todo` |

---

## D. Decisions needed (from spec §24)

| ID | Decision | Proposed answer (from spec) | Blocks | Status |
|---|---|---|---|---|
| D-1 | Core offline vs online modules | Core offline by default; online modules explicitly gated, toggleable, never auto-on | E18, E17, E14-07 | ✅ **Confirmed 2026-11-05** |
| D-2 | What/where is "Jev" (LLM model/host on Unraid)? | A llama.cpp server. URL and model are entered by the household owner in **Settings** (PO, 2026-11-05) | — | ✅ **Resolved 2026-11-05** |
| D-3 | Spec sync between Mac (Claude) and Unraid (PO) | Repo: **github.com/nuno2229/Astris** (public). Image: `ghcr.io/nuno2229/astris`. BACKLOG.md lives in the repo | — | ✅ **Resolved 2026-11-05** |
| D-4 | How far to go with native Unraid updates + multi-user self-service login/roles | **Built as proposed (2026-11-06), PO to confirm:** Unraid's own update check; opt-in in-app "update available" banner; automatic backup before database changes; rollback = pin previous tag + restore; server admin set by ADMIN_EMAIL (else first account) who can close sign-ups; backups stay on the server (never downloadable in the app) | — | ✅ **Confirmed 2026-11-07** |
| D-5 | Multi-household sharing is server-local (not cloud SaaS) | Yes. Cross-user visibility is a local scoping concern, never a network sync | E02, E19 | ✅ **Confirmed 2026-11-05** |
| D-6 | Geography / retailer scope | UK only (PO, 2026-11-06) | — | ✅ **Resolved 2026-11-06** |
| D-7 | Spending limits and cash wallet alerts are device-local only (no server-side blocking) | Alerts only, never blocking (PO "ahead", 2026-11-06) | — | ✅ **Resolved 2026-11-06** |
| D-8 | Offline FX rate cache source | (b) Opt-in daily ECB reference rates, cached locally (PO, 2026-11-06) | — | ✅ **Resolved 2026-11-06** |
| D-9 | Copilot may mutate data (scoped) vs read-only first | (b) May act, within the user's scope, after explicit confirmation (PO, 2026-11-06) | — | ✅ **Resolved 2026-11-06** |

---

## E. Epics & stories

### E01 · Scope lock & architecture — `Todo` · §1, §2, §25.1
- [ ] **E01-01** Architecture diagram: offline core, optional network modules, multi-tenant data model, inventory
- [ ] **E01-02** Scope lock sign-off by PO (resolve D-1, D-5)
- [ ] **E01-03** Document golden rules (§2) as checkable architectural constraints

### E02 · Data model, auth & tenant scoping — `Todo` · §3, §23, §25.2
- [ ] **E02-01** Schema: users, sessions, households, memberships (roles: owner/admin/member)
- [ ] **E02-02** Schema: accounts (bank / card / cash / subscription / goal) with `owner_user_id`
- [ ] **E02-03** Schema: ledger, products, stock, budgets, shopping lists, goals
- [ ] **E02-04** Per-account household attribution: `Include` / `Hide (separate)`. Affects aggregation only, never access (§3.5)
- [ ] **E02-05** Two-layer scoping (user → household) enforced on every query layer: ledger, products, budgets, shopping list, stats, favourites, stock (§3.3)
- [ ] **E02-06** Distinct per-user login and sessions; a session never spans users (§2, §3.4)
- [ ] **E02-07** Invitations: link / email / code; explicit and revocable; revocation takes effect immediately (§3.2)
- [ ] **E02-08** Per-user preferences and defaults stored under the user only (§3.4)
- [ ] **E02-09** Product/price data global; user- and household-scoped product notes kept private (§3.3)
- [ ] **E02-10** Encryption at rest for the local DB (§23)

### E03 · Statement parser — `In progress` · §4.1, §25.3
- [x] **E03-01** CSV import — `Done` (M1)
- [x] **E03-02** OFX / QFX import — `Done` (M1)
- [x] **E03-03** MT940 import — `Review`
- [x] **E03-04** PDF (text) import — `Review` (line reader; in/out from running balance. Real bank PDFs needed to tune — see G-12)
- [x] **E03-05** PDF (scanned) and statement images via OCR — `Review` (Tesseract in Docker/CI; verify on real scans)
- [ ] **E03-06** Extract: date, merchant, category, amount, counterparty, running balance, currency
- [ ] **E03-07** Bank import is the master per account; receipts and manual entries reconcile onto it (§2)
- [x] **E03-08** Prompt-injection guard: document content is data only, with structured JSON output (§23) — `Review` (JSON data payload, schema-constrained reply, allowlist validation, tested)

### E04 · Receipt scanner & unit price — `Review` · §4.2, §25.4
- [x] **E04-01** Photo upload, single and batch; multiple receipts per image — `Review` (OCR in the sandbox; several receipts in one photo split at TOTAL lines, best effort)
- [x] **E04-02** Line-item extraction: name, brand, weight/unit, UoM, line price — `Review` (quantity lines, weighed produce, pack sizes; brand not detected; optional local-AI pass, validated, prices must appear on the receipt)
- [x] **E04-03** Normalised unit price (e.g. per 100 g) across SKUs — `Review` (per 100 g / 100 ml / each, after discounts)
- [x] **E04-04** Promotions/discounts handled explicitly; loyalty discounts missing from the receipt flagged as a known limitation — `Review`
- [x] **E04-05** Receipt products reconcile onto the bank transaction (§2) — `Review` (suggested by amount ±5p, ±3 days, shop; one receipt per payment)
- [ ] **E04-06** Capture expiry date where readable (§15.2) — `Todo` (UK receipts don't print use-by dates; set them in the pantry)

### E23 · Keep original files (opt-in) — `Review` · **PO request 2026-11-06**
- [x] **E23-01** Optional "keep a copy" for receipt photos (visible to the household) and statement files (owner only) — `Review`
- [x] **E23-02** Download as attachment only (never rendered in the app), delete any time, 1 GB per person — `Review`
- [x] **E23-03** Included in backup/export (E19-05) — `Review` (in backups; encrypted backups with BACKUP_PASSPHRASE; the live DB is unencrypted until E02-10)

### E05 · AI categorisation pipeline — `Review` · §4.1, §22, §25.5
- [x] **E05-00** LLM server address/model configured in Settings, owner only (PO instruction 2026-11-05) — `Review`
- [x] **E05-01** Local LLM integration (llama.cpp / Ollama), run as a background queue so the UI stays responsive — `Review` (worker thread; runs as each account owner under RLS)
- [x] **E05-02** Merchant/alias dictionary with fuzzy matching — `Review` (built-in UK dictionary + personal learned rules)
- [x] **E05-03** Confidence score; uncertain items flagged rather than silently guessed — `Review` ("Needs a category" filter + Home prompt)
- [x] **E05-04** Rules-based fallback when no model is configured (§22) — `Review`

### E06 · Recurring & scheduled transactions — `Review` · §5, §25.6
- [x] **E06-01** Auto-detect recurring charges (rent, subscriptions, standing orders, dividends) and scheduled income — `Review` (weekly / fortnightly / monthly / yearly; calendar source still open, G-5)
- [x] **E06-02** Store pattern (daily/weekly/monthly/yearly), next occurrence and expected amount; update when they drift — `Review` (price changes >10% flagged with old amount; daily not detected, mostly misfires on coffee)
- [x] **E06-03** Missed-payment detection (expected charge didn't appear) — `Review` (only when imported statements cover the due date + grace)
- [x] **E06-04** Upcoming due dates surfaced — `Review` (Home "Coming up", Regular payments screen)

### E07 · Subscription registry — `Review` · §11, §25.7
- [x] **E07-01** Registry separate from the ledger: provider, cost, cycle, renewal date, next renewal, auto-renew, status — `Review` (household-level, any member manages; Bills tab)
- [x] **E07-02** Cancellation tracking — `Review` (flags charges after the cancel date)
- [x] **E07-03** Unused-trial detection — `Review` (interpretation G-15: trial ending soon / became paid / ended quietly)
- [x] **E07-04** Upcoming-renewal alerts — `Review` (in-app; push via E22)
- [x] **E07-05** Feeds recurring transactions (E06) and budgets (E12) — `Review` for E06 (suggestions + linked renewal dates); E12 not built yet

### E08 · Cash wallet & multi-currency — `Review` · §8, §9, §25.8
- [x] **E08-01** Cash as a first-class account type, owned per user (§3) — `Review`
- [x] **E08-02** Opening/closing balances, deposits, withdrawals — `Review` (starting balance, manual entries, "Count my cash" adjustments)
- [x] **E08-03** Optional reconciliation against card/statement — `Review` (move an ATM withdrawal into a wallet; the linked pair is excluded from spend/income so cash isn't counted twice. General transfer detection is still P-1)
- [x] **E08-04** Base currency per account; multi-currency transactions — `Review` (original foreign amount from CSV columns / manual entry)
- [x] **E08-05** Offline FX cache fallback — `Review` (last ECB set cached on the server; manual rates override)
- [x] **E08-06** Live FX rates as an opt-in add-on (`uses internet`) — `Review` (daily ECB file, owner opt-in, off by default)
- [x] **E08-07** Display in the user's home currency; flag conversions when cards are used abroad — `Review` (converted totals; currencies without a rate listed separately, never guessed)

### E09 · Bulk import wizard — `Review` · §10, §25.9
- [x] **E09-01** Multi-file ingest with format detection — `Review` (up to 20 files)
- [x] **E09-02** Dry run: dedupe, balance-mismatch vs statement, mapping preview — `Review` (also flags duplicates across files and gaps vs earlier imports)
- [x] **E09-03** Conflict view; proceed or adjust per-account rules — `Review` (interpreted as per-file target account, date order and column mapping — G-16)
- [x] **E09-04** Rollback/cancel of a bulk run without touching existing data — `Review`

### E10 · Change-log & audit trail — `Review` · §13, §25.10
- [x] **E10-01** Log who/when/why for manual edits, imports/reconciles, stock changes and shopping-list mutations — `Review` for edits, imports, cash moves/counts, starting balances, subscriptions, members, invites, AI/FX settings; stock & shopping list get hooked in when E14/E17 are built
- [x] **E10-02** Tamper-evident local log — `Review` (append-only for the app role; per-account/household hash chain; "History intact" check)
- [x] **E10-03** Owner can view and reverse changes — `Review` (History screen; undo refuses if the record changed since; undo is itself logged)

### E11 · Savings goals — `Review` · §6, §25.11
- [x] **E11-01** Named goals with a target amount and optional deadline — `Review` (household-level; archive)
- [x] **E11-02** Per-member contributions vs the household goal — `Review` (who put in what + share; you only add/remove your own)
- [x] **E11-03** Progress: %, days remaining and projected completion, auto-updated from the ledger — `Review` (count a statement transfer toward a goal; "always" rule auto-counts future ones on import; pace over the last 90 days, min 30)

### E12 · Budgets, alerts & spending limits — `Review` · §7, §12, §25.12
- [x] **E12-01** Monthly caps per category and per merchant
- [x] **E12-02** Alert thresholds (% or amount): approaching / hit / exceeded
- [x] **E12-03** Daily spend summary
- [x] **E12-04** Shared (household) and personal budgets
- [x] **E12-05** Recompute when recurring transactions fire
- [x] **E12-06** Per-card and per-merchant thresholds (daily/weekly/monthly) — **E12b**, `Blocked (D-7)`
- [x] **E12-07** UI states plainly that there is no server-side blocking; the app never claims to block (§12)

### E13 · Consumption analytics — `Review` · §17, §25.13
- [x] **E13-01** Weekly average per household (e.g. 2.0 L milk/week) and monthly rollups — `Review` (spending: weekly avg per category + 12-month trend; quantities: litres/kg/items a week per product from scanned receipts, 12-week chart)
- [x] **E13-02** Seasonality detection and surfacing — `Review` (same month last year ≥25% and ≥£20 above that category's usual → heads-up for this and next month; needs a year of history)
- [x] **E13-03** Budget vs actual with % over/under and colour-blind-safe thresholds — `Review` (Insights + Budgets; status always in words, not colour alone)
- [x] **E13-04** Top spenders and top stores — `Review`
- [x] **E13-05** Attribution-aware rollups plus an optional "excluded accounts" breakdown — `Review`
- [x] **E13-06** Charts render on mobile and desktop — `Review`

### E14 · Inventory, barcode & expiry — `Review` · §15, §25.14
- [x] **E14-01** Household stock table: product, quantity on hand, restock threshold — `Review` (Pantry tab; batches per use-by date)
- [x] **E14-02** On-device offline barcode decoder; scan to add/increment — `Review` (browser BarcodeDetector or bundled ZXing; live camera needs HTTPS (G-4), photo scanning works anywhere)
- [x] **E14-03** Multiple barcodes per scan, batch scans, dedupe/merge on the same barcode, unknown SKU → create or skip — `Review`
- [x] **E14-04** Record who scanned and when (audit, E10) — `Review`
- [x] **E14-05** Expiry date and days remaining; expiring-soon alerts; auto-expire on/after the date — `Review` (expired batches flagged with one-tap clear; alerts in app + notifications)
- [x] **E14-06** Unit standardisation (g/kg/l/ml → household unit); flag items with no barcode/expiry — `Review`
- [x] **E14-07** Low-stock weekly shop engine: suggested list, configurable trigger (e.g. Sunday), history of generated lists — `Review`; cheapest-store part waits for E18; moving items onto a real shopping list waits for E17
- [x] **E14-08** Tenant-scoped stock queries (§15.4) — `Review` (RLS + isolation tests; same barcode in two households = two products)

### E15 · In-app AI copilot — `Review` · §14, §23, §25.15
- [x] **E15-01** Floating, collapsible chat available app-wide (mobile included)
- [x] **E15-02** Grounded answers mapped onto the real data model; each number cites its account/date/category — `Review` (the model only picks the question/action; every figure is computed from the database and listed with sources)
- [x] **E15-03** Scope enforcement: reads nothing outside the acting user's households/accounts
- [x] **E15-04** Scoped actions (add to list, set budget) — `Review` (set budget, add to shopping list, pantry add/use, goal contribution; always confirmed first)
- [x] **E15-05** Local streaming, < ~2 s typical; graceful fallback (canned answers) when no LLM is available — `Review` (built-in phrase matcher works offline; answers are instant since they come from the database)
- [x] **E15-06** Optional online augmentation only when explicitly enabled

### E16 · Web UI, mobile shell & PWA — `Todo` · §21, §2, §25.16
- [ ] **E16-01** Dashboard grid of widgets: spend today, budget burn, cheapest item, milk trend, list count, upcoming recipes, household selector, low-stock and expiring-soon
- [ ] **E16-02** Dark mode by default, plus a light theme
- [ ] **E16-03** Separate mobile shell (bottom nav, thumb-sized targets) and desktop shell
- [ ] **E16-04** PWA; no mandatory install
- [ ] **E16-05** Household switcher with isolated context per screen
- [ ] **E16-06** Accessibility: contrast, keyboard nav, colour-blind-safe thresholds
- [ ] **E16-07** Every flow works on mobile from day one: scanning, barcode, chat, budgets, goals, wallet, import review

### E17 · Shopping list & product intelligence — `Review` · §16, §18
- [x] **E17-01** Household list by default, personal list optional — `Review` (shared list created automatically; personal lists private incl. in History)
- [x] **E17-02** Add from receipts, manually, from Cookidoo carry-over and from low-stock suggestions — `Review` for manual, low-stock (pantry) and the assistant; receipts wait for E04, Cookidoo for E20
- [x] **E17-03** Per-item checkbox, notes, qty, expiry — `Review` (+ "Done shopping" puts ticked pantry items back into stock)
- [x] **E17-04** Share/notify household members (local-first) — `Review` (list refreshes while open; others notified via their channels, once per batch)
- [x] **E17-05** Favourites (user/household scoped); filter the list by favourites — `Review`
- [x] **E17-06** "Buy cheapest" store tagging — `Review` ("Cheapest at Aldi" on list items when ≥2 shops seen)

### E18 · Price comparison (opt-in online) — `Review` (UK; sources: your receipts + opt-in Open Prices; no scraping — G-17) · §16, §25.18
- [x] **E18-01** Cheapest store per product this week (UK grocer sources TBD) — `Review` (latest price per shop, like-for-like unit prices, pints handled)
- [x] **E18-02** "Is it worth it?" side-by-side view — `Review` (Shopping → Prices)
- [x] **E18-03** Disclaimer on data freshness and scraping ethics; `uses internet` label and toggle — `Review` (dates shown, >60 days flagged)

### E19 · Platform, release & ops — `Review` · §20, §25.19
- [x] **E19-01** Dockerfile and Unraid template — `Review` (backups folder + passphrase in the template)
- [x] **E19-02** GitHub source, release-tagged images, one-command upgrade — `Done` (v0.1.2 published)
- [x] **E19-03** Research: native-feeling Unraid updates (auto-update vs in-app banner, rollback) — `Review` (Unraid's own update check + opt-in in-app banner + automatic pre-migrate backup + tag-pin rollback, see README)
- [x] **E19-04** Multi-user self-service registration and role separation (owner/admin vs member) — `Review` (server admin = first account; sign-ups switch; household roles from E02)
- [x] **E19-05** Full DB export/import, backup and restore — `Review` (daily + pre-migrate backups, optional encryption, download; CLI restore; personal JSON export. Importing a personal export into another server isn't built)
- [x] **E19-06** No telemetry leaving the home by default (§23) — `Review` (no outbound calls unless opted in: local LLM, ECB rates, update check, Discord/Telegram/ntfy)

### E22 · Notifications (Discord / Telegram / ntfy) — `Review` · **PO request 2026-11-06** (supersedes P-5) · §2, §7, §11
- [x] **E22-01** Per-user channels: Discord webhook, Telegram bot, ntfy (self-hostable). Internet channels opt-in + labelled "uses internet"
- [x] **E22-02** Minimal message content by default (no names/amounts) unless the user turns details on
- [x] **E22-03** Triggers: subscription renewing (1/3/7 days ahead), trial ending, charged after cancel, missed regular payment
- [x] **E22-04** Each alert sent once (dedupe), test-message button, secrets never returned by the API
- [x] **E22-05** Strict destinations: Discord only discord.com webhooks, Telegram only api.telegram.org; ntfy via the pinned, home-network-checked client

### E24 · PO decisions of 2026-11-07 — `In progress` · G-5…G-17, P-1…P-13
- [x] **E24-09** Transfers between your own accounts detected (current → savings, card repayments) and left out of spending (P-1) — `Review` (same amount out and in within 4 days; your own accounts with one clear match are linked on import, everything else is asked in Activity; partners' accounts in the same household can be linked by either owner; undo from History or the transaction)
- [x] **E24-10** Refunds linked to the original purchase and netted off its category (P-2) — `Review` (money in from a shop you paid in the last 120 days, no more than you paid; counts as negative spending in that category everywhere: Home, Insights, budgets, assistant, daily summary; "Not a refund" is remembered)
- [x] **E24-04** Household-defined categories alongside the built-in list (G-13) — `Review` (Household → Categories; any member adds, the person who added it or an owner/admin removes; usable on transactions, budgets, the assistant and the AI labeller; up to 60 per household)
- [x] **E24-05** Account owner picks which household's AI processes a shared account (G-14) — `Review` (Accounts → AI labelling: automatic, a specific household's AI, or off)
- [x] **E24-07** Saved import profiles: each bank's column layout remembered (G-16) — `Review` (columns/date order you set in the wizard are remembered per person by the file's header row and applied next time, in the wizard and quick import; "Forget the saved layout")
- [x] **E24-06** Likely trials spotted from £0.00/£1.00 charges (G-15) — `Review` (Bills → "Free trials?": £0.00/£1.00 from a shop in the last 45 days with no real charge since; groceries/eating out/transport ignored; Track trial (end date defaults to 30 days) or Not a trial)
- [x] **E24-03** A goal can follow a savings account's balance (G-6) — `Review` (choose "The balance of …" when creating a goal, or "Follow its balance" later; only the account owner can link it, the account must be shared into the household; pace from the balance change over 90 days; switch back to contributions any time)
- [x] **E24-01a** Leaving a household: the member chooses whether past transactions stay visible (G-9) — `Review` (kept history is frozen at the day they left: later imports never show, enforced in the database; they lose access immediately; "Take back" from Accounts any time; rejoining clears it)
- [x] **E24-01b** "Delete my account and data" (P-13) — `Review` (Settings → Your data; password + type DELETE; personal data deleted, solo households deleted, shared ownership handed to an admin or longest-standing member; history entries kept as "Former member" so the tamper-evident chain stays intact; the last server admin must hand over first)
- [x] **E24-11** Split payments across categories/people, and settle up; household on/off option (P-3, P-4) — `Review` ("Split this payment" on any of your transactions: parts with their own category, totals everywhere use the parts; with settling up on (Household → Settle up, owner switch) each part can be for someone, giving "B owes A £45"; record payments either way; a former member's later split edits never show)
- [x] **E24-02** Household option to share product names (never prices) with other households on this server (G-8) — `Review` (Pantry → "Share product names", owner's switch; barcode, name, brand and size only, no household recorded; scanned unknown barcodes are named from it)
- [x] **E24-12** Offline product database (Open Food Facts download) and use-by date scanning from a package photo (P-7) — `Review` (admin CLI imports the UK products from the Open Food Facts CSV, see README; "Read the date from a photo" in the pantry reads use-by / best-before dates with the sandboxed OCR, prefers use-by over display-until; photo not kept. OCR on real packaging still to be confirmed on the Unraid build)
- [x] **E24-08** Calendar (iCal) import for scheduled bills and income (G-5) — `Review` (Bills → All regular payments → From your calendar: upload an .ics or add a calendar link (uses internet, private, refreshed at most daily when Bills is opened); repeat rules, amount from the event title, pay/salary = money in; shown in Home → Coming up)
- [x] **E24-13** Local HTTPS so live camera scanning works on phones (P-6, G-4) — `Review` (HTTPS=self-signed in the template: the server makes its own CA + certificate for the addresses you list, renews it, CA key never leaves; phones install /ca.crt once; Settings → Camera scanning explains; reverse proxy / tailscale still an option. To be confirmed on a real phone)
- [x] **E24-14** Database encryption at rest with key management (P-10, E02-10) — `Review` (two layers: the database files via Unraid's encrypted array/pool (Postgres has no built-in file encryption); kept files, notification secrets and calendar links encrypted by the app with AES-256-GCM, each bound to its row; key in the template (masked), never in the DB or backups; rotation via ASTRIS_OLD_KEYS + `rewrap`; admin sees the status)
- [ ] **E24-15** Supermarket website price lookups, opt-in, rate-limited and cached, best effort (G-17)

### E20 · Recipe integrations — **Low** · `Todo` · §19, §25.17
- [ ] **E20-01** Cookidoo via `miaucl/cookidoo-api` (OAuth2 PKCE, `.env` creds): meal plan → shopping list; ingredients vs pantry; "cook this week" widget
- [ ] **E20-02** Mealie (opt-in): meal plans → list and stock suggestions; deduct/estimate ingredients from stock
- [ ] **E20-03** Rate limiting, aggressive caching and opt-in warnings about reverse-engineered APIs

### E21 · Non-functional targets — `Todo` · §22
- [ ] **E21-01** Define the cold-parse target for a 5k-line PDF (spec says "~X s") — see G-2
- [ ] **E21-02** Barcode decode under 1 s offline
- [ ] **E21-03** Receipt scan under 15–20 s per image, perceived (batched, with progress)
- [ ] **E21-04** Indexing by account / date / store / user; performance holds at multi-year scale

---

## G. Gaps & ambiguities in the spec

*These are flags, not new requirements. The PO decides how each is resolved.*

| ID | Gap | Where |
|---|---|---|
| G-1 | **Cross-references are stale.** E.g. "§5 isolation" (§5 is now Recurring; isolation is in §3), "§15 attribution" (it's §3.5), "§12 AI scope" (copilot is §14), "§17 data model" (§17 is analytics), "§18 research note" (it's §20), "§21 privacy" (it's §23), and "respects §5 attribution" in §15.3/§17. | Throughout |
| G-2 | NFR "cold parse of 5k-line PDF under ~X s" has no number. | §22 |
| G-3 | ✅ **Resolved by E22 (2026-11-06).** **Notifications are required** (§7, §11, §15.2, §18) **but there's no delivery channel.** Standard PWA web push goes through Apple/Google push servers, which conflicts with "no data leaves home". | §2 vs §7 |
| G-4 | ✅ **Resolved by E24-13 (P-6).** **Camera/barcode scanning in a PWA needs HTTPS** (secure context). Plain `http://unraid-ip:port` on the LAN won't allow camera access. A local TLS strategy is needed. | §15.1, §21 |
| G-5 | ✅ **PO 2026-11-07: add calendar (iCal) import** → E24-08. — *was:* "Scheduled income/known bills from calendar or recurring-ofx blocks": which calendar? None is defined as a data source. | §5 |
| G-6 | ✅ **PO 2026-11-07: both** — contributions (built) *and* a goal can follow a savings account's balance → E24-03. — *was:* Goals are listed as an account type in §25.2 but defined as targets in §6. Is a goal an account or a view over accounts? **Interim (E11):** a goal is a view over contributions (manual, or statement transfers you link; future ones auto-counted). Tracking a savings account's balance directly isn't built. PO to confirm. | §6, §25.2 |
| G-7 | ✅ **PO 2026-11-07: no offline copy on devices** (app shell only, as built). — *was:* "Mirror to client device" (§2) implies offline client storage/sync, but §23 says sharing is server-local. What is cached on the device, and is it encrypted? | §2, §23 |
| G-8 | ✅ **PO 2026-11-07: a household option** — product data stays per household unless the owner opts in to share product names (never prices) with other households on this server → E24-02. — *was:* §3.3 says product/price data "may be shared globally". Globally across households on the same server only, presumably — confirm. | §3.3 |
| G-9 | ✅ **PO 2026-11-07: the leaving member chooses** whether their past transactions stay visible to the household or leave with them → E24-01. — *was:* No defined behaviour when a member leaves or is revoked: do their accounts, contributions and history stay in the household's rollups? | §3.2 |
| G-11 | ✅ **Confirmed by PO 2026-11-05 (interim rules adopted).** **Account visibility and default attribution.** §3.2 says a user's accounts "default to" counting toward the household or being hidden, but doesn't pick one. §3.5 "Hide" keeps an account "visible & usable within that account's own scope" — can other household members see a hidden account's transactions? **M1 interim:** the owner explicitly shares an account into a household and must choose the attribution (no default). Every member of that household can see it; attribution only affects totals. Revisit once the PO decides. | §3.2, §3.5 |
| G-12 | 🟡 **Partly resolved 2026-11-08:** AMEX UK CSV and Chase UK current-account PDF checked against real statements (totals and every running balance reconcile; fixes: AMEX signs flipped, AMEX categories used as hints, quote handling, Chase closing-balance line). Still welcome: other banks you use, a scanned/photo statement. — *was:* **Real statements needed for PDF/OCR tuning.** Every bank lays out PDFs differently. The reader handles the common UK "Date / Description / Paid out / Paid in / Balance" shape, but needs a few real (redacted) statements from the banks you use to confirm. Statements are processed locally only. | E03-04/05 |
| G-13 | ✅ **PO 2026-11-07: households can add their own categories** → E24-04. — *was:* **No category list in the spec.** Interim list: Groceries, Eating out, Transport, Bills, Housing, Shopping, Entertainment, Subscriptions, Health, Personal care, Travel, Kids, Pets, Gifts & donations, Cash, Transfers, Income, Fees & charges, Other. The "Transfers" label doesn't change totals (that's P-1). Custom per-household categories are not built. | E05, E07, E12 |
| G-14 | ✅ **PO 2026-11-07: the account owner chooses** which household's AI processes the account → E24-05. — *was:* **Which household's AI processes a shared account?** An account can be shared into several households, each with its own LLM setting. **Interim:** the first household it was shared into that has AI turned on. Nothing is sent if none do. | E05 |
| G-15 | ✅ **PO 2026-11-07: also spot likely trials** from £0.00/£1.00 charges on statements → E24-06. — *was:* **"Unused trial" interpretation.** Built as: trials you record with an end date → warning before it ends, then "became a paid plan" (charge seen) or "ended quietly". Automatic detection of trials from statements isn't possible (free trials leave no charge). | E07-03 |
| G-16 | ✅ **PO 2026-11-07: remember each bank's column layout** (import profiles) → E24-07. — *was:* **"Adjust per-account rules" (E09-03) interpretation.** Built as per-file choice of target account, day/month order and CSV column mapping, applied before import. Saved per-bank import profiles (remembering a mapping for next time) aren't built. | E09 |
| G-17 | ✅ **PO 2026-11-07: accepts supermarket website price lookups** despite the sites' terms → E24-15 (opt-in, polite, best effort). — *was:* **Price sources (E18).** No public UK supermarket price APIs exist and scraping their sites breaches their terms, so prices come from your own receipts (offline) and, opt-in, Open Prices (Open Food Facts, open data, barcode lookups, UK results only). Coverage will be thinner than a scraper's. PO to confirm. | E18 |
| G-10 | ✅ **PO 2026-11-07: current level is enough.** — *was:* "Tamper-evident" audit log has no threat model (tamper-evident against whom: other members, the admin, disk access?). **Interim answer (E10):** the app's database role can only add entries, never edit or delete them, and a hash chain flags any entry altered or removed directly in the database. It does **not** stop someone with full database admin access who also rebuilds the chain; that would need an off-server copy of the chain head. PO to confirm this is enough. | §13 |

---

## P. Proposed — awaiting PO approval

*Claude's suggestions. **Not in scope.** Reply approve / reject / modify per ID.*

| ID | Proposal | Why | Status |
|---|---|---|---|
| P-1 | **Internal transfer detection** between a household's own accounts (e.g. current → savings, card repayment) | Without it, transfers double-count as spend and inflate budgets and analytics | ✅ **Approved 2026-11-07** → E24-09 |
| P-2 | **Refunds/returns** linked to the original transaction | Otherwise refunds skew category spend and budgets | ✅ **Approved 2026-11-07** → E24-10 |
| P-3 | **Split transactions** (one payment across several categories or members) | Needed for "who paid for what" (§14) and per-member goal contributions | ✅ **Approved 2026-11-07** (household can turn it on/off) → E24-11 |
| P-4 | **Settle-up between household members** (who owes whom) | Natural follow-on to P-3 for partners/roommates | ✅ **Approved 2026-11-07** (household option) → E24-11 |
| P-5 | **Self-hosted notification channel** (in-app inbox plus optional ntfy/Gotify/Unraid notifications) | Resolves G-3 without breaking privacy | ✅ **Approved 2026-11-06** as E22 (Discord/Telegram/ntfy) |
| P-6 | **Local TLS** (e.g. reverse proxy with internal CA, or Tailscale/HTTPS cert) | Resolves G-4 so camera scanning works on phones | ✅ **Approved 2026-11-07** → E24-13 |
| P-7 | **Offline product database seed** (import an Open Food Facts dump locally) | Barcode scans can resolve product names/brands offline without an internet lookup | ✅ **Approved 2026-11-07**, plus use-by date scanning → E24-12 |
| P-8 | **Tenant-isolation test suite as a CI gate** (automated cross-household leakage tests on every query/AI path) | §2 says leakage = defect; this makes it enforceable | ✅ **Approved 2026-11-07** (already in CI) |
| P-9 | **Scheduled automatic backups** to an Unraid share, with a periodic restore check | §20 has export/restore but no schedule; financial data loss is costly | ✅ **Approved 2026-11-07** (built in E19) |
| P-10 | **Encryption-key management** (where the at-rest key lives, rotation, recovery) | §23 requires encryption at rest but doesn't say how keys are handled | ✅ **Approved 2026-11-07** → E24-14 (with E02-10) |
| P-11 | **MVP slice / milestones** (e.g. M1 = auth + households + CSV/OFX import + ledger + basic dashboard on mobile) | The handoff order is long; an early usable release reduces risk | ✅ **Approved 2026-11-05** → see M1 |
| P-12 | **Tech-stack ADR** (language, DB, frontend, OCR engine, barcode lib) before E02 | Locks foundational choices with PO sign-off | ✅ **Approved 2026-11-05**: Python/FastAPI + React/Vite PWA + PostgreSQL with row-level security → `docs/architecture.md` |
| P-13 | **Data deletion / account removal** (GDPR-style "delete my data" for a user leaving) | Pairs with G-9 | ✅ **Approved 2026-11-07** → E24-01 |

---

## Changelog

| Date | Version | Change |
|---|---|---|
| 2026-11-08 | 0.29 | E24 built: P-1, P-2, P-3/4, P-6, P-7, P-10, P-13, G-5, G-6, G-8, G-9, G-13, G-14, G-15, G-16 (all Review). Real AMEX/Chase statements tuned (G-12). G-17 scraping and G-2 still open. |
| 2026-11-07 | 0.28 | PO decisions: D-4 confirmed; G-5/6/7/8/9/10/13/14/15/16/17 resolved; P-1/2/3/4/6/7/8/9/10/13 approved → new epic E24. G-2 still open. |
| 2026-11-06 | 0.27 | Quantity trends from receipts (E13-01 complete). v0.1.3 image published. |
| 2026-11-06 | 0.26 | E18 price comparison (receipts + opt-in Open Prices, UK); cheapest-shop tags on the list. Security: ADMIN_EMAIL only claims admin while none exists; CLI set-admin. G-17. |
| 2026-11-06 | 0.25 | PO: every section is its own tab (no More page; phone tab bar scrolls). Security: backups no longer downloadable in the app; ADMIN_EMAIL decides the server admin. Released **v0.1.3**. |
| 2026-11-06 | 0.24 | E19 platform: backups (daily, pre-upgrade, encrypted option, restore CLI), server admin + sign-ups switch, opt-in update banner, personal export. D-4 built as proposed. |
| 2026-11-06 | 0.23 | E04 receipts (OCR, unit prices, discounts, payment matching, pantry/list). E23 kept originals (PO request). |
| 2026-11-06 | 0.22 | E17 shopping lists (shared + personal, favourites, from pantry, done-shopping → pantry, notifications); assistant can add to the list. Nav tab Pantry → Shopping (List / Pantry). |
| 2026-11-06 | 0.21 | E15 assistant (grounded answers with sources, confirmed actions, rules fallback). D-9 resolved. Same-day statement order preserved for balances. |
| 2026-11-06 | 0.20 | E14 pantry: products, batches with use-by, offline barcode scanning (camera/photo/typed), weekly shop suggestions, expiry alerts. Nav: Home, Activity, Bills, Pantry, More. |
| 2026-11-06 | 0.19 | E13 Insights (categories vs usual/budget, 12-month trend, weekly averages, top shops, who spent, seasonality, separate accounts). |
| 2026-11-06 | 0.18 | E12 budgets/limits/alerts + daily summary. Goal & budget edit permissions hardened. D-7 resolved. |
| 2026-11-06 | 0.17 | E11 savings goals. v0.1.2 image published. G-6 interim. |
| 2026-11-06 | 0.16 | E10 audit trail (append-only, hash-chained, History screen with undo). G-10 interim answer. |
| 2026-11-06 | 0.15 | v0.1.1 image build cancelled (stalled; tag kept, no image). Released **v0.1.2** from main (E08, ECB rates, E09, security fixes, faster build). |
| 2026-11-06 | 0.14 | E09 bulk import wizard (dry run, balance checks, adjust, whole-run rollback). G-16. |
| 2026-11-06 | 0.13 | D-8 resolved (ECB). E08-05/06 done. |
| 2026-11-06 | 0.12 | E08 cash wallet + multi-currency (manual rates). Security fixes for ntfy. 124 tests. |
| 2026-11-06 | 0.11 | E22 notifications (Discord/Telegram/ntfy, minimal by default, once per alert). Released **v0.1.1**. |
| 2026-11-06 | 0.10 | E07 subscriptions (Bills tab, trials, cancellations, suggestions). Version in side panel. E22 notifications approved by PO. G-15. |
| 2026-11-06 | 0.9 | E06 recurring payments (detection, next due, missed, price change, dismiss). Security: LLM URL origin-only + total deadline. 99 tests. |
| 2026-11-06 | 0.8 | E05 categorisation: rules + learned merchant rules + background local-LLM pass with injection guard; review flow in UI. G-13, G-14 added. |
| 2026-11-05 | 0.7 | E03: MT940, text PDF, scanned PDF / photo OCR import. G-12 added. |
| 2026-11-05 | 0.6 | UI redesign; security fixes; summary gains daily spend series. 61 tests. |
| 2026-11-05 | 0.5 | Repo set (D-3 resolved). Version shown in Settings → About. Renamed to Astris. First push + tag v0.1.0. |
| 2026-11-05 | 0.4 | PO accepted M1 and confirmed G-11. D-2 resolved: LLM configured in Settings. Added Settings screen (theme + LLM), Docker/Compose/Unraid template, release workflow → M1.1 in Review. GitHub owner/repo name added to D-3. 43 tests passing. |
| 2026-11-05 | 0.3 | M1 build: FastAPI + Postgres row-level security backend, CSV/OFX import, React PWA. 29 backend tests passing (incl. cross-household isolation at the API and DB level) plus a mobile/desktop browser click-through. M1 items moved to Review. G-11 interim rules implemented. |
| 2026-11-05 | 0.2 | D-1 and D-5 confirmed; D-2 partly answered (llama.cpp). P-11 (MVP) and P-12 (stack) approved. Added M1 milestone and G-11. Work started on M1. |
| 2026-11-05 | 0.1 | Initial backlog created from spec v0.4: 21 epics, 9 decisions, 10 gaps, 13 proposals awaiting approval. |
