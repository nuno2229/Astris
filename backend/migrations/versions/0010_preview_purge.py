"""E09 hardening: global purge of expired import previews.

Revision ID: 0010
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE FUNCTION purge_expired_previews() RETURNS int
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ DECLARE n int; BEGIN DELETE FROM import_previews WHERE expires_at <= now(); GET DIAGNOSTICS n = ROW_COUNT; RETURN n; END $$;
    REVOKE ALL ON FUNCTION purge_expired_previews() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION purge_expired_previews() TO ft_app;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS purge_expired_previews()")
