"""Hardening: server admin can be fixed by ADMIN_EMAIL; closed sign-ups have no empty-table bypass.

Revision ID: 0021
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    DROP FUNCTION auth_register_user(text, text, text);
    -- p_admin_email set (ADMIN_EMAIL): only that address becomes server admin. Unset: first account (legacy).
    CREATE FUNCTION auth_register_user(p_email text, p_name text, p_hash text, p_admin_email text) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE uid uuid; admin boolean;
    BEGIN
      PERFORM pg_advisory_xact_lock(hashtextextended('register', 0));
      IF coalesce(p_admin_email, '') <> '' THEN
        admin := lower(p_email) = lower(p_admin_email);
      ELSE
        admin := NOT EXISTS (SELECT 1 FROM users);
      END IF;
      INSERT INTO users (email, display_name, password_hash, is_server_admin)
      VALUES (lower(p_email), p_name, p_hash, admin) RETURNING id INTO uid;
      RETURN uid;
    END $$;
    REVOKE ALL ON FUNCTION auth_register_user(text, text, text, text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION auth_register_user(text, text, text, text) TO ft_app;

    CREATE OR REPLACE FUNCTION registration_open() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT registration_open FROM server_settings WHERE id = 1 $$;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION auth_register_user(text, text, text, text);
    CREATE FUNCTION auth_register_user(p_email text, p_name text, p_hash text) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ DECLARE uid uuid; first boolean;
    BEGIN PERFORM pg_advisory_xact_lock(hashtextextended('register', 0)); first := NOT EXISTS (SELECT 1 FROM users);
      INSERT INTO users (email, display_name, password_hash, is_server_admin) VALUES (lower(p_email), p_name, p_hash, first)
      RETURNING id INTO uid; RETURN uid; END $$;
    GRANT EXECUTE ON FUNCTION auth_register_user(text, text, text) TO ft_app;
    CREATE OR REPLACE FUNCTION registration_open() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT registration_open OR NOT EXISTS (SELECT 1 FROM users) FROM server_settings WHERE id = 1 $$;
    """)
