"""E15: assistant actions awaiting confirmation (short-lived; per user).

Revision ID: 0016
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE copilot_actions (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        kind         text NOT NULL CHECK (kind IN ('set_budget', 'pantry_add', 'pantry_use', 'goal_contribute')),
        params       jsonb NOT NULL,
        summary      text NOT NULL CHECK (length(summary) <= 300),
        created_at   timestamptz NOT NULL DEFAULT now(),
        expires_at   timestamptz NOT NULL DEFAULT now() + interval '10 minutes'
    );
    ALTER TABLE copilot_actions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE copilot_actions FORCE ROW LEVEL SECURITY;
    CREATE POLICY ca_own ON copilot_actions FOR ALL
        USING (user_id = app_uid())
        WITH CHECK (user_id = app_uid() AND household_id IN (SELECT app_my_household_ids()));
    GRANT SELECT, INSERT, DELETE ON copilot_actions TO ft_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS copilot_actions")
