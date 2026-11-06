"""E18: price comparison — household cache of opt-in Open Prices lookups (UK).

Revision ID: 0022
"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    -- hardening: ADMIN_EMAIL only claims admin while there is no admin yet (no takeover once set up)
    CREATE OR REPLACE FUNCTION auth_register_user(p_email text, p_name text, p_hash text, p_admin_email text) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE uid uuid; admin boolean; has_admin boolean;
    BEGIN
      PERFORM pg_advisory_xact_lock(hashtextextended('register', 0));
      has_admin := EXISTS (SELECT 1 FROM users WHERE is_server_admin);
      IF coalesce(p_admin_email, '') <> '' THEN
        admin := NOT has_admin AND lower(p_email) = lower(p_admin_email);
      ELSE
        admin := NOT EXISTS (SELECT 1 FROM users);
      END IF;
      INSERT INTO users (email, display_name, password_hash, is_server_admin)
      VALUES (lower(p_email), p_name, p_hash, admin) RETURNING id INTO uid;
      RETURN uid;
    END $$;

    ALTER TABLE household_settings ADD COLUMN prices_online boolean NOT NULL DEFAULT false;   -- opt-in, uses internet

    -- per household, so which barcodes a household looked up isn't visible to anyone else
    CREATE TABLE online_prices (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        barcode      text NOT NULL CHECK (barcode ~ '^[0-9]{6,14}$'),
        store        text NOT NULL CHECK (length(store) BETWEEN 1 AND 80),
        price        numeric(10,2) NOT NULL CHECK (price > 0 AND price < 10000),
        currency     char(3) NOT NULL,
        size_qty     numeric(12,3),
        size_unit    text CHECK (size_unit IS NULL OR size_unit IN ('g', 'kg', 'ml', 'l', 'item')),
        observed_on  date,
        source       text NOT NULL DEFAULT 'open_prices',
        fetched_at   timestamptz NOT NULL DEFAULT now(),
        UNIQUE (household_id, barcode, store)
    );
    ALTER TABLE online_prices ENABLE ROW LEVEL SECURITY;
    ALTER TABLE online_prices FORCE ROW LEVEL SECURITY;
    CREATE POLICY op_rw ON online_prices FOR ALL USING (household_id IN (SELECT app_my_household_ids()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()));
    GRANT SELECT, INSERT, UPDATE, DELETE ON online_prices TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS online_prices;
    ALTER TABLE household_settings DROP COLUMN IF EXISTS prices_online;
    """)
