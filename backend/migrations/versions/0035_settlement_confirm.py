"""Security (P-4): a payment recorded by the payer is pending until the receiver confirms it, so nobody can clear
their own debt. Only confirmed payments count.

Revision ID: 0035
"""
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE settlements ADD COLUMN confirmed boolean NOT NULL DEFAULT false;
    UPDATE settlements SET confirmed = true;
    DROP POLICY st_insert ON settlements;
    -- the receiver records a confirmed payment; the payer can only record a pending one
    CREATE POLICY st_insert ON settlements FOR INSERT WITH CHECK (
        household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid()
        AND (to_user = app_uid() OR (from_user = app_uid() AND NOT confirmed)));
    CREATE POLICY st_confirm ON settlements FOR UPDATE
        USING (household_id IN (SELECT app_my_household_ids()) AND to_user = app_uid())
        WITH CHECK (to_user = app_uid());
    GRANT UPDATE (confirmed) ON settlements TO ft_app;
    DROP POLICY st_delete ON settlements;
    CREATE POLICY st_delete ON settlements FOR DELETE USING (
        household_id IN (SELECT app_my_household_ids()) AND (created_by = app_uid() OR to_user = app_uid()));
    """)


def downgrade() -> None:
    op.execute(r"""
    DROP POLICY IF EXISTS st_confirm ON settlements;
    DROP POLICY IF EXISTS st_insert ON settlements;
    DROP POLICY IF EXISTS st_delete ON settlements;
    ALTER TABLE settlements DROP COLUMN IF EXISTS confirmed;
    CREATE POLICY st_insert ON settlements FOR INSERT WITH CHECK (
        household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid() AND app_uid() IN (from_user, to_user));
    CREATE POLICY st_delete ON settlements FOR DELETE USING (
        household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid());
    """)
