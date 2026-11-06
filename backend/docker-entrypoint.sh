#!/bin/sh
set -e
python -m app.bootstrap
# safety net (D-4): if this version brings database changes, back up before applying them
python -m app.backup pre-migrate
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"
