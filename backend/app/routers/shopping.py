"""Shopping lists (E17): a shared household list (created automatically) plus optional personal lists;
items from people, the pantry's low-stock suggestions and the assistant; favourites; 'done shopping' -> pantry."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import audit
from app.deps import DB, User, not_found
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["shopping"])


class ListIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    scope: Literal["household", "personal"] = "personal"


class ListOut(BaseModel):
    id: UUID
    name: str
    scope: str
    is_default: bool
    open_items: int
    can_delete: bool


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    quantity: Decimal = Field(default=Decimal(1), gt=0, lt=Decimal("100000"))
    note: str | None = Field(default=None, max_length=200)
    expires_on: date | None = None
    product_id: UUID | None = None


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    quantity: Decimal | None = Field(default=None, gt=0, lt=Decimal("100000"))
    note: str | None = Field(default=None, max_length=200)
    expires_on: date | None = None
    checked: bool | None = None


class ItemOut(BaseModel):
    id: UUID
    name: str
    quantity: Decimal
    note: str | None
    expires_on: date | None
    product_id: UUID | None
    in_pantry: Decimal | None       # how many you already have, when linked to a pantry product
    source: str
    checked: bool
    added_by: str | None
    added_at: datetime
    favourite: bool
    cheapest: dict | None = None      # best known shop from your receipts / Open Prices (E17-06, E18)


class ListDetail(ListOut):
    items: list[ItemOut]


class FavouriteIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scope: Literal["household", "personal"] = "personal"


class FavouriteOut(BaseModel):
    id: UUID
    name: str
    scope: str


class FinishIn(BaseModel):
    to_pantry: bool = True


def default_list(db: Session, hid: UUID, user_id: UUID) -> UUID:
    lid = db.execute(text("SELECT id FROM shopping_lists WHERE household_id = :h AND is_default"), {"h": hid}).scalar()
    if lid:
        return lid
    return db.execute(text("""INSERT INTO shopping_lists (household_id, scope, name, is_default, created_by)
                              VALUES (:h, 'household', 'Shopping', true, :u)
                              ON CONFLICT (household_id) WHERE is_default DO UPDATE SET name = shopping_lists.name
                              RETURNING id"""), {"h": hid, "u": user_id}).scalar_one()


def _list(db: DB, lid: UUID):
    r = db.execute(text("SELECT * FROM shopping_lists WHERE id = :l"), {"l": lid}).first()
    if r is None:
        raise not_found("List not found")
    return r


def _log(db: DB, user_id: UUID, lst, action: str, summary: str) -> None:
    # personal lists log to the user's own chain, so household admins don't see personal items
    audit.log(db, user_id, action, "shopping", summary, entity_id=lst.id,
              household_id=lst.household_id if lst.scope == "household" else None)


def _can_delete(db: DB, lst, user_id: UUID) -> bool:
    if lst.is_default:
        return False
    if lst.scope == "personal":
        return lst.owner_user_id == user_id
    return lst.created_by == user_id or db.execute(text("SELECT app_my_role(:h)"), {"h": lst.household_id}).scalar() in ("owner", "admin")


def _favs(db: DB, hid: UUID) -> set[str]:
    return {r[0].lower() for r in db.execute(text("SELECT name FROM favourites WHERE household_id = :h"), {"h": hid})}


def detail(db: DB, lst, user_id: UUID) -> ListDetail:
    favs = _favs(db, lst.household_id)
    from app.routers.prices import cheapest_for, price_book
    book = price_book(db, lst.household_id)
    rows = db.execute(text("""
        SELECT i.*, u.display_name AS added_by_name,
               (SELECT sum(l.quantity) FROM stock_lots l WHERE l.product_id = i.product_id) AS in_pantry
          FROM shopping_items i LEFT JOIN users u ON u.id = i.added_by
         WHERE i.list_id = :l ORDER BY i.checked, i.added_at"""), {"l": lst.id}).all()
    items = [ItemOut(id=r.id, name=r.name, quantity=Decimal(r.quantity).normalize(), note=r.note, expires_on=r.expires_on,
                     product_id=r.product_id, in_pantry=Decimal(r.in_pantry).normalize() if r.in_pantry is not None else
                     (Decimal(0) if r.product_id else None), source=r.source, checked=r.checked, added_by=r.added_by_name,
                     added_at=r.added_at, favourite=r.name.lower() in favs,
                     cheapest=(lambda s: s.model_dump(mode="json") if s else None)(cheapest_for(book, r.name))) for r in rows]
    return ListDetail(id=lst.id, name=lst.name, scope=lst.scope, is_default=lst.is_default,
                      open_items=sum(1 for i in items if not i.checked), can_delete=_can_delete(db, lst, user_id), items=items)


def add_item(db: DB, lst, user_id: UUID, body: ItemIn, source: str = "manual") -> None:
    """Same unticked name already on the list -> bump its quantity instead of a duplicate line."""
    if body.product_id and not db.execute(text("SELECT 1 FROM products WHERE id = :p AND household_id = :h"),
                                          {"p": body.product_id, "h": lst.household_id}).first():
        raise not_found("Pantry item not found")
    n = db.execute(text("""UPDATE shopping_items SET quantity = quantity + :q, note = coalesce(:n, note)
                            WHERE list_id = :l AND NOT checked AND lower(name) = lower(:name)"""),
                   {"q": body.quantity, "n": body.note, "l": lst.id, "name": body.name.strip()}).rowcount
    if not n:
        db.execute(text("""INSERT INTO shopping_items (list_id, name, quantity, note, expires_on, product_id, source, added_by)
                           VALUES (:l, :n, :q, :note, :e, :p, :s, :u)"""),
                   {"l": lst.id, "n": body.name.strip(), "q": body.quantity, "note": body.note, "e": body.expires_on,
                    "p": body.product_id, "s": source, "u": user_id})


@router.get("/households/{hid}/shopping-lists", response_model=list[ListOut])
def lists(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    default_list(db, hid, user.id)
    rows = db.execute(text("""SELECT l.*, (SELECT count(*) FROM shopping_items i WHERE i.list_id = l.id AND NOT i.checked) AS open_items
                                FROM shopping_lists l WHERE l.household_id = :h ORDER BY l.is_default DESC, l.scope, l.created_at"""),
                      {"h": hid}).all()
    return [ListOut(id=r.id, name=r.name, scope=r.scope, is_default=r.is_default, open_items=r.open_items,
                    can_delete=_can_delete(db, r, user.id)) for r in rows]


@router.post("/households/{hid}/shopping-lists", response_model=ListOut, status_code=201)
def create_list(hid: UUID, body: ListIn, user: User, db: DB):
    my_role(db, hid)
    lid = db.execute(text("""INSERT INTO shopping_lists (household_id, scope, owner_user_id, name, created_by)
                             VALUES (:h, :s, :o, :n, :u) RETURNING id"""),
                     {"h": hid, "s": body.scope, "o": user.id if body.scope == "personal" else None, "n": body.name.strip(),
                      "u": user.id}).scalar_one()
    lst = _list(db, lid)
    _log(db, user.id, lst, "list.create", f"New list {lst.name}")
    return ListOut(id=lid, name=lst.name, scope=lst.scope, is_default=False, open_items=0, can_delete=True)


@router.delete("/shopping-lists/{lid}", status_code=204)
def delete_list(lid: UUID, user: User, db: DB):
    lst = _list(db, lid)
    if not _can_delete(db, lst, user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This list can't be deleted by you")
    db.execute(text("DELETE FROM shopping_lists WHERE id = :l"), {"l": lid})
    _log(db, user.id, lst, "list.delete", f"Deleted list {lst.name}")


@router.get("/shopping-lists/{lid}", response_model=ListDetail)
def get_list(lid: UUID, user: User, db: DB):
    return detail(db, _list(db, lid), user.id)


@router.post("/shopping-lists/{lid}/items", response_model=ListDetail, status_code=201)
def add(lid: UUID, body: ItemIn, user: User, db: DB):
    lst = _list(db, lid)
    add_item(db, lst, user.id, body)
    _log(db, user.id, lst, "list.add", f"Added {body.quantity:g}× {body.name.strip()} to {lst.name}")
    return detail(db, lst, user.id)


@router.patch("/shopping-items/{iid}", response_model=ListDetail)
def update_item(iid: UUID, body: ItemUpdate, user: User, db: DB):
    it = db.execute(text("SELECT * FROM shopping_items WHERE id = :i"), {"i": iid}).first()
    if it is None:
        raise not_found("Item not found")
    lst = _list(db, it.list_id)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if not (k in ("name", "quantity", "checked") and v is None)}
    if "checked" in fields:
        fields["checked_by"] = user.id if fields["checked"] else None
        db.execute(text("UPDATE shopping_items SET checked_at = CASE WHEN :c THEN now() END WHERE id = :i"), {"c": fields["checked"], "i": iid})
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)   # keys from the schema (+checked_by) only
        db.execute(text(f"UPDATE shopping_items SET {sets} WHERE id = :id"), {**fields, "id": iid})
        verb = ("Ticked off" if fields.get("checked") else "Unticked") if "checked" in fields else "Changed"
        _log(db, user.id, lst, "list.update", f"{verb} {it.name} on {lst.name}")
    return detail(db, lst, user.id)


@router.delete("/shopping-items/{iid}", response_model=ListDetail)
def remove_item(iid: UUID, user: User, db: DB):
    it = db.execute(text("SELECT * FROM shopping_items WHERE id = :i"), {"i": iid}).first()
    if it is None:
        raise not_found("Item not found")
    lst = _list(db, it.list_id)
    db.execute(text("DELETE FROM shopping_items WHERE id = :i"), {"i": iid})
    _log(db, user.id, lst, "list.remove", f"Removed {it.name} from {lst.name}")
    return detail(db, lst, user.id)


@router.post("/shopping-lists/{lid}/from-pantry", response_model=ListDetail)
def from_pantry(lid: UUID, user: User, db: DB):
    """Add the latest weekly 'running low' suggestions from the pantry (E14-07 -> E17-02)."""
    lst = _list(db, lid)
    from app.routers.inventory import generate_suggestions
    sug = generate_suggestions(db, lst.household_id, "manual")
    for i in sug["items"]:
        add_item(db, lst, user.id, ItemIn(name=i["name"], quantity=Decimal(str(i["buy"])), product_id=UUID(i["product_id"])), "pantry")
    if sug["items"]:
        _log(db, user.id, lst, "list.add", f"Added {len(sug['items'])} running-low item(s) from the pantry to {lst.name}")
    return detail(db, lst, user.id)


@router.post("/shopping-lists/{lid}/finish", response_model=ListDetail)
def finish(lid: UUID, body: FinishIn, user: User, db: DB):
    """Done shopping: ticked items linked to the pantry are added to stock, then ticked items are cleared."""
    lst = _list(db, lid)
    ticked = db.execute(text("SELECT * FROM shopping_items WHERE list_id = :l AND checked"), {"l": lid}).all()
    stocked = 0
    if body.to_pantry:
        from app.routers.inventory import _add_lot
        for i in ticked:
            if i.product_id:
                _add_lot(db, i.product_id, Decimal(i.quantity), None, user.id)
                stocked += 1
    db.execute(text("DELETE FROM shopping_items WHERE list_id = :l AND checked"), {"l": lid})
    if ticked:
        _log(db, user.id, lst, "list.finish", f"Finished shopping on {lst.name}: {len(ticked)} item(s), {stocked} added to the pantry")
    return detail(db, lst, user.id)


@router.get("/households/{hid}/favourites", response_model=list[FavouriteOut])
def favourites(hid: UUID, user: User, db: DB):
    my_role(db, hid)
    rows = db.execute(text("SELECT id, name, user_id FROM favourites WHERE household_id = :h ORDER BY lower(name)"), {"h": hid})
    return [FavouriteOut(id=r.id, name=r.name, scope="personal" if r.user_id else "household") for r in rows]


@router.post("/households/{hid}/favourites", response_model=FavouriteOut, status_code=201)
def add_favourite(hid: UUID, body: FavouriteIn, user: User, db: DB):
    my_role(db, hid)
    fid = db.execute(text("""INSERT INTO favourites (household_id, user_id, name, created_by) VALUES (:h, :u, :n, :by)
                             ON CONFLICT DO NOTHING RETURNING id"""),
                     {"h": hid, "u": user.id if body.scope == "personal" else None, "n": body.name.strip(), "by": user.id}).scalar()
    if fid is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already a favourite")
    return FavouriteOut(id=fid, name=body.name.strip(), scope=body.scope)


@router.delete("/favourites/{fid}", status_code=204)
def remove_favourite(fid: UUID, user: User, db: DB):
    if not db.execute(text("DELETE FROM favourites WHERE id = :f"), {"f": fid}).rowcount:
        raise not_found("Favourite not found")
