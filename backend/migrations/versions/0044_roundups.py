"""P-18: weekly and monthly round-up messages, each on a day and time the person picks.

Revision ID: 0044
"""
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE notification_prefs
        ADD COLUMN weekly_roundup  boolean NOT NULL DEFAULT false,
        ADD COLUMN weekly_day      smallint NOT NULL DEFAULT 0 CHECK (weekly_day BETWEEN 0 AND 6),     -- 0 = Monday
        ADD COLUMN weekly_time     time NOT NULL DEFAULT '08:00',
        ADD COLUMN monthly_roundup boolean NOT NULL DEFAULT false,
        ADD COLUMN monthly_day     smallint NOT NULL DEFAULT 1 CHECK (monthly_day BETWEEN 1 AND 31),   -- 31 = last day
        ADD COLUMN monthly_time    time NOT NULL DEFAULT '08:00';
    """)


def downgrade() -> None:
    op.execute("""ALTER TABLE notification_prefs DROP COLUMN IF EXISTS weekly_roundup, DROP COLUMN IF EXISTS weekly_day,
                  DROP COLUMN IF EXISTS weekly_time, DROP COLUMN IF EXISTS monthly_roundup, DROP COLUMN IF EXISTS monthly_day,
                  DROP COLUMN IF EXISTS monthly_time""")
