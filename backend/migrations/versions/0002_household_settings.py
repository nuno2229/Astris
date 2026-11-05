"""Household settings: local LLM endpoint (D-2 → configured in Settings, owner only).

Revision ID: 0002
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE household_settings (
        household_id  uuid PRIMARY KEY REFERENCES households(id) ON DELETE CASCADE,
        llm_enabled   boolean NOT NULL DEFAULT false,
        llm_base_url  text CHECK (llm_base_url IS NULL OR llm_base_url ~ '^https?://'),
        llm_model     text,
        -- a non-LAN URL means household data would leave home: must be explicitly acknowledged (§2, D-1)
        llm_allow_internet boolean NOT NULL DEFAULT false,
        updated_by    uuid REFERENCES users(id),
        updated_at    timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE household_settings ENABLE ROW LEVEL SECURITY;
    ALTER TABLE household_settings FORCE ROW LEVEL SECURITY;
    CREATE POLICY hs_select ON household_settings FOR SELECT USING (household_id IN (SELECT app_my_household_ids()));
    CREATE POLICY hs_write ON household_settings FOR ALL
        USING (app_my_role(household_id) = 'owner')
        WITH CHECK (app_my_role(household_id) = 'owner' AND updated_by = app_uid());
    GRANT SELECT, INSERT, UPDATE ON household_settings TO ft_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS household_settings")
