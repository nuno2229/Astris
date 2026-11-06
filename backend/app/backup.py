"""Backups (E19-05). Logical, version-aware, optionally encrypted. Runs with the schema-owner role (BYPASSRLS),
never through the API's user-scoped connection.

  python -m app.backup create [reason]   # write a backup now
  python -m app.backup list
  python -m app.backup pre-migrate       # (entrypoint) back up only if migrations are pending
  python -m app.backup restore FILE --yes   # REPLACES all data with the backup's

Format: zip with manifest.json + one CSV per table (COPY), tables in foreign-key order. With BACKUP_PASSPHRASE the
zip is encrypted (scrypt key + Fernet/AES) and saved as .astrisbak.enc.
"""
import base64
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from app.config import app_version, get_settings

NAME_RE = re.compile(r"^astris-\d{8}-\d{6}-[a-z0-9-]{1,30}\.astrisbak(\.enc)?$")
MAGIC = b"ASTRISENC1"


def _dsn() -> str:
    return get_settings().owner_db_url.replace("postgresql+psycopg://", "postgresql://", 1)


def backup_dir() -> Path:
    d = Path(get_settings().backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tables(cur) -> list[str]:
    """Public tables in foreign-key dependency order (parents first). Self-references are ignored."""
    tabs = [r[0] for r in cur.execute("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                                         WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'
                                         ORDER BY c.relname""")]
    deps = {t: set() for t in tabs}
    for child, parent in cur.execute("""SELECT cl.relname, pr.relname FROM pg_constraint co
                                        JOIN pg_class cl ON cl.oid = co.conrelid JOIN pg_class pr ON pr.oid = co.confrelid
                                        JOIN pg_namespace n ON n.oid = cl.relnamespace
                                        WHERE co.contype = 'f' AND n.nspname = 'public'"""):
        if child in deps and parent in deps and child != parent:
            deps[child].add(parent)
    order, seen = [], set()

    def visit(t, stack=()):
        if t in seen:
            return
        if t in stack:
            return                      # cycle: break it (FKs are deferred/filled in order anyway)
        for p in sorted(deps[t]):
            visit(p, stack + (t,))
        seen.add(t)
        order.append(t)
    for t in tabs:
        visit(t)
    return order


def _key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return base64.urlsafe_b64encode(Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode()))


def encrypt(data: bytes, passphrase: str) -> bytes:
    from cryptography.fernet import Fernet
    salt = os.urandom(16)
    return MAGIC + salt + Fernet(_key(passphrase, salt)).encrypt(data)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    from cryptography.fernet import Fernet, InvalidToken
    if not blob.startswith(MAGIC):
        raise ValueError("Not an encrypted Astris backup")
    salt = blob[len(MAGIC):len(MAGIC) + 16]
    try:
        return Fernet(_key(passphrase, salt)).decrypt(blob[len(MAGIC) + 16:])
    except InvalidToken:
        raise ValueError("Wrong passphrase, or the backup is damaged")


def create(reason: str = "manual") -> Path:
    s = get_settings()
    buf = io.BytesIO()
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")   # one consistent snapshot
        version = cur.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = _tables(cur)
        counts = {}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for t in tables:
                out = io.BytesIO()
                with cur.copy(f'COPY public."{t}" TO STDOUT WITH (FORMAT csv, HEADER true)') as cp:
                    for chunk in cp:
                        out.write(chunk)
                z.writestr(f"tables/{t}.csv", out.getvalue())
                counts[t] = cur.execute(f'SELECT count(*) FROM public."{t}"').fetchone()[0]
            z.writestr("manifest.json", json.dumps({
                "format": 1, "schema_version": version, "app_version": app_version(),
                "created_at": datetime.now(timezone.utc).isoformat(), "reason": reason, "tables": tables, "rows": counts}, indent=1))
        conn.rollback()
    data = buf.getvalue()
    tag = re.sub(r"[^a-z0-9-]", "-", reason.lower())[:30] or "manual"
    name = f"astris-{datetime.now():%Y%m%d-%H%M%S}-{tag}.astrisbak"
    if s.backup_passphrase:
        data, name = encrypt(data, s.backup_passphrase), name + ".enc"
    path = backup_dir() / name
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    prune()
    return path


def list_backups() -> list[dict]:
    out = []
    for p in sorted(backup_dir().glob("astris-*.astrisbak*"), reverse=True):
        if NAME_RE.match(p.name):
            st = p.stat()
            out.append({"name": p.name, "size": st.st_size, "created_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                        "encrypted": p.name.endswith(".enc")})
    return out


def prune() -> None:
    """Keep the newest N daily/manual backups; pre-migration backups are kept for 90 days regardless."""
    keep = get_settings().backup_keep
    regular = [b for b in list_backups() if "-pre-migrate" not in b["name"]]
    for b in regular[keep:]:
        (backup_dir() / b["name"]).unlink(missing_ok=True)
    cutoff = datetime.now(timezone.utc).timestamp() - 90 * 86400
    for b in list_backups():
        if "-pre-migrate" in b["name"] and datetime.fromisoformat(b["created_at"]).timestamp() < cutoff:
            (backup_dir() / b["name"]).unlink(missing_ok=True)


def resolve(name: str) -> Path:
    """Safe path for a backup name (no traversal)."""
    if not NAME_RE.match(name or ""):
        raise FileNotFoundError(name)
    p = (backup_dir() / name).resolve()
    if p.parent != backup_dir().resolve() or not p.is_file():
        raise FileNotFoundError(name)
    return p


def _alembic(target: str, direction: str) -> None:
    from alembic import command
    from alembic.config import Config
    here = Path(__file__).resolve().parents[1]
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "migrations"))
    (command.upgrade if direction == "up" else command.downgrade)(cfg, target)


def migrations_pending() -> bool:
    from alembic.script import ScriptDirectory
    from alembic.config import Config
    here = Path(__file__).resolve().parents[1]
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    try:
        with psycopg.connect(_dsn()) as conn:
            cur = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except psycopg.errors.UndefinedTable:
        return False                      # brand-new database: nothing to protect
    return cur is not None and cur[0] != head


def restore(path: Path, passphrase: str | None = None) -> dict:
    """Replace ALL data with the backup's. Schema is brought to the backup's version, data loaded, then upgraded."""
    blob = path.read_bytes()
    if blob.startswith(MAGIC):
        blob = decrypt(blob, passphrase or get_settings().backup_passphrase)
    z = zipfile.ZipFile(io.BytesIO(blob))
    m = json.loads(z.read("manifest.json"))
    if m.get("format") != 1:
        raise ValueError("Unknown backup format")
    _alembic("base", "down")
    _alembic(m["schema_version"], "up")
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        tables = _tables(cur)
        cur.execute("TRUNCATE " + ", ".join(f'public."{t}"' for t in tables) + " CASCADE")
        for t in tables:
            cur.execute(f'ALTER TABLE public."{t}" DISABLE TRIGGER USER')   # keep audit hashes exactly as backed up
        for t in m["tables"]:
            if t not in tables:
                continue
            data = z.read(f"tables/{t}.csv")
            with cur.copy(f'COPY public."{t}" FROM STDIN WITH (FORMAT csv, HEADER true)') as cp:
                cp.write(data)
        for t in tables:
            cur.execute(f'ALTER TABLE public."{t}" ENABLE TRIGGER USER')
        for tbl, col in cur.execute("""SELECT c.table_name, c.column_name FROM information_schema.columns c
                                        WHERE c.table_schema = 'public' AND c.column_default LIKE 'nextval%'""").fetchall():
            cur.execute(f"""SELECT setval(pg_get_serial_sequence('public."{tbl}"', '{col}'),
                                          COALESCE((SELECT max("{col}") FROM public."{tbl}"), 0) + 1, false)""")
        conn.commit()
    _alembic("head", "up")
    return m


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"
    if cmd == "create":
        print(create(argv[1] if len(argv) > 1 else "manual"))
    elif cmd == "list":
        for b in list_backups():
            print(f"{b['name']}  {b['size'] // 1024} KB")
    elif cmd == "pre-migrate":
        if migrations_pending():
            print("[backup] migrations pending: backing up first ->", create("pre-migrate"))
        else:
            print("[backup] no pending migrations")
    elif cmd == "restore":
        if len(argv) < 2 or "--yes" not in argv:
            print("Usage: python -m app.backup restore FILE --yes   (replaces ALL data)", file=sys.stderr)
            return 2
        p = Path(argv[1])
        if not p.is_file():
            p = resolve(argv[1])
        m = restore(p, os.environ.get("BACKUP_PASSPHRASE"))
        print(f"Restored backup from {m['created_at']} (app {m['app_version']}, {sum(m['rows'].values())} rows)")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
