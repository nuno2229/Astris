"""E12: budgets (category / shop / card) with alert thresholds; daily summary preference.

Revision ID: 0013
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE budgets (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id  uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        scope         text NOT NULL CHECK (scope IN ('household', 'personal')),
        owner_user_id uuid REFERENCES users(id) ON DELETE CASCADE,          -- set for personal budgets
        created_by    uuid NOT NULL REFERENCES users(id),
        kind          text NOT NULL CHECK (kind IN ('category', 'merchant', 'account')),
        category      text,
        merchant_key  text,
        merchant_name text,
        account_id    uuid REFERENCES accounts(id) ON DELETE CASCADE,
        period        text NOT NULL DEFAULT 'monthly' CHECK (period IN ('daily', 'weekly', 'monthly')),
        amount        numeric(18,4) NOT NULL CHECK (amount > 0 AND amount < 1e12),
        currency      char(3) NOT NULL,
        warn_percent  int CHECK (warn_percent IS NULL OR warn_percent BETWEEN 1 AND 99),
        warn_amount   numeric(18,4) CHECK (warn_amount IS NULL OR warn_amount > 0),
        created_at    timestamptz NOT NULL DEFAULT now(),
        CHECK ((scope = 'personal') = (owner_user_id IS NOT NULL)),
        CHECK ((kind = 'category') = (category IS NOT NULL)),
        CHECK ((kind = 'merchant') = (merchant_key IS NOT NULL)),
        CHECK ((kind = 'account') = (account_id IS NOT NULL)),
        CHECK (kind <> 'account' OR scope = 'personal')        -- card limits are personal: it's your card
    );
    CREATE INDEX budgets_household_idx ON budgets (household_id);
    ALTER TABLE budgets ENABLE ROW LEVEL SECURITY;
    ALTER TABLE budgets FORCE ROW LEVEL SECURITY;
    CREATE POLICY budgets_rw ON budgets FOR ALL
        USING (household_id IN (SELECT app_my_household_ids()) AND (scope = 'household' OR owner_user_id = app_uid()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()) AND (scope = 'household' OR owner_user_id = app_uid())
                    AND (account_id IS NULL OR account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid())));
    GRANT SELECT, INSERT, UPDATE, DELETE ON budgets TO ft_app;

    -- E11 hardening: any member may create/see goals; only the creator or a household owner/admin may
    -- change or delete one (and the API refuses deleting a goal holding other members' contributions).
    DROP POLICY goals_rw ON goals;
    CREATE POLICY goals_select ON goals FOR SELECT USING (household_id IN (SELECT app_my_household_ids()));
    CREATE POLICY goals_insert ON goals FOR INSERT
        WITH CHECK (household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid());
    CREATE POLICY goals_modify ON goals FOR UPDATE
        USING (created_by = app_uid() OR app_my_role(household_id) IN ('owner', 'admin'))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()));
    CREATE POLICY goals_delete ON goals FOR DELETE
        USING (created_by = app_uid() OR app_my_role(household_id) IN ('owner', 'admin'));

    ALTER TABLE notification_prefs
        ADD COLUMN budgets       boolean NOT NULL DEFAULT true,
        ADD COLUMN daily_summary boolean NOT NULL DEFAULT false;
    """)


def downgrade() -> None:
    op.execute("""
    DROP POLICY IF EXISTS goals_select ON goals; DROP POLICY IF EXISTS goals_insert ON goals;
    DROP POLICY IF EXISTS goals_modify ON goals; DROP POLICY IF EXISTS goals_delete ON goals;
    CREATE POLICY goals_rw ON goals FOR ALL USING (household_id IN (SELECT app_my_household_ids()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()));
    ALTER TABLE notification_prefs DROP COLUMN IF EXISTS budgets, DROP COLUMN IF EXISTS daily_summary;
    DROP TABLE IF EXISTS budgets;
    """)
