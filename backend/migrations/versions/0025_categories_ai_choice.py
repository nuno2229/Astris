"""G-13: households add their own categories. G-14: the account owner picks which household's AI processes
an account shared into several households (or none).

Revision ID: 0025
"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE household_categories (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        name         text NOT NULL CHECK (name ~ '^[[:alnum:] &''()/,.+-]{1,40}$' AND name = btrim(name)),
        created_by   uuid NOT NULL DEFAULT app_uid() REFERENCES users(id),
        created_at   timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX household_categories_name ON household_categories (household_id, lower(name));
    ALTER TABLE household_categories ENABLE ROW LEVEL SECURITY;
    ALTER TABLE household_categories FORCE ROW LEVEL SECURITY;
    -- members see and add; the creator or an owner/admin removes
    CREATE POLICY hc_select ON household_categories FOR SELECT USING (household_id IN (SELECT app_my_household_ids()));
    CREATE POLICY hc_insert ON household_categories FOR INSERT
        WITH CHECK (household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid());
    CREATE POLICY hc_delete ON household_categories FOR DELETE
        USING (household_id IN (SELECT app_my_household_ids())
               AND (created_by = app_uid() OR app_my_role(household_id) IN ('owner', 'admin')));
    GRANT SELECT, INSERT, DELETE ON household_categories TO ft_app;

    -- NULL + ai_off false = automatic (first household with AI on, by share date)
    ALTER TABLE accounts ADD COLUMN ai_household_id uuid REFERENCES households(id) ON DELETE SET NULL,
                         ADD COLUMN ai_off boolean NOT NULL DEFAULT false;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE accounts DROP COLUMN IF EXISTS ai_household_id, DROP COLUMN IF EXISTS ai_off;
    DROP TABLE IF EXISTS household_categories;
    """)
