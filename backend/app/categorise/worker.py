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


class Worker:
    def __init__(self, idle_seconds: float = 10):
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._idle = idle_seconds
        self._t: threading.Thread | None = None

    def start(self) -> None:
        self._t = threading.Thread(target=self._loop, name="ai-worker", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                busy = run_once() > 0
            except Exception:
                log.exception("AI worker iteration failed")
                busy = False
            if not busy:
                self._wake.wait(self._idle)
                self._wake.clear()


worker = Worker()
