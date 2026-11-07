"""G-9: when a member leaves, they choose whether the household keeps seeing their past transactions.

Kept history = the household_accounts row stays, frozen at the moment they left: only transactions dated up to
that day *and* already imported by then stay visible (anything imported later never shows). The leaver keeps no
access to the household. They (the account owner) can withdraw the history later from their Accounts screen.

Revision ID: 0029
"""
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE household_accounts ADD COLUMN until date, ADD COLUMN frozen_at timestamptz,
        ADD CONSTRAINT ha_frozen_pair CHECK ((until IS NULL) = (frozen_at IS NULL));

    CREATE TABLE former_members (
        household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        left_at      timestamptz NOT NULL DEFAULT now(),
        household_name text,                -- so the leaver can still tell which household holds their history
        PRIMARY KEY (household_id, user_id)
    );
    ALTER TABLE former_members ENABLE ROW LEVEL SECURITY;
    ALTER TABLE former_members FORCE ROW LEVEL SECURITY;
    CREATE POLICY fm_select ON former_members FOR SELECT
        USING (household_id IN (SELECT app_my_household_ids()) OR user_id = app_uid());
    CREATE POLICY fm_delete_self ON former_members FOR DELETE USING (user_id = app_uid());
    GRANT SELECT, DELETE ON former_members TO ft_app;

    -- everyone whose accounts can count in a household: current members + former members who kept their history
    CREATE VIEW household_people WITH (security_invoker = true) AS
        SELECT household_id, user_id FROM memberships
        UNION                                   -- distinct: never count someone twice
        SELECT household_id, user_id FROM former_members;
    GRANT SELECT ON household_people TO ft_app;

    -- frozen shares visible to me: former members' accounts in my households, with their cut-off
    CREATE FUNCTION app_frozen_shares() RETURNS TABLE (account_id uuid, until date, frozen_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
      SELECT ha.account_id, max(ha.until), max(ha.frozen_at)
        FROM household_accounts ha
        JOIN memberships me ON me.household_id = ha.household_id AND me.user_id = app_uid()
        JOIN accounts a ON a.id = ha.account_id AND a.owner_user_id <> app_uid()
        JOIN former_members f ON f.household_id = ha.household_id AND f.user_id = a.owner_user_id
       WHERE ha.frozen_at IS NOT NULL
       GROUP BY ha.account_id
    $$;
    REVOKE ALL ON FUNCTION app_frozen_shares() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION app_frozen_shares() TO ft_app;

    -- transactions: live visibility as before, plus a former member's lines up to the cut-off
    DROP POLICY tx_select ON transactions;
    CREATE POLICY tx_select ON transactions FOR SELECT USING (
        account_id IN (SELECT app_visible_account_ids())
        OR EXISTS (SELECT 1 FROM app_frozen_shares() f WHERE f.account_id = transactions.account_id
                    AND transactions.posted_on <= f.until AND transactions.created_at <= f.frozen_at));
    -- the account's name/type (for labels), not anything else about it
    DROP POLICY accounts_select ON accounts;
    CREATE POLICY accounts_select ON accounts FOR SELECT USING (
        owner_user_id = app_uid() OR id IN (SELECT app_visible_account_ids())
        OR id IN (SELECT account_id FROM app_frozen_shares()));
    -- former members' names, so their past lines still say whose they were
    DROP POLICY users_select ON users;
    CREATE POLICY users_select ON users FOR SELECT USING (
        id = app_uid()
        OR id IN (SELECT user_id FROM memberships WHERE household_id IN (SELECT app_my_household_ids()))
        OR id IN (SELECT user_id FROM former_members WHERE household_id IN (SELECT app_my_household_ids())));
    -- an account owner can always see and withdraw their own shares, even from a household they've left
    CREATE POLICY ha_owner_select ON household_accounts FOR SELECT
        USING (account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()));
    CREATE POLICY ha_owner_delete ON household_accounts FOR DELETE
        USING (account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()));

    -- leave: the member decides whether their past transactions stay with the household
    CREATE FUNCTION leave_household(p_household uuid, p_keep boolean) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE uid uuid := app_uid(); r household_role;
    BEGIN
      SELECT role INTO r FROM memberships WHERE household_id = p_household AND user_id = uid;
      IF r IS NULL THEN RAISE EXCEPTION 'not a member' USING ERRCODE = 'P0002'; END IF;
      IF r = 'owner' THEN RAISE EXCEPTION 'the owner can''t leave' USING ERRCODE = '42501'; END IF;
      DELETE FROM memberships WHERE household_id = p_household AND user_id = uid;
      IF p_keep THEN
        INSERT INTO former_members (household_id, user_id, household_name)
          VALUES (p_household, uid, (SELECT name FROM households WHERE id = p_household))
          ON CONFLICT (household_id, user_id) DO UPDATE SET left_at = now(), household_name = EXCLUDED.household_name;
        UPDATE household_accounts SET until = current_date, frozen_at = now()
         WHERE household_id = p_household AND frozen_at IS NULL
           AND account_id IN (SELECT id FROM accounts WHERE owner_user_id = uid);
      ELSE
        DELETE FROM household_accounts
         WHERE household_id = p_household AND account_id IN (SELECT id FROM accounts WHERE owner_user_id = uid);
      END IF;
    END $$;
    REVOKE ALL ON FUNCTION leave_household(uuid, boolean) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION leave_household(uuid, boolean) TO ft_app;
    """)


def downgrade() -> None:
    op.execute(r"""
    DROP FUNCTION IF EXISTS leave_household(uuid, boolean);
    DROP POLICY IF EXISTS ha_owner_delete ON household_accounts;
    DROP POLICY IF EXISTS ha_owner_select ON household_accounts;
    DROP POLICY users_select ON users;
    CREATE POLICY users_select ON users FOR SELECT USING (
        id = app_uid() OR id IN (SELECT user_id FROM memberships WHERE household_id IN (SELECT app_my_household_ids())));
    DROP POLICY accounts_select ON accounts;
    CREATE POLICY accounts_select ON accounts FOR SELECT USING (owner_user_id = app_uid() OR id IN (SELECT app_visible_account_ids()));
    DROP POLICY tx_select ON transactions;
    CREATE POLICY tx_select ON transactions FOR SELECT USING (account_id IN (SELECT app_visible_account_ids()));
    DROP FUNCTION IF EXISTS app_frozen_shares();
    DROP VIEW IF EXISTS household_people;
    DROP TABLE IF EXISTS former_members;
    ALTER TABLE household_accounts DROP CONSTRAINT IF EXISTS ha_frozen_pair, DROP COLUMN IF EXISTS until, DROP COLUMN IF EXISTS frozen_at;
    """)
