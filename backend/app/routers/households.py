"""Households, memberships and invitations (§3.2). Postgres RLS is the backstop; these
handlers also check role explicitly so errors are clear. Out-of-scope → 404."""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.deps import DB, User, not_found
from app.schemas import (
    AcceptInviteIn, HouseholdDetail, HouseholdIn, HouseholdOut, InviteCreated, InviteIn, InviteOut, MemberOut,
)
from app import audit
from app.security import new_token, token_hash

router = APIRouter(prefix="/api", tags=["households"])


def my_role(db: DB, hid: UUID) -> str:
    role = db.execute(text("SELECT app_my_role(:h)"), {"h": hid}).scalar()
    if role is None:
        raise not_found("Household not found")
    return role


def require_manager(db: DB, hid: UUID) -> str:
    role = my_role(db, hid)
    if role not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an admin can do this")
    return role


@router.post("/households", response_model=HouseholdOut, status_code=201)
def create_household(body: HouseholdIn, user: User, db: DB):
    hid = db.execute(text("SELECT create_household(:n)"), {"n": body.name.strip()}).scalar_one()
    return HouseholdOut(id=hid, name=body.name.strip(), role="owner")


@router.get("/households/{hid}", response_model=HouseholdDetail)
def get_household(hid: UUID, user: User, db: DB):
    role = my_role(db, hid)
    name = db.execute(text("SELECT name FROM households WHERE id = :h"), {"h": hid}).scalar_one()
    members = db.execute(text("""
        SELECT m.user_id, u.display_name, u.email, m.role, m.joined_at
          FROM memberships m JOIN users u ON u.id = m.user_id
         WHERE m.household_id = :h ORDER BY m.joined_at"""), {"h": hid})
    return HouseholdDetail(id=hid, name=name, role=role, members=[MemberOut(**r._mapping) for r in members])


@router.patch("/households/{hid}", response_model=HouseholdOut)
def rename_household(hid: UUID, body: HouseholdIn, user: User, db: DB):
    if my_role(db, hid) != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can rename the household")
    db.execute(text("UPDATE households SET name = :n WHERE id = :h"), {"n": body.name.strip(), "h": hid})
    return HouseholdOut(id=hid, name=body.name.strip(), role="owner")


@router.delete("/households/{hid}/members/{uid}", status_code=204)
def remove_member(hid: UUID, uid: UUID, user: User, db: DB, keep_history: bool = False):
    """Leave (uid = me) or remove someone. Access ends immediately: membership drives every RLS policy.
    Leaving, you choose whether the household keeps your past transactions (keep_history, G-9)."""
    role = my_role(db, hid)
    if uid == user.id:
        if role == "owner":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "The owner can't leave. Make someone else the owner first, or delete the household.")
        db.execute(text("SELECT leave_household(:h, :k)"), {"h": hid, "k": keep_history})
        who = db.execute(text("SELECT display_name FROM users WHERE id = :u"), {"u": uid}).scalar() or "a member"
        audit.log(db, user.id, "member.leave", "household",
                  f"{who} left the household" + (" (past transactions kept)" if keep_history else ""), entity_id=uid, household_id=hid)
        return
    if uid != user.id and role not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an admin can remove members")
    deleted = db.execute(text("DELETE FROM memberships WHERE household_id = :h AND user_id = :u"),
                         {"h": hid, "u": uid}).rowcount
    if not deleted:
        target = db.execute(text("SELECT role FROM memberships WHERE household_id = :h AND user_id = :u"),
                            {"h": hid, "u": uid}).scalar()
        if target is None:
            raise not_found("Member not found")
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "The owner can't be removed" if target == "owner" else "Not allowed to remove this member")
    who = db.execute(text("SELECT display_name FROM users WHERE id = :u"), {"u": uid}).scalar() or "a member"
    audit.log(db, user.id, "member.leave" if uid == user.id else "member.remove", "household",
              f"{who} left the household" if uid == user.id else f"Removed {who}", entity_id=uid, household_id=hid)


@router.post("/households/{hid}/invites", response_model=InviteCreated, status_code=201)
def create_invite(hid: UUID, body: InviteIn, user: User, db: DB):
    role = require_manager(db, hid)
    if body.role == "admin" and role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can invite admins")
    code = new_token()
    expires = datetime.now(UTC) + timedelta(days=get_settings().invite_days)
    row = db.execute(text("""
        INSERT INTO invitations (household_id, code_hash, email, role, created_by, expires_at)
        VALUES (:h, :c, :e, :r, :u, :x)
        RETURNING id, email, role, created_at, expires_at, accepted_at, revoked_at"""),
        {"h": hid, "c": token_hash(code), "e": body.email.lower() if body.email else None,
         "r": body.role, "u": user.id, "x": expires}).one()
    audit.log(db, user.id, "invite.create", "household", f"Created an invite ({body.role}"
              + (f" for {body.email.lower()})" if body.email else ")"), entity_id=row.id, household_id=hid)
    return InviteCreated(**row._mapping, code=code)


@router.get("/households/{hid}/invites", response_model=list[InviteOut])
def list_invites(hid: UUID, user: User, db: DB):
    require_manager(db, hid)
    rows = db.execute(text("""
        SELECT id, email, role, created_at, expires_at, accepted_at, revoked_at
          FROM invitations WHERE household_id = :h ORDER BY created_at DESC"""), {"h": hid})
    return [InviteOut(**r._mapping) for r in rows]


@router.delete("/households/{hid}/invites/{iid}", status_code=204)
def revoke_invite(hid: UUID, iid: UUID, user: User, db: DB):
    require_manager(db, hid)
    n = db.execute(text("""UPDATE invitations SET revoked_at = now()
                            WHERE id = :i AND household_id = :h AND revoked_at IS NULL AND accepted_at IS NULL"""),
                   {"i": iid, "h": hid}).rowcount
    if not n:
        raise not_found("Invite not found or no longer pending")
    audit.log(db, user.id, "invite.revoke", "household", "Revoked an invite", entity_id=iid, household_id=hid)


@router.post("/invites/accept", response_model=HouseholdOut)
def accept_invite(body: AcceptInviteIn, user: User, db: DB):
    try:
        with db.begin_nested():
            hid = db.execute(text("SELECT accept_invite(:c)"), {"c": token_hash(body.code.strip())}).scalar_one()
    except DBAPIError as e:
        code = getattr(e.orig, "sqlstate", None)
        if code == "23505":
            raise HTTPException(status.HTTP_409_CONFLICT, "You're already a member of this household")
        if code == "P0002":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invite is invalid, expired or already used")
        raise
    row = db.execute(text("""SELECT h.id, h.name, m.role FROM households h
                              JOIN memberships m ON m.household_id = h.id AND m.user_id = :u
                             WHERE h.id = :h"""), {"h": hid, "u": user.id}).one()
    # rejoining after leaving with history kept: back to normal sharing (re-share accounts as you like)
    db.execute(text("DELETE FROM former_members WHERE household_id = :h AND user_id = :u"), {"h": hid, "u": user.id})
    db.execute(text("""DELETE FROM household_accounts WHERE household_id = :h AND frozen_at IS NOT NULL
                        AND account_id IN (SELECT id FROM accounts WHERE owner_user_id = :u)"""), {"h": hid, "u": user.id})
    me = db.execute(text("SELECT display_name FROM users WHERE id = :u"), {"u": user.id}).scalar()
    audit.log(db, user.id, "member.join", "household", f"{me} joined as {row.role}", entity_id=user.id, household_id=hid)
    return HouseholdOut(**row._mapping)
