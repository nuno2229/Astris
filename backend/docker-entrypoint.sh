#!/bin/sh
# Astris container start.
#  - All-in-one (default when DB_HOST isn't set): Astris runs its own PostgreSQL inside /data. One container, one
#    folder, passwords generated on first start and kept in /data/secrets.env.
#  - External database: set DB_HOST (+ DB_ADMIN_URL on first run, DB_APP_PASSWORD, DB_OWNER_PASSWORD) as before.
set -e
DATA="${DATA_DIR:-/data}"

# Unraid appdata belongs to nobody:users (99:100). Start as root only to fix ownership, then drop privileges.
if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-99}"; PGID="${PGID:-100}"
  mkdir -p "$DATA" "${BACKUP_DIR:-$DATA/backups}"
  chown "$PUID:$PGID" "$DATA"
  chown -R "$PUID:$PGID" "${BACKUP_DIR:-$DATA/backups}"
  [ -d "$DATA/postgres" ] && chown -R "$PUID:$PGID" "$DATA/postgres"
  [ -f "$DATA/secrets.env" ] && chown "$PUID:$PGID" "$DATA/secrets.env"
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups env HOME=/tmp "$0" "$@"
fi

EMBEDDED=""
if [ -z "${DB_HOST:-}" ] || [ "$DB_HOST" = "embedded" ]; then
  EMBEDDED=1
  PGBIN="${PG_BIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
  PGPORT="${EMBEDDED_PG_PORT:-5432}"
  PGDATA="$DATA/postgres"
  umask 077
  if [ ! -f "$DATA/secrets.env" ]; then
    rnd() { python -c "import secrets; print(secrets.token_urlsafe(32))"; }
    { echo "PG_SUPER_PASSWORD=$(rnd)"; echo "DB_APP_PASSWORD=$(rnd)"; echo "DB_OWNER_PASSWORD=$(rnd)"; } > "$DATA/secrets.env"
    echo "[astris] created $DATA/secrets.env (database passwords; keep it with your backups)"
  fi
  . "$DATA/secrets.env"
  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "[astris] creating the built-in database in $PGDATA"
    PWF=$(mktemp); printf '%s' "$PG_SUPER_PASSWORD" > "$PWF"
    "$PGBIN/initdb" -D "$PGDATA" -U postgres --auth=scram-sha-256 --pwfile="$PWF" -E UTF8 --locale="${PG_LOCALE:-C.UTF-8}" >/dev/null
    rm -f "$PWF"
  fi
  HAVE=$(cat "$PGDATA/PG_VERSION"); WANT=$("$PGBIN/pg_ctl" --version | sed -E 's/.* ([0-9]+)\..*/\1/')
  if [ "$HAVE" != "$WANT" ]; then
    echo "[astris] the database in $PGDATA is PostgreSQL $HAVE but this image has $WANT. Restore your latest backup into a" \
         "fresh folder (README: Updating) or use an image version with PostgreSQL $HAVE." >&2
    exit 1
  fi
  "$PGBIN/pg_ctl" -D "$PGDATA" -l "$DATA/postgres.log" -w -t 60 \
      -o "-c listen_addresses=127.0.0.1 -c port=$PGPORT -c unix_socket_directories=/tmp" start >/dev/null
  export DB_HOST=127.0.0.1 DB_PORT="$PGPORT" DB_APP_PASSWORD DB_OWNER_PASSWORD
  export DB_ADMIN_URL="postgresql://postgres:${PG_SUPER_PASSWORD}@127.0.0.1:${PGPORT}/postgres"
  unset PG_SUPER_PASSWORD
fi

python -m app.bootstrap
# safety net (D-4): if this version brings database changes, back up before applying them
python -m app.backup pre-migrate
alembic upgrade head

set -- uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT:-8000}" --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"
if [ "${HTTPS:-off}" = "self-signed" ]; then
  # local HTTPS (P-6) so phones allow live camera scanning; install /ca.crt on each phone once
  TLS=$(python -m app.tls ensure)
  set -- "$@" --ssl-keyfile "$TLS/server.key" --ssl-certfile "$TLS/server.crt"
fi
if [ -z "$EMBEDDED" ]; then
  exec "$@"
fi
# all-in-one: run the app, and shut the database down cleanly when the container stops
"$@" &
APP=$!
trap 'kill -TERM $APP 2>/dev/null' TERM INT
wait $APP || true
wait $APP 2>/dev/null || true
"$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null
