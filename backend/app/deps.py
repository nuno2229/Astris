from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import bind_user, session_scope
from app.security import SESSION_COOKIE, token_hash

DB = Annotated[Session, Depends(session_scope)]


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    token_hash: str


def current_user(request: Request, db: DB) -> CurrentUser:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    th = token_hash(token)
    uid = db.execute(text("SELECT auth_resolve_session(:h)"), {"h": th}).scalar()
    if uid is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    bind_user(db, uid)
    return CurrentUser(id=uid, token_hash=th)


User = Annotated[CurrentUser, Depends(current_user)]


def not_found(what: str = "Not found") -> HTTPException:
    # 404 (not 403) for anything outside the user's scope: don't reveal other households exist.
    return HTTPException(status.HTTP_404_NOT_FOUND, what)
