"""G-17 (PO 2026-11-08): prices from shop websites. Either you send one from the page you're looking at ("Send
price to Astris"), or, opt-in, Astris looks up Morrisons and Aldi itself (shops that don't block a normal browser).

Revision ID: 0041
"""
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE household_settings ADD COLUMN web_lookups boolean NOT NULL DEFAULT false,
                                   ADD COLUMN web_lookups_at timestamptz;
    CREATE TABLE web_prices (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        added_by     uuid REFERENCES users(id) ON DELETE SET NULL,
        source       text NOT NULL CHECK (source IN ('you', 'auto')),
        store        text NOT NULL CHECK (length(store) BETWEEN 1 AND 40),
        name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 160),
        query        text CHECK (query IS NULL OR length(query) <= 80),       -- auto: what was searched for
        price        numeric(10,2) NOT NULL CHECK (price > 0 AND price < 10000),
        size_qty     numeric(12,3),
        size_unit    text CHECK (size_unit IS NULL OR size_unit IN ('g', 'kg', 'ml', 'l', 'item')),
        url          text CHECK (url IS NULL OR length(url) <= 500),
        seen_on      date NOT NULL DEFAULT current_date,
        created_at   timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX web_prices_household ON web_prices (household_id, store);
    ALTER TABLE web_prices ENABLE ROW LEVEL SECURITY;
    ALTER TABLE web_prices FORCE ROW LEVEL SECURITY;
    CREATE POLICY wp_rw ON web_prices FOR ALL USING (household_id IN (SELECT app_my_household_ids()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()));
    GRANT SELECT, INSERT, DELETE ON web_prices TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""DROP TABLE IF EXISTS web_prices;
                  ALTER TABLE household_settings DROP COLUMN IF EXISTS web_lookups, DROP COLUMN IF EXISTS web_lookups_at;""")
