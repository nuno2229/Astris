"""Database access. Every request runs in ONE transaction; the acting user is bound with
SET LOCAL app.user_id so Postgres row-level security scopes every query (architecture.md)."""
from collections.abc import Iterator
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().app_db_url, pool_pre_ping=True)


def session_scope() -> Iterator[Session]:
    with Session(get_engine()) as s, s.begin():
        yield s


def bind_user(s: Session, user_id: UUID) -> None:
    # is_local=true -> reset at transaction end, so a pooled connection never carries a stale identity
    s.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(user_id)})
