from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import documents
from app.routers import accounts, admin, audit, auth, budgets, copilot, fx, prices, transfers, categories, splits, calendar, meals, receipts, shopping, goals, households, insights, inventory, import_wizard, ledger, notifications, recurring, settings, subscriptions

@asynccontextmanager
async def lifespan(_app):
    from app.categorise.worker import worker
    from app.config import get_settings
    if get_settings().ai_worker:
        worker.start()
    yield
    worker.stop()


app = FastAPI(title="Astris", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def security(request: Request, call_next):
    # CSRF defence in depth (cookies are already SameSite=Strict): state-changing requests
    # must come from our own origin when the browser sends an Origin header.
    if request.method not in SAFE_METHODS:
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


for r in (auth.router, households.router, accounts.router, ledger.router, recurring.router, settings.router,
          subscriptions.router, notifications.router, fx.router, import_wizard.router,
          audit.router, goals.router, budgets.router, insights.router, inventory.router,
          copilot.router, shopping.router, receipts.router, documents.router, admin.router,
          prices.router, transfers.router, categories.router, splits.router, calendar.router, meals.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    from sqlalchemy import text
    from app.config import app_version
    from app.db import get_engine
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        db = "ok"
    except Exception:
        return JSONResponse({"status": "degraded", "db": "unreachable", "version": app_version()}, 503)
    return {"status": "ok", "db": db, "version": app_version()}


@app.get("/ca.crt", include_in_schema=False)
def ca_certificate():
    """The public certificate of this server's own small CA (P-6), to install on phones. 404 when HTTPS isn't self-signed."""
    import os
    from fastapi.responses import Response
    from app.tls import tls_dir
    p = tls_dir() / "ca.crt"
    if os.environ.get("HTTPS", "off") != "self-signed" or not p.is_file():
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return Response(p.read_bytes(), media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="astris-home-ca.crt"'})


@app.get("/api/https")
def https_status():
    import os
    return {"self_signed": os.environ.get("HTTPS", "off") == "self-signed"}


# Serve the built PWA (frontend/dist) when present; SPA fallback to index.html.
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)   # unknown API paths never get the web app
        f = (_DIST / path).resolve()
        if path and f.is_file() and _DIST in f.parents:
            return FileResponse(f)
        return FileResponse(_DIST / "index.html")
