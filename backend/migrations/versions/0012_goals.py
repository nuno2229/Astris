"""E11: savings goals, per-member contributions, auto-match rules.

Revision ID: 0012
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE goals (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        created_by   uuid NOT NULL REFERENCES users(id),
        name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
        target       numeric(18,4) NOT NULL CHECK (target > 0 AND target < 1e12),
        currency     char(3) NOT NULL,
        deadline     date,
        archived     boolean NOT NULL DEFAULT false,
        achieved_at  timestamptz,
        created_at   timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX goals_household_idx ON goals (household_id);

    -- Who put in what. Negative = taken back out. Optionally tied to one statement line (counted once).
    CREATE TABLE goal_contributions (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        goal_id        uuid NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
        user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        amount         numeric(18,4) NOT NULL CHECK (amount <> 0 AND abs(amount) < 1e12),
        made_on        date NOT NULL,
        transaction_id uuid UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
        note           text CHECK (note IS NULL OR length(note) <= 200),
        created_at     timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX goal_contrib_goal_idx ON goal_contributions (goal_id, made_on);

    -- "My transfers to HOLIDAY POT count toward this goal" (per user, matched on their own accounts only).
    CREATE TABLE goal_rules (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        goal_id      uuid NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
        user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        merchant_key text NOT NULL CHECK (length(merchant_key) BETWEEN 1 AND 100),
        label        text NOT NULL CHECK (length(label) <= 100),
        UNIQUE (goal_id, user_id, merchant_key)
    );

    ALTER TABLE goals ENABLE ROW LEVEL SECURITY;              ALTER TABLE goals FORCE ROW LEVEL SECURITY;
    ALTER TABLE goal_contributions ENABLE ROW LEVEL SECURITY; ALTER TABLE goal_contributions FORCE ROW LEVEL SECURITY;
    ALTER TABLE goal_rules ENABLE ROW LEVEL SECURITY;         ALTER TABLE goal_rules FORCE ROW LEVEL SECURITY;

    CREATE POLICY goals_rw ON goals FOR ALL
        USING (household_id IN (SELECT app_my_household_ids()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()));
    -- members see every contribution to their household's goals; you only add/remove your own,
    -- and a statement line must be on an account you own
    CREATE POLICY gc_select ON goal_contributions FOR SELECT
        USING (goal_id IN (SELECT id FROM goals));
    CREATE POLICY gc_write ON goal_contributions FOR ALL
        USING (user_id = app_uid() AND goal_id IN (SELECT id FROM goals))
        WITH CHECK (user_id = app_uid() AND goal_id IN (SELECT id FROM goals) AND (transaction_id IS NULL OR
                    transaction_id IN (SELECT t.id FROM transactions t JOIN accounts a ON a.id = t.account_id
                                        WHERE a.owner_user_id = app_uid())));
    CREATE POLICY gr_own ON goal_rules FOR ALL
        USING (user_id = app_uid()) WITH CHECK (user_id = app_uid() AND goal_id IN (SELECT id FROM goals));
    GRANT SELECT, INSERT, UPDATE, DELETE ON goals, goal_contributions, goal_rules TO ft_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS goal_rules, goal_contributions, goals")
