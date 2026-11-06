"""E09: bulk import wizard — staged previews (dry run) and import runs (whole-run rollback).

Revision ID: 0009
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    -- A parsed-but-not-saved file. Raw bytes kept so the user can adjust options and re-preview.
    CREATE TABLE import_previews (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        filename    text NOT NULL,
        raw         bytea NOT NULL,
        options     jsonb NOT NULL DEFAULT '{}'::jsonb,   -- day_first, column mapping
        created_at  timestamptz NOT NULL DEFAULT now(),
        expires_at  timestamptz NOT NULL DEFAULT now() + interval '1 day'
    );
    CREATE INDEX import_previews_user_idx ON import_previews (user_id);

    CREATE TABLE import_runs (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        files       int NOT NULL,
        rows_new    int NOT NULL,
        created_at  timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE import_batches ADD COLUMN run_id uuid REFERENCES import_runs(id) ON DELETE CASCADE;

    ALTER TABLE import_previews ENABLE ROW LEVEL SECURITY; ALTER TABLE import_previews FORCE ROW LEVEL SECURITY;
    ALTER TABLE import_runs     ENABLE ROW LEVEL SECURITY; ALTER TABLE import_runs     FORCE ROW LEVEL SECURITY;
    CREATE POLICY ip_own ON import_previews FOR ALL
        USING (user_id = app_uid())
        WITH CHECK (user_id = app_uid() AND account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()));
    CREATE POLICY ir_own ON import_runs FOR ALL USING (user_id = app_uid()) WITH CHECK (user_id = app_uid());
    GRANT SELECT, INSERT, UPDATE, DELETE ON import_previews, import_runs TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE import_batches DROP COLUMN IF EXISTS run_id;
    DROP TABLE IF EXISTS import_runs, import_previews;
    """)
