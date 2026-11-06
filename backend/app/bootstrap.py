"""First-run database setup for Docker/Unraid. Idempotent; safe to run on every start.

With DB_ADMIN_URL (a Postgres superuser, e.g. the postgres container's POSTGRES_USER):
  * create/refresh roles ft_owner (schema owner, BYPASSRLS) and ft_app (runtime, subject to RLS)
  * create the database owned by ft_owner and lock down the public schema
Without DB_ADMIN_URL it only waits for the database to be reachable (roles created by hand, see scripts/bootstrap_db.sql).
"""
import sys
import time

import psycopg
from psycopg import sql

from app.config import get_settings


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _wait(url: str, timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            psycopg.connect(_plain(url), connect_timeout=3).close()
            return
        except psycopg.OperationalError as e:
            if time.monotonic() > deadline:
                print(f"[bootstrap] database not reachable: {e}", file=sys.stderr)
                raise SystemExit(1)
            time.sleep(2)


def _ensure_role(cur, name: str, password: str, extra: str) -> None:
    exists = cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    cur.execute(sql.SQL("{} ROLE {} LOGIN {} PASSWORD {}").format(
        sql.SQL(verb), sql.Identifier(name), sql.SQL(extra), sql.Literal(password)))


def main() -> None:
    s = get_settings()
    if not s.admin_email:
        print("[bootstrap] WARNING: ADMIN_EMAIL is not set, so the FIRST account registered becomes server admin. "
              "Set ADMIN_EMAIL to your own address in the container settings.", file=sys.stderr)
    if not s.db_admin_url:
        _wait(s.owner_db_url)
        print("[bootstrap] no DB_ADMIN_URL; assuming roles exist")
        return
    if s.db_app_password in ("", "ft_app_dev") or s.db_owner_password in ("", "ft_owner_dev"):
        print("[bootstrap] refusing to run with default/empty DB passwords: set DB_APP_PASSWORD and DB_OWNER_PASSWORD",
              file=sys.stderr)
        raise SystemExit(1)
    _wait(s.db_admin_url)
    with psycopg.connect(_plain(s.db_admin_url), autocommit=True) as c, c.cursor() as cur:
        _ensure_role(cur, "ft_owner", s.db_owner_password, "BYPASSRLS NOSUPERUSER")
        _ensure_role(cur, "ft_app", s.db_app_password, "NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE")
        if not cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (s.db_name,)).fetchone():
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER ft_owner").format(sql.Identifier(s.db_name)))
            print(f"[bootstrap] created database {s.db_name}")
    admin_db = _plain(s.db_admin_url).rsplit("/", 1)[0] + "/" + s.db_name
    with psycopg.connect(admin_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        cur.execute("ALTER SCHEMA public OWNER TO ft_owner")
        cur.execute("GRANT USAGE ON SCHEMA public TO ft_app")
    print("[bootstrap] roles and database ready")


if __name__ == "__main__":
    main()
