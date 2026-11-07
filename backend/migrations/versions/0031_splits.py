"""P-3 split payments and P-4 settle-up between household members (household on/off option, PO 2026-11-07).

A split divides one payment into parts, each with its own category and optionally who it was for. Category totals
read `tx_lines` (one row per part, or the payment itself when it isn't split). In a household with splitting on,
a part marked as someone else's means they owe the payer; settlements record paying it back.

Revision ID: 0031
"""
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE household_settings ADD COLUMN splits_enabled boolean NOT NULL DEFAULT false;

    CREATE TABLE transaction_splits (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        transaction_id uuid NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        position       smallint NOT NULL CHECK (position BETWEEN 0 AND 19),
        amount         numeric(18,4) NOT NULL CHECK (amount <> 0),
        category       text CHECK (category IS NULL OR length(category) BETWEEN 1 AND 40),
        for_user       uuid REFERENCES users(id) ON DELETE SET NULL,
        note           text CHECK (note IS NULL OR length(note) <= 120),
        UNIQUE (transaction_id, position)
    );
    CREATE INDEX transaction_splits_tx ON transaction_splits (transaction_id);
    ALTER TABLE transaction_splits ENABLE ROW LEVEL SECURITY;
    ALTER TABLE transaction_splits FORCE ROW LEVEL SECURITY;
    -- visible with the payment (transactions' own RLS applies inside); written by the account owner
    CREATE POLICY ts_select ON transaction_splits FOR SELECT
        USING (EXISTS (SELECT 1 FROM transactions t WHERE t.id = transaction_id));
    CREATE POLICY ts_write ON transaction_splits FOR ALL
        USING (EXISTS (SELECT 1 FROM transactions t JOIN accounts a ON a.id = t.account_id
                        WHERE t.id = transaction_id AND a.owner_user_id = app_uid()))
        WITH CHECK (EXISTS (SELECT 1 FROM transactions t JOIN accounts a ON a.id = t.account_id
                        WHERE t.id = transaction_id AND a.owner_user_id = app_uid()));
    GRANT SELECT, INSERT, UPDATE, DELETE ON transaction_splits TO ft_app;

    -- one row per spending line: split parts, or the whole payment when it isn't split
    CREATE VIEW tx_lines WITH (security_invoker = true) AS
        SELECT t.id, t.account_id, t.posted_on, t.currency, t.description, t.merchant, t.merchant_key, t.transfer_group,
               t.refund_of, t.created_at, t.category_source,
               coalesce(s.amount, t.amount) AS amount, CASE WHEN s.id IS NULL THEN t.category ELSE s.category END AS category,
               s.for_user
          FROM transactions t LEFT JOIN transaction_splits s ON s.transaction_id = t.id;
    GRANT SELECT ON tx_lines TO ft_app;

    CREATE TABLE settlements (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        from_user    uuid REFERENCES users(id) ON DELETE SET NULL,     -- who paid back
        to_user      uuid REFERENCES users(id) ON DELETE SET NULL,     -- who received it
        amount       numeric(18,4) NOT NULL CHECK (amount > 0 AND amount < 1e9),
        currency     char(3) NOT NULL,
        settled_on   date NOT NULL DEFAULT current_date,
        note         text CHECK (note IS NULL OR length(note) <= 200),
        created_by   uuid NOT NULL DEFAULT app_uid() REFERENCES users(id),
        created_at   timestamptz NOT NULL DEFAULT now(),
        CHECK (from_user <> to_user)
    );
    CREATE INDEX settlements_household ON settlements (household_id);
    ALTER TABLE settlements ENABLE ROW LEVEL SECURITY;
    ALTER TABLE settlements FORCE ROW LEVEL SECURITY;
    CREATE POLICY st_select ON settlements FOR SELECT USING (household_id IN (SELECT app_my_household_ids()));
    -- either side records a settlement they took part in; the person who recorded it can remove it
    CREATE POLICY st_insert ON settlements FOR INSERT WITH CHECK (
        household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid() AND app_uid() IN (from_user, to_user));
    CREATE POLICY st_delete ON settlements FOR DELETE USING (
        household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid());
    GRANT SELECT, INSERT, DELETE ON settlements TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS settlements;
    DROP VIEW IF EXISTS tx_lines;
    DROP TABLE IF EXISTS transaction_splits;
    ALTER TABLE household_settings DROP COLUMN IF EXISTS splits_enabled;
    """)
