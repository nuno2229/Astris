"""Security: splits on a former member's kept history (G-9) only show if they existed when that person left;
later edits fall back to showing the whole payment.

Revision ID: 0033
"""
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE transaction_splits ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
    DROP POLICY ts_select ON transaction_splits;
    CREATE POLICY ts_select ON transaction_splits FOR SELECT USING (
        EXISTS (SELECT 1 FROM transactions t WHERE t.id = transaction_id
                 AND (t.account_id IN (SELECT app_visible_account_ids())
                      OR EXISTS (SELECT 1 FROM app_frozen_shares() f
                                  WHERE f.account_id = t.account_id AND transaction_splits.created_at <= f.frozen_at))));
    """)


def downgrade() -> None:
    op.execute(r"""
    DROP POLICY ts_select ON transaction_splits;
    CREATE POLICY ts_select ON transaction_splits FOR SELECT USING (EXISTS (SELECT 1 FROM transactions t WHERE t.id = transaction_id));
    ALTER TABLE transaction_splits DROP COLUMN IF EXISTS created_at;
    """)
