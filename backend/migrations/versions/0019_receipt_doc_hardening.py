"""Hardening: receipt deletion limited to uploader or household owner/admin (kept photos would cascade);
document quota counted outside RLS.

Revision ID: 0019
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    DROP POLICY receipts_rw ON receipts;
    CREATE POLICY receipts_select ON receipts FOR SELECT USING (household_id IN (SELECT app_my_household_ids()));
    CREATE POLICY receipts_insert ON receipts FOR INSERT
        WITH CHECK (household_id IN (SELECT app_my_household_ids()) AND uploaded_by = app_uid()
                    AND (transaction_id IS NULL OR transaction_id IN (SELECT id FROM transactions)));
    CREATE POLICY receipts_update ON receipts FOR UPDATE USING (household_id IN (SELECT app_my_household_ids()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids())
                    AND (transaction_id IS NULL OR transaction_id IN (SELECT id FROM transactions)));
    CREATE POLICY receipts_delete ON receipts FOR DELETE
        USING (uploaded_by = app_uid() OR app_my_role(household_id) IN ('owner', 'admin'));

    CREATE FUNCTION doc_usage_bytes() RETURNS bigint
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT COALESCE(sum(size), 0)::bigint FROM documents WHERE owner_user_id = app_uid() $$;
    REVOKE ALL ON FUNCTION doc_usage_bytes() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION doc_usage_bytes() TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS doc_usage_bytes();
    DROP POLICY IF EXISTS receipts_select ON receipts; DROP POLICY IF EXISTS receipts_insert ON receipts;
    DROP POLICY IF EXISTS receipts_update ON receipts; DROP POLICY IF EXISTS receipts_delete ON receipts;
    CREATE POLICY receipts_rw ON receipts FOR ALL USING (household_id IN (SELECT app_my_household_ids()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()));
    """)
