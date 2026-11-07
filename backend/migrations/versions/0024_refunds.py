"""P-2: refunds linked to the original purchase. A linked refund takes the purchase's category and is netted off
spending (not counted as income).

Revision ID: 0024
"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE transactions
        ADD COLUMN refund_of uuid REFERENCES transactions(id) ON DELETE SET NULL,
        ADD COLUMN not_refund boolean NOT NULL DEFAULT false,      -- the owner said "not a refund": never suggest again
        ADD CONSTRAINT refund_is_money_in CHECK (refund_of IS NULL OR amount > 0);
    CREATE INDEX transactions_refund_idx ON transactions (refund_of) WHERE refund_of IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE transactions DROP COLUMN IF EXISTS refund_of, DROP COLUMN IF EXISTS not_refund")
