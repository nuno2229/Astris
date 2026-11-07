"""G-16: import profiles — the column layout and date order you chose for a bank's CSV, remembered per person
and applied the next time a file with the same header row is imported.

Revision ID: 0026
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE import_profiles (
        user_id    uuid NOT NULL DEFAULT app_uid() REFERENCES users(id) ON DELETE CASCADE,
        signature  text NOT NULL CHECK (signature ~ '^[0-9a-f]{64}$'),   -- sha256 of the normalised header row
        headers    text NOT NULL CHECK (length(headers) <= 600),          -- shown so you can recognise it
        options    jsonb NOT NULL CHECK (octet_length(options::text) <= 2000),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, signature)
    );
    ALTER TABLE import_profiles ENABLE ROW LEVEL SECURITY;
    ALTER TABLE import_profiles FORCE ROW LEVEL SECURITY;
    CREATE POLICY ip_own ON import_profiles FOR ALL USING (user_id = app_uid()) WITH CHECK (user_id = app_uid());
    GRANT SELECT, INSERT, UPDATE, DELETE ON import_profiles TO ft_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS import_profiles")
