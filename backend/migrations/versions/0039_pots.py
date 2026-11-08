"""Savings pots (PO 2026-11-08: "pots are savings"). A bank's in-app pot (e.g. Chase "To Bills pot") becomes a
savings account of its own; moves to/from it are transfers, never spending or income.

Revision ID: 0039
"""
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE account_type ADD VALUE IF NOT EXISTS 'savings'")
    op.execute("""
    ALTER TABLE accounts ADD COLUMN pot_of uuid REFERENCES accounts(id) ON DELETE CASCADE,   -- the bank account holding the pot
                         ADD COLUMN pot_key text CHECK (pot_key IS NULL OR length(pot_key) <= 60);
    CREATE UNIQUE INDEX accounts_pot ON accounts (pot_of, pot_key) WHERE pot_of IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS accounts_pot; ALTER TABLE accounts DROP COLUMN IF EXISTS pot_of, DROP COLUMN IF EXISTS pot_key")
