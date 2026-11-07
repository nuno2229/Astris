# Astris — Finance Tracker

Private, self-hosted household money hub. Scope and status are tracked in [BACKLOG.md](BACKLOG.md); design in [docs/architecture.md](docs/architecture.md).

**Navigation:** Home, Activity, Bills, Budgets, Goals, Insights, Shopping (list, pantry, receipts), Accounts, Household,
History, and Settings (gear). On phones the tab bar scrolls sideways.

## Local development (macOS)

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

## Install on Unraid

The image is published to GHCR when a `v*` tag is pushed (`.github/workflows/release.yml`).

**Option A — Docker Compose Manager plugin:** copy `docker-compose.yml` and `.env.docker.example` (renamed to `.env`, real passwords set) into a stack, then *Compose Up*. This runs Postgres and the app together.

**Option B — template + existing Postgres container:**
1. Copy `unraid/astris.xml` to `/boot/config/plugins/dockerMan/templates-user/my-astris.xml`.
2. Docker → Add Container → pick *astris*.
3. Fill in the Postgres host, the admin URL (first run only), two new random passwords and **Admin email** (your address).
4. Open `http://<unraid-ip>:8484` and create the first account. Turn off *Allow new sign-ups* once everyone has one.

On every start the container waits for Postgres, creates roles and the database if needed, applies migrations, then starts. `/api/health` reports the DB state and version.

## Updating, backups and rolling back (D-4)

- **Update:** Unraid's Docker tab shows "update ready" when a new image is published; click *apply update*. Server admins
  can also turn on Settings → Server → "Tell me when an update is available" (checks GitHub once a day; off by default).
- **Automatic safety net:** when a new version changes the database, the container takes a `pre-migrate` backup before
  applying the change. Daily backups are kept for 14 days (`BACKUP_KEEP`) in the *Backups* folder of the template.
- **Server admin:** register with the `ADMIN_EMAIL` address right after the first start (it only claims admin while
  there is none), or assign it from the server: `docker exec -it astris python -m app.admin_cli set-admin you@example.com`.
- **Back up now:** Settings → Server.
  Backups contain everyone's data and credentials, so they're never downloadable through the app: copy them from the
  *Backups* share on the server.
- **Encrypted backups:** set `BACKUP_PASSPHRASE`. Without it, an encrypted backup can't be restored.
- **Roll back to an older version:**
  1. In the Unraid template, change the image tag from `:latest` to the previous version, e.g. `ghcr.io/nuno2229/astris:0.1.2`.
  2. Restore the backup taken just before the upgrade:
     ```bash
     docker exec -it astris python -m app.backup list
     docker exec -it astris python -m app.backup restore astris-YYYYMMDD-HHMMSS-pre-migrate.astrisbak --yes
     ```
     Restore **replaces all data** with the backup's. It needs the schema-owner credentials the container already has.
- **Personal export:** anyone can download their own data (Settings → Your data) as JSON.

## Naming scanned products offline (optional)

Scanned barcodes can be named automatically without the internet. Load the UK part of the Open Food Facts product list once:

1. Download `en.openfoodfacts.org.products.csv.gz` from <https://world.openfoodfacts.org/data> (about 1 GB) into the Backups folder you mapped (e.g. `/mnt/user/appdata/astris/backups/`, which the container sees as `/data/backups`).
2. Run it through the container (only UK products are kept, no prices):
   ```sh
   docker exec -it astris python -m app.catalog import-off /data/backups/en.openfoodfacts.org.products.csv.gz
   ```
3. Delete the download afterwards if you like. Run it again now and then to refresh.

Households can also choose to share the names they give products (Shopping → Pantry → "Share product names"). Only barcode, name and size are shared, never prices or who bought it.

## Live camera scanning on phones (HTTPS)

Phones only allow the live camera on a secure (https) address. Taking a photo of a barcode works without it.

1. In the Unraid template set **HTTPS** to `self-signed` and **HTTPS addresses** to how you open Astris (e.g. `192.168.1.20,tower.local`). Apply.
2. Open `https://192.168.1.20:8484/ca.crt` on each phone and install it:
   - **iPhone:** open the downloaded profile in Settings, install it, then Settings → General → About → Certificate Trust Settings → turn on "Astris home server CA".
   - **Android:** Settings → Security → Encryption & credentials → Install a certificate → CA certificate.
3. Use `https://…` from now on.

The certificate files live in the Backups folder under `tls/`; the private key never leaves the server. Prefer a real certificate? Put Astris behind your own reverse proxy (or `tailscale serve`) instead and leave HTTPS off.
