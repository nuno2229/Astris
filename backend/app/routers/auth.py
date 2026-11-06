"""Per-user login (§3.4): each user has their own credentials and server-side session."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import bind_user
from app.deps import DB, User
from app.schemas import HouseholdOut, LoginIn, MeOut, MeUpdate, RegisterIn
from app.security import (
    SESSION_COOKIE, hash_password, needs_rehash, new_token, token_hash, verify_password,
)

router = APIRouter(prefix="/api", tags=["auth"])


def _start_session(db: DB, request: Request, response: Response, user_id) -> None:
    s = get_settings()
    secure = request.url.scheme == "https" if s.cookie_secure == "auto" else s.cookie_secure == "true"
    token = new_token()
    expires = datetime.now(UTC) + timedelta(days=s.session_days)
    db.execute(text("SELECT auth_create_session(:u, :h, :e)"), {"u": user_id, "h": token_hash(token), "e": expires})
    response.set_cookie(
        SESSION_COOKIE, token, max_age=s.session_days * 86400, httponly=True,
        secure=secure, samesite="strict", path="/",
    )


def _me(db: DB, uid) -> MeOut:
    row = db.execute(text("SELECT id, email, display_name, home_currency, is_server_admin FROM users WHERE id = :u"), {"u": uid}).one()
    return MeOut(**row._mapping)


@router.post("/auth/register", response_model=MeOut, status_code=201)
def register(body: RegisterIn, db: DB, request: Request, response: Response):
    if not get_settings().allow_registration or not db.execute(text("SELECT registration_open()")).scalar():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "New sign-ups are closed on this server. Ask the admin for an invite.")
    try:
        with db.begin_nested():
            uid = db.execute(
                text("SELECT auth_register_user(:e, :n, :h)"),
                {"e": body.email, "n": body.display_name.strip(), "h": hash_password(body.password)},
            ).scalar_one()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    _start_session(db, request, response, uid)
    bind_user(db, uid)
    return _me(db, uid)


@router.post("/auth/login", response_model=MeOut)
def login(body: LoginIn, db: DB, request: Request, response: Response):
    row = db.execute(text("SELECT user_id, password_hash FROM auth_login_lookup(:e)"), {"e": body.email}).first()
    if not verify_password(body.password, row.password_hash if row else None):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    if needs_rehash(row.password_hash):
        db.execute(text("SELECT auth_set_password_hash(:u, :h)"), {"u": row.user_id, "h": hash_password(body.password)})
    _start_session(db, request, response, row.user_id)
    bind_user(db, row.user_id)
    return _me(db, row.user_id)


@router.post("/auth/logout", status_code=204)
def logout(user: User, db: DB, response: Response):
    db.execute(text("SELECT auth_revoke_session(:h)"), {"h": user.token_hash})
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeOut)
def me(user: User, db: DB):
    return _me(db, user.id)


@router.patch("/me", response_model=MeOut)
def update_me(body: MeUpdate, user: User, db: DB):
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)  # keys come from the schema, not the client
        db.execute(text(f"UPDATE users SET {sets} WHERE id = :u"), {**fields, "u": user.id})
    return _me(db, user.id)


@router.get("/me/households", response_model=list[HouseholdOut])
def my_households(user: User, db: DB):
    rows = db.execute(text("""
        SELECT h.id, h.name, m.role FROM households h
        JOIN memberships m ON m.household_id = h.id AND m.user_id = :u
        ORDER BY h.created_at"""), {"u": user.id})
    return [HouseholdOut(**r._mapping) for r in rows]
