"""E14: household inventory — products, stock lots (quantity + expiry), weekly shop suggestions.

Revision ID: 0015
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE products (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id   uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        barcode        text CHECK (barcode IS NULL OR barcode ~ '^[0-9A-Za-z\-]{4,48}$'),
        name           text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
        brand          text CHECK (brand IS NULL OR length(brand) <= 80),
        size_qty       numeric(12,3) CHECK (size_qty IS NULL OR size_qty > 0),   -- e.g. 500 (g) per item
        size_unit      text CHECK (size_unit IS NULL OR size_unit IN ('g', 'kg', 'ml', 'l', 'item')),
        restock_below  numeric(12,3) CHECK (restock_below IS NULL OR restock_below >= 0),  -- in items
        location       text CHECK (location IS NULL OR length(location) <= 40),
        created_by     uuid REFERENCES users(id) ON DELETE SET NULL,
        created_at     timestamptz NOT NULL DEFAULT now(),
        UNIQUE (household_id, barcode)
    );
    CREATE INDEX products_household_idx ON products (household_id);

    -- one row per batch: two cartons with different use-by dates are two lots
    CREATE TABLE stock_lots (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        product_id  uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        quantity    numeric(12,3) NOT NULL CHECK (quantity > 0 AND quantity < 1e6),   -- items
        expires_on  date,
        added_by    uuid REFERENCES users(id) ON DELETE SET NULL,
        added_at    timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX stock_lots_product_idx ON stock_lots (product_id);

    CREATE TABLE shop_suggestions (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        generated_at timestamptz NOT NULL DEFAULT now(),
        trigger      text NOT NULL CHECK (trigger IN ('weekly', 'manual')),
        items        jsonb NOT NULL
    );
    CREATE INDEX shop_suggestions_household_idx ON shop_suggestions (household_id, generated_at DESC);

    ALTER TABLE notification_prefs ADD COLUMN stock boolean NOT NULL DEFAULT true;
    ALTER TABLE household_settings ADD COLUMN shop_day int CHECK (shop_day IS NULL OR shop_day BETWEEN 0 AND 6);  -- 0 = Monday

    ALTER TABLE products ENABLE ROW LEVEL SECURITY;         ALTER TABLE products FORCE ROW LEVEL SECURITY;
    ALTER TABLE stock_lots ENABLE ROW LEVEL SECURITY;       ALTER TABLE stock_lots FORCE ROW LEVEL SECURITY;
    ALTER TABLE shop_suggestions ENABLE ROW LEVEL SECURITY; ALTER TABLE shop_suggestions FORCE ROW LEVEL SECURITY;
    -- the pantry is shared by the household (§15.1): members read and update it
    CREATE POLICY products_rw ON products FOR ALL USING (household_id IN (SELECT app_my_household_ids()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()));
    CREATE POLICY lots_rw ON stock_lots FOR ALL USING (product_id IN (SELECT id FROM products))
        WITH CHECK (product_id IN (SELECT id FROM products));
    CREATE POLICY sugg_rw ON shop_suggestions FOR ALL USING (household_id IN (SELECT app_my_household_ids()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()));
    GRANT SELECT, INSERT, UPDATE, DELETE ON products, stock_lots, shop_suggestions TO ft_app;

    -- weekly trigger discovery for the background worker (ids only; work then runs as a member, RLS)
    CREATE FUNCTION shop_day_households(p_dow int) RETURNS TABLE (household_id uuid, member uuid)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT hs.household_id, (SELECT m.user_id FROM memberships m WHERE m.household_id = hs.household_id
                                 ORDER BY (m.role = 'owner') DESC, m.joined_at LIMIT 1)
         FROM household_settings hs WHERE hs.shop_day = p_dow $$;
    REVOKE ALL ON FUNCTION shop_day_households(int) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION shop_day_households(int) TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS shop_day_households(int);
    ALTER TABLE household_settings DROP COLUMN IF EXISTS shop_day;
    ALTER TABLE notification_prefs DROP COLUMN IF EXISTS stock;
    DROP TABLE IF EXISTS shop_suggestions, stock_lots, products;
    """)
