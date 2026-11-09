# syntax=docker/dockerfile:1.7
# ---------- frontend build ----------
# built once on the build machine (platform-independent output), not under ARM emulation
FROM --platform=$BUILDPLATFORM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- runtime ----------
FROM python:3.13-slim AS runtime
ARG APP_VERSION=dev
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 APP_VERSION=${APP_VERSION}
LABEL org.opencontainers.image.title="Astris" \
      org.opencontainers.image.description="Private, self-hosted household finance hub" \
      org.opencontainers.image.version="${APP_VERSION}" \
      net.unraid.docker.icon="https://raw.githubusercontent.com/nuno2229/Astris/main/unraid/icon.png" \
      net.unraid.docker.webui="http://[IP]:[PORT:8000]/"
# tesseract: reading photos of receipts/statements and use-by dates (sandboxed). Chromium (via Playwright): opt-in
# Morrisons/Aldi price lookups (G-17); stored where the unprivileged app user can read it.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
# postgresql: the built-in database for the all-in-one setup (no auto-created cluster: Astris makes its own in /data)
RUN mkdir -p /etc/postgresql-common/createcluster.d \
    && echo "create_main_cluster = false" > /etc/postgresql-common/createcluster.d/astris.conf \
    && apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng postgresql tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/ ./
RUN pip install --upgrade pip && pip install . \
    && python -m playwright install --with-deps chromium-headless-shell \
    && rm -rf /var/lib/apt/lists/* && chmod -R a+rX /opt/ms-playwright
COPY --from=web /web/dist /app/frontend/dist
RUN rm -f .env && mkdir -p /data/backups
ENV BACKUP_DIR=/data/backups DATA_DIR=/data PUID=99 PGID=100
VOLUME ["/data"]
# starts as root only to fix /data ownership, then drops to PUID:PGID (docker-entrypoint.sh)
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,ssl,sys,urllib.request; s=os.environ.get('HTTPS')=='self-signed'; c=ssl._create_unverified_context() if s else None; sys.exit(0 if urllib.request.urlopen(('https' if s else 'http')+'://127.0.0.1:8000/api/health', timeout=4, context=c).status == 200 else 1)"
ENTRYPOINT ["./docker-entrypoint.sh"]
