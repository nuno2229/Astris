<p align="center"><img src="unraid/icon.png" width="96" alt="Astris logo"></p>

# Astris

A private, self-hosted money and shopping hub for your household. Import your bank statements and see where the money goes, keep bills, budgets and savings goals in one place, and run the pantry and shopping list together. It runs on your own server (made for Unraid), and nothing leaves your home unless you switch it on.

Scope and status: [BACKLOG.md](BACKLOG.md). Design: [docs/architecture.md](docs/architecture.md).

## What it does

- **Statements in, sense out.** Import CSV, OFX, MT940 or PDF statements, or a photo of a paper one (tested with AMEX UK and Chase UK). Duplicates are skipped, balances are checked, and each bank's column layout is remembered.
- **Categories that sort themselves.** Built-in UK rules, rules learned from your corrections, the bank's own labels (e.g. AMEX) and, if you have one, your local AI. Households can add their own categories.
- **Counted once, correctly.** Moves between your own accounts (current → savings, card repayments, Chase Saver and pots) are transfers, not spending. Refunds come off what they refund. Payments can be split across categories or people.
- **Households.** Share an account "in totals" or "separate"; everyone sees only what's shared with them. Settle up who owes whom. When someone leaves, they choose whether their past transactions stay.
- **Bills and subscriptions.** Regular payments are found automatically, with what's due next, what didn't arrive and price changes. Trials are spotted from £0/£1 card checks. Bills and pay can also come from a calendar file or link.
- **Budgets, goals, insights.** Monthly budgets with warnings. Savings goals that count contributions or follow an account's balance. Insights cover spending vs usual, seasonal months, top shops, net worth over time, and what you go through (e.g. litres of milk a week).
- **Pantry and shopping.** Scan barcodes (camera or photo), track use-by dates (read from a photo of the pack), get low-stock lists, and share lists that tick back into the pantry. Scanning a receipt gives unit prices, links it to the card payment (never counted twice) and splits that payment by what was bought.
- **Prices.** Compare shops from your receipts, prices you send from supermarket websites with one tap, and optional Morrisons/Aldi and Open Prices lookups.
- **Assistant.** Ask "how much did we spend on groceries this month?" or "add milk to the list". Answers come from your data with sources; actions always ask first.
- **Messages.** Discord, Telegram or ntfy alerts for renewals, budgets, missed payments and expiring food, plus weekly and monthly round-ups on the day and time you choose.
- **Looked after.** Daily backups (optionally encrypted) plus one before every upgrade, a tamper-evident history with undo, encryption of the most sensitive stored items, and "delete my account".

## Install on Unraid

Astris is one container with its own database inside its AppData folder. There's nothing else to install or set up.

1. Open the Unraid **terminal** and run once:
   ```sh
   wget -O /boot/config/plugins/dockerMan/templates-user/my-astris.xml https://raw.githubusercontent.com/nuno2229/Astris/main/unraid/astris.xml
   ```
2. **Docker → Add Container → Template: astris.** Put your email in **Admin email**, then **Apply**.
3. Open `http://<unraid-ip>:8484` and create your account with that email: it becomes the server admin. Invite the rest of your household from **Household**, then turn off sign-ups in **Settings → Server**.

Everything lives in `/mnt/user/appdata/astris`:

| | |
|---|---|
| `postgres/` | the database |
| `backups/` | daily backups, one before every upgrade, and `tls/` if HTTPS is on |
| `secrets.env` | the database passwords generated on first start; keep it with your backups |

**Already running Postgres?** In the template's advanced view, set *External database host* and the three external database settings, and Astris uses that instead.

**Docker Compose** (any machine): copy `docker-compose.yml` and `.env.docker.example` (as `.env`), then run `docker compose up -d`.

## Optional features

Each of these is off until someone turns it on.

| Feature | Where | Notes |
|---|---|---|
| Local AI (llama.cpp) for categories and the assistant | Settings → AI assistant | Address of your llama.cpp server. Each account owner can pick which household's AI labels their account, or none (Accounts). |
| Notifications and round-ups | Settings → Notifications | Discord, Telegram or ntfy. Messages say little unless you turn on details. Round-ups: weekly and/or monthly, any day and time. |
| Meal plans from Mealie | Settings → Meal plans | Address plus an API token. Home then shows **Cooking this week**, with ingredients checked against the pantry. Read-only. |
| Calendar bills and pay | Bills → All regular payments | Upload an `.ics` file, or add a calendar link. Put the amount in the event title, e.g. "Rent £950". |
| Settle up | Household → Settle up | Owner turns it on. Split a payment "for" people, record repayments; the receiver confirms. |
| Send prices from shop websites | Shopping → Prices | Add the **Send price to Astris** button to your bookmarks, then tap it on any Tesco, Sainsbury's, Asda… product page. |
| Morrisons and Aldi lookups | Shopping → Prices | Owner turns it on. The server searches those two sites for items on the shared list and household favourites, a few times a week. Tesco, Sainsbury's and Asda block automated lookups, so use the button for them. |
| Open Prices, exchange rates, update check | Shopping → Prices, Settings | Open data lookups, opt-in each. |
| Share product names | Shopping → Pantry | Other households on your server can name scanned barcodes from yours. Name and size only, never prices or who bought it. |

## What uses the internet

Nothing, unless you switch it on: the local AI if it's outside your network, Discord/Telegram, calendar links, Morrisons/Aldi lookups, Open Prices, ECB exchange rates and the update check. Each of these is labelled **Uses internet** where you turn it on. Statements, receipts and photos are read on your server.

## Updating, backups and rolling back

- **Update:** Unraid's Docker tab shows "update ready" when a new version is published; click *apply update*. Server admins can also turn on Settings → Server → "Tell me when an update is available" (checks GitHub once a day; off by default).
- **Automatic safety net:** when a new version changes the database, a backup is taken first. Daily backups are kept for 14 days (`BACKUP_KEEP`) in `backups/`.
- **Back up now:** Settings → Server. Backups contain everyone's data, so they're never downloadable through the app: copy them from the AppData folder.
- **Encrypted backups:** set **Backup passphrase** in the template. Without the passphrase an encrypted backup can't be restored.
- **Server admin:** register with the **Admin email** address (it only claims admin while there is none), or assign it from the terminal: `docker exec -it astris python -m app.admin_cli set-admin you@example.com`.
- **Roll back to an older version:**
  1. In the template, change the image tag from `:latest` to the previous version, e.g. `ghcr.io/nuno2229/astris:0.1.6`.
  2. Restore the backup taken just before the upgrade:
     ```bash
     docker exec -it astris python -m app.backup list
     docker exec -it astris python -m app.backup restore astris-YYYYMMDD-HHMMSS-pre-migrate.astrisbak --yes
     ```
     Restoring **replaces all data** with the backup's.
- **Your own data:** anyone can download everything they can see (Settings → Your data) as JSON, or delete their account there.

## Naming scanned products offline (optional)

Scanned barcodes can be named automatically without the internet. Load the UK part of the Open Food Facts product list once:

1. Download `en.openfoodfacts.org.products.csv.gz` from <https://world.openfoodfacts.org/data> (about 1 GB) into the AppData folder, `/mnt/user/appdata/astris/` (the container sees it as `/data`).
2. Import it (only UK products are kept, no prices):
   ```sh
   docker exec -it astris python -m app.catalog import-off /data/en.openfoodfacts.org.products.csv.gz
   ```
3. Delete the download afterwards if you like. Run it again now and then to refresh.

## Live camera scanning on phones (HTTPS)

Phones only allow the live camera on a secure (https) address. Taking a photo of a barcode works without it.

1. In the Unraid template set **HTTPS** to `self-signed` and **HTTPS addresses** to how you open Astris (e.g. `192.168.1.20,tower.local`). Apply.
2. Open `https://192.168.1.20:8484/ca.crt` on each phone and install it:
   - **iPhone:** open the downloaded profile in Settings, install it, then Settings → General → About → Certificate Trust Settings → turn on "Astris home server CA".
   - **Android:** Settings → Security → Encryption & credentials → Install a certificate → CA certificate.
3. Use `https://…` from now on.

The certificate files live in `backups/tls/` in the AppData folder; the private key never leaves the server. The certificate can only vouch for the addresses you listed, so it can't be misused for other websites. Changing the addresses makes a new one: install it on the phones again. Prefer a real certificate? Put Astris behind your own reverse proxy (or `tailscale serve`) instead and leave HTTPS off.

## Encryption at rest

Two layers, because Postgres itself doesn't encrypt its files:

1. **The whole database: encrypt the disk.** Put the appdata share (Postgres data and backups) on an encrypted Unraid array or pool (XFS/BTRFS-encrypted). The key is your Unraid passphrase or keyfile, entered when the array starts.
2. **The most sensitive things Astris stores: encrypted by the app.** Kept statement/receipt files, notification webhook URLs and bot tokens, calendar links and the Mealie token are encrypted with AES-256 using your **Encryption key**:
   ```sh
   docker exec astris python -m app.crypto new-key      # put the output in the template's "Encryption key", apply
   docker exec astris python -m app.crypto rewrap       # encrypt anything stored before the key was set
   docker exec astris python -m app.crypto status
   ```
   **Rotating:** put the old key in `ASTRIS_OLD_KEYS`, the new one in "Encryption key", restart, run `rewrap`, then clear `ASTRIS_OLD_KEYS`.
   **Recovery:** the key never goes in the database or backups. Without it, those files and secrets can't be read, even from a backup. Keep it in your password manager next to the backup passphrase.

## Troubleshooting

- **The page won't open:** run `docker logs astris` in the Unraid terminal. The first start takes a minute while the database is created.
- **"the database … is PostgreSQL N but this image has M":** a new image moved to a newer PostgreSQL. Either use the previous image version, or start a fresh AppData folder and restore your latest backup into it.
- **Wrong or old icon in Unraid:** Docker → astris → Edit → Advanced View → set *Icon URL* to `https://raw.githubusercontent.com/nuno2229/Astris/main/unraid/icon.png`. If the old one still shows, delete Unraid's saved copy with `rm -f /var/lib/docker/unraid/images/astris-icon.png`.
- **Health check:** `http://<unraid-ip>:8484/api/health` shows the version and whether the database is reachable.

## Development

Prereqs: PostgreSQL 15+, Python 3.13+, Node 20+.

```bash
# 1. one-time: roles + databases (as a Postgres superuser)
for db in finance_tracker finance_tracker_test; do
  psql -d postgres -v ON_ERROR_STOP=1 -v owner_pw=ft_owner_dev -v app_pw=ft_app_dev -v dbname=$db -f scripts/bootstrap_db.sql
done

# 2. backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env
.venv/bin/alembic upgrade head
.venv/bin/pytest -q                       # includes cross-household isolation tests
.venv/bin/uvicorn app.main:app --reload   # http://localhost:8000 (serves frontend/dist if built)

# 3. frontend (hot reload on :5173, proxies /api to :8000)
cd ../frontend && npm install && npm run dev
```

Production build of the PWA: `cd frontend && npm run build`. The backend then serves it at `/`.
