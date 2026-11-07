"""G-6: a goal can follow a savings account's balance instead of (or as well as tracking) contributions.

Revision ID: 0028
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE goals ADD COLUMN account_id uuid REFERENCES accounts(id) ON DELETE SET NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE goals DROP COLUMN IF EXISTS account_id")
