"""E19: server admin (first registered user), server settings (sign-ups, update check).

Revision ID: 0020
"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE users ADD COLUMN is_server_admin boolean NOT NULL DEFAULT false;
    GRANT SELECT (is_server_admin) ON users TO ft_app;
    -- existing installs: the earliest account becomes the server admin
    UPDATE users SET is_server_admin = true WHERE id = (SELECT id FROM users ORDER BY created_at LIMIT 1);

    CREATE OR REPLACE FUNCTION auth_register_user(p_email text, p_name text, p_hash text) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE uid uuid; first boolean;
    BEGIN
      PERFORM pg_advisory_xact_lock(hashtextextended('register', 0));
      first := NOT EXISTS (SELECT 1 FROM users);
      INSERT INTO users (email, display_name, password_hash, is_server_admin)
      VALUES (lower(p_email), p_name, p_hash, first) RETURNING id INTO uid;
      RETURN uid;
    END $$;

    CREATE TABLE server_settings (
        id                int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
        registration_open boolean NOT NULL DEFAULT true,
        update_check      boolean NOT NULL DEFAULT false,      -- opt-in, uses internet (D-1)
        latest_version    text,
        latest_url        text,
        checked_at        timestamptz,
        updated_at        timestamptz NOT NULL DEFAULT now()
    );
    INSERT INTO server_settings (id) VALUES (1);
    ALTER TABLE server_settings ENABLE ROW LEVEL SECURITY;
    ALTER TABLE server_settings FORCE ROW LEVEL SECURITY;
    CREATE POLICY ss_select ON server_settings FOR SELECT USING (app_uid() IS NOT NULL);
    CREATE POLICY ss_update ON server_settings FOR UPDATE
        USING (EXISTS (SELECT 1 FROM users WHERE id = app_uid() AND is_server_admin))
        WITH CHECK (EXISTS (SELECT 1 FROM users WHERE id = app_uid() AND is_server_admin));
    GRANT SELECT, UPDATE ON server_settings TO ft_app;

    -- pre-auth: can a new account be created right now?
    CREATE FUNCTION registration_open() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT registration_open OR NOT EXISTS (SELECT 1 FROM users) FROM server_settings WHERE id = 1 $$;
    -- background update check writes its result (no user involved)
    CREATE FUNCTION server_record_latest(p_version text, p_url text) RETURNS void
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ UPDATE server_settings SET latest_version = left(p_version, 40), latest_url = left(p_url, 300), checked_at = now()
        WHERE id = 1 AND update_check $$;
    CREATE FUNCTION server_update_check_on() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT update_check FROM server_settings WHERE id = 1 $$;
    REVOKE ALL ON FUNCTION registration_open(), server_record_latest(text, text), server_update_check_on() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION registration_open(), server_record_latest(text, text), server_update_check_on() TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS registration_open(), server_record_latest(text, text), server_update_check_on();
    DROP TABLE IF EXISTS server_settings;
    CREATE OR REPLACE FUNCTION auth_register_user(p_email text, p_name text, p_hash text) RETURNS uuid
    LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ INSERT INTO users (email, display_name, password_hash) VALUES (lower(p_email), p_name, p_hash) RETURNING id $$;
    ALTER TABLE users DROP COLUMN IF EXISTS is_server_admin;
    """)
