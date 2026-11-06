"""E12 hardening: per-command budget policies (creator or owner/admin manage household budgets).

Revision ID: 0014
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    DROP POLICY budgets_rw ON budgets;
    CREATE POLICY budgets_select ON budgets FOR SELECT
        USING (household_id IN (SELECT app_my_household_ids()) AND (scope = 'household' OR owner_user_id = app_uid()));
    CREATE POLICY budgets_insert ON budgets FOR INSERT
        WITH CHECK (household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid()
                    AND (scope = 'household' OR owner_user_id = app_uid())
                    AND (account_id IS NULL OR account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid())));
    CREATE POLICY budgets_update ON budgets FOR UPDATE
        USING ((scope = 'personal' AND owner_user_id = app_uid())
               OR (scope = 'household' AND (created_by = app_uid() OR app_my_role(household_id) IN ('owner', 'admin'))))
        WITH CHECK (household_id IN (SELECT app_my_household_ids())
                    AND (account_id IS NULL OR account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid())));
    CREATE POLICY budgets_delete ON budgets FOR DELETE
        USING ((scope = 'personal' AND owner_user_id = app_uid())
               OR (scope = 'household' AND (created_by = app_uid() OR app_my_role(household_id) IN ('owner', 'admin'))));
    """)


def downgrade() -> None:
    op.execute("""
    DROP POLICY IF EXISTS budgets_select ON budgets; DROP POLICY IF EXISTS budgets_insert ON budgets;
    DROP POLICY IF EXISTS budgets_update ON budgets; DROP POLICY IF EXISTS budgets_delete ON budgets;
    CREATE POLICY budgets_rw ON budgets FOR ALL
        USING (household_id IN (SELECT app_my_household_ids()) AND (scope = 'household' OR owner_user_id = app_uid()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()) AND (scope = 'household' OR owner_user_id = app_uid()));
    """)
