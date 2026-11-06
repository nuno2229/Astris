"""Background AI worker (E05-01): keeps LLM work off the request path so the UI stays responsive (§22)."""
import logging
import threading

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.categorise.service import run_ai_for_owner
from app.db import bind_user, get_engine

log = logging.getLogger("astris.worker")


def run_once() -> int:
    """Process queued AI work for up to 20 owners. Each owner runs in its own transaction as that user (RLS)."""
    eng = get_engine()
    with Session(eng) as s:
        owners = [r[0] for r in s.execute(text("SELECT ai_pending_owners(20)"))]
    done = 0
    for owner in owners:
        try:
            with Session(eng) as s, s.begin():
                bind_user(s, owner)
                done += run_ai_for_owner(s, owner)
        except Exception:
            log.exception("AI pass failed for owner %s", owner)
    return done


def run_notifications() -> int:
    """Send new alerts for every user with a channel. Each user runs in their own transaction, as that user."""
    from app.notify.scheduler import notify_user
    eng = get_engine()
    with Session(eng) as s:
        users = [r[0] for r in s.execute(text("SELECT notify_users(500)"))]
    sent = 0
    for u in users:
        try:
            with Session(eng) as s, s.begin():
                bind_user(s, u)
                sent += notify_user(s, u)
        except Exception:
            log.exception("notifications failed for user %s", u)
    return sent


NOTIFY_EVERY_S = 15 * 60


def run_fx(force: bool = False) -> bool:
    from app.fx_ecb import refresh_if_due
    with Session(get_engine()) as s, s.begin():
        return refresh_if_due(s, force)


class Worker:
    def __init__(self, idle_seconds: float = 10):
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._idle = idle_seconds
        self._t: threading.Thread | None = None
        self._fx_now = False

    def start(self) -> None:
        self._t = threading.Thread(target=self._loop, name="ai-worker", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def wake_fx(self) -> None:
        self._fx_now = True
        self._wake.set()

    def _loop(self) -> None:
        import time
        last_notify = 0.0
        while not self._stop.is_set():
            try:
                busy = run_once() > 0
            except Exception:
                log.exception("AI worker iteration failed")
                busy = False
            try:
                force, self._fx_now = self._fx_now, False
                run_fx(force)
            except Exception:
                log.exception("ECB rates refresh failed")
            if time.monotonic() - last_notify > NOTIFY_EVERY_S:
                last_notify = time.monotonic()
                try:
                    run_notifications()
                except Exception:
                    log.exception("notification round failed")
            if not busy:
                self._wake.wait(self._idle)
                self._wake.clear()


worker = Worker()
