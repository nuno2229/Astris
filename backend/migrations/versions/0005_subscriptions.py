"""E07: subscription registry (separate from the ledger).

Revision ID: 0005
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE subscriptions (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id  uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        created_by    uuid NOT NULL REFERENCES users(id),
        provider      text NOT NULL CHECK (length(provider) BETWEEN 1 AND 80),
        plan          text CHECK (plan IS NULL OR length(plan) <= 80),
        amount        numeric(18,4) NOT NULL CHECK (amount >= 0 AND amount < 1e9),
        currency      char(3) NOT NULL,
        cycle         text NOT NULL CHECK (cycle IN ('weekly', 'fortnightly', 'monthly', 'quarterly', 'yearly')),
        next_renewal  date,
        auto_renew    boolean NOT NULL DEFAULT true,
        status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'trial', 'cancelled')),
        trial_ends    date,
        cancelled_on  date,
        access_until  date,                         -- after cancelling, when access actually stops
        merchant_key  text,                         -- matches statement lines (from rules.merchant_key)
        recurring_id  uuid REFERENCES recurring_series(id) ON DELETE SET NULL,
        notes         text CHECK (notes IS NULL OR length(notes) <= 500),
        created_at    timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now(),
        CHECK (status <> 'trial' OR trial_ends IS NOT NULL),
        CHECK (status <> 'cancelled' OR cancelled_on IS NOT NULL)
    );
    CREATE INDEX subscriptions_household_idx ON subscriptions (household_id);
    ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE subscriptions FORCE ROW LEVEL SECURITY;
    -- household-level record (like a shared list): any member can view and manage
    CREATE POLICY subs_select ON subscriptions FOR SELECT USING (household_id IN (SELECT app_my_household_ids()));
    CREATE POLICY subs_insert ON subscriptions FOR INSERT
        WITH CHECK (household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid());
    CREATE POLICY subs_update ON subscriptions FOR UPDATE
        USING (household_id IN (SELECT app_my_household_ids()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()));
    CREATE POLICY subs_delete ON subscriptions FOR DELETE USING (household_id IN (SELECT app_my_household_ids()));
    GRANT SELECT, INSERT, UPDATE, DELETE ON subscriptions TO ft_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS subscriptions")
