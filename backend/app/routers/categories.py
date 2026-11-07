"""Household categories (G-13): any member adds; the person who added it, or an owner/admin, removes it.
Transactions keep their label if a category is removed (it just stops being offered)."""
import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import audit
from app.categorise.rules import CATEGORIES
from app.deps import DB, User, not_found
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["categories"])
MAX_PER_HOUSEHOLD = 60


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class CategoryOut(BaseModel):
    id: UUID | None
    name: str
    built_in: bool
    can_remove: bool = False


@router.get("/households/{hid}/categories", response_model=list[CategoryOut])
def list_categories(hid: UUID, user: User, db: DB):
    role = my_role(db, hid)
    own = db.execute(text("SELECT id, name, created_by FROM household_categories WHERE household_id = :h ORDER BY lower(name)"),
                     {"h": hid}).all()
    return [CategoryOut(id=None, name=c, built_in=True) for c in CATEGORIES] + \
        [CategoryOut(id=r.id, name=r.name, built_in=False, can_remove=r.created_by == user.id or role in ("owner", "admin"))
         for r in own]


@router.post("/households/{hid}/categories", response_model=CategoryOut, status_code=201)
def add_category(hid: UUID, body: CategoryIn, user: User, db: DB):
    my_role(db, hid)
    name = " ".join(body.name.split())
    if not re.fullmatch(r"[\w &'()/,.+-]{1,40}", name) or "_" in name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Use letters, numbers, spaces and & ' ( ) / , . + - only")
    if name.lower() in {c.lower() for c in CATEGORIES} or db.execute(text(
            "SELECT 1 FROM household_categories WHERE household_id = :h AND lower(name) = lower(:n)"), {"h": hid, "n": name}).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "That category already exists")
    if db.execute(text("SELECT count(*) FROM household_categories WHERE household_id = :h"), {"h": hid}).scalar() >= MAX_PER_HOUSEHOLD:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A household can have up to {MAX_PER_HOUSEHOLD} of its own categories")
    r = db.execute(text("INSERT INTO household_categories (household_id, name) VALUES (:h, :n) RETURNING id"), {"h": hid, "n": name}).scalar_one()
    audit.log(db, user.id, "category.add", "household", f"New category: {name}", entity_id=r, household_id=hid)
    return CategoryOut(id=r, name=name, built_in=False, can_remove=True)


@router.delete("/households/{hid}/categories/{cid}", status_code=204)
def remove_category(hid: UUID, cid: UUID, user: User, db: DB):
    my_role(db, hid)
    row = db.execute(text("SELECT name FROM household_categories WHERE id = :c AND household_id = :h"), {"c": cid, "h": hid}).first()
    if row is None:
        raise not_found("Category not found")
    if not db.execute(text("DELETE FROM household_categories WHERE id = :c"), {"c": cid}).rowcount:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only whoever added it, or an owner or admin, can remove it")
    audit.log(db, user.id, "category.remove", "household", f"Removed category: {row.name}", household_id=hid)
