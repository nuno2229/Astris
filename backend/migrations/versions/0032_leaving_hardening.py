"""G-9 hardening: only list someone as a former member when they actually left history behind.

Revision ID: 0032
"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE OR REPLACE FUNCTION leave_household(p_household uuid, p_keep boolean) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE uid uuid := app_uid(); r household_role; n int;
    BEGIN
      SELECT role INTO r FROM memberships WHERE household_id = p_household AND user_id = uid;
      IF r IS NULL THEN RAISE EXCEPTION 'not a member' USING ERRCODE = 'P0002'; END IF;
      IF r = 'owner' THEN RAISE EXCEPTION 'the owner can''t leave' USING ERRCODE = '42501'; END IF;
      DELETE FROM memberships WHERE household_id = p_household AND user_id = uid;
      IF p_keep THEN
        UPDATE household_accounts SET until = current_date, frozen_at = now()
         WHERE household_id = p_household AND frozen_at IS NULL
           AND account_id IN (SELECT id FROM accounts WHERE owner_user_id = uid);
        GET DIAGNOSTICS n = ROW_COUNT;
        IF n > 0 THEN
          INSERT INTO former_members (household_id, user_id, household_name)
          VALUES (p_household, uid, (SELECT name FROM households WHERE id = p_household))
          ON CONFLICT (household_id, user_id) DO UPDATE SET left_at = now(), household_name = EXCLUDED.household_name;
        END IF;
      ELSE
        DELETE FROM household_accounts
         WHERE household_id = p_household AND account_id IN (SELECT id FROM accounts WHERE owner_user_id = uid);
      END IF;
    END $$;
    """)


def downgrade() -> None:
    pass
