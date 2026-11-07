#!/bin/sh
set -e
python -m app.bootstrap
# safety net (D-4): if this version brings database changes, back up before applying them
python -m app.backup pre-migrate
alembic upgrade head
if [ "${HTTPS:-off}" = "self-signed" ]; then
  # local HTTPS (P-6) so phones allow live camera scanning; install /ca.crt on each phone once
  TLS=$(python -m app.tls ensure)
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}" \
       --ssl-keyfile "$TLS/server.key" --ssl-certfile "$TLS/server.crt"
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"
