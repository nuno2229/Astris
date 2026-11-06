"""D-8 (b): opt-in daily ECB reference rates, cached locally so conversions work offline.

Revision ID: 0008
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE household_settings ADD COLUMN fx_auto boolean NOT NULL DEFAULT false;

    -- Public reference data (no household data): EUR-based rates from the European Central Bank.
    CREATE TABLE fx_ecb (
        currency   char(3) PRIMARY KEY CHECK (currency ~ '^[A-Z]{3}$'),
        per_eur    numeric(18,8) NOT NULL CHECK (per_eur > 0 AND per_eur < 1e8),
        as_of      date NOT NULL,
        fetched_at timestamptz NOT NULL DEFAULT now()
    );
    GRANT SELECT ON fx_ecb TO ft_app;

    -- The only write path: the background fetcher. Replaces the whole set atomically.
    CREATE FUNCTION fx_ecb_store(p_as_of date, p_rates jsonb) RETURNS int
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE n int;
    BEGIN
      IF jsonb_typeof(p_rates) <> 'object' OR p_as_of > current_date + 1 OR p_as_of < current_date - 30 THEN
        RAISE EXCEPTION 'bad rates payload';
      END IF;
      DELETE FROM fx_ecb;
      INSERT INTO fx_ecb (currency, per_eur, as_of)
        SELECT key, value::numeric, p_as_of FROM jsonb_each_text(p_rates);
      GET DIAGNOSTICS n = ROW_COUNT;
      RETURN n;
    END $$;

    -- Should the fetcher run at all? Only if some household opted in.
    CREATE FUNCTION fx_auto_wanted() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT EXISTS (SELECT 1 FROM household_settings WHERE fx_auto) $$;

    REVOKE ALL ON FUNCTION fx_ecb_store(date, jsonb), fx_auto_wanted() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION fx_ecb_store(date, jsonb), fx_auto_wanted() TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS fx_ecb_store(date, jsonb), fx_auto_wanted();
    DROP TABLE IF EXISTS fx_ecb;
    ALTER TABLE household_settings DROP COLUMN IF EXISTS fx_auto;
    """)
