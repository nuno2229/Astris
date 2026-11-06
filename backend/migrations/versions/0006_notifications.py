"""E22: per-user notification channels (Discord / Telegram / ntfy), preferences, sent-log.

Revision ID: 0006
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE notification_channels (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind            text NOT NULL CHECK (kind IN ('discord', 'telegram', 'ntfy')),
        label           text NOT NULL CHECK (length(label) BETWEEN 1 AND 60),
        config          jsonb NOT NULL,          -- webhook URL / bot token + chat id / ntfy server + topic (+token): secret
        include_details boolean NOT NULL DEFAULT false,   -- names and amounts in messages (off = minimal)
        allow_internet  boolean NOT NULL DEFAULT false,
        enabled         boolean NOT NULL DEFAULT true,
        last_sent_at    timestamptz,
        last_error      text,
        created_at      timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX notification_channels_user_idx ON notification_channels (user_id);

    CREATE TABLE notification_prefs (
        user_id          uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        renew_days       int NOT NULL DEFAULT 3 CHECK (renew_days IN (1, 3, 7)),
        renewals         boolean NOT NULL DEFAULT true,
        trials           boolean NOT NULL DEFAULT true,
        cancelled_charges boolean NOT NULL DEFAULT true,
        missed_payments  boolean NOT NULL DEFAULT true
    );

    CREATE TABLE notification_log (
        user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        event_key  text NOT NULL,
        sent_at    timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, event_key)
    );

    ALTER TABLE notification_channels ENABLE ROW LEVEL SECURITY; ALTER TABLE notification_channels FORCE ROW LEVEL SECURITY;
    ALTER TABLE notification_prefs    ENABLE ROW LEVEL SECURITY; ALTER TABLE notification_prefs    FORCE ROW LEVEL SECURITY;
    ALTER TABLE notification_log      ENABLE ROW LEVEL SECURITY; ALTER TABLE notification_log      FORCE ROW LEVEL SECURITY;
    CREATE POLICY nc_own ON notification_channels FOR ALL USING (user_id = app_uid()) WITH CHECK (user_id = app_uid());
    CREATE POLICY np_own ON notification_prefs    FOR ALL USING (user_id = app_uid()) WITH CHECK (user_id = app_uid());
    CREATE POLICY nl_own ON notification_log      FOR ALL USING (user_id = app_uid()) WITH CHECK (user_id = app_uid());
    GRANT SELECT, INSERT, UPDATE, DELETE ON notification_channels, notification_prefs, notification_log TO ft_app;

    -- Scheduler discovery: users with at least one enabled channel (ids only; work then runs as each user, RLS).
    CREATE FUNCTION notify_users(p_limit int) RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT DISTINCT user_id FROM notification_channels WHERE enabled LIMIT least(p_limit, 500) $$;
    REVOKE ALL ON FUNCTION notify_users(int) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION notify_users(int) TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS notify_users(int);
    DROP TABLE IF EXISTS notification_log, notification_prefs, notification_channels;
    """)
