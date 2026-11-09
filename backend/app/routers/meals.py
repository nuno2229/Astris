"""Mealie meal plans (E20-02, opt-in). Reads this week's plan from your Mealie server, checks each recipe's
ingredients against the pantry and puts what's missing on the shared shopping list. Read-only towards Mealie.
The API token is kept server-side (encrypted at rest with ASTRIS_KEY) and never sent back to the app."""
import json
from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import audit
from app.deps import DB, User
from app.netcheck import is_local_url, origin_only
from app.routers.households import my_role

router = APIRouter(prefix="/api", tags=["meals"])


class MealieIn(BaseModel):
    url: str | None = Field(default=None, max_length=300)
    token: str | None = Field(default=None, max_length=1500)     # omitted = keep the saved one
    allow_internet: bool = False

    @field_validator("url")
    @classmethod
    def origin(cls, v):
        if v is None or not v.strip():
            return None
        o = origin_only(v)
        if o is None:
            raise ValueError("Use just the server address, like http://192.168.1.20:9925 (no path)")
        return o


class Ingredient(BaseModel):
    name: str            # the food, for matching (e.g. "onion")
    display: str         # as written in the recipe (e.g. "2 large onions")
    in_pantry: bool


class Meal(BaseModel):
    date: date
    kind: str            # breakfast / lunch / dinner / side
    title: str
    ingredients: list[Ingredient] = []


class MealsOut(BaseModel):
    configured: bool
    can_edit: bool
    url: str | None = None
    allow_internet: bool = False
    meals: list[Meal] = []
    error: str | None = None


class ToListIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=100)


def _cfg(db: Session, hid: UUID):
    return db.execute(text("SELECT mealie_url, mealie_token, mealie_allow_internet FROM household_settings WHERE household_id = :h"),
                      {"h": hid}).first()


def _get(base: str, token: str, path: str, allow_internet: bool) -> dict | list:
    from app.netcheck import pinned_request
    if not allow_internet and not is_local_url(base):
        raise RuntimeError("Mealie isn't on your home network; tick 'uses internet' to allow it")
    code, raw = pinned_request(base + path, timeout=15, max_bytes=4 * 1024 * 1024, block_self=True,
                               headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    if code == 401:
        raise RuntimeError("Mealie didn't accept the API token")
    if code != 200:
        raise RuntimeError(f"Mealie answered {code}")
    return json.loads(raw)


def _items(d) -> list:
    return d.get("items", []) if isinstance(d, dict) else d if isinstance(d, list) else []


def fetch_plan(base: str, token: str, allow_internet: bool, days: int = 7) -> list[dict]:
    """Meal plan entries for today + days, with ingredients. Works with Mealie v1 (groups) and v2 (households)."""
    s, e = date.today(), date.today() + timedelta(days=days)
    q = f"?start_date={s}&end_date={e}&perPage=100"
    try:
        plan = _items(_get(base, token, "/api/households/mealplans" + q, allow_internet))
    except RuntimeError as err:
        if "404" not in str(err):
            raise
        plan = _items(_get(base, token, "/api/groups/mealplans" + q, allow_internet))
    out, cache = [], {}
    for p in plan[:60]:
        recipe = p.get("recipe") or {}
        slug = recipe.get("slug")
        ings = []
        if slug:
            if slug not in cache:
                try:
                    cache[slug] = _get(base, token, f"/api/recipes/{slug}", allow_internet).get("recipeIngredient") or []
                except (RuntimeError, AttributeError):
                    cache[slug] = []
            for i in cache[slug][:60]:
                food = ((i.get("food") or {}).get("name") or i.get("note") or "").strip()
                shown = (i.get("display") or i.get("originalText") or food or "").strip()
                if food or shown:
                    ings.append({"name": (food or shown)[:80], "display": shown[:160]})
        try:
            d = date.fromisoformat(str(p.get("date"))[:10])
        except ValueError:
            continue
        out.append({"date": d, "kind": str(p.get("entryType") or "dinner")[:20],
                    "title": str(recipe.get("name") or p.get("title") or "Meal")[:120], "ingredients": ings})
    return sorted(out, key=lambda m: m["date"])


def _pantry_keys(db: Session, hid: UUID) -> set[str]:
    from app.routers.prices import product_key
    rows = db.execute(text("""SELECT p.name FROM products p WHERE p.household_id = :h
                               AND EXISTS (SELECT 1 FROM stock_lots l WHERE l.product_id = p.id AND l.quantity > 0)"""), {"h": hid})
    return {product_key(r[0]) for r in rows}


def _stem(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("oes") and len(w) > 4:
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def _in_pantry(name: str, keys: set[str]) -> bool:
    """'Onion' is in the pantry if there's 'Brown onions'; all the ingredient's words must appear (singular)."""
    from app.routers.prices import product_key
    words = {_stem(w) for w in product_key(name).split()}
    if not words:
        return False
    return any(words <= {_stem(w) for w in p.split()} for p in keys)


@router.get("/households/{hid}/meals", response_model=MealsOut)
async def meals(hid: UUID, user: User, db: DB, days: int = 7):
    role = my_role(db, hid)
    c = _cfg(db, hid)
    if not c or not c.mealie_url or not c.mealie_token:
        return MealsOut(configured=False, can_edit=role == "owner", url=c.mealie_url if c else None)
    from app.crypto import decrypt_text
    try:
        plan = await run_in_threadpool(fetch_plan, c.mealie_url, decrypt_text(c.mealie_token, str(hid)), c.mealie_allow_internet,
                                       max(1, min(days, 14)))
    except Exception as e:
        msg = str(e) if isinstance(e, RuntimeError) else "Couldn't reach Mealie"
        return MealsOut(configured=True, can_edit=role == "owner", url=c.mealie_url, allow_internet=c.mealie_allow_internet, error=msg)
    keys = _pantry_keys(db, hid)
    return MealsOut(configured=True, can_edit=role == "owner", url=c.mealie_url, allow_internet=c.mealie_allow_internet,
                    meals=[Meal(**{**m, "ingredients": [Ingredient(**i, in_pantry=_in_pantry(i["name"], keys)) for i in m["ingredients"]]})
                           for m in plan])


@router.put("/households/{hid}/mealie", response_model=MealsOut)
async def set_mealie(hid: UUID, body: MealieIn, user: User, db: DB):
    if my_role(db, hid) != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the household owner can change this")
    from app.crypto import encrypt_text
    c = _cfg(db, hid)
    token = c.mealie_token if (c and body.token is None) else (encrypt_text(body.token.strip(), str(hid)) if body.token else None)
    if body.url is None:
        token = None
    db.execute(text("""INSERT INTO household_settings (household_id, mealie_url, mealie_token, mealie_allow_internet, updated_by)
                       VALUES (:h, :u, :t, :a, :by)
                       ON CONFLICT (household_id) DO UPDATE SET mealie_url = EXCLUDED.mealie_url, mealie_token = EXCLUDED.mealie_token,
                           mealie_allow_internet = EXCLUDED.mealie_allow_internet, updated_by = EXCLUDED.updated_by, updated_at = now()"""),
               {"h": hid, "u": body.url, "t": token, "a": body.allow_internet, "by": user.id})
    audit.log(db, user.id, "settings.mealie", "household", "Mealie connected" if body.url else "Mealie disconnected", household_id=hid)
    return await meals(hid, user, db)


@router.post("/households/{hid}/meals/to-list")
def to_list(hid: UUID, body: ToListIn, user: User, db: DB):
    """Put recipe ingredients on the household's shared list (merged with what's already there)."""
    my_role(db, hid)
    from app.routers.shopping import ItemIn, add_item, default_list
    lid = default_list(db, hid, user.id)
    lst = db.execute(text("SELECT * FROM shopping_lists WHERE id = :l"), {"l": lid}).one()
    names = list(dict.fromkeys(" ".join(n.split())[:120] for n in body.names if n and n.strip()))
    for n in names:
        add_item(db, lst, user.id, ItemIn(name=n), source="recipe")
    audit.log(db, user.id, "list.add", "shopping", f"Added {len(names)} recipe ingredient(s) to {lst.name}", household_id=hid)
    return {"added": len(names), "list_id": lst.id}
