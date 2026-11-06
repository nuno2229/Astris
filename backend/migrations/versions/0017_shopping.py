"""E17: shopping lists (household or personal), items, favourites; assistant 'list_add' action.

Revision ID: 0017
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE shopping_lists (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id  uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        scope         text NOT NULL CHECK (scope IN ('household', 'personal')),
        owner_user_id uuid REFERENCES users(id) ON DELETE CASCADE,
        name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
        is_default    boolean NOT NULL DEFAULT false,
        created_by    uuid NOT NULL REFERENCES users(id),
        created_at    timestamptz NOT NULL DEFAULT now(),
        CHECK ((scope = 'personal') = (owner_user_id IS NOT NULL))
    );
    CREATE UNIQUE INDEX shopping_default_once ON shopping_lists (household_id) WHERE is_default;

    CREATE TABLE shopping_items (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        list_id     uuid NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
        name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
        quantity    numeric(10,3) NOT NULL DEFAULT 1 CHECK (quantity > 0 AND quantity < 100000),
        note        text CHECK (note IS NULL OR length(note) <= 200),
        expires_on  date,
        product_id  uuid REFERENCES products(id) ON DELETE SET NULL,
        source      text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'pantry', 'assistant', 'favourite')),
        checked     boolean NOT NULL DEFAULT false,
        checked_by  uuid REFERENCES users(id) ON DELETE SET NULL,
        checked_at  timestamptz,
        added_by    uuid REFERENCES users(id) ON DELETE SET NULL,
        added_at    timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX shopping_items_list_idx ON shopping_items (list_id, checked, added_at);

    CREATE TABLE favourites (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        user_id      uuid REFERENCES users(id) ON DELETE CASCADE,       -- NULL = whole household's favourite
        name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
        created_by   uuid NOT NULL REFERENCES users(id),
        created_at   timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX favourites_unique ON favourites (household_id, coalesce(user_id::text, ''), lower(name));

    ALTER TABLE shopping_lists ENABLE ROW LEVEL SECURITY; ALTER TABLE shopping_lists FORCE ROW LEVEL SECURITY;
    ALTER TABLE shopping_items ENABLE ROW LEVEL SECURITY; ALTER TABLE shopping_items FORCE ROW LEVEL SECURITY;
    ALTER TABLE favourites     ENABLE ROW LEVEL SECURITY; ALTER TABLE favourites     FORCE ROW LEVEL SECURITY;
    CREATE POLICY sl_select ON shopping_lists FOR SELECT
        USING (household_id IN (SELECT app_my_household_ids()) AND (scope = 'household' OR owner_user_id = app_uid()));
    CREATE POLICY sl_insert ON shopping_lists FOR INSERT
        WITH CHECK (household_id IN (SELECT app_my_household_ids()) AND created_by = app_uid()
                    AND (scope = 'household' OR owner_user_id = app_uid()));
    CREATE POLICY sl_modify ON shopping_lists FOR UPDATE
        USING ((scope = 'personal' AND owner_user_id = app_uid())
               OR (scope = 'household' AND (created_by = app_uid() OR app_my_role(household_id) IN ('owner', 'admin'))));
    CREATE POLICY sl_delete ON shopping_lists FOR DELETE
        USING (NOT is_default AND ((scope = 'personal' AND owner_user_id = app_uid())
               OR (scope = 'household' AND (created_by = app_uid() OR app_my_role(household_id) IN ('owner', 'admin')))));
    -- items: anyone who can see the list can add, tick and remove (it's a shared list)
    CREATE POLICY si_rw ON shopping_items FOR ALL USING (list_id IN (SELECT id FROM shopping_lists))
        WITH CHECK (list_id IN (SELECT id FROM shopping_lists));
    CREATE POLICY fav_select ON favourites FOR SELECT
        USING (household_id IN (SELECT app_my_household_ids()) AND (user_id IS NULL OR user_id = app_uid()));
    CREATE POLICY fav_write ON favourites FOR ALL
        USING (household_id IN (SELECT app_my_household_ids()) AND (user_id IS NULL OR user_id = app_uid()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids()) AND (user_id IS NULL OR user_id = app_uid())
                    AND created_by = app_uid());
    GRANT SELECT, INSERT, UPDATE, DELETE ON shopping_lists, shopping_items, favourites TO ft_app;

    ALTER TABLE copilot_actions DROP CONSTRAINT copilot_actions_kind_check;
    ALTER TABLE copilot_actions ADD CONSTRAINT copilot_actions_kind_check
        CHECK (kind IN ('set_budget', 'pantry_add', 'pantry_use', 'goal_contribute', 'list_add'));
    ALTER TABLE notification_prefs ADD COLUMN shopping boolean NOT NULL DEFAULT true;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE notification_prefs DROP COLUMN IF EXISTS shopping;
    ALTER TABLE copilot_actions DROP CONSTRAINT copilot_actions_kind_check;
    ALTER TABLE copilot_actions ADD CONSTRAINT copilot_actions_kind_check
        CHECK (kind IN ('set_budget', 'pantry_add', 'pantry_use', 'goal_contribute'));
    DROP TABLE IF EXISTS favourites, shopping_items, shopping_lists;
    """)
