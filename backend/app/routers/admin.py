"""Server administration (E19): sign-ups, opt-in update check, backups. Server admin only (first account).
Plus a personal export for every user (portability)."""
import io
import json
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text

from app import backup
from app.config import app_version
from app.deps import DB, User

router = APIRouter(prefix="/api", tags=["admin"])


class ServerOut(BaseModel):
    is_admin: bool
    version: str
    registration_open: bool
    update_check: bool
    latest_version: str | None
    latest_url: str | None
    update_available: bool
    checked_at: datetime | None
    backups: list[dict] = []
    backups_encrypted: bool = False
    files_encrypted: bool = False   # ASTRIS_KEY set: kept files and secrets encrypted at rest (P-10)
    backup_location: str = ""   # backups stay on the server: full-database dumps are never served over the app (isolation)


class ServerIn(BaseModel):
    registration_open: bool | None = None
    update_check: bool | None = None


def _is_admin(db: DB, user) -> bool:
    return bool(db.execute(text("SELECT is_server_admin FROM users WHERE id = :u"), {"u": user.id}).scalar())


def _require_admin(db: DB, user) -> None:
    if not _is_admin(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the server admin can do this")


def newer(latest: str | None, current: str) -> bool:
    def parts(v):
        import re
        m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", v or "")
        return tuple(int(x) for x in m.groups()) if m else None
    a, b = parts(latest), parts(current)
    return bool(a and b and a > b)


@router.get("/server", response_model=ServerOut)
def server(user: User, db: DB):
    s = db.execute(text("SELECT * FROM server_settings WHERE id = 1")).one()
    admin = _is_admin(db, user)
    from app.config import get_settings
    return ServerOut(is_admin=admin, version=app_version(), registration_open=s.registration_open, update_check=s.update_check,
                     latest_version=s.latest_version, latest_url=s.latest_url, checked_at=s.checked_at,
                     update_available=s.update_check and newer(s.latest_version, app_version()),
                     backups=backup.list_backups() if admin else [], backups_encrypted=bool(get_settings().backup_passphrase),
                     files_encrypted=__import__("app.crypto", fromlist=["enabled"]).enabled(),
                     backup_location=str(backup.backup_dir()) if admin else "")


@router.put("/server", response_model=ServerOut)
def update_server(body: ServerIn, user: User, db: DB):
    _require_admin(db, user)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)   # keys from the schema only
        db.execute(text(f"UPDATE server_settings SET {sets}, updated_at = now() WHERE id = 1"), fields)
        from app import audit
        audit.log(db, user.id, "server.settings", "server", "Server settings: " + ", ".join(f"{k}={v}" for k, v in fields.items()))
    if body.update_check:
        from app.categorise.worker import worker
        worker.wake_updates()
    return server(user, db)


@router.post("/server/backups", status_code=201)
async def backup_now(user: User, db: DB):
    _require_admin(db, user)
    p = await run_in_threadpool(backup.create, "manual")
    from app import audit
    audit.log(db, user.id, "server.backup", "server", f"Backup created: {p.name}")
    return {"name": p.name}


def check_for_update(db_factory) -> str | None:
    """Worker hook (opt-in): ask GitHub for the latest release tag. Sends nothing about the user."""
    from app.config import get_settings
    from app.netcheck import pinned_request
    with db_factory() as s:
        if not s.execute(text("SELECT server_update_check_on()")).scalar():
            return None
    try:
        code, raw = pinned_request(f"https://api.github.com/repos/{get_settings().github_repo}/releases/latest", timeout=15,
                                   max_bytes=512 * 1024, headers={"User-Agent": "astris-update-check", "Accept": "application/vnd.github+json"})
        if code != 200:
            return None
        d = json.loads(raw)
        tag, url = str(d.get("tag_name", ""))[:40], str(d.get("html_url", ""))[:300]
    except Exception:
        return None
    if not url.startswith("https://github.com/"):
        url = ""
    with db_factory() as s, s.begin():
        s.execute(text("SELECT server_record_latest(:v, :u)"), {"v": tag, "u": url})
    return tag


EXPORT_TABLES = ["households", "memberships", "accounts", "household_accounts", "transactions", "import_batches",
                 "recurring_series", "subscriptions", "goals", "goal_contributions", "budgets", "products", "stock_lots",
                 "shopping_lists", "shopping_items", "favourites", "receipts", "receipt_items", "merchant_rules",
                 "notification_prefs", "fx_rates", "audit_log"]


@router.get("/me/export")
def export_mine(user: User, db: DB):
    """Everything you can see, as JSON in a zip (portability). Kept files are listed, not included."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        me = db.execute(text("SELECT id, email, display_name, home_currency, created_at FROM users WHERE id = :u"), {"u": user.id}).one()
        z.writestr("me.json", json.dumps(dict(me._mapping), default=str, indent=1))
        for t in EXPORT_TABLES:
            rows = db.execute(text(f"SELECT to_jsonb(x) FROM {t} x")).scalars().all()   # RLS: only what you can see
            z.writestr(f"{t}.json", json.dumps(rows, default=str))
        docs = db.execute(text("SELECT id, kind, filename, size, created_at FROM documents")).all()
        z.writestr("kept_files.json", json.dumps([dict(r._mapping) for r in docs], default=str))
        z.writestr("README.txt", f"Astris export for {me.email}, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, app {app_version()}.\n"
                                 "One JSON file per table, containing only the rows you can see. Kept original files are listed in\n"
                                 "kept_files.json and can be downloaded from the app.\n")
    name = f"astris-export-{datetime.now():%Y%m%d}.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "private, no-store"})
