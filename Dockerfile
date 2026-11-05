# syntax=docker/dockerfile:1.7
# ---------- frontend build ----------
FROM node:22-alpine AS web
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
      org.opencontainers.image.version="${APP_VERSION}"
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/ ./
RUN pip install --upgrade pip && pip install .
COPY --from=web /web/dist /app/frontend/dist
RUN useradd --system --uid 10001 --home /app ledger && rm -f .env && chown -R ledger /app
USER ledger
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["./docker-entrypoint.sh"]
