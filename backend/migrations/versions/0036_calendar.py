"""G-5: known bills and income from a calendar (.ics file, or an opt-in calendar link).

Revision ID: 0036
"""
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE calendar_feeds (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        created_by   uuid NOT NULL DEFAULT app_uid() REFERENCES users(id) ON DELETE CASCADE,
        name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
        url          text CHECK (url IS NULL OR length(url) <= 1000),     -- secret calendar links: never sent back to the app
        fetched_at   timestamptz,
        last_error   text CHECK (last_error IS NULL OR length(last_error) <= 200),
        created_at   timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE calendar_items (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        feed_id      uuid NOT NULL REFERENCES calendar_feeds(id) ON DELETE CASCADE,
        uid          text NOT NULL CHECK (length(uid) BETWEEN 1 AND 300),
        name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
        amount       numeric(18,2) CHECK (amount IS NULL OR (amount >= 0 AND amount < 1e9)),
        currency     char(3) NOT NULL,
        direction    text NOT NULL CHECK (direction IN ('in', 'out')),
        start_date   date NOT NULL,
        cadence      text NOT NULL CHECK (cadence IN ('once', 'daily', 'weekly', 'monthly', 'yearly')),
        every        smallint NOT NULL DEFAULT 1 CHECK (every BETWEEN 1 AND 52),
        until        date,
        UNIQUE (feed_id, uid)
    );
    CREATE INDEX calendar_items_household ON calendar_items (household_id);
    ALTER TABLE calendar_feeds ENABLE ROW LEVEL SECURITY; ALTER TABLE calendar_feeds FORCE ROW LEVEL SECURITY;
    ALTER TABLE calendar_items ENABLE ROW LEVEL SECURITY; ALTER TABLE calendar_items FORCE ROW LEVEL SECURITY;
    CREATE POLICY cf_select ON calendar_feeds FOR SELECT USING (household_id IN (SELECT app_my_household_ids()));
    CREATE POLICY cf_write ON calendar_feeds FOR ALL
        USING (household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid())
        WITH CHECK (household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid());
    CREATE POLICY ci_select ON calendar_items FOR SELECT USING (household_id IN (SELECT app_my_household_ids()));
    CREATE POLICY ci_write ON calendar_items FOR ALL
        USING (EXISTS (SELECT 1 FROM calendar_feeds f WHERE f.id = feed_id AND f.created_by = app_uid()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids())
                    AND EXISTS (SELECT 1 FROM calendar_feeds f WHERE f.id = feed_id AND f.created_by = app_uid()
                                 AND f.household_id = calendar_items.household_id));
    GRANT SELECT, INSERT, UPDATE, DELETE ON calendar_feeds, calendar_items TO ft_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS calendar_items; DROP TABLE IF EXISTS calendar_feeds;")
