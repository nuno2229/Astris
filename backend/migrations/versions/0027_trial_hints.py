"""G-15: likely free trials spotted from £0.00 / £1.00 card checks; "not a trial" remembered per household.

Revision ID: 0027
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE trial_dismissals (
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        merchant_key text NOT NULL CHECK (length(merchant_key) BETWEEN 1 AND 200),
        dismissed_by uuid NOT NULL DEFAULT app_uid() REFERENCES users(id) ON DELETE CASCADE,
        created_at   timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (household_id, merchant_key)
    );
    ALTER TABLE trial_dismissals ENABLE ROW LEVEL SECURITY;
    ALTER TABLE trial_dismissals FORCE ROW LEVEL SECURITY;
    CREATE POLICY td_household ON trial_dismissals FOR ALL USING (household_id IN (SELECT app_my_household_ids()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()) AND dismissed_by = app_uid());
    GRANT SELECT, INSERT, DELETE ON trial_dismissals TO ft_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS trial_dismissals")
