"""E08: cash wallet (opening balance, manual entries, cash counts, ATM links) + multi-currency.

Revision ID: 0007
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE accounts
        ADD COLUMN opening_balance numeric(18,4) NOT NULL DEFAULT 0 CHECK (abs(opening_balance) < 1e14),
        ADD COLUMN opening_date    date;

    ALTER TABLE transactions
        ADD COLUMN source            text NOT NULL DEFAULT 'import' CHECK (source IN ('import', 'manual', 'adjustment', 'transfer')),
        ADD COLUMN created_by        uuid REFERENCES users(id),
        ADD COLUMN original_amount   numeric(18,4) CHECK (original_amount IS NULL OR abs(original_amount) < 1e14),
        ADD COLUMN original_currency char(3),
        -- both legs of a cash move (ATM withdrawal <-> wallet top-up) share a group; excluded from spend/income
        ADD COLUMN transfer_group    uuid,
        ADD CHECK ((original_amount IS NULL) = (original_currency IS NULL));
    CREATE INDEX transactions_transfer_idx ON transactions (transfer_group) WHERE transfer_group IS NOT NULL;

    -- Household exchange rates (manual for now; automatic source is decision D-8). 1 from_ccy = rate to_ccy.
    CREATE TABLE fx_rates (
        household_id  uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        from_currency char(3) NOT NULL,
        to_currency   char(3) NOT NULL,
        rate          numeric(18,8) NOT NULL CHECK (rate > 0 AND rate < 1e8),
        as_of         date NOT NULL,
        source        text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual')),
        updated_by    uuid REFERENCES users(id),
        updated_at    timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (household_id, from_currency, to_currency),
        CHECK (from_currency <> to_currency)
    );
    ALTER TABLE fx_rates ENABLE ROW LEVEL SECURITY;
    ALTER TABLE fx_rates FORCE ROW LEVEL SECURITY;
    CREATE POLICY fx_select ON fx_rates FOR SELECT USING (household_id IN (SELECT app_my_household_ids()));
    CREATE POLICY fx_write ON fx_rates FOR ALL
        USING (app_my_role(household_id) IN ('owner', 'admin'))
        WITH CHECK (app_my_role(household_id) IN ('owner', 'admin') AND updated_by = app_uid());
    GRANT SELECT, INSERT, UPDATE, DELETE ON fx_rates TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS fx_rates;
    ALTER TABLE transactions DROP COLUMN IF EXISTS source, DROP COLUMN IF EXISTS created_by,
        DROP COLUMN IF EXISTS original_amount, DROP COLUMN IF EXISTS original_currency, DROP COLUMN IF EXISTS transfer_group;
    ALTER TABLE accounts DROP COLUMN IF EXISTS opening_balance, DROP COLUMN IF EXISTS opening_date;
    """)
