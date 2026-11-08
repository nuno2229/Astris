"""Statement category hints (e.g. AMEX's own "Entertainment-Restaurants"), used when our rules don't know the shop.

Revision ID: 0038
"""
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE transactions ADD COLUMN bank_category text CHECK (bank_category IS NULL OR length(bank_category) <= 80)")


def downgrade() -> None:
    op.execute("ALTER TABLE transactions DROP COLUMN IF EXISTS bank_category")
