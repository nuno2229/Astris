"""Household categories (G-13): the built-in list plus any a household adds. Labels only; totals group by name."""
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.categorise.rules import CATEGORIES


def for_household(db: Session, hid: UUID) -> list[str]:
    extra = [r[0] for r in db.execute(text("SELECT name FROM household_categories WHERE household_id = :h ORDER BY lower(name)"),
                                      {"h": hid})]
    return CATEGORIES + [c for c in extra if c not in CATEGORIES]


def for_user(db: Session) -> list[str]:
    """Every category the signed-in user may pick: built-ins plus those of all their households (RLS-scoped)."""
    extra = [r[0] for r in db.execute(text("SELECT DISTINCT name FROM household_categories ORDER BY name"))]
    return CATEGORIES + [c for c in extra if c not in CATEGORIES]


def for_accounts(db: Session, account_ids: list) -> list[str]:
    """Categories usable for an account's lines: built-ins plus those of the households it is shared into."""
    extra = [r[0] for r in db.execute(text("""
        SELECT DISTINCT c.name FROM household_categories c JOIN household_accounts ha ON ha.household_id = c.household_id
         WHERE ha.account_id = ANY(:a) ORDER BY c.name"""), {"a": list(account_ids)})]
    return CATEGORIES + [c for c in extra if c not in CATEGORIES]
