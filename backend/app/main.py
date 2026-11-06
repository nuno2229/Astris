from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routers import accounts, auth, fx, households, ledger, notifications, recurring, settings, subscriptions

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
          subscriptions.router, notifications.router, fx.router):
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


# Serve the built PWA (frontend/dist) when present; SPA fallback to index.html.
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (_DIST / path).resolve()
        if path and f.is_file() and _DIST in f.parents:
            return FileResponse(f)
        return FileResponse(_DIST / "index.html")
