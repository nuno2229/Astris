"""E06: recurring & scheduled transactions.

Revision ID: 0004
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE recurring_series (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id       uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        merchant_key     text NOT NULL,
        direction        text NOT NULL CHECK (direction IN ('in', 'out')),
        name             text NOT NULL,
        category         text,
        cadence          text NOT NULL CHECK (cadence IN ('weekly', 'fortnightly', 'monthly', 'yearly')),
        expected_amount  numeric(18,4) NOT NULL,        -- signed, like transactions
        previous_amount  numeric(18,4),                 -- set when the amount drifted (E06-02)
        currency         char(3) NOT NULL,
        occurrences      int NOT NULL,
        last_date        date NOT NULL,
        next_date        date NOT NULL,
        -- active: on schedule; missed: statements cover the due date but it never came (E06-03);
        -- dismissed: user said it isn't recurring (detector won't resurrect it)
        status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'missed', 'dismissed')),
        created_at       timestamptz NOT NULL DEFAULT now(),
        updated_at       timestamptz NOT NULL DEFAULT now(),
        UNIQUE (account_id, merchant_key, direction)
    );
    CREATE INDEX recurring_next_idx ON recurring_series (account_id, next_date) WHERE status <> 'dismissed';
    ALTER TABLE recurring_series ENABLE ROW LEVEL SECURITY;
    ALTER TABLE recurring_series FORCE ROW LEVEL SECURITY;
    CREATE POLICY rs_select ON recurring_series FOR SELECT USING (account_id IN (SELECT app_visible_account_ids()));
    CREATE POLICY rs_write ON recurring_series FOR ALL
        USING (account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()))
        WITH CHECK (account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()));
    GRANT SELECT, INSERT, UPDATE, DELETE ON recurring_series TO ft_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS recurring_series")
