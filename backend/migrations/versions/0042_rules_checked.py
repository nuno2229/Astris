"""Performance (E21-04): the categorisation rules look at each transaction once, not at every unrecognised line on
every import. Clearing a category (e.g. undoing a transfer) makes the rules look again.

Revision ID: 0042
"""
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE transactions ADD COLUMN rules_checked boolean NOT NULL DEFAULT false;
    UPDATE transactions SET rules_checked = true WHERE category_source IS NOT NULL;
    CREATE INDEX transactions_rules_todo ON transactions (account_id) WHERE category_source IS NULL AND NOT rules_checked;
    CREATE FUNCTION tx_rules_recheck() RETURNS trigger LANGUAGE plpgsql AS
    $$ BEGIN
         IF NEW.category_source IS NULL AND OLD.category_source IS NOT NULL THEN NEW.rules_checked := false; END IF;
         RETURN NEW;
       END $$;
    CREATE TRIGGER tx_rules_recheck BEFORE UPDATE OF category_source ON transactions
        FOR EACH ROW EXECUTE FUNCTION tx_rules_recheck();
    """)


def downgrade() -> None:
    op.execute("""DROP TRIGGER IF EXISTS tx_rules_recheck ON transactions; DROP FUNCTION IF EXISTS tx_rules_recheck();
                  DROP INDEX IF EXISTS transactions_rules_todo; ALTER TABLE transactions DROP COLUMN IF EXISTS rules_checked;""")
