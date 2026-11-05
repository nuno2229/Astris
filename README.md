# Astris — Finance Tracker

Private, self-hosted household money hub. Scope and status are tracked in [BACKLOG.md](BACKLOG.md); design in [docs/architecture.md](docs/architecture.md).

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
3. Fill in the Postgres host, the admin URL (first run only) and two new random passwords.
4. Open `http://<unraid-ip>:8484` and create the first account. Turn off *Allow new sign-ups* once everyone has one.

On every start the container waits for Postgres, creates roles and the database if needed, applies migrations, then starts. `/api/health` reports the DB state and version.
