"""Security: a pot is always shared exactly like the account that holds it (unshare, freeze on leaving and
attribution changes follow the parent), never left behind.

Revision ID: 0040
"""
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE FUNCTION ha_follow_parent() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        DELETE FROM household_accounts WHERE household_id = OLD.household_id
           AND account_id IN (SELECT id FROM accounts WHERE pot_of = OLD.account_id);
        RETURN OLD;
      END IF;
      IF TG_OP = 'UPDATE' THEN
        UPDATE household_accounts SET attribution = NEW.attribution, until = NEW.until, frozen_at = NEW.frozen_at
         WHERE household_id = NEW.household_id AND account_id IN (SELECT id FROM accounts WHERE pot_of = NEW.account_id);
        RETURN NEW;
      END IF;
      -- INSERT: sharing the parent shares its pots the same way
      INSERT INTO household_accounts (household_id, account_id, attribution)
      SELECT NEW.household_id, id, NEW.attribution FROM accounts WHERE pot_of = NEW.account_id
      ON CONFLICT DO NOTHING;
      RETURN NEW;
    END $$;
    CREATE TRIGGER ha_follow_parent AFTER INSERT OR UPDATE OR DELETE ON household_accounts
        FOR EACH ROW WHEN (pg_trigger_depth() = 0) EXECUTE FUNCTION ha_follow_parent();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ha_follow_parent ON household_accounts; DROP FUNCTION IF EXISTS ha_follow_parent();")
