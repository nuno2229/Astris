"""G-8 + P-7: a server-wide product name catalogue (barcode -> name/brand/size; never prices, never which household).
Filled from households that opt in to sharing product names, and from an offline Open Food Facts import.

Revision ID: 0034
"""
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE household_settings ADD COLUMN share_products boolean NOT NULL DEFAULT false;   -- opt-in (G-8)

    CREATE TABLE product_catalog (
        barcode    text PRIMARY KEY CHECK (barcode ~ '^[0-9]{6,14}$'),
        name       text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
        brand      text CHECK (brand IS NULL OR length(brand) <= 80),
        size_qty   numeric(12,3),
        size_unit  text CHECK (size_unit IS NULL OR size_unit IN ('g', 'kg', 'ml', 'l', 'item')),
        source     text NOT NULL CHECK (source IN ('household', 'open_food_facts')),
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    -- readable by everyone on the server; written only through catalog_share() or the admin import
    GRANT SELECT ON product_catalog TO ft_app;

    CREATE FUNCTION catalog_share(p_household uuid, p_barcode text, p_name text, p_brand text, p_qty numeric, p_unit text)
    RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    BEGIN
      IF p_household NOT IN (SELECT app_my_household_ids()) THEN RETURN; END IF;
      IF NOT coalesce((SELECT share_products FROM household_settings WHERE household_id = p_household), false) THEN RETURN; END IF;
      IF p_barcode !~ '^[0-9]{6,14}$' OR length(btrim(coalesce(p_name, ''))) = 0 THEN RETURN; END IF;
      INSERT INTO product_catalog (barcode, name, brand, size_qty, size_unit, source)
      VALUES (p_barcode, left(btrim(p_name), 120), left(nullif(btrim(p_brand), ''), 80), p_qty,
              CASE WHEN p_unit IN ('g', 'kg', 'ml', 'l', 'item') THEN p_unit END, 'household')
      ON CONFLICT (barcode) DO UPDATE SET name = EXCLUDED.name, brand = coalesce(EXCLUDED.brand, product_catalog.brand),
             size_qty = coalesce(EXCLUDED.size_qty, product_catalog.size_qty),
             size_unit = coalesce(EXCLUDED.size_unit, product_catalog.size_unit), source = 'household', updated_at = now();
    END $$;
    REVOKE ALL ON FUNCTION catalog_share(uuid, text, text, text, numeric, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION catalog_share(uuid, text, text, text, numeric, text) TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS catalog_share(uuid, text, text, text, numeric, text);
    DROP TABLE IF EXISTS product_catalog;
    ALTER TABLE household_settings DROP COLUMN IF EXISTS share_products;
    """)
