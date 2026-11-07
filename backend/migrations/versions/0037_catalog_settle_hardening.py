"""Security: catalogue entries are owned by the household that shared them (no overwriting others' or Open Food
Facts names; removed when sharing is turned off; which household isn't readable). Confirmed settlements can only be
removed by the payer; the receiver can only reject a pending one.

Revision ID: 0037
"""
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE product_catalog ADD COLUMN contributed_by uuid REFERENCES households(id) ON DELETE CASCADE;
    REVOKE SELECT ON product_catalog FROM ft_app;
    GRANT SELECT (barcode, name, brand, size_qty, size_unit, source, updated_at) ON product_catalog TO ft_app;

    CREATE OR REPLACE FUNCTION catalog_share(p_household uuid, p_barcode text, p_name text, p_brand text, p_qty numeric, p_unit text)
    RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    BEGIN
      IF p_household NOT IN (SELECT app_my_household_ids()) THEN RETURN; END IF;
      IF NOT coalesce((SELECT share_products FROM household_settings WHERE household_id = p_household), false) THEN RETURN; END IF;
      IF p_barcode !~ '^[0-9]{6,14}$' OR length(btrim(coalesce(p_name, ''))) = 0 THEN RETURN; END IF;
      INSERT INTO product_catalog (barcode, name, brand, size_qty, size_unit, source, contributed_by)
      VALUES (p_barcode, left(btrim(p_name), 120), left(nullif(btrim(p_brand), ''), 80), p_qty,
              CASE WHEN p_unit IN ('g', 'kg', 'ml', 'l', 'item') THEN p_unit END, 'household', p_household)
      ON CONFLICT (barcode) DO UPDATE SET name = EXCLUDED.name, brand = EXCLUDED.brand, size_qty = EXCLUDED.size_qty,
             size_unit = EXCLUDED.size_unit, updated_at = now()
       WHERE product_catalog.source = 'household' AND product_catalog.contributed_by = p_household;   -- only your own entries
    END $$;

    CREATE FUNCTION catalog_unshare(p_household uuid) RETURNS void
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ DELETE FROM product_catalog WHERE contributed_by = p_household AND source = 'household'
         AND p_household IN (SELECT app_my_household_ids()) $$;
    REVOKE ALL ON FUNCTION catalog_unshare(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION catalog_unshare(uuid) TO ft_app;

    DROP POLICY st_delete ON settlements;
    CREATE POLICY st_delete ON settlements FOR DELETE USING (
        household_id IN (SELECT app_my_household_ids())
        AND (from_user = app_uid() OR (to_user = app_uid() AND NOT confirmed)));
    """)


def downgrade() -> None:
    op.execute(r"""
    DROP POLICY st_delete ON settlements;
    CREATE POLICY st_delete ON settlements FOR DELETE USING (
        household_id IN (SELECT app_my_household_ids()) AND (created_by = app_uid() OR to_user = app_uid()));
    DROP FUNCTION IF EXISTS catalog_unshare(uuid);
    REVOKE SELECT ON product_catalog FROM ft_app;
    GRANT SELECT ON product_catalog TO ft_app;
    ALTER TABLE product_catalog DROP COLUMN IF EXISTS contributed_by;
    """)
