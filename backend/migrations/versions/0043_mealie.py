"""E20-02: Mealie meal plans (opt-in): this week's meals, ingredients checked against the pantry, missing ones to the list.

Revision ID: 0043
"""
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE household_settings
        ADD COLUMN mealie_url text CHECK (mealie_url IS NULL OR mealie_url ~ '^https?://[^/]+$'),
        ADD COLUMN mealie_token text CHECK (mealie_token IS NULL OR length(mealie_token) <= 2000),   -- encrypted when ASTRIS_KEY is set
        ADD COLUMN mealie_allow_internet boolean NOT NULL DEFAULT false;
    ALTER TABLE shopping_items DROP CONSTRAINT IF EXISTS shopping_items_source_check;
    ALTER TABLE shopping_items ADD CONSTRAINT shopping_items_source_check
        CHECK (source IN ('manual', 'pantry', 'assistant', 'favourite', 'recipe'));
    """)


def downgrade() -> None:
    op.execute("""ALTER TABLE household_settings DROP COLUMN IF EXISTS mealie_url, DROP COLUMN IF EXISTS mealie_token,
                  DROP COLUMN IF EXISTS mealie_allow_internet""")
